"""Data providers.

Every provider reports a ``status()`` so the UI can say plainly whether a source
is connected, disconnected (no credential), failing, or serving cached data.
Nothing here fabricates fixtures or prices: a missing source yields an empty
result plus a status message.

Sources implemented
-------------------
* football-data.co.uk — free CSVs (no key): historical results with corners,
  shots, shots on target, xG (recent seasons), referee; weekly ``fixtures.csv``
  with upcoming matches of the main leagues. No corners odds.
* The Odds API (v4) — pre-match corners totals (market
  ``alternate_totals_corners``) via the per-event odds endpoint. Needs
  ``THE_ODDS_API_KEY``. Each event request consumes credits.
* CSV supplied by the user (results, fixtures, odds) — kept in the browser.
"""
from __future__ import annotations

import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd

from engine import DataError, parse_matches
from . import teamnames
from .http import ProviderError, fetch
from .leagues import LEAGUES, label, recent_seasons, season_code, season_label

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if ts else None


class Status:
    def __init__(self, key: str, name: str, kind: str, docs: str, terms: str):
        self.key, self.name, self.kind, self.docs, self.terms = key, name, kind, docs, terms
        self.configured = True
        self.state = "nao_consultado"     # nao_consultado | conectado | cache | desconectado | erro
        self.message = "Ainda não consultado nesta instância."
        self.last_fetch: float | None = None
        self.extra: dict = {}

    def ok(self, fetched_at: float, from_cache: bool, message: str):
        self.state = "cache" if from_cache else "conectado"
        self.last_fetch = fetched_at
        self.message = message

    def fail(self, message: str):
        self.state = "erro"
        self.message = message

    def as_dict(self):
        return {"key": self.key, "name": self.name, "kind": self.kind, "configured": self.configured,
                "state": self.state, "message": self.message, "last_fetch": _iso(self.last_fetch),
                "docs": self.docs, "terms": self.terms, **self.extra}


# --------------------------------------------------------------------------- football-data.co.uk
class FootballData:
    BASE = "https://www.football-data.co.uk"
    TTL_PAST = 7 * 86400      # finished seasons barely change
    TTL_CURRENT = 6 * 3600    # current season is updated a few times a week
    TTL_FIXTURES = 3600

    def __init__(self):
        self.status = Status("football_data", "football-data.co.uk", "histórico + calendário",
                             "https://www.football-data.co.uk/notes.txt",
                             "Dados gratuitos publicados pelo site; confira as condições antes de redistribuir. Sem odds de escanteios.")
        self.fixtures_status = Status("football_data_fixtures", "football-data.co.uk — fixtures.csv", "calendário",
                                      "https://www.football-data.co.uk/matches.php",
                                      "Lista semanal publicada pelo site (atualizada às sextas e terças).")

    def season(self, league: str, season: str, today: date | None = None) -> pd.DataFrame:
        today = today or date.today()
        ttl = self.TTL_CURRENT if season == season_code(today) else self.TTL_PAST
        url = f"{self.BASE}/mmz4281/{season}/{league}.csv"
        item = fetch(url, ttl=ttl, max_bytes=2_000_000)
        try:
            df = parse_matches(item.body, drop_invalid=True)
        except DataError as exc:
            raise ProviderError(f"{label(league)} {season_label(season)}: {exc}") from exc
        df["league"] = label(league)
        df["league_code"] = league
        df["season"] = season_label(season)
        df.attrs["fetched_at"] = item.fetched_at
        df.attrs["from_cache"] = item.from_cache
        return df

    def history(self, leagues: list[str], seasons: int, today: date | None = None) -> tuple[pd.DataFrame, list[str]]:
        """Download leagues x seasons (in parallel, cached). Returns (matches, warnings)."""
        today = today or date.today()
        jobs = [(lg, s) for lg in leagues if lg in LEAGUES for s in recent_seasons(today, seasons)]
        if not jobs:
            raise DataError("Nenhuma liga válida selecionada.")
        frames, warnings, stamps, cached = [], [], [], True

        def run(job):
            lg, s = job
            try:
                return self.season(lg, s, today), None
            except ProviderError as exc:
                return None, str(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            for df, err in pool.map(run, jobs):
                if err:
                    warnings.append(err)
                elif df is not None and len(df):
                    frames.append(df)
                    stamps.append(df.attrs["fetched_at"])
                    cached = cached and df.attrs["from_cache"]
        if not frames:
            self.status.fail("; ".join(warnings[:3]) or "Sem dados.")
            raise ProviderError("football-data.co.uk indisponível: " + ("; ".join(warnings[:2]) or "sem dados"))
        out = pd.concat(frames, ignore_index=True).sort_values(["date", "league", "home", "away"]).reset_index(drop=True)
        self.status.ok(min(stamps), cached, f"{len(out)} partidas de {len(frames)} arquivos." +
                       (f" {len(warnings)} arquivo(s) com falha." if warnings else ""))
        return out, warnings

    def fixtures(self) -> pd.DataFrame:
        """Upcoming matches from fixtures.csv, kickoff converted from UK time to UTC."""
        try:
            item = fetch(f"{self.BASE}/fixtures.csv", ttl=self.TTL_FIXTURES, max_bytes=2_000_000)
        except ProviderError as exc:
            self.fixtures_status.fail(str(exc))
            raise
        raw = pd.read_csv(io.BytesIO(item.body), encoding="utf-8-sig", dtype=str).dropna(how="all")
        raw.columns = [c.strip() for c in raw.columns]
        need = {"Div", "Date", "HomeTeam", "AwayTeam"}
        if need - set(raw.columns):
            self.fixtures_status.fail("Formato inesperado em fixtures.csv.")
            raise ProviderError("fixtures.csv sem as colunas esperadas.")
        raw = raw[raw.Div.isin(LEAGUES)]
        rows = []
        for r in raw.itertuples(index=False):
            d = pd.to_datetime(r.Date, dayfirst=True, errors="coerce")
            if pd.isna(d):
                continue
            t = str(getattr(r, "Time", "") or "")
            kickoff = None
            if len(t) >= 4 and ":" in t:
                hh, mm = t.split(":")[:2]
                local = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=LONDON)
                kickoff = local.astimezone(UTC).isoformat(timespec="minutes")
            rows.append({"date": d.normalize(), "league": label(r.Div), "league_code": r.Div,
                         "home": str(r.HomeTeam).strip(), "away": str(r.AwayTeam).strip(),
                         "kickoff_utc": kickoff, "source": "football-data.co.uk"})
        df = pd.DataFrame(rows)
        self.fixtures_status.ok(item.fetched_at, item.from_cache, f"{len(df)} jogos na lista semanal.")
        return df


# --------------------------------------------------------------------------- The Odds API
class TheOddsAPI:
    BASE = "https://api.the-odds-api.com/v4"
    MARKET = "alternate_totals_corners"
    TTL_ODDS = 15 * 60
    TTL_EVENTS = 30 * 60

    def __init__(self):
        self.status = Status("the_odds_api", "The Odds API", "odds pré-jogo (escanteios)",
                             "https://the-odds-api.com/liveapi/guides/v4/",
                             "API oficial com chave. Mercado alternate_totals_corners consultado jogo a jogo; cada consulta consome créditos do plano.")
        self.refresh_config()

    def refresh_config(self):
        self.key = os.environ.get("THE_ODDS_API_KEY", "").strip()
        self.regions = os.environ.get("ODDS_API_REGIONS", "eu,uk")
        self.max_events = int(os.environ.get("ODDS_API_MAX_EVENTS", "12"))
        self.status.configured = bool(self.key)
        if not self.key:
            self.status.state = "desconectado"
            self.status.message = "THE_ODDS_API_KEY não configurada. Nenhuma odd ao vivo é exibida; importe um CSV de odds."

    def _get(self, path: str, params: dict, ttl: float):
        safe = f"{self.BASE}{path}?{urlencode(params)}"
        item = fetch(f"{self.BASE}{path}?{urlencode({**params, 'apiKey': self.key})}", ttl=ttl, safe_url=safe, max_bytes=3_000_000)
        if "x-requests-remaining" in item.headers:
            self.status.extra["credits_remaining"] = item.headers.get("x-requests-remaining")
            self.status.extra["credits_used"] = item.headers.get("x-requests-used")
        try:
            return json.loads(item.body), item
        except ValueError as exc:
            raise ProviderError("Resposta JSON inválida da The Odds API.") from exc

    def sports(self) -> set[str]:
        data, _ = self._get("/sports", {}, ttl=86400)
        return {s["key"] for s in data if isinstance(s, dict) and "key" in s}

    def corners_odds(self, leagues: list[str], hours_ahead: int = 72) -> pd.DataFrame:
        """Corners totals for upcoming events of the given leagues (one row per bookmaker/line)."""
        self.refresh_config()
        cols = ["event_id", "commence_time", "league", "league_code", "home_api", "away_api", "bookmaker",
                "line", "over_odds", "under_odds", "observed_at", "fetched_at", "source"]
        if not self.key:
            return pd.DataFrame(columns=cols)
        try:
            available = self.sports()
            now = datetime.now(UTC)
            events = []
            for lg in leagues:
                sport = LEAGUES.get(lg, {}).get("odds_api")
                if not sport or sport not in available:
                    continue
                evs, _ = self._get(f"/sports/{sport}/events", {"dateFormat": "iso"}, ttl=self.TTL_EVENTS)
                for ev in evs:
                    start = pd.Timestamp(ev.get("commence_time"))
                    if now <= start <= now + timedelta(hours=hours_ahead):
                        events.append((lg, sport, ev, start))
            events.sort(key=lambda x: x[3])
            skipped = max(0, len(events) - self.max_events)
            rows, last = [], None
            for lg, sport, ev, start in events[: self.max_events]:
                data, item = self._get(f"/sports/{sport}/events/{ev['id']}/odds",
                                       {"regions": self.regions, "markets": self.MARKET, "oddsFormat": "decimal", "dateFormat": "iso"},
                                       ttl=self.TTL_ODDS)
                last = item
                for bk in data.get("bookmakers", []):
                    for mk in bk.get("markets", []):
                        if mk.get("key") != self.MARKET:
                            continue
                        observed = mk.get("last_update") or bk.get("last_update")
                        by_point: dict[float, dict] = {}
                        for oc in mk.get("outcomes", []):
                            if oc.get("point") is None or oc.get("price") is None:
                                continue
                            side = str(oc.get("name", "")).lower()
                            if side not in ("over", "under"):
                                continue
                            by_point.setdefault(float(oc["point"]), {})[side] = float(oc["price"])
                        for point, prices in by_point.items():
                            if "over" in prices and "under" in prices:
                                rows.append({"event_id": ev["id"], "commence_time": ev.get("commence_time"),
                                             "league": label(lg), "league_code": lg,
                                             "home_api": ev.get("home_team"), "away_api": ev.get("away_team"),
                                             "bookmaker": bk.get("title") or bk.get("key"), "line": point,
                                             "over_odds": prices["over"], "under_odds": prices["under"],
                                             "observed_at": observed or _iso(item.fetched_at),
                                             "fetched_at": _iso(item.fetched_at), "source": "The Odds API"})
            msg = f"{len(rows)} cotações de escanteios em {min(len(events), self.max_events)} jogos."
            if skipped:
                msg += f" {skipped} jogo(s) não consultados (limite ODDS_API_MAX_EVENTS)."
            self.status.ok(last.fetched_at if last else time.time(), bool(last and last.from_cache), msg)
            return pd.DataFrame(rows, columns=cols)
        except ProviderError as exc:
            self.status.fail(str(exc))
            raise


# --------------------------------------------------------------------------- user CSV
ODDS_REQUIRED = {"date", "league", "home", "away", "line", "over_odds", "under_odds", "observed_at"}


def parse_user_fixtures(text: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str).dropna(how="all")
    except Exception as exc:  # noqa: BLE001
        raise DataError(f"CSV de jogos futuros inválido: {exc}") from exc
    df.columns = [c.strip().lower() for c in df.columns]
    if missing := {"date", "home", "away"} - set(df.columns):
        raise DataError("Colunas faltando nos jogos futuros: " + ", ".join(sorted(missing)))
    from engine import parse_dates
    df["date"] = parse_dates(df["date"])
    if df.date.isna().any():
        raise DataError("Há datas inválidas nos jogos futuros.")
    if "league" not in df:
        df["league"] = "Liga não informada"
    for c in ("league", "home", "away"):
        df[c] = df[c].astype(str).str.strip()
    df["kickoff_utc"] = df.get("kickoff_utc", df.get("kickoff_at"))
    df["source"] = "CSV importado"
    return df[["date", "league", "home", "away", "kickoff_utc", "source"]]


def odds_to_frame(odds: pd.DataFrame) -> pd.DataFrame:
    """Normalise user odds (engine.parse_odds output) to the radar row format."""
    out = odds.copy()
    out["source"] = "CSV importado (não autenticado)"
    out["observed_at"] = out["observed_at"].map(lambda x: x.isoformat() if pd.notna(x) else None)
    out["commence_time"] = out["kickoff_at"].map(lambda x: x.isoformat() if pd.notna(x) else None)
    out["fetched_at"] = None
    return out


FOOTBALL_DATA = FootballData()
ODDS_API = TheOddsAPI()


def attach_names(odds: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Resolve The Odds API team names to the historical dataset's names (per league)."""
    if odds.empty:
        return odds.assign(home=pd.Series(dtype=str), away=pd.Series(dtype=str), date=pd.Series(dtype="datetime64[ns]"))
    known = {lg: sorted(set(g.home) | set(g.away)) for lg, g in matches.groupby("league")}
    out = odds.copy()
    out["home"] = [teamnames.resolve(h, known.get(lg, [])) for h, lg in zip(out.home_api, out.league)]
    out["away"] = [teamnames.resolve(a, known.get(lg, [])) for a, lg in zip(out.away_api, out.league)]
    ct = pd.to_datetime(out.commence_time, utc=True)
    out["date"] = ct.dt.tz_convert(LONDON).dt.tz_localize(None).dt.normalize()
    return out


def all_status() -> list[dict]:
    ODDS_API.refresh_config()
    db = {"key": "database", "name": "Banco de dados", "kind": "persistência",
          "configured": bool(os.environ.get("DATABASE_URL")), "state": "conectado" if os.environ.get("DATABASE_URL") else "desconectado",
          "message": "DATABASE_URL configurada." if os.environ.get("DATABASE_URL") else
                     "Sem DATABASE_URL: apostas e oportunidades ficam no navegador (localStorage).",
          "last_fetch": None, "docs": "https://vercel.com/marketplace?category=storage&search=postgres", "terms": ""}
    return [FOOTBALL_DATA.status.as_dict(), FOOTBALL_DATA.fixtures_status.as_dict(), ODDS_API.status.as_dict(), db]
