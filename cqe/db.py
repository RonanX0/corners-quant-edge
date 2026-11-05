"""Optional persistence (PostgreSQL on Vercel/Neon, SQLite for local use).

Enabled only when ``DATABASE_URL`` is set. Without it the API stays stateless
and the browser keeps bets and tracked opportunities in localStorage.

Access control: every private row has ``user_id``. Users authenticate with a
bearer token; only its SHA-256 is stored. ``APP_ACCESS_TOKEN`` bootstraps the
owner account. Requests without a valid token get 401 and never see rows of
another user.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, delete, insert, select, update)
from sqlalchemy.engine import Engine

metadata = MetaData()

users = Table("users", metadata,
              Column("id", String(36), primary_key=True),
              Column("name", String(120), nullable=False),
              Column("token_sha256", String(64), nullable=False, unique=True),
              Column("created_at", DateTime(timezone=True), nullable=False))
competitions = Table("competitions", metadata,
                     Column("code", String(16), primary_key=True),
                     Column("name", String(120), nullable=False),
                     Column("country", String(80)))
teams = Table("teams", metadata,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("name", String(120), nullable=False),
              Column("competition", String(16), ForeignKey("competitions.code")),
              UniqueConstraint("name", "competition"))
matches = Table("matches", metadata,
                Column("id", Integer, primary_key=True, autoincrement=True),
                Column("competition", String(16), nullable=False),
                Column("season", String(16)),
                Column("date", DateTime(timezone=False), nullable=False),
                Column("kickoff_utc", DateTime(timezone=True)),
                Column("home", String(120), nullable=False),
                Column("away", String(120), nullable=False),
                Column("source", String(80), nullable=False),
                UniqueConstraint("competition", "date", "home", "away"))
match_stats = Table("match_stats", metadata,           # results + statistics (final)
                    Column("match_id", Integer, ForeignKey("matches.id"), primary_key=True),
                    Column("home_corners", Integer), Column("away_corners", Integer),
                    Column("home_shots", Float), Column("away_shots", Float),
                    Column("home_sot", Float), Column("away_sot", Float),
                    Column("home_xg", Float), Column("away_xg", Float),
                    Column("referee", String(120)), Column("updated_at", DateTime(timezone=True)))
odds_snapshots = Table("odds_snapshots", metadata,
                       Column("id", Integer, primary_key=True, autoincrement=True),
                       Column("competition", String(120), nullable=False),
                       Column("date", String(10), nullable=False),
                       Column("home", String(120), nullable=False), Column("away", String(120), nullable=False),
                       Column("market", String(40), nullable=False, default="corners_total"),
                       Column("line", Float, nullable=False),
                       Column("over_odds", Float, nullable=False), Column("under_odds", Float, nullable=False),
                       Column("bookmaker", String(80), nullable=False),
                       Column("source", String(80), nullable=False),
                       Column("observed_at", DateTime(timezone=True), nullable=False),
                       Column("fetched_at", DateTime(timezone=True)),
                       Column("kickoff_utc", DateTime(timezone=True)),
                       UniqueConstraint("competition", "date", "home", "away", "line", "bookmaker", "observed_at"))
model_versions = Table("model_versions", metadata,
                       Column("version", String(80), primary_key=True),
                       Column("code", String(40), nullable=False),
                       Column("description", Text),
                       Column("created_at", DateTime(timezone=True), nullable=False),
                       Column("backtest", JSON))
predictions = Table("predictions", metadata,             # tracked opportunities / forecasts
                    Column("id", String(36), primary_key=True),
                    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
                    Column("created_at", DateTime(timezone=True), nullable=False),
                    Column("payload", JSON, nullable=False),
                    Column("status", String(20), nullable=False, default="acompanhando"))
bets = Table("bets", metadata,
             Column("id", String(36), primary_key=True),
             Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
             Column("date", String(10), nullable=False),
             Column("match", String(200), nullable=False),
             Column("market", String(60), nullable=False),
             Column("line", Float),
             Column("side", String(10)),
             Column("odd", Float, nullable=False),
             Column("bookmaker", String(80)),
             Column("stake", Float, nullable=False),
             Column("result", String(12), nullable=False, default="pendente"),
             Column("profit", Float),
             Column("rationale", Text),
             Column("model_version", String(80)),
             Column("created_at", DateTime(timezone=True), nullable=False),
             Column("updated_at", DateTime(timezone=True)))

BET_FIELDS = ("date", "match", "market", "line", "side", "odd", "bookmaker", "stake", "result", "profit",
              "rationale", "model_version")
RESULTS = ("pendente", "ganha", "perdida", "devolvida", "meio_ganha", "meio_perdida")


def _url() -> str | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def enabled() -> bool:
    return _url() is not None


@lru_cache(maxsize=4)
def _engine_for(url: str) -> Engine:
    eng = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2) if url.startswith("postgresql") \
        else create_engine(url)
    metadata.create_all(eng)
    _bootstrap_owner(eng)
    return eng


def engine() -> Engine:
    url = _url()
    if not url:
        raise RuntimeError("DATABASE_URL não configurada.")
    return _engine_for(url)


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc)


def _bootstrap_owner(eng: Engine) -> None:
    token = os.environ.get("APP_ACCESS_TOKEN", "").strip()
    if len(token) < 16:
        return
    with eng.begin() as c:
        if not c.execute(select(users.c.id).where(users.c.token_sha256 == sha(token))).first():
            c.execute(insert(users).values(id=str(uuid.uuid4()), name="Proprietário", token_sha256=sha(token), created_at=now()))


def user_for_token(token: str | None) -> str | None:
    if not token:
        return None
    with engine().connect() as c:
        row = c.execute(select(users.c.id).where(users.c.token_sha256 == sha(token))).first()
    return row[0] if row else None


def settle(odd: float, stake: float, result: str) -> float | None:
    return {"ganha": stake * (odd - 1), "perdida": -stake, "devolvida": 0.0,
            "meio_ganha": stake * (odd - 1) / 2, "meio_perdida": -stake / 2}.get(result)


def _clean_bet(data: dict) -> dict:
    out = {k: data.get(k) for k in BET_FIELDS if k in data}
    if out.get("result", "pendente") not in RESULTS:
        raise ValueError("Resultado inválido.")
    if "odd" in out and not (1.01 <= float(out["odd"]) <= 1000):
        raise ValueError("Odd inválida.")
    if "stake" in out and not (0 < float(out["stake"]) <= 1e7):
        raise ValueError("Valor apostado inválido.")
    return out


def list_bets(user_id: str) -> list[dict]:
    with engine().connect() as c:
        rows = c.execute(select(bets).where(bets.c.user_id == user_id).order_by(bets.c.date, bets.c.created_at)).mappings()
        return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items() if k != "user_id"} for r in rows]


def save_bet(user_id: str, data: dict) -> dict:
    vals = _clean_bet(data)
    vals.setdefault("result", "pendente")
    vals["profit"] = settle(float(vals["odd"]), float(vals["stake"]), vals["result"])
    bet_id = data.get("id") or str(uuid.uuid4())
    with engine().begin() as c:
        exists = c.execute(select(bets.c.id).where(bets.c.id == bet_id, bets.c.user_id == user_id)).first()
        if exists:
            c.execute(update(bets).where(bets.c.id == bet_id, bets.c.user_id == user_id).values(**vals, updated_at=now()))
        else:
            other = c.execute(select(bets.c.id).where(bets.c.id == bet_id)).first()
            if other:
                bet_id = str(uuid.uuid4())   # never touch another user's row
            c.execute(insert(bets).values(id=bet_id, user_id=user_id, created_at=now(), **vals))
    return {"id": bet_id, **vals}


def delete_bet(user_id: str, bet_id: str) -> bool:
    with engine().begin() as c:
        return c.execute(delete(bets).where(bets.c.id == bet_id, bets.c.user_id == user_id)).rowcount > 0


def track(user_id: str, payload: dict) -> str:
    pid = str(uuid.uuid4())
    with engine().begin() as c:
        c.execute(insert(predictions).values(id=pid, user_id=user_id, created_at=now(), payload=payload, status="acompanhando"))
    return pid


def list_tracked(user_id: str) -> list[dict]:
    with engine().connect() as c:
        rows = c.execute(select(predictions).where(predictions.c.user_id == user_id).order_by(predictions.c.created_at.desc())).mappings()
        return [{"id": r["id"], "created_at": r["created_at"].isoformat(), "status": r["status"], **(r["payload"] or {})} for r in rows]


def delete_tracked(user_id: str, pid: str) -> bool:
    with engine().begin() as c:
        return c.execute(delete(predictions).where(predictions.c.id == pid, predictions.c.user_id == user_id)).rowcount > 0


def store_odds(rows: list[dict]) -> int:
    """Insert odds snapshots (idempotent on the unique key). Returns rows inserted."""
    n = 0
    with engine().begin() as c:
        for r in rows:
            key = (odds_snapshots.c.competition == r["competition"]) & (odds_snapshots.c.date == r["date"]) & \
                  (odds_snapshots.c.home == r["home"]) & (odds_snapshots.c.away == r["away"]) & \
                  (odds_snapshots.c.line == r["line"]) & (odds_snapshots.c.bookmaker == r["bookmaker"]) & \
                  (odds_snapshots.c.observed_at == r["observed_at"])
            if not c.execute(select(odds_snapshots.c.id).where(key)).first():
                c.execute(insert(odds_snapshots).values(**r))
                n += 1
    return n


def register_model(version: str, code: str, description: str, backtest: dict | None) -> None:
    with engine().begin() as c:
        if c.execute(select(model_versions.c.version).where(model_versions.c.version == version)).first():
            c.execute(update(model_versions).where(model_versions.c.version == version).values(backtest=backtest))
        else:
            c.execute(insert(model_versions).values(version=version, code=code, description=description,
                                                    created_at=now(), backtest=backtest))
