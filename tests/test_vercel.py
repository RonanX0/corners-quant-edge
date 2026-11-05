"""API smoke tests for immutable, per-request Vercel deployment."""
import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from app import app, SAMPLE
from engine import DataError, parse_matches, parse_odds, roi_backtest, run_backtest

class VercelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)

    def query(self,action,**kw):
        return self.client.post('/api/query',json={'action':action,**kw})

    def test_pages_and_health(self):
        for url in ('/','/static/app.js','/static/style.css','/api/health'):
            self.assertEqual(self.client.get(url).status_code,200,url)

    def test_default_overview(self):
        j=self.query('overview').json()
        self.assertEqual(j['count'],452)
        self.assertEqual(j['odds_count'],0)
        self.assertEqual(j['split']['test'],55)

    def test_backtest_and_csv_download(self):
        r=self.query('backtest',params={'line':9.5,'limit':0})
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json()['predictions']),55)
        r=self.query('export_predictions',params={'line':9.5})
        self.assertEqual(len(r.json()['rows']),55)

    def test_forecast_and_market(self):
        r=self.query('forecast',params={'date':'2026-11-10','league':'Premier League',
          'home':'Arsenal','away':'Liverpool','line':9.5,'over_odds':1.9,'under_odds':1.9})
        self.assertEqual(r.status_code,200)
        self.assertIn('market',r.json())
        self.assertAlmostEqual(r.json()['p_over']+r.json()['p_under'],1,places=7)

    def test_import_validation_per_request(self):
        raw=SAMPLE.read_text(encoding='utf-8')
        v=self.client.post('/api/validate/matches',json={'csv':raw})
        self.assertEqual(v.status_code,200)
        custom=self.query('overview',matches_csv=raw).json()
        self.assertEqual(custom['source']['type'],'browser')
        normal=self.query('overview').json()
        self.assertEqual(normal['source']['type'],'amostra')

    def test_odds_missing(self):
        r=self.query('odds_backtest',params={'line':9.5,'min_ev':0.03})
        self.assertEqual(r.status_code,200)
        self.assertIsNone(r.json()['roi'])

    def test_prevent_server_file_writes(self):
        for uri in ('/api/reset','/api/upload/matches','/api/upload/odds'):
            self.assertEqual(self.client.post(uri).status_code,404)

    def test_invalid_inputs(self):
        self.assertEqual(self.client.post('/api/validate/matches',json={'csv':'bad,headers\n1,2'}).status_code,422)
        self.assertEqual(self.query('backtest',params={'line':9}).status_code,422)

class EngineFixesTest(unittest.TestCase):
    def test_day_first_dates(self):
        raw=('Date,HomeTeam,AwayTeam,HC,AC\n'
             '05/08/2016,Hull,Leicester,5,4\n'
             '13/08/2016,Burnley,Swansea,6,3\n')
        df=parse_matches(raw)
        self.assertEqual([str(d.date()) for d in df.date],['2016-08-05','2016-08-13'])

    def test_iso_dates_unchanged(self):
        df=parse_matches('date,home,away,home_corners,away_corners\n2026-03-04,A,B,1,2\n')
        self.assertEqual(str(df.date[0].date()),'2026-03-04')

    def test_mixed_date_formats_rejected(self):
        raw=('date,home,away,home_corners,away_corners\n'
             '2026-03-04,A,B,1,2\n05/03/2026,C,D,1,2\n')
        with self.assertRaises(DataError):
            parse_matches(raw)

    def _odds_rows(self,test,extra_rows):
        first=test.iloc[0]
        base=dict(date=str(first.date.date()),league=first.league,home=first.home,away=first.away,line=9.5)
        lines=['date,league,home,away,line,over_odds,under_odds,observed_at,kickoff_at,bookmaker']
        for over,under,obs,book in extra_rows:
            lines.append(f"{base['date']},{base['league']},{base['home']},{base['away']},9.5,{over},{under},"
                         f"{base['date']}T{obs}:00Z,{base['date']}T20:00:00Z,{book}")
        return parse_odds('\n'.join(lines).encode())

    def test_roi_uses_latest_quote_not_best(self):
        bt=run_backtest(parse_matches(SAMPLE),9.5)
        test=bt['_internal']['test']
        # An earlier, very generous quote on both sides would pass the EV filter;
        # the later (latest) quote has no value and must be the one used.
        odds=self._odds_rows(test,[(50,50,'10:00','Generosa'),(1.02,1.02,'18:00','Recente')])
        r=roi_backtest(bt,odds,0.03)
        self.assertEqual(r['n'],0)
        self.assertEqual(r['rejected']['cotacoes_duplicadas'],1)
        self.assertEqual(r['rejected']['sem_valor_robusto'],1)

    def test_bootstrap_is_deterministic(self):
        df=parse_matches(SAMPLE)
        a=run_backtest(df,9.5,include_rows=False)['uncertainty']
        b=run_backtest(df,9.5,include_rows=False)['uncertainty']
        self.assertEqual(a['ci95'],b['ci95'])
        self.assertLessEqual(a['ci95'][0],a['ci95'][1])


class ImportRateTest(unittest.TestCase):
    def test_limit(self):
        import app as app_module
        app_module._import_hits.clear()
        for _ in range(app_module.IMPORT_LIMIT):
            app_module.check_import_rate('1.2.3.4',now=100.0)
        with self.assertRaises(HTTPException) as ctx:
            app_module.check_import_rate('1.2.3.4',now=101.0)
        self.assertEqual(ctx.exception.status_code,429)
        app_module.check_import_rate('5.6.7.8',now=101.0)          # other client unaffected
        app_module.check_import_rate('1.2.3.4',now=100.0+app_module.IMPORT_WINDOW+1)  # window expired
        app_module._import_hits.clear()


if __name__=='__main__': unittest.main()
