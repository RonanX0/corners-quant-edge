"""Corners Quant Edge v4 HTTP API (mounted by app.py under /api/v4).

Stateless by default: datasets come from football-data.co.uk (cached), the
bundled demo file, or CSV sent by the browser. Bets/tracked opportunities are
stored server-side only when DATABASE_URL is configured, behind a bearer token.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from cqe import VERSION, db
from cqe.analysis import CLASSES, Forecaster, build_radar, match_analysis, team_profile
from cqe.backtest import MODELS, odds_backtest, public, walk_forward
from cqe.http import ProviderError
from cqe.leagues import DEFAULT_LEAGUES, LEAGUES, code_for_label, label
from cqe.model import REFERENCE_LINES
from cqe.providers import (FOOTBALL_DATA, ODDS_API, all_status, attach_names, odds_to_frame, parse_user_fixtures)
from engine import DataError, parse_matches, parse_odds

log = logging.getLogger("cqe")
ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "data" / "historico_452_jogos.csv"
MAX_CSV = 8_000_000
MAX_ODDS_CSV = 2_000_000
router = APIRouter(prefix="/api/v4")


# --------------------------------------------------------------------------- request models
class DataSpec(BaseModel):
    source: Literal["demo", "football_data", "csv"] = "demo"
    leagues: list[str] = Field(default_factory=lambda: list(DEFAULT_LEAGUES), max_length=8)
    seasons: int = Field(default=3, ge=1, le=6)
    matches_csv: str | None = Field(default=None, max_length=MAX_CSV)


class BacktestReq(BaseModel):
    data: DataSpec = Field(default_factory=DataSpec)
    n_folds: int = Field(default=10, ge=4, le=24)
    main_line: float = Field(default=9.5, ge=.5, le=30)


class OddsBacktestReq(BacktestReq):
    odds_csv: str = Field(max_length=MAX_ODDS_CSV)
    min_ev: float = Field(default=.03, ge=0, le=.5)
    commission: float = Field(default=0.0, ge=0, le=.2)


class RadarReq(BaseModel):
    data: DataSpec = Field(default_factory=DataSpec)
    fixtures_csv: str | None = Field(default=None, max_length=MAX_ODDS_CSV)
    odds_csv: str | None = Field(default=None, max_length=MAX_ODDS_CSV)
    live_odds: bool = True
    model: str | None = None
    line: float | None = Field(default=None, ge=.5, le=30)
    league: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    min_ev: float = Field(default=.03, ge=0, le=.5)
    min_odd: float = Field(default=1.01, ge=1.01, le=100)
    max_odd: float = Field(default=100, ge=1.01, le=1000)
    stale_hours: float = Field(default=12, ge=.25, le=168)


class MatchReq(BaseModel):
    data: DataSpec = Field(default_factory=DataSpec)
    league: str
    home: str
    away: str
    date: str
    model: str | None = None
    odds_csv: str | None = Field(default=None, max_length=MAX_ODDS_CSV)
    live_odds: bool = True


class BetIn(BaseModel):
    id: str | None = Field(default=None, max_length=36)
    date: str = Field(max_length=10)
    match: str = Field(max_length=200)
    market: str = Field(default="Total de escanteios", max_length=60)
    line: float | None = None
    side: str | None = Field(default=None, max_length=10)
    odd: float = Field(ge=1.01, le=1000)
    bookmaker: str | None = Field(default=None, max_length=80)
    stake: float = Field(gt=0, le=1e7)
    result: str = "pendente"
    rationale: str | None = Field(default=None, max_length=2000)
    model_version: str | None = Field(default=None, max_length=80)


# --------------------------------------------------------------------------- dataset loading (cached)
def _hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:20]


_csv_store: dict[str, str] = {}


def spec_key(spec: DataSpec) -> tuple:
    if spec.source == "csv":
        if not spec.matches_csv:
            raise DataError("Fonte CSV selecionada, mas nenhum arquivo foi enviado.")
        h = _hash(spec.matches_csv)
        if len(_csv_store) > 6:
            _csv_store.clear()
        _csv_store[h] = spec.matches_csv
        return ("csv", h)
    if spec.source == "football_data":
        leagues = tuple(sorted({lg for lg in spec.leagues if lg in LEAGUES})) or DEFAULT_LEAGUES
        return ("football_data", leagues, spec.seasons, date.today().isoformat())
    return ("demo",)


@lru_cache(maxsize=6)
def _load(key: tuple):
    if key[0] == "csv":
        df = parse_matches(_csv_store[key[1]])
        info = {"source": "csv", "label": "CSV importado neste navegador", "warnings": [], "fetched_at": None,
                "live": False, "description": "Resultados enviados pelo navegador; não ficam armazenados no servidor."}
    elif key[0] == "football_data":
        df, warns = FOOTBALL_DATA.history(list(key[1]), key[2])
        info = {"source": "football_data", "label": "football-data.co.uk", "warnings": warns,
                "fetched_at": FOOTBALL_DATA.status.as_dict()["last_fetch"], "live": True,
                "description": f"Resultados reais: {', '.join(label(c) for c in key[1])}; {key[2]} temporada(s)."}
    else:
        df = parse_matches(SAMPLE)
        info = {"source": "demo", "label": "MODO DEMONSTRAÇÃO", "warnings": ["Modo demonstração: amostra fixa de 452 jogos, sem calendário futuro e sem odds."],
                "fetched_at": None, "live": False,
                "description": "Amostra estática incluída no repositório (open-matchday, 2026/27). Não é uma conexão ao vivo."}
    return df, info


def dataset(spec: DataSpec):
    try:
        return _load(spec_key(spec))
    except ProviderError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@lru_cache(maxsize=6)
def _forecaster(key: tuple) -> Forecaster:
    return Forecaster(_load(key)[0])


@lru_cache(maxsize=12)
def _walk_forward(key: tuple, n_folds: int, main_line: float):
    return walk_forward(_load(key)[0], n_folds=n_folds, main_line=main_line)


def skill_or_none(key):
    try:
        return _walk_forward(key, 10, 9.5)
    except DataError:
        return None


def handle(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except ProviderError as exc:
        log.warning("provider error: %s", exc)
        raise HTTPException(502, detail=str(exc)) from exc
    except (DataError, ValueError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def clean(obj):
    """Make numpy/pandas values JSON-safe (NaN -> None)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


# --------------------------------------------------------------------------- odds/fixtures assembly
def user_odds(text: str | None) -> pd.DataFrame | None:
    if not text:
        return None
    o = parse_odds(text.encode("utf-8"))
    return odds_to_frame(o)


def live_odds(spec: DataSpec, matches: pd.DataFrame, warnings: list[str]) -> pd.DataFrame | None:
    """Corners odds from The Odds API (only for football-data datasets, where names can be mapped)."""
    ODDS_API.refresh_config()
    if not ODDS_API.key:
        return None
    if spec.source != "football_data":
        warnings.append("Odds ao vivo só são cruzadas com o histórico do football-data.co.uk.")
        return None
    leagues = list(spec_key(spec)[1])
    try:
        raw = ODDS_API.corners_odds(leagues)
    except ProviderError as exc:
        warnings.append(f"The Odds API: {exc}")
        return None
    o = attach_names(raw, matches)
    if db.enabled() and len(o):
        try:
            snap = o.dropna(subset=["home", "away"])
            db.store_odds([{"competition": r.league, "date": str(r.date.date()), "home": r.home, "away": r.away,
                            "market": "corners_total", "line": float(r.line), "over_odds": float(r.over_odds),
                            "under_odds": float(r.under_odds), "bookmaker": r.bookmaker, "source": r.source,
                            "observed_at": pd.Timestamp(r.observed_at).to_pydatetime(),
                            "fetched_at": pd.Timestamp(r.fetched_at).to_pydatetime() if r.fetched_at else None,
                            "kickoff_utc": pd.Timestamp(r.commence_time).to_pydatetime()} for r in snap.itertuples(index=False)])
        except Exception as exc:  # noqa: BLE001 - storage must not break the radar
            log.warning("could not store odds snapshot: %s", exc)
            warnings.append("Não foi possível gravar o histórico de odds no banco.")
    return o


def fixtures_for(spec: DataSpec, fixtures_csv: str | None, warnings: list[str]) -> pd.DataFrame:
    frames = []
    if fixtures_csv:
        frames.append(parse_user_fixtures(fixtures_csv))
    if spec.source == "football_data":
        try:
            fx = FOOTBALL_DATA.fixtures()
            wanted = {label(c) for c in spec_key(spec)[1]}
            frames.append(fx[fx.league.isin(wanted)])
        except ProviderError as exc:
            warnings.append(f"Calendário football-data indisponível: {exc}")
    elif not fixtures_csv:
        warnings.append("Sem calendário: no modo demonstração ou CSV, importe um CSV de jogos futuros.")
    if not frames:
        return pd.DataFrame(columns=["date", "league", "home", "away", "kickoff_utc", "source"])
    return pd.concat(frames, ignore_index=True)


def default_model(key) -> str:
    wf = skill_or_none(key)
    return wf["selected"] if wf else "hier_nb"


# --------------------------------------------------------------------------- endpoints
@router.get("/status")
def status():
    return {"version": VERSION, "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "providers": all_status(), "database": db.enabled(),
            "auth_configured": len(os.environ.get("APP_ACCESS_TOKEN", "")) >= 16,
            "leagues": [{"code": c, "label": label(c)} for c in LEAGUES], "default_leagues": list(DEFAULT_LEAGUES),
            "models": MODELS, "reference_lines": list(REFERENCE_LINES), "classes": list(CLASSES)}


@router.post("/dataset")
def dataset_summary(spec: DataSpec):
    def run():
        df, info = dataset(spec)
        leagues = []
        for lg, g in df.groupby("league"):
            leagues.append({"name": lg, "matches": int(len(g)), "teams": sorted(set(g.home) | set(g.away)),
                            "avg_corners": float(g.total.mean()), "date_last": str(g.date.max().date()),
                            "over": {str(l): float((g.total > l).mean()) for l in REFERENCE_LINES}})
        seasons = sorted(df.season.unique().tolist()) if "season" in df else []
        stats_cols = [c for c in ("home_shots", "home_sot", "home_xg") if c in df and df[c].notna().any()]
        return clean({"info": info, "count": int(len(df)), "date_first": str(df.date.min().date()),
                      "date_last": str(df.date.max().date()), "avg_corners": float(df.total.mean()),
                      "leagues": sorted(leagues, key=lambda x: -x["matches"]), "seasons": seasons,
                      "optional_stats": stats_cols,
                      "over_frequency": {str(l): float((df.total > l).mean()) for l in REFERENCE_LINES}})
    return handle(run)


@router.post("/backtest")
def backtest(req: BacktestReq):
    def run():
        key = spec_key(req.data)
        dataset(req.data)
        return clean(public(_walk_forward(key, req.n_folds, req.main_line)))
    return handle(run)


@router.post("/backtest/odds")
def backtest_odds(req: OddsBacktestReq):
    def run():
        key = spec_key(req.data)
        dataset(req.data)
        wf = _walk_forward(key, req.n_folds, req.main_line)
        odds = parse_odds(req.odds_csv.encode("utf-8"))
        return clean(odds_backtest(wf, odds, req.min_ev, req.commission))
    return handle(run)


@router.post("/radar")
def radar(req: RadarReq):
    def run():
        key = spec_key(req.data)
        matches, info = dataset(req.data)
        warnings = list(info["warnings"])
        fc = _forecaster(key)
        model = req.model or default_model(key)
        if model not in MODELS:
            raise DataError("Modelo indisponível.")
        fixtures = fixtures_for(req.data, req.fixtures_csv, warnings)
        frames = []
        if req.live_odds:
            lo = live_odds(req.data, matches, warnings)
            if lo is not None and len(lo):
                frames.append(lo)
        uo = user_odds(req.odds_csv)
        if uo is not None and len(uo):
            frames.append(uo)
        odds = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        today = pd.Timestamp(date.today())
        d0 = pd.Timestamp(req.date_from) if req.date_from else today
        d1 = pd.Timestamp(req.date_to) if req.date_to else None
        def in_window(frame):
            if not len(frame):
                return frame
            d = pd.to_datetime(frame.date)
            mask = d >= d0
            if d1 is not None:
                mask &= d <= d1
            return frame[mask]

        fixtures, odds = in_window(fixtures), in_window(odds)
        if req.league:
            fixtures = fixtures[fixtures.league == req.league] if len(fixtures) else fixtures
            odds = odds[odds.league == req.league] if len(odds) else odds
        wf = skill_or_none(key)
        res = build_radar(fc, fixtures, odds, model=model, skill=wf["skill"] if wf else None, min_ev=req.min_ev,
                          min_odd=req.min_odd, max_odd=req.max_odd, line=req.line, stale_hours=req.stale_hours)
        if not wf:
            warnings.append("Sem backtest walk-forward para este conjunto: nenhum mercado pode ser classificado como VALOR POTENCIAL.")
        res["warnings"] = warnings + res["warnings"]
        res["data_source"] = info
        res["odds_status"] = ODDS_API.status.as_dict()
        res["odds_rows"] = int(len(odds))
        res["rows"].sort(key=lambda r: (r["ev"] is None, -(r["ev"] or 0)))
        return clean(res)
    return handle(run)


@router.post("/match")
def match(req: MatchReq):
    def run():
        key = spec_key(req.data)
        matches, info = dataset(req.data)
        fc = _forecaster(key)
        model = req.model or default_model(key)
        wf = skill_or_none(key)
        out = match_analysis(fc, req.league, req.home, req.away, req.date, model, None, wf["skill"] if wf else None)
        warnings = list(info["warnings"])
        frames = []
        if req.live_odds:
            lo = live_odds(req.data, matches, warnings)
            if lo is not None and len(lo):
                frames.append(lo)
        uo = user_odds(req.odds_csv)
        if uo is not None and len(uo):
            frames.append(uo)
        odds = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        fx = pd.DataFrame([{"date": pd.Timestamp(req.date), "league": req.league, "home": req.home, "away": req.away,
                            "kickoff_utc": None, "source": "consulta"}])
        if len(odds):
            odds = odds[(odds.league == req.league) & (odds.home == req.home) & (odds.away == req.away)]
        rad = build_radar(fc, fx, odds, model=model, skill=wf["skill"] if wf else None)
        out["markets"] = [r for r in rad["rows"] if r.get("odd") is not None]
        out["warnings"] = warnings + rad["warnings"]
        out["data_source"] = info
        return clean(out)
    return handle(run)


@router.post("/team")
def team(spec: DataSpec, name: str):
    def run():
        df, _ = dataset(spec)
        return clean(team_profile(df, name))
    return handle(run)


# --------------------------------------------------------------------------- private data (DB mode)
def require_user(authorization: str | None) -> str:
    if not db.enabled():
        raise HTTPException(404, detail="Banco de dados não configurado: os registros ficam no navegador.")
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = db.user_for_token(token) if token else None
    if not user:
        raise HTTPException(401, detail="Token de acesso ausente ou inválido.")
    return user


@router.get("/bets")
def bets_list(authorization: str | None = Header(default=None)):
    return {"bets": db.list_bets(require_user(authorization))}


@router.post("/bets")
def bets_save(bet: BetIn, authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    return handle(lambda: db.save_bet(user, bet.model_dump()))


@router.delete("/bets/{bet_id}")
def bets_delete(bet_id: str, authorization: str | None = Header(default=None)):
    if not db.delete_bet(require_user(authorization), bet_id):
        raise HTTPException(404, detail="Aposta não encontrada.")
    return {"ok": True}


@router.get("/tracked")
def tracked_list(authorization: str | None = Header(default=None)):
    return {"items": db.list_tracked(require_user(authorization))}


@router.post("/tracked")
def tracked_add(payload: dict, authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    if len(str(payload)) > 20_000:
        raise HTTPException(413, detail="Registro grande demais.")
    return {"id": db.track(user, payload)}


@router.delete("/tracked/{pid}")
def tracked_delete(pid: str, authorization: str | None = Header(default=None)):
    if not db.delete_tracked(require_user(authorization), pid):
        raise HTTPException(404, detail="Registro não encontrado.")
    return {"ok": True}


# --------------------------------------------------------------------------- scheduled jobs (Vercel Cron)
@router.get("/cron/snapshot-odds")
def cron_snapshot(request: Request):
    """Store a corners-odds snapshot (needs CRON_SECRET, THE_ODDS_API_KEY and DATABASE_URL)."""
    secret = os.environ.get("CRON_SECRET", "")
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not secret or not secrets.compare_digest(got, secret):
        raise HTTPException(401, detail="Não autorizado.")
    if not db.enabled() or not os.environ.get("THE_ODDS_API_KEY"):
        return {"ok": False, "reason": "DATABASE_URL ou THE_ODDS_API_KEY ausente."}
    spec = DataSpec(source="football_data", leagues=list(DEFAULT_LEAGUES), seasons=2)
    matches, _ = dataset(spec)
    warnings: list[str] = []
    started = time.time()
    o = live_odds(spec, matches, warnings)
    return {"ok": True, "rows": 0 if o is None else int(len(o)), "warnings": warnings, "seconds": round(time.time() - started, 2)}
