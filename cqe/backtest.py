"""Walk-forward validation of corner models.

Protocol (no look-ahead):
1. Dates are sorted. The first ``warmup`` share of dates is only ever training.
2. The remaining dates are cut into ``n_folds`` consecutive blocks. For each
   block, every model is (re)fitted on matches strictly before the block and
   predicts the block.
3. The first half of the blocks is the VALIDATION period, the second half the
   TEST period. The model is selected on validation only; the test period is
   reported as-is and never used for choices.

Features of the legacy engine (rolling form) are computed online, day by day,
so they are pre-match by construction.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from engine import DataError, build_features, estimate_alpha, fit_nb_regression, predict_params
from . import VERSION
from .model import MAX_K, REFERENCE_LINES, expected_profit, fit_team_strength, no_vig, total_pmf

MODELS = {
    "liga_nb": "Média da liga · Binomial negativa (modelo-base)",
    "form5_pois": "Forma L5 · Poisson",
    "form10_nb": "Forma L10 · Binomial negativa",
    "venue10_nb": "Mando L10 · Binomial negativa",
    "nb_regression": "Regressão binomial negativa",
    "hier_pois": "Força ataque/defesa · Poisson · meia-vida 240d",
    "hier_nb": "Força ataque/defesa · Binomial negativa · meia-vida 240d",
    "hier_nb_rapido": "Força ataque/defesa · Binomial negativa · meia-vida 90d",
}
BASELINE = "liga_nb"


def model_version(code: str) -> str:
    return f"cqe-{VERSION}/{code}"


def _metrics(pmf: np.ndarray, y: np.ndarray, lines) -> dict:
    yk = np.clip(y.astype(int), 0, MAX_K)
    k = np.arange(pmf.shape[1])
    mean = (pmf * k).sum(1)
    out = {"n": int(len(y)), "nll": float(np.mean(-np.log(np.clip(pmf[np.arange(len(y)), yk], 1e-12, 1)))),
           "mae": float(np.mean(np.abs(mean - y))), "mean_pred": float(mean.mean()), "mean_obs": float(y.mean()),
           "lines": {}}
    for ln in lines:
        p = np.clip(pmf[:, k > ln].sum(1), 1e-6, 1 - 1e-6)
        a = (y > ln).astype(float)
        out["lines"][str(ln)] = {"brier": float(np.mean((p - a) ** 2)),
                                 "logloss": float(np.mean(-(a * np.log(p) + (1 - a) * np.log(1 - p)))),
                                 "accuracy": float(np.mean((p >= .5) == (a == 1))),
                                 "over_obs": float(a.mean()), "p_mean": float(p.mean())}
    return out


def _date_bootstrap(delta: np.ndarray, dates: np.ndarray, boots: int = 1000, seed: int = 20261104):
    _, g = np.unique(dates, return_inverse=True)
    G = int(g.max()) + 1 if len(g) else 0
    if G < 5:
        return None, None
    sums = np.bincount(g, weights=delta, minlength=G)
    cnt = np.bincount(g, minlength=G).astype(float)
    pick = np.random.default_rng(seed).integers(0, G, size=(boots, G))
    stats = sums[pick].sum(1) / cnt[pick].sum(1)
    lo, hi = np.quantile(stats, [.025, .975])
    return float(lo), float(hi)


def _verdict(lo, hi) -> str:
    if lo is None:
        return "Amostra insuficiente"
    if hi < 0:
        return "Melhor que o modelo-base"
    if lo > 0:
        return "Pior que o modelo-base"
    return "Inconclusivo"


def walk_forward(df: pd.DataFrame, n_folds: int = 10, warmup: float = .35, min_history: int = 4,
                 lines=REFERENCE_LINES, main_line: float = 9.5) -> dict:
    df = df.sort_values(["date", "league", "home", "away"]).reset_index(drop=True)
    dates = np.sort(df.date.unique())
    if len(df) < 300 or len(dates) < 30:
        raise DataError("Walk-forward exige pelo menos 300 partidas em 30 datas diferentes. Amplie o histórico.")
    feats = build_features(df[["date", "league", "home", "away", "home_corners", "away_corners", "total"]], min_history)
    if len(feats) != len(df) or not (feats.home.to_numpy() == df.home.to_numpy()).all():
        raise RuntimeError("Desalinhamento interno de features.")
    feats["season"] = df["season"].to_numpy() if "season" in df else ""
    start = int(len(dates) * warmup)
    blocks = []
    for b in np.array_split(dates[start:], n_folds):
        if not len(b):
            continue
        fold = feats[(feats.date >= b[0]) & (feats.date <= b[-1]) & feats.eligible]
        train = feats[(feats.date < b[0]) & feats.eligible]
        if len(fold) and len(train) >= 40:
            blocks.append((b[0], b[-1], fold, train))
    if len(blocks) < 2:
        raise DataError("Histórico insuficiente para formar blocos de validação e teste no walk-forward.")
    half = len(blocks) // 2
    pieces, folds = [], []
    for k, (f0, f1, fold, train) in enumerate(blocks):
        a_form = estimate_alpha(train)
        a_base = estimate_alpha(train, "baseline")
        reg = fit_nb_regression(train)
        hs = fit_team_strength(df, f0, half_life=240)
        hs_fast = fit_team_strength(df, f0, half_life=90)
        period = "validacao" if k < half else "teste"
        folds.append({"fold": k + 1, "start": str(pd.Timestamp(f0).date()), "end": str(pd.Timestamp(f1).date()),
                      "n": int(len(fold)), "train": int((df.date < f0).sum()), "period": period})
        base = fold[["date", "league", "home", "away", "total", "season"]].copy()
        base["fold"] = k + 1
        base["period"] = period
        specs = {"liga_nb": (fold.baseline.to_numpy(), None, a_base)}
        for code in ("form5_pois", "form10_nb", "venue10_nb", "nb_regression"):
            mu, a = predict_params(fold, code, reg, a_form)
            specs[code] = (mu, None, a)
        for code, model, alpha in (("hier_pois", hs, 0.0), ("hier_nb", hs, hs.alpha), ("hier_nb_rapido", hs_fast, hs_fast.alpha)):
            pf = model.predict_frame(fold)
            specs[code] = (None, (pf.mu_home.to_numpy(), pf.mu_away.to_numpy()), alpha)
        for code, (mu, pair, alpha) in specs.items():
            part = base.copy()
            part["model"] = code
            part["alpha"] = alpha
            if pair is None:
                part["mu_home"], part["mu_away"], part["mu"] = np.nan, np.nan, mu
            else:
                part["mu_home"], part["mu_away"], part["mu"] = pair[0], pair[1], pair[0] + pair[1]
            pieces.append(part)
    if not pieces or half == 0:
        raise DataError("Não foi possível formar blocos de validação e teste.")
    preds = pd.concat(pieces, ignore_index=True)
    pmfs = {}
    for code, g in preds.groupby("model", sort=False):
        if g.mu_home.notna().all():
            pmfs[code] = total_pmf(g.mu_home.to_numpy(), g.mu_away.to_numpy(), g.alpha.to_numpy())
        else:
            pmfs[code] = total_pmf(mu=g.mu.to_numpy(), alpha=g.alpha.to_numpy())
    idx = {code: g.index.to_numpy() for code, g in preds.groupby("model", sort=False)}

    def subset(code, mask_fn):
        g = preds.loc[idx[code]]
        m = mask_fn(g).to_numpy()
        return pmfs[code][m], g[m]

    results = []
    for code in MODELS:
        if code not in pmfs:
            continue
        pv, gv = subset(code, lambda g: g.period == "validacao")
        pt, gt = subset(code, lambda g: g.period == "teste")
        results.append({"code": code, "name": MODELS[code], "version": model_version(code),
                        "validation": _metrics(pv, gv.total.to_numpy(float), lines),
                        "test": _metrics(pt, gt.total.to_numpy(float), lines)})
    selected = min(results, key=lambda r: r["validation"]["nll"])["code"]
    for r in results:
        r["selected"] = r["code"] == selected

    # skill vs baseline on TEST, per model and line, with date-block bootstrap
    pb, gb = subset(BASELINE, lambda g: g.period == "teste")
    yb = gb.total.to_numpy(float)
    kk = np.arange(MAX_K + 1)
    skill = {}
    for r in results:
        pt, gt = subset(r["code"], lambda g: g.period == "teste")
        y = gt.total.to_numpy(float)
        d = gt.date.dt.strftime("%Y-%m-%d").to_numpy()
        per = {}
        yk = np.clip(y.astype(int), 0, MAX_K)
        nll_m = -np.log(np.clip(pt[np.arange(len(y)), yk], 1e-12, 1))
        nll_b = -np.log(np.clip(pb[np.arange(len(yb)), yk], 1e-12, 1))
        lo, hi = _date_bootstrap(nll_m - nll_b, d)
        per["nll"] = {"delta": float(np.mean(nll_m - nll_b)), "ci95": [lo, hi], "verdict": _verdict(lo, hi)}
        for ln in lines:
            pm = np.clip(pt[:, kk > ln].sum(1), 1e-6, 1 - 1e-6)
            pbl = np.clip(pb[:, kk > ln].sum(1), 1e-6, 1 - 1e-6)
            a = (y > ln).astype(float)
            ll = lambda p: -(a * np.log(p) + (1 - a) * np.log(1 - p))  # noqa: E731
            delta = ll(pm) - ll(pbl)
            lo, hi = _date_bootstrap(delta, d)
            per[str(ln)] = {"delta_logloss": float(delta.mean()), "ci95": [lo, hi], "verdict": _verdict(lo, hi)}
        if r["code"] == BASELINE:
            for v in per.values():
                v["verdict"] = "Referência"
        skill[r["code"]] = per
        r["skill"] = per

    # breakdowns for the selected model on TEST
    pt, gt = subset(selected, lambda g: g.period == "teste")
    y = gt.total.to_numpy(float)
    p_main = pt[:, kk > main_line].sum(1)
    pbm = pb[:, kk > main_line].sum(1)

    def breakdown(col):
        rows = []
        for key, g in gt.groupby(col):
            m = (gt[col] == key).to_numpy()
            a = (y[m] > main_line).astype(float)
            rows.append({"key": str(key), "n": int(m.sum()),
                         "brier": float(np.mean((p_main[m] - a) ** 2)), "brier_base": float(np.mean((pbm[m] - a) ** 2)),
                         "accuracy": float(np.mean((p_main[m] >= .5) == (a == 1))), "over_obs": float(a.mean()),
                         "mae": float(np.mean(np.abs((pt[m] * kk).sum(1) - y[m])))})
        return sorted(rows, key=lambda r: -r["n"])

    by_line = []
    for ln in lines:
        pl = pt[:, kk > ln].sum(1); a = (y > ln).astype(float)
        by_line.append({"key": str(ln), "n": int(len(y)), "brier": float(np.mean((pl - a) ** 2)),
                        "brier_base": float(np.mean((pb[:, kk > ln].sum(1) - a) ** 2)),
                        "accuracy": float(np.mean((pl >= .5) == (a == 1))), "over_obs": float(a.mean()),
                        "p_mean": float(pl.mean())})
    edges = np.linspace(0, 1, 11)
    calib = []
    a_main = (y > main_line).astype(float)
    for i in range(10):
        m = (p_main >= edges[i]) & (p_main < edges[i + 1] + (1e-9 if i == 9 else 0))
        calib.append({"range": f"{int(edges[i]*100)}–{int(edges[i+1]*100)}%", "n": int(m.sum()),
                      "predicted": float(p_main[m].mean()) if m.any() else None,
                      "observed": float(a_main[m].mean()) if m.any() else None})
    recent = gt.assign(p_over=p_main, mu_pred=(pt * kk).sum(1)).tail(300)
    pred_rows = [{"date": str(r.date.date()), "league": r.league, "home": r.home, "away": r.away,
                  "actual": int(r.total), "mu": round(float(r.mu_pred), 2), "p_over": round(float(r.p_over), 4),
                  "prediction": "Over" if r.p_over >= .5 else "Under",
                  "correct": bool((r.p_over >= .5) == (r.total > main_line))} for r in recent.itertuples(index=False)]
    sel = next(r for r in results if r["selected"])
    return {
        "main_line": main_line, "lines": list(lines), "total_matches": int(len(df)),
        "date_first": str(pd.Timestamp(dates[0]).date()), "date_last": str(pd.Timestamp(dates[-1]).date()),
        "warmup_until": str(pd.Timestamp(dates[start]).date()), "folds": folds,
        "n_validation": sel["validation"]["n"], "n_test": sel["test"]["n"],
        "selected": selected, "selected_name": MODELS[selected], "selected_version": model_version(selected),
        "baseline": BASELINE, "models": results, "skill": skill,
        "by_league": breakdown("league"), "by_season": breakdown("season"), "by_line": by_line,
        "calibration": calib, "predictions": pred_rows,
        "notes": ["Modelo escolhido apenas pela log-verossimilhança no período de validação.",
                  "O período de teste não influencia nenhuma escolha.",
                  "Frequências históricas são referência descritiva, não probabilidade verdadeira.",
                  "Sem odds históricas verificáveis não há ROI: as métricas medem qualidade probabilística."],
        "_internal": {"preds": preds, "pmfs": pmfs, "idx": idx},
    }


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def odds_backtest(wf: dict, odds: pd.DataFrame, min_ev: float = .03, commission: float = 0.0,
                  max_lead_days: float = 7, model: str | None = None) -> dict:
    """ROI on the TEST period using user-supplied timestamped odds; 1 unit per bet.

    One quote per fixture and line: the latest valid pre-match observation,
    chosen before EV is looked at. Conservative EV uses the expected count
    shifted by 10% against the bet.
    """
    model = model or wf["selected"]
    preds, pmfs, idx = wf["_internal"]["preds"], wf["_internal"]["pmfs"], wf["_internal"]["idx"]
    g = preds.loc[idx[model]].copy()
    g["row"] = np.arange(len(g))
    g = g[g.period == "teste"]
    key = ["date", "league", "home", "away"]
    d = g.merge(odds, on=key, how="inner")
    rej = {"sem_odds": int(len(g) - len(d.drop_duplicates(key))), "timestamp_invalido": 0, "fora_do_pre_jogo": 0,
           "cotacoes_duplicadas": 0, "sem_valor_robusto": 0}
    bad = d.observed_at.isna() | d.kickoff_at.isna()
    rej["timestamp_invalido"] = int(bad.sum()); d = d[~bad]
    lead = (d.kickoff_at - d.observed_at).dt.total_seconds()
    out = (lead <= 0) | (lead > max_lead_days * 86400)
    rej["fora_do_pre_jogo"] = int(out.sum()); d = d[~out]
    d = d.sort_values("observed_at", kind="stable")
    latest = d.drop_duplicates(key + ["line"], keep="last")
    rej["cotacoes_duplicadas"] = int(len(d) - len(latest))
    d = latest.sort_values(key + ["line"], kind="stable")
    has_close = {"closing_over_odds", "closing_under_odds"} <= set(d.columns)
    if has_close:
        for c in ("closing_over_odds", "closing_under_odds"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    P = pmfs[model]
    bets, seen = [], set()
    for r in d.itertuples(index=False):
        if (r.date, r.league, r.home, r.away) in seen:
            continue  # one bet per fixture (first line in sorted order; chosen before EV)
        base_row = P[int(r.row)][None, :]
        mu = float(r.mu)
        def shifted(f):
            if not math.isnan(r.mu_home):
                return total_pmf(r.mu_home * f, r.mu_away * f, r.alpha)
            return total_pmf(mu=mu * f, alpha=r.alpha)
        cands = []
        for side, odd, worst in (("over", r.over_odds, shifted(.9)), ("under", r.under_odds, shifted(1.1))):
            ev = float(expected_profit(base_row, r.line, side, odd)[0])
            ev_c = float(expected_profit(worst, r.line, side, odd)[0])
            cands.append((ev_c, ev, side, odd))
        ev_c, ev, side, odd = max(cands)
        if ev < min_ev or ev_c <= 0:
            rej["sem_valor_robusto"] += 1
            continue
        seen.add((r.date, r.league, r.home, r.away))
        profit = 0.0
        from .model import _line_parts
        for part, w in _line_parts(r.line):
            if (r.total > part) if side == "over" else (r.total < part):
                profit += w * (odd - 1) * (1 - commission)
            elif r.total != part:
                profit -= w
        clv = None
        if has_close:
            co, cu = getattr(r, "closing_over_odds"), getattr(r, "closing_under_odds")
            if pd.notna(co) and pd.notna(cu) and co > 1 and cu > 1:
                po, pu, _ = no_vig(co, cu)
                clv = odd * (po if side == "over" else pu) - 1
        bets.append({"date": str(r.date.date()), "league": r.league, "home": r.home, "away": r.away,
                     "line": float(r.line), "side": "Over" if side == "over" else "Under", "odd": float(odd),
                     "bookmaker": getattr(r, "bookmaker", ""), "ev": ev, "ev_conservative": ev_c,
                     "actual": int(r.total), "profit": profit, "clv": clv})
    n = len(bets)
    profits = np.array([b["profit"] for b in bets])
    cum = np.cumsum(profits) if n else np.array([])
    peak = np.maximum.accumulate(np.concatenate([[0], cum]))[1:] if n else np.array([])
    dd = float((peak - cum).max()) if n else 0.0
    ci = [None, None]
    if n >= 20:
        rng = np.random.default_rng(7)
        boots = profits[rng.integers(0, n, size=(2000, n))].mean(1)
        ci = [float(x) for x in np.quantile(boots, [.025, .975])]
    clvs = [b["clv"] for b in bets if b["clv"] is not None]
    for b, c in zip(bets, cum):
        b["cumulative"] = float(c)
    return {"model": model, "n": n, "profit": float(profits.sum()) if n else 0.0,
            "roi": float(profits.mean()) if n else None, "roi_ci95": ci,
            "wins": int(sum(b["profit"] > 0 for b in bets)), "max_drawdown": dd,
            "clv_mean": float(np.mean(clvs)) if clvs else None, "clv_n": len(clvs), "clv_available": has_close,
            "commission": commission, "bets": bets, "rejected": rej, "sample_warning": n < 200,
            "notes": "ROI retrospectivo hipotético no período de teste, com odds e horários informados pelo usuário "
                     "(não autenticados). 1 unidade por aposta; sem limites de casa ou liquidez. Mudar o EV mínimo "
                     "depois de ver o resultado gera sobreajuste."}
