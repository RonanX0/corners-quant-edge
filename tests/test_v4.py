"""Tests for Corners Quant Edge v4 (models, walk-forward, radar, storage, API)."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import parse_matches, parse_odds  # noqa: E402
from cqe import model as M  # noqa: E402
from cqe.analysis import Forecaster, build_radar, classify  # noqa: E402
from cqe.backtest import odds_backtest, walk_forward  # noqa: E402
from cqe.leagues import recent_seasons, season_code  # noqa: E402
from cqe.teamnames import resolve  # noqa: E402
from cqe.http import Fetched  # noqa: E402

SAMPLE = ROOT / "data" / "historico_452_jogos.csv"


def synthetic(n_teams=12, rounds=4, seed=1, start="2024-08-01"):
    """Round-robin league with known attack/defence effects."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    att = dict(zip(teams, rng.normal(0, .2, n_teams)))
    de = dict(zip(teams, rng.normal(0, .15, n_teams)))
    rows, day = [], pd.Timestamp(start)
    for r in range(rounds):
        for i, h in enumerate(teams):
            for a in teams[i + 1:]:
                hh, aa = (h, a) if (r + i) % 2 == 0 else (a, h)
                mh = np.exp(np.log(5.2) + .1 + att[hh] + de[aa])
                ma = np.exp(np.log(5.2) + att[aa] + de[hh])
                rows.append({"date": day, "league": "Liga X", "home": hh, "away": aa,
                             "home_corners": rng.poisson(mh), "away_corners": rng.poisson(ma)})
                day += pd.Timedelta(days=1) if len(rows) % 3 == 0 else pd.Timedelta(0)
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(["date", "league", "home", "away"])
    df["total"] = df.home_corners + df.away_corners
    return df.sort_values(["date", "league", "home", "away"]).reset_index(drop=True), att, de


class ModelMath(unittest.TestCase):
    def test_pmf_sums_to_one(self):
        for pmf in (M.total_pmf(mu=10, alpha=.05), M.total_pmf(5, 4.5, .03), M.total_pmf(mu=35, alpha=.2)):
            self.assertAlmostEqual(float(pmf.sum()), 1.0, places=9)

    def test_convolution_matches_poisson_sum(self):
        from scipy.stats import poisson
        pmf = M.total_pmf(4.0, 6.0, 0.0)[0]
        self.assertTrue(np.allclose(pmf[:30], poisson.pmf(np.arange(30), 10.0), atol=1e-12))

    def test_ev_half_integer_quarter(self):
        pmf = M.total_pmf(mu=10, alpha=0.0)
        k = np.arange(M.MAX_K + 1)
        over95 = pmf[0, k > 9.5].sum()
        self.assertAlmostEqual(M.expected_profit(pmf, 9.5, "over", 2.0)[0], over95 * 1 - (1 - over95))
        p_over10, p_push = pmf[0, k > 10].sum(), pmf[0, 10]
        self.assertAlmostEqual(M.expected_profit(pmf, 10, "over", 2.0)[0], p_over10 - (1 - p_over10 - p_push))
        q = M.expected_profit(pmf, 9.75, "over", 2.0)[0]
        self.assertAlmostEqual(q, .5 * M.expected_profit(pmf, 9.5, "over", 2.0)[0] + .5 * M.expected_profit(pmf, 10, "over", 2.0)[0])

    def test_fair_odd_zero_ev_and_no_vig(self):
        pmf = M.total_pmf(5.1, 4.6, .04)
        for line in (8.5, 10, 10.25):
            fo = M.fair_odd(pmf[0], line, "over")
            self.assertAlmostEqual(M.expected_profit(pmf, line, "over", fo)[0], 0, places=6)
        po, pu, margin = M.no_vig(1.90, 1.90)
        self.assertAlmostEqual(po, .5); self.assertAlmostEqual(pu, .5); self.assertAlmostEqual(margin, 2 / 1.9 - 1)

    def test_team_strength_recovers_effects(self):
        df, att, de = synthetic(rounds=8)
        fit = M.fit_team_strength(df, df.date.max() + pd.Timedelta(days=1), half_life=10_000, ridge_team=5)
        p = fit.params()
        est = np.array([p["att"][t] for t in sorted(att)]); true = np.array([att[t] for t in sorted(att)])
        self.assertGreater(np.corrcoef(est, true)[0, 1], .7)
        self.assertGreater(p["h"]["Liga X"], 0)

    def test_fit_ignores_matches_on_or_after_ref(self):
        df, _, _ = synthetic()
        ref = df.date.iloc[len(df) // 2]
        a = M.fit_team_strength(df, ref)
        df2 = df.copy(); df2.loc[df2.date >= ref, ["home_corners", "away_corners"]] = 30
        b = M.fit_team_strength(df2, ref)
        self.assertTrue(np.allclose(a.theta, b.theta))


class WalkForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = parse_matches(SAMPLE)
        cls.wf = walk_forward(cls.df, n_folds=6)

    def test_selection_uses_validation_only(self):
        sel = min(self.wf["models"], key=lambda m: m["validation"]["nll"])
        self.assertEqual(sel["code"], self.wf["selected"])
        periods = [f["period"] for f in self.wf["folds"]]
        self.assertEqual(periods, sorted(periods, key=lambda p: p != "validacao"))  # validation blocks come first

    def test_no_future_leakage(self):
        """Changing results after a block must not change that block's predictions."""
        folds = self.wf["folds"]
        cut = pd.Timestamp(folds[-1]["start"])
        df2 = self.df.copy()
        df2.loc[df2.date >= cut, ["home_corners", "away_corners"]] = 25
        df2["total"] = df2.home_corners + df2.away_corners
        wf2 = walk_forward(df2, n_folds=6)
        p1, p2 = self.wf["_internal"]["preds"], wf2["_internal"]["preds"]
        early1 = p1[p1.date < cut].reset_index(drop=True)
        early2 = p2[p2.date < cut].reset_index(drop=True)
        self.assertGreater(len(early1), 0)
        self.assertTrue(np.allclose(early1.mu.to_numpy(), early2.mu.to_numpy()))

    def test_metrics_present(self):
        m = self.wf["models"][0]["test"]
        for k in ("nll", "mae"):
            self.assertIn(k, m)
        self.assertIn("brier", m["lines"]["9.5"]); self.assertIn("logloss", m["lines"]["9.5"])
        self.assertTrue(self.wf["by_league"] and self.wf["calibration"])

    def test_odds_backtest_dedupe_drawdown_clv(self):
        preds = self.wf["_internal"]["preds"]
        test = preds[(preds.model == self.wf["selected"]) & (preds.period == "teste")].head(30)
        lines = ["date,league,home,away,line,over_odds,under_odds,observed_at,kickoff_at,bookmaker,closing_over_odds,closing_under_odds"]
        for r in test.itertuples(index=False):
            d = r.date.date()
            # an early generous quote and the later real quote: only the later one may be used
            lines.append(f"{d},{r.league},{r.home},{r.away},9.5,9.0,9.0,{d}T08:00:00Z,{d}T18:00:00Z,Velha,2.0,2.0")
            lines.append(f"{d},{r.league},{r.home},{r.away},9.5,2.40,2.40,{d}T12:00:00Z,{d}T18:00:00Z,Recente,2.0,2.0")
        odds = parse_odds("\n".join(lines).encode())
        res = odds_backtest(self.wf, odds, min_ev=.0)
        self.assertEqual(res["rejected"]["cotacoes_duplicadas"], len(test))
        self.assertTrue(all(b["odd"] == 2.4 for b in res["bets"]))
        self.assertGreaterEqual(res["max_drawdown"], 0)
        if res["bets"]:
            self.assertAlmostEqual(res["bets"][0]["clv"], 2.4 * .5 - 1)
            self.assertAlmostEqual(res["profit"], sum(b["profit"] for b in res["bets"]))


class RadarRules(unittest.TestCase):
    def test_classify(self):
        base = dict(has_quote=True, enough=True, fresh=True, ev=.08, ev_cons=.02, validated=True, divergence=.03, min_ev=.03, divergence_cap=.12)
        self.assertEqual(classify(**base)[0], "VALOR POTENCIAL")
        self.assertEqual(classify(**{**base, "validated": False})[0], "AGUARDAR CONFIRMAÇÃO")
        self.assertEqual(classify(**{**base, "ev_cons": -.01})[0], "AGUARDAR CONFIRMAÇÃO")
        self.assertEqual(classify(**{**base, "divergence": .2})[0], "AGUARDAR CONFIRMAÇÃO")
        self.assertEqual(classify(**{**base, "ev": -.01})[0], "SEM VANTAGEM")
        self.assertEqual(classify(**{**base, "fresh": False})[0], "DADOS INSUFICIENTES")
        self.assertEqual(classify(**{**base, "fresh": None})[0], "DADOS INSUFICIENTES")
        self.assertEqual(classify(**{**base, "has_quote": False})[0], "DADOS INSUFICIENTES")
        self.assertEqual(classify(**{**base, "enough": False})[0], "DADOS INSUFICIENTES")

    def test_radar_never_invents_odds(self):
        df = parse_matches(SAMPLE)
        fc = Forecaster(df)
        fx = pd.DataFrame([{"date": pd.Timestamp("2026-11-08"), "league": "Premier League", "home": "Arsenal",
                            "away": "Liverpool", "kickoff_utc": None, "source": "teste"}])
        res = build_radar(fc, fx, pd.DataFrame(), model="hier_nb", skill=None)
        self.assertEqual(len(res["rows"]), 1)
        row = res["rows"][0]
        self.assertIsNone(row["odd"]); self.assertIsNone(row["ev"])
        self.assertEqual(row["classification"], "DADOS INSUFICIENTES")
        self.assertIsNotNone(row["fair_odd"])

    def test_radar_validated_value(self):
        df = parse_matches(SAMPLE)
        fc = Forecaster(df)
        now = dt.datetime(2026, 11, 8, 10, tzinfo=dt.timezone.utc)
        fx = pd.DataFrame([{"date": pd.Timestamp("2026-11-08"), "league": "Premier League", "home": "Arsenal",
                            "away": "Liverpool", "kickoff_utc": "2026-11-08T15:00:00+00:00", "source": "teste"}])
        pr = fc.project("hier_nb", "Premier League", "Arsenal", "Liverpool", "2026-11-08")
        fo = M.fair_odd(pr["pmf_low"], 9.5, "over")          # price that is still +EV in the conservative case
        odd_over = round(fo * 1.08, 2)
        odds = pd.DataFrame([{"date": pd.Timestamp("2026-11-08"), "league": "Premier League", "home": "Arsenal", "away": "Liverpool",
                              "line": 9.5, "over_odds": odd_over, "under_odds": 1.30, "bookmaker": "Casa", "source": "teste",
                              "observed_at": "2026-11-08T09:30:00Z", "commence_time": "2026-11-08T15:00:00Z"}])
        skill = {"hier_nb": {"9.5": {"verdict": "Melhor que o modelo-base"}}}
        rows = build_radar(fc, fx, odds, model="hier_nb", skill=skill, now=now, divergence_cap=1)["rows"]
        over = next(r for r in rows if r["side"] == "Over")
        self.assertEqual(over["classification"], "VALOR POTENCIAL")
        self.assertGreater(over["ev_conservative"], 0)
        rows2 = build_radar(fc, fx, odds, model="hier_nb", skill=None, now=now, divergence_cap=1)["rows"]
        self.assertEqual(next(r for r in rows2 if r["side"] == "Over")["classification"], "AGUARDAR CONFIRMAÇÃO")
        stale = build_radar(fc, fx, odds, model="hier_nb", skill=skill, now=now + dt.timedelta(days=2), divergence_cap=1)["rows"]
        self.assertTrue(all(r["classification"] == "DADOS INSUFICIENTES" for r in stale))


class Helpers(unittest.TestCase):
    def test_seasons(self):
        self.assertEqual(season_code(dt.date(2026, 11, 4)), "2627")
        self.assertEqual(season_code(dt.date(2027, 3, 1)), "2627")
        self.assertEqual(recent_seasons(dt.date(2026, 11, 4), 3), ["2425", "2526", "2627"])

    def test_team_names(self):
        known = ["Man United", "Man City", "Newcastle", "Tottenham", "Nott'm Forest", "Wolves", "Brighton", "Arsenal"]
        self.assertEqual(resolve("Manchester United", known), "Man United")
        self.assertEqual(resolve("Manchester City", known), "Man City")
        self.assertEqual(resolve("Nottingham Forest", known), "Nott'm Forest")
        self.assertEqual(resolve("Arsenal FC", known), "Arsenal")
        self.assertIsNone(resolve("Real Madrid", known))

    def test_football_data_fixtures_parsing(self):
        from cqe import providers
        body = ("Div,Date,Time,HomeTeam,AwayTeam,B365H\nE0,08/11/2026,15:00,Arsenal,Chelsea,1.9\n"
                "XX,08/11/2026,15:00,A,B,2\nSP1,09/11/2026,20:00,Betis,Sevilla,2.1\n").encode()
        with mock.patch.object(providers, "fetch", return_value=Fetched("u", body, 200, {}, 0.0)):
            fx = providers.FootballData().fixtures()
        self.assertEqual(len(fx), 2)
        self.assertEqual(fx.iloc[0].kickoff_utc, "2026-11-08T15:00+00:00")  # GMT in November
        self.assertEqual(str(fx.iloc[0].date.date()), "2026-11-08")

    def test_odds_api_parsing_pairs_over_under(self):
        from cqe import providers
        import json
        api = providers.TheOddsAPI()
        start = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=20)).isoformat()
        responses = {
            "/sports": [{"key": "soccer_epl"}],
            "/sports/soccer_epl/events": [{"id": "e1", "commence_time": start, "home_team": "Arsenal", "away_team": "Chelsea"}],
            "/sports/soccer_epl/events/e1/odds": {"bookmakers": [{"title": "Book", "last_update": "2026-11-05T10:00:00Z", "markets": [
                {"key": "alternate_totals_corners", "last_update": "2026-11-05T10:00:00Z", "outcomes": [
                    {"name": "Over", "price": 1.9, "point": 9.5}, {"name": "Under", "price": 1.9, "point": 9.5},
                    {"name": "Over", "price": 2.5, "point": 10.5}]}]}]},
        }

        def fake_get(path, params, ttl):
            item = Fetched("u", b"", 200, {"x-requests-remaining": "99"}, 0.0)
            return responses[path], item

        with mock.patch.dict(os.environ, {"THE_ODDS_API_KEY": "k"}), mock.patch.object(api, "_get", side_effect=fake_get):
            df = api.corners_odds(["E0"])
        self.assertEqual(len(df), 1)                       # 10.5 has no Under: dropped, never imputed
        self.assertEqual(float(df.iloc[0].line), 9.5)
        self.assertIn(api.status.state, ("conectado", "cache"))


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{self.tmp}/t.db", "APP_ACCESS_TOKEN": "owner-token-1234567890"})
        self.env.start()
        from cqe import db
        db._engine_for.cache_clear()
        self.db = db

    def tearDown(self):
        self.env.stop()
        self.db._engine_for.cache_clear()

    def test_bets_isolated_per_user(self):
        db = self.db
        owner = db.user_for_token("owner-token-1234567890")
        self.assertIsNotNone(owner)
        self.assertIsNone(db.user_for_token("wrong"))
        with db.engine().begin() as c:
            c.execute(db.insert(db.users).values(id="u2", name="Outro", token_sha256=db.sha("other-token-12345678"), created_at=db.now()))
        b = db.save_bet(owner, {"date": "2026-11-01", "match": "A x B", "market": "Total de escanteios", "odd": 2.0, "stake": 10, "result": "ganha"})
        self.assertEqual(b["profit"], 10.0)
        self.assertEqual(len(db.list_bets(owner)), 1)
        self.assertEqual(db.list_bets("u2"), [])
        self.assertFalse(db.delete_bet("u2", b["id"]))
        # another user reusing the id creates its own row, never overwrites
        b2 = db.save_bet("u2", {"id": b["id"], "date": "2026-11-02", "match": "C x D", "market": "x", "odd": 1.5, "stake": 5})
        self.assertNotEqual(b2["id"], b["id"])
        self.assertEqual(db.list_bets(owner)[0]["match"], "A x B")
        self.assertTrue(db.delete_bet(owner, b["id"]))

    def test_api_requires_token(self):
        from fastapi.testclient import TestClient
        from app import app
        c = TestClient(app)
        self.assertEqual(c.get("/api/v4/bets").status_code, 401)
        self.assertEqual(c.get("/api/v4/bets", headers={"Authorization": "Bearer nope"}).status_code, 401)
        h = {"Authorization": "Bearer owner-token-1234567890"}
        r = c.post("/api/v4/bets", json={"date": "2026-11-01", "match": "A x B", "odd": 1.9, "stake": 10, "result": "perdida"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/v4/bets", headers=h).json()["bets"][0]["profit"], -10)
        self.assertEqual(c.post("/api/v4/tracked", json={"home": "A"}, headers=h).status_code, 200)
        self.assertEqual(len(c.get("/api/v4/tracked", headers=h).json()["items"]), 1)


class ApiV4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import app
        cls.c = TestClient(app)

    def test_status_and_demo_dataset(self):
        s = self.c.get("/api/v4/status").json()
        self.assertIn("providers", s)
        odds = next(p for p in s["providers"] if p["key"] == "the_odds_api")
        if not os.environ.get("THE_ODDS_API_KEY"):
            self.assertEqual(odds["state"], "desconectado")
        d = self.c.post("/api/v4/dataset", json={"source": "demo"}).json()
        self.assertEqual(d["count"], 452)
        self.assertFalse(d["info"]["live"])

    def test_backtest_radar_match_demo(self):
        r = self.c.post("/api/v4/backtest", json={"data": {"source": "demo"}, "n_folds": 6})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("_internal", r.json())
        fx = "date,league,home,away\n2026-11-08,Premier League,Arsenal,Liverpool\n"
        r = self.c.post("/api/v4/radar", json={"data": {"source": "demo"}, "fixtures_csv": fx, "date_from": "2026-11-01", "live_odds": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows"][0]["classification"], "DADOS INSUFICIENTES")
        r = self.c.post("/api/v4/match", json={"data": {"source": "demo"}, "league": "Premier League", "home": "Arsenal", "away": "Liverpool", "date": "2026-11-08", "model": "hier_nb"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["projection"]["distribution"]), 22)

    def test_bets_without_db(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertEqual(self.c.get("/api/v4/bets").status_code, 404)

    def test_csv_source_and_errors(self):
        r = self.c.post("/api/v4/dataset", json={"source": "csv"})
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/v4/dataset", json={"source": "csv", "matches_csv": SAMPLE.read_text(encoding="utf-8")})
        self.assertEqual(r.json()["count"], 452)

    def test_cron_requires_secret(self):
        self.assertEqual(self.c.get("/api/v4/cron/snapshot-odds").status_code, 401)


if __name__ == "__main__":
    unittest.main()
