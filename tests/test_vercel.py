"""API smoke tests for immutable, per-request Vercel deployment."""
import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import app, SAMPLE

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

if __name__=='__main__': unittest.main()
