"""Stateless Corners Quant Edge FastAPI deployment on Vercel Python.

Shared files are read-only; user CSV data is sent with each request and retained
only in the user's browser. No shared write endpoints or user accounts.
"""
from __future__ import annotations

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import (DataError, METHODS, make_match_features, market_eval, new_prediction,
                    parse_matches, parse_odds, public_backtest, roi_backtest, run_backtest)

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / 'data' / 'historico_452_jogos.csv'
PUBLIC = ROOT / 'public'
MAX_MATCHES = 1_000_000
MAX_ODDS = 300_000
app = FastAPI(title='CORNERS QUANT EDGE / Vercel', version='3.1.0')

# Local development only; Vercel promotes public/ content to the CDN.
app.mount('/static', StaticFiles(directory=PUBLIC / 'static'), name='static')

class Query(BaseModel):
    action: Literal['overview','teams','backtest','forecast','odds_backtest','export_predictions']
    matches_csv: str | None = Field(default=None, max_length=MAX_MATCHES)
    odds_csv: str | None = Field(default=None, max_length=MAX_ODDS)
    params: dict[str, Any] = Field(default_factory=dict)

class CsvPayload(BaseModel):
    csv: str = Field(max_length=MAX_MATCHES)

class Forecast(BaseModel):
    date: str
    league: str
    home: str
    away: str
    line: float = Field(default=9.5, ge=0.5, le=40)
    model: str | None = None
    over_odds: float | None = Field(default=None, ge=1.01, le=100)
    under_odds: float | None = Field(default=None, ge=1.01, le=100)

class MarketConfig(BaseModel):
    line: float = Field(default=9.5, ge=0.5, le=40)
    min_ev: float = Field(default=0.03, ge=0, le=.5)


def matches(csv_data: str | None):
    if csv_data is None:
        return parse_matches(SAMPLE)
    if len(csv_data.encode('utf-8')) > MAX_MATCHES:
        raise DataError('Base superior ao limite de 1 MB.')
    return parse_matches(csv_data.encode('utf-8'))


def odds(csv_data: str | None):
    if not csv_data:
        return None
    if len(csv_data.encode('utf-8')) > MAX_ODDS:
        raise DataError('Odds superiores ao limite de 300 KB.')
    return parse_odds(csv_data.encode('utf-8'))


def overview(df, odds_df, is_custom: bool):
    f = run_backtest(df, 9.5, include_rows=False)
    leagues=[]
    for l,x in df.groupby('league'):
        leagues.append({'name':l,'matches':len(x),'teams':int(len(set(x.home)|set(x.away))),
                        'avg_corners':float(x.total.mean()),'over_9_5':float((x.total>=10).mean())})
    return {'count':len(df),'date_first':str(df.date.min().date()),'date_last':str(df.date.max().date()),
            'league_count':df.league.nunique(),
            'teams':len(set(zip(df.league,df.home))|set(zip(df.league,df.away))),
            'avg_corners':float(df.total.mean()),'over_9_5':float((df.total>=10).mean()),
            'eligible':f['eligible'],'split':f['split'],'selected':f['selected'],
            'selected_name':f['selected_name'],
            'test_metrics':next(m['test'] for m in f['models'] if m['selected']),
            'source':{'type':'browser' if is_custom else 'amostra',
                      'description':'Dados importados neste navegador (sem persistência no servidor).' if is_custom else
                      '452 partidas, 5 ligas, 2026/27; resultados históricos, sem odds.',
                      'url':None if is_custom else 'https://github.com/oladeeayo/open-matchday'},
            'leagues':sorted(leagues,key=lambda x:-x['matches']),
            'odds_count':0 if odds_df is None else len(odds_df),
            'methods':METHODS,'assumption':'ROI indisponível sem odds históricas verificáveis.'}


def execute(req: Query):
    df = matches(req.matches_csv)
    params = req.params
    if req.action == 'overview':
        return overview(df, odds(req.odds_csv), req.matches_csv is not None)
    if req.action == 'teams':
        league = str(params.get('league', ''))
        sub = df[df.league == league]
        if sub.empty: raise DataError('Liga não encontrada.')
        return {'league':league,'teams':sorted(set(sub.home)|set(sub.away)),
                'last_date':str(sub.date.max().date())}
    if req.action in ['backtest','export_predictions']:
        line = float(params.get('line',9.5))
        res = public_backtest(run_backtest(df,line=line))
        if req.action == 'export_predictions':
            return {'rows':res['predictions']}
        limit = int(params.get('limit',120))
        if limit > 0: res['predictions']=res['predictions'][-min(limit,1000):]
        return res
    if req.action == 'forecast':
        q = Forecast.model_validate(params)
        result = new_prediction(df,q.date,q.league,q.home,q.away,q.line,q.model)
        if q.over_odds is not None and q.under_odds is not None:
            result['market'] = market_eval(result,q.over_odds,q.under_odds)
        return result
    if req.action == 'odds_backtest':
        q = MarketConfig.model_validate(params)
        o = odds(req.odds_csv)
        if o is None:
            return {'n':0,'roi':None,'profit':0,'wins':0,'bets':[],
                    'reason':'Nenhum arquivo de odds importado. Não é possível comprovar ROI.'}
        o = o[abs(o.line-q.line)<1e-6].copy()
        return roi_backtest(run_backtest(df,q.line),o,q.min_ev)
    raise DataError('Ação inválida.')


@app.get('/')
def index():
    return FileResponse(PUBLIC / 'index.html')


@app.get('/api/health')
def health():
    return {'ok':True,'version':'3.1.0','sample_available':SAMPLE.exists(),
            'storage':'client-side; stateless API'}


@app.post('/api/query')
def query(req: Query):
    try:
        return execute(req)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post('/api/validate/matches')
def validate_matches(req: CsvPayload):
    try:
        df=matches(req.csv)
        run_backtest(df, 9.5, include_rows=False)
        return {'ok':True,'count':len(df),'message':'Base validada e armazenada somente no seu navegador.'}
    except DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post('/api/validate/odds')
def validate_odds(req: CsvPayload):
    try:
        if len(req.csv.encode('utf-8')) > MAX_ODDS:
            raise DataError('Arquivo de odds superior a 300 KB.')
        df=odds(req.csv)
        return {'ok':True,'count':len(df),'message':'Odds validadas e armazenadas somente no navegador.'}
    except DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/download/template/{which}')
def template(which: str):
    if which=='matches':
        text='date,league,home,away,home_corners,away_corners\n2026-10-10,Premier League,Equipe A,Equipe B,6,4\n'
    elif which=='odds':
        text='date,league,home,away,line,over_odds,under_odds,observed_at,kickoff_at,bookmaker\n2026-10-10,Premier League,Equipe A,Equipe B,9.5,1.90,1.92,2026-10-10T15:00:00Z,2026-10-10T17:00:00Z,Exemplo (ODDS FICTÍCIAS)\n'
    else:
        raise HTTPException(404,'Modelo não encontrado.')
    return Response(content=text,media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="modelo_{which}.csv"'})


@app.post('/api/import/premier10')
def import_premier10():
    """Explicit user action; imports in memory only, returns CSV to user browser."""
    seasons=['1617','1718','1819','1920','2021','2122','2223','2324','2425','2526']
    def download(season):
        url=f'https://raw.githubusercontent.com/datasets/football-datasets/main/datasets/premier-league/season-{season}.csv'
        request=Request(url,headers={'User-Agent':'CornersQuantEdge/3.1 (research)'})
        with urlopen(request,timeout=10) as response:
            data=response.read(100_001)
        if len(data)>100_000: raise DataError('Arquivo remoto excedeu 100 KB.')
        df=parse_matches(data)
        df['league']='Premier League'
        return df.drop(columns='total')
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            frames=list(pool.map(download,seasons))
        import pandas as pd
        merged=pd.concat(frames,ignore_index=True)
        cleaned=parse_matches(merged.to_csv(index=False).encode('utf-8'))
        content=cleaned.drop(columns='total').to_csv(index=False)
        return {'ok':True,'count':len(cleaned),'csv':content,
                'message':'Dez temporadas baixadas. Armazenadas apenas no navegador; sem odds.'}
    except Exception as exc:
        raise HTTPException(502,detail=f'Falha no download remoto: {exc}') from exc


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8765)
