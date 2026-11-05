"""Match analysis and the opportunity radar.

The radar never creates prices: rows without a real quote are shown with the
model's fair odds and the class DADOS INSUFICIENTES.

Classification (per quote and side)
-----------------------------------
* DADOS INSUFICIENTES — no quote, quote without timestamp, stale quote, quote
  after kickoff, or too little team history.
* SEM VANTAGEM — central EV <= 0.
* VALOR POTENCIAL — central EV >= minimum AND conservative EV > 0 (model
  probability at the unfavourable end of its uncertainty band) AND the model
  beat the baseline out-of-sample (walk-forward TEST, bootstrap CI below zero)
  at the nearest reference line AND the model does not diverge from the
  no-vig market by more than the cap.
* AGUARDAR CONFIRMAÇÃO — positive EV that fails any of the evidence checks.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engine import DataError, build_features, estimate_alpha, fit_nb_regression, make_match_features, predict_params
from .backtest import MODELS, model_version
from .model import (REFERENCE_LINES, expected_profit, fair_odd, fit_team_strength, no_vig, summarize, total_pmf,
                    win_prob)

Z80 = 1.2816
MIN_GAMES = 6
CLASSES = ("VALOR POTENCIAL", "AGUARDAR CONFIRMAÇÃO", "SEM VANTAGEM", "DADOS INSUFICIENTES")


class Forecaster:
    """All models fitted on the full history (for genuinely future fixtures)."""

    def __init__(self, matches: pd.DataFrame):
        self.matches = matches
        self.ref = matches.date.max() + pd.Timedelta(days=1)
        self._hs = {}
        cols = ["date", "league", "home", "away", "home_corners", "away_corners", "total"]
        feats = build_features(matches[cols])
        elig = feats[feats.eligible]
        if len(elig) < 30:
            raise DataError("Histórico insuficiente para projeções (mínimo de 30 partidas elegíveis).")
        self.alpha_form = estimate_alpha(elig)
        self.alpha_base = estimate_alpha(elig, "baseline")
        self.reg = fit_nb_regression(elig)

    def strength(self, half_life: float):
        if half_life not in self._hs:
            self._hs[half_life] = fit_team_strength(self.matches, self.ref, half_life=half_life)
        return self._hs[half_life]

    def project(self, model: str, league: str, home: str, away: str, date) -> dict:
        if model not in MODELS:
            raise DataError("Modelo indisponível.")
        if model.startswith("hier"):
            hs = self.strength(90 if model == "hier_nb_rapido" else 240)
            if league not in hs.leagues:
                raise DataError("Liga sem histórico no conjunto de dados.")
            p = hs.predict(league, home, away)
            alpha = 0.0 if model == "hier_pois" else hs.alpha
            mh, ma = p["mu_home"], p["mu_away"]
            pmf = total_pmf(mh, ma, alpha)[0]
            low = total_pmf(mh * math.exp(-Z80 * p["se_home"]), ma * math.exp(-Z80 * p["se_away"]), alpha)[0]
            high = total_pmf(mh * math.exp(Z80 * p["se_home"]), ma * math.exp(Z80 * p["se_away"]), alpha)[0]
            par = hs.params()
            comps = {"league_level": math.exp(par["c"][league]) * (1 + math.exp(par["h"][league])),
                     "home_advantage": math.exp(par["h"][league]) - 1,
                     "attack_home": math.exp(par["att"].get(home, 0)) - 1, "defense_away": math.exp(par["def"].get(away, 0)) - 1,
                     "attack_away": math.exp(par["att"].get(away, 0)) - 1, "defense_home": math.exp(par["def"].get(home, 0)) - 1}
            return {"model": model, "model_name": MODELS[model], "version": model_version(model),
                    "pmf": pmf, "pmf_low": low, "pmf_high": high, "mu": mh + ma, "mu_home": mh, "mu_away": ma,
                    "alpha": alpha, "games_home": p["games_home"], "games_away": p["games_away"],
                    "uncertainty": "intervalo de 80% dos parâmetros (informação de Fisher penalizada) + 5% de erro de especificação",
                    "components": comps, "enough": min(p["games_home"], p["games_away"]) >= MIN_GAMES}
        f = make_match_features(self.matches, str(pd.Timestamp(date).date()), league, home, away)
        if model == "liga_nb":
            mu, a = float(f["baseline"]), self.alpha_base
        else:
            m, a = predict_params([f], model, self.reg, self.alpha_form)
            mu = float(m[0])
        return {"model": model, "model_name": MODELS[model], "version": model_version(model),
                "pmf": total_pmf(mu=mu, alpha=a)[0], "pmf_low": total_pmf(mu=mu * .9, alpha=a)[0],
                "pmf_high": total_pmf(mu=mu * 1.1, alpha=a)[0], "mu": mu, "mu_home": None, "mu_away": None,
                "alpha": a, "games_home": f["n_home"], "games_away": f["n_away"],
                "uncertainty": "sensibilidade de ±10% na contagem esperada (não é intervalo formal)",
                "components": None, "enough": min(f["n_home"], f["n_away"]) >= MIN_GAMES}


# --------------------------------------------------------------------------- team profile
def team_profile(matches: pd.DataFrame, team: str, before=None) -> dict:
    df = matches if before is None else matches[matches.date < pd.Timestamp(before)]
    home = df[df.home == team].assign(cf=lambda x: x.home_corners, ca=lambda x: x.away_corners, venue="Casa", opp=lambda x: x.away)
    away = df[df.away == team].assign(cf=lambda x: x.away_corners, ca=lambda x: x.home_corners, venue="Fora", opp=lambda x: x.home)
    for side, src in (("shots", ("home_shots", "away_shots")), ("sot", ("home_sot", "away_sot")), ("xg", ("home_xg", "away_xg"))):
        if src[0] in df:
            home = home.assign(**{f"{side}_f": home[src[0]], f"{side}_a": home[src[1]]})
            away = away.assign(**{f"{side}_f": away[src[1]], f"{side}_a": away[src[0]]})
    g = pd.concat([home, away]).sort_values("date")
    if g.empty:
        return {"team": team, "games": 0}

    def window(x, n):
        t = x.tail(n)
        return {"n": int(len(t)), "for": float(t.cf.mean()) if len(t) else None,
                "against": float(t.ca.mean()) if len(t) else None,
                "total": float((t.cf + t.ca).mean()) if len(t) else None}

    out = {"team": team, "games": int(len(g)), "last5": window(g, 5), "last10": window(g, 10), "last20": window(g, 20),
           "home10": window(g[g.venue == "Casa"], 10), "away10": window(g[g.venue == "Fora"], 10)}
    if "season" in g and g.season.iloc[-1]:
        out["season"] = {**window(g[g.season == g.season.iloc[-1]], 999), "label": g.season.iloc[-1]}
    l5, l20 = out["last5"], out["last20"]
    out["trend"] = {"for": (l5["for"] - l20["for"]) if l20["n"] >= 10 else None,
                    "against": (l5["against"] - l20["against"]) if l20["n"] >= 10 else None}
    extra = {}
    for side in ("shots", "sot", "xg"):
        if f"{side}_f" in g and g[f"{side}_f"].tail(10).notna().sum() >= 5:
            t = g.tail(10)
            extra[side] = {"for": float(t[f"{side}_f"].mean()), "against": float(t[f"{side}_a"].mean())}
    out["shots_profile"] = extra
    tot = (g.cf + g.ca).tail(20)
    out["over_frequency"] = [{"line": ln, "freq": float((tot > ln).mean()), "n": int(len(tot))} for ln in REFERENCE_LINES]
    out["recent"] = [{"date": str(r.date.date()), "venue": r.venue, "opponent": r.opp, "for": int(r.cf), "against": int(r.ca)}
                     for r in g.tail(10).iloc[::-1].itertuples(index=False)]
    return out


# --------------------------------------------------------------------------- helpers
def _parse_ts(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _nearest_ref(line: float) -> str:
    return str(min(REFERENCE_LINES, key=lambda r: abs(r - line)))


def classify(*, has_quote: bool, enough: bool, fresh: bool | None, ev: float | None, ev_cons: float | None,
             validated: bool, divergence: float | None, min_ev: float, divergence_cap: float) -> tuple[str, list[str]]:
    reasons = []
    if not has_quote:
        return "DADOS INSUFICIENTES", ["Sem cotação de uma fonte configurada ou importada."]
    if not enough:
        return "DADOS INSUFICIENTES", [f"Menos de {MIN_GAMES} jogos de histórico para uma das equipes."]
    if fresh is None:
        return "DADOS INSUFICIENTES", ["Cotação sem horário de observação."]
    if not fresh:
        return "DADOS INSUFICIENTES", ["Cotação desatualizada ou observada após o início do jogo."]
    if ev <= 0:
        return "SEM VANTAGEM", ["EV central não positivo: o preço não supera a probabilidade estimada."]
    ok = True
    if ev < min_ev:
        ok = False; reasons.append(f"EV central abaixo do mínimo de {min_ev:.0%}.")
    if ev_cons <= 0:
        ok = False; reasons.append("EV desaparece no cenário conservador da incerteza do modelo.")
    if not validated:
        ok = False; reasons.append("O modelo não superou o modelo-base de forma conclusiva no teste walk-forward desta linha.")
    if divergence is not None and abs(divergence) > divergence_cap:
        ok = False; reasons.append(f"Diferença de {abs(divergence):.0%} para o mercado sem margem: possível informação que o modelo não captura.")
    if ok:
        return "VALOR POTENCIAL", ["EV central e conservador positivos, modelo validado fora da amostra e preço coerente com o mercado."]
    return "AGUARDAR CONFIRMAÇÃO", reasons


def opp_id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def build_radar(fc: Forecaster, fixtures: pd.DataFrame, odds: pd.DataFrame, *, model: str, skill: dict | None,
                min_ev: float = .03, min_odd: float = 1.01, max_odd: float = 100.0, line: float | None = None,
                stale_hours: float = 12, divergence_cap: float = .12, now: datetime | None = None) -> dict:
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    rows, warnings, projections = [], [], {}
    odds = odds.copy() if odds is not None else pd.DataFrame()
    if not odds.empty:
        unmapped = odds[odds.home.isna() | odds.away.isna()]
        if len(unmapped):
            names = sorted({f"{a} x {b}" for a, b in zip(unmapped.get("home_api", unmapped.home), unmapped.get("away_api", unmapped.away))})
            warnings.append(f"{len(names)} jogo(s) com odds sem correspondência de nomes no histórico: " + "; ".join(names[:6]))
        odds = odds.dropna(subset=["home", "away"])
        odds["obs_ts"] = odds.observed_at.map(_parse_ts)
        odds = odds.sort_values("obs_ts", na_position="first").drop_duplicates(
            ["date", "league", "home", "away", "bookmaker", "line"], keep="last")
    fx = fixtures.copy()
    # fixtures that only exist in the odds feed are real events too
    if not odds.empty:
        extra = odds.drop_duplicates(["date", "league", "home", "away"])[["date", "league", "home", "away", "commence_time", "source"]]
        extra = extra.rename(columns={"commence_time": "kickoff_utc"})
        fx = pd.concat([fx, extra], ignore_index=True)
    fx = fx.drop_duplicates(["date", "league", "home", "away"]).sort_values(["date", "league", "home"])
    for f in fx.itertuples(index=False):
        key = (f.league, f.home, f.away)
        fixture = {"date": str(pd.Timestamp(f.date).date()), "league": f.league, "home": f.home, "away": f.away,
                   "kickoff_utc": f.kickoff_utc if isinstance(f.kickoff_utc, str) else None, "source": f.source}
        try:
            pr = fc.project(model, f.league, f.home, f.away, f.date)
            projections[key] = pr
        except (DataError, KeyError, ValueError) as exc:
            rows.append({**fixture, "id": opp_id(*key, f.date), "market": "Total de escanteios", "line": line or 9.5,
                         "side": None, "odd": None, "bookmaker": None, "p_implied": None, "p_novig": None, "p_model": None,
                         "fair_odd": None, "ev": None, "ev_conservative": None, "classification": "DADOS INSUFICIENTES",
                         "reasons": [f"Sem projeção: {exc}"], "observed_at": None, "odds_source": None})
            continue
        q = odds[(odds.league == f.league) & (odds.home == f.home) & (odds.away == f.away)] if not odds.empty else odds
        if not q.empty:
            q = q[(pd.to_datetime(q.date) - pd.Timestamp(f.date)).abs() <= pd.Timedelta(days=1)]
        if line is not None and not q.empty:
            q = q[(q.line - line).abs() < 1e-9]
        kickoff = _parse_ts(fixture["kickoff_utc"])
        if q.empty:
            ln = line or 9.5
            p_over = float(win_prob(pr["pmf"][None, :], ln, "over")[0])
            cls, why = classify(has_quote=False, enough=pr["enough"], fresh=None, ev=None, ev_cons=None, validated=False,
                                divergence=None, min_ev=min_ev, divergence_cap=divergence_cap)
            rows.append({**fixture, "id": opp_id(*key, f.date, ln), "market": "Total de escanteios", "line": ln, "side": "Over",
                         "odd": None, "bookmaker": None, "p_implied": None, "p_novig": None, "p_model": p_over,
                         "fair_odd": fair_odd(pr["pmf"], ln, "over"), "ev": None, "ev_conservative": None,
                         "mu": pr["mu"], "classification": cls, "reasons": why, "observed_at": None, "odds_source": None,
                         "model": pr["model"], "model_version": pr["version"]})
            continue
        for o in q.itertuples(index=False):
            obs = o.obs_ts
            fresh = None if obs is None else bool(now - obs <= pd.Timedelta(hours=stale_hours) and (kickoff is None or obs < kickoff))
            pn_over, pn_under, margin = no_vig(o.over_odds, o.under_odds)
            ref = _nearest_ref(o.line)
            validated = bool(skill and skill.get(model, {}).get(ref, {}).get("verdict") == "Melhor que o modelo-base")
            for side, odd, pn, worst in (("over", o.over_odds, pn_over, pr["pmf_low"]), ("under", o.under_odds, pn_under, pr["pmf_high"])):
                if not (min_odd <= odd <= max_odd):
                    continue
                pm = float(win_prob(pr["pmf"][None, :], o.line, side)[0])
                ev = float(expected_profit(pr["pmf"][None, :], o.line, side, odd)[0])
                ev_c = float(expected_profit(worst[None, :], o.line, side, odd)[0])
                cls, why = classify(has_quote=True, enough=pr["enough"], fresh=fresh, ev=ev, ev_cons=ev_c, validated=validated,
                                    divergence=pm - pn, min_ev=min_ev, divergence_cap=divergence_cap)
                rows.append({**fixture, "id": opp_id(*key, f.date, o.line, side, o.bookmaker), "market": "Total de escanteios",
                             "line": float(o.line), "side": "Over" if side == "over" else "Under", "odd": float(odd),
                             "bookmaker": o.bookmaker, "p_implied": 1 / odd, "p_novig": pn, "margin": margin,
                             "p_model": pm, "fair_odd": fair_odd(pr["pmf"], o.line, side), "ev": ev, "ev_conservative": ev_c,
                             "mu": pr["mu"], "classification": cls, "reasons": why,
                             "observed_at": obs.isoformat() if obs is not None else None,
                             "odds_source": getattr(o, "source", None), "model": pr["model"], "model_version": pr["version"],
                             "validated": validated})
    counts = {c: sum(r["classification"] == c for r in rows) for c in CLASSES}
    return {"rows": rows, "counts": counts, "warnings": warnings, "generated_at": now.isoformat(timespec="seconds"),
            "model": model, "model_name": MODELS.get(model, model), "fixtures": int(len(fx))}


def match_analysis(fc: Forecaster, league: str, home: str, away: str, date, model: str, odds: pd.DataFrame | None,
                   skill: dict | None, lines=REFERENCE_LINES) -> dict:
    pr = fc.project(model, league, home, away, date)
    summ = summarize(pr["pmf"], lines)
    low, high = summarize(pr["pmf_low"], lines), summarize(pr["pmf_high"], lines)
    for i, ln in enumerate(summ["lines"]):
        ln["p_over_low"] = low["lines"][i]["p_over"]
        ln["p_over_high"] = high["lines"][i]["p_over"]
        ref = _nearest_ref(ln["line"])
        ln["validation"] = (skill or {}).get(model, {}).get(ref, {}).get("verdict", "Sem backtest")
    m = fc.matches
    h2h = m[((m.home == home) & (m.away == away)) | ((m.home == away) & (m.away == home))].tail(6)
    risks = []
    if not pr["enough"]:
        risks.append(f"Histórico curto: {pr['games_home']} e {pr['games_away']} jogos das equipes na base.")
    if pr["alpha"] > .05:
        risks.append("Dispersão alta: totais de escanteios variam mais do que um Poisson simples prevê.")
    if (fc.ref - pd.Timestamp(date)).days < -21:
        risks.append("Jogo distante da última partida da base; a forma das equipes pode ter mudado.")
    risks.append("O modelo não usa escalações, lesões, clima, árbitro nem notícias do dia.")
    return {"fixture": {"league": league, "home": home, "away": away, "date": str(pd.Timestamp(date).date())},
            "model": {k: pr[k] for k in ("model", "model_name", "version", "mu", "mu_home", "mu_away", "alpha",
                                          "games_home", "games_away", "uncertainty", "components")},
            "projection": summ, "home_profile": team_profile(m, home), "away_profile": team_profile(m, away),
            "h2h": [{"date": str(r.date.date()), "home": r.home, "away": r.away, "home_corners": int(r.home_corners),
                     "away_corners": int(r.away_corners)} for r in h2h.iloc[::-1].itertuples(index=False)],
            "risks": risks, "data_last_date": str(m.date.max().date())}
