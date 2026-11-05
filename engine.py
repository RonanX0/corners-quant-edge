"""CORNERS QUANT EDGE — deterministic, locally reproducible statistical engine.

Pre-match-only features, grouped by day to avoid same-day data leakage. This is a
research tool, NOT a verified strategy. Never implies ROI without timestamped odds.
"""
from __future__ import annotations

import io
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

METHODS = {
    "baseline_pois": "Média histórica · Poisson",
    "form5_pois": "Forma L5 · Poisson",
    "form10_pois": "Forma L10 · Poisson",
    "form10_nb": "Forma L10 · Binomial negativa",
    "form20_pois": "Forma L20 · Poisson",
    "venue10_nb": "Mando L10 · Binomial negativa",
    "nb_regression": "Regressão binomial negativa",
}
FEATURE_METHODS = ("baseline", "form5", "form10", "form20", "venue10")
DEFAULT_LINE = 9.5

class DataError(ValueError):
    pass


_ISO_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
_SLASH_DATE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(\s|$)")


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse match dates without silently swapping day and month.

    ISO dates (2026-08-15) are parsed as year-month-day. Slash/dot dates such as
    05/08/2016 (the football-data.co.uk convention) are parsed day-first. Mixing
    the two styles in one file is rejected because the order would be ambiguous.
    """
    s = values.astype(str).str.strip()
    filled = s[(s != "") & (s.str.lower() != "nan")]
    iso = filled.str.match(_ISO_DATE)
    slash = filled.str.match(_SLASH_DATE)
    if iso.all():
        return pd.to_datetime(s, errors="coerce", format="ISO8601").dt.normalize()
    if slash.all():
        return pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed").dt.normalize()
    if (iso | slash).all():
        raise DataError("Datas com formatos misturados (AAAA-MM-DD e DD/MM/AAAA). Use um único formato.")
    return pd.to_datetime(s, errors="coerce", format="mixed").dt.normalize()


OPTIONAL_STATS = ("home_shots", "away_shots", "home_sot", "away_sot", "home_xg", "away_xg")
OPTIONAL_TEXT = ("time", "referee", "season")


def parse_matches(data: bytes | str | Path, drop_invalid: bool = False) -> pd.DataFrame:
    """Parse a results CSV into the canonical match table.

    Required: date, home, away, home_corners, away_corners (league optional).
    Optional columns kept when present: shots, shots on target, xG, time,
    referee, season. With ``drop_invalid`` (external feeds), rows without valid
    corners are dropped instead of rejecting the whole file.
    """
    try:
        if isinstance(data, bytes):
            df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
        elif isinstance(data, Path):
            df = pd.read_csv(data, encoding="utf-8-sig")
        elif isinstance(data, str) and "\n" not in data and Path(data).exists():
            df = pd.read_csv(data, encoding="utf-8-sig")
        else:
            df = pd.read_csv(io.StringIO(str(data)))
    except Exception as exc:
        raise DataError(f"CSV de partidas inválido: {exc}") from exc
    df.columns = [str(k).strip().lower() for k in df.columns]
    aliases = {"hometeam": "home", "awayteam": "away", "hc": "home_corners", "ac": "away_corners",
               "league_name": "league", "competition": "league",
               "hs": "home_shots", "as": "away_shots", "hst": "home_sot", "ast": "away_sot",
               "hxg": "home_xg", "axg": "away_xg"}
    df = df.rename(columns={k:v for k,v in aliases.items() if k in df.columns and v not in df.columns})
    df = df.dropna(how="all")
    required = {"date", "home", "away", "home_corners", "away_corners"}
    if missing := required - set(df.columns):
        raise DataError("Colunas faltando: " + ", ".join(sorted(missing)))
    if "league" not in df.columns:
        df["league"] = "Liga não informada"
    df["date"] = parse_dates(df["date"])
    df["home"] = df["home"].astype(str).str.strip()
    df["away"] = df["away"].astype(str).str.strip()
    df["league"] = df["league"].astype(str).str.strip()
    for k in ("home_corners", "away_corners"):
        df[k] = pd.to_numeric(df[k], errors="coerce")
    invalid = (df.date.isna() | df.home.eq("") | df.away.eq("") | df.home.eq(df.away) |
               df.league.eq("") | df.home_corners.isna() | df.away_corners.isna() |
               (df.home_corners < 0) | (df.away_corners < 0) |
               (df.home_corners != np.floor(df.home_corners.fillna(0))) |
               (df.away_corners != np.floor(df.away_corners.fillna(0))))
    if invalid.any():
        if not drop_invalid:
            raise DataError(f"{int(invalid.sum())} linha(s) inválida(s). Confira datas e escanteios inteiros não negativos.")
        df = df[~invalid]
    key = ["date", "league", "home", "away"]
    if df.duplicated(key).any():
        if not drop_invalid:
            raise DataError("Há partidas duplicadas com a mesma data, liga, mandante e visitante.")
        df = df.drop_duplicates(key, keep="last")
    extra = [c for c in OPTIONAL_STATS + OPTIONAL_TEXT if c in df.columns]
    df = df[["date", "league", "home", "away", "home_corners", "away_corners", *extra]].copy()
    for c in OPTIONAL_STATS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in OPTIONAL_TEXT:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
    df[["home_corners", "away_corners"]] = df[["home_corners", "away_corners"]].astype(int)
    df["total"] = df.home_corners + df.away_corners
    df = df.sort_values(["date", "league", "home", "away"]).reset_index(drop=True)
    if df.empty:
        raise DataError("O arquivo não tem partidas.")
    if len(df) > 50000:
        raise DataError("Limite atual: até 50.000 partidas por arquivo.")
    return df


def parse_odds(data: bytes | str | Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(data) if isinstance(data,bytes) else data, encoding="utf-8-sig")
    except Exception as exc:
        raise DataError(f"CSV de odds inválido: {exc}") from exc
    df.columns = [str(k).strip().lower() for k in df.columns]
    expected = {"date", "league", "home", "away", "line", "over_odds", "under_odds", "observed_at", "kickoff_at"}
    if missing := expected - set(df):
        raise DataError("Colunas de odds faltando: " + ", ".join(sorted(missing)))
    df["date"] = parse_dates(df["date"])
    for col in ["league","home","away"]:
        df[col] = df[col].astype(str).str.strip()
    for col in ["line", "over_odds", "under_odds"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["observed_at", "kickoff_at"]:
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    good = (df.date.notna() & df.line.notna() & (df.line>=0) &
            df.over_odds.between(1.01,100) & df.under_odds.between(1.01,100))
    if (~good).any():
        raise DataError(f"{int((~good).sum())} linha(s) de odds com data/linha/cotação inválida.")
    if "bookmaker" not in df:
        df["bookmaker"] = "Não informado"
    return df.sort_values("date").reset_index(drop=True)


def _shrink(values: list, prior: float, window: int, strength: float) -> float:
    z = values[-window:]
    return (sum(z)+prior*strength)/(len(z)+strength)


def _features_one(league: str, home: str, away: str, league_state: dict, team_state: dict) -> dict:
    league_prior = (league_state.get("sum",0) + 200.0)/(league_state.get("n",0) + 20)
    half = league_prior/2
    h = team_state.get((league,home), {})
    a = team_state.get((league,away), {})
    f = {"baseline": league_prior}
    for n,k in [(5,3),(10,6),(20,12)]:
        home_exp = (_shrink(h.get("for",[]),half,n,k) + _shrink(a.get("against",[]),half,n,k))/2
        away_exp = (_shrink(a.get("for",[]),half,n,k) + _shrink(h.get("against",[]),half,n,k))/2
        f[f"form{n}"] = .15*league_prior + .85*(home_exp + away_exp)
    home_exp = (_shrink(h.get("home_for",[]),half,10,8)+_shrink(a.get("away_against",[]),half,10,8))/2
    away_exp = (_shrink(a.get("away_for",[]),half,10,8)+_shrink(h.get("home_against",[]),half,10,8))/2
    f["venue10"] = .25*league_prior+.75*(home_exp+away_exp)
    f["n_home"] = len(h.get("for",[]));f["n_away"] = len(a.get("for",[]))
    return f


def build_features(df: pd.DataFrame, min_history: int = 4) -> pd.DataFrame:
    """Games on a date are all scored BEFORE any game from that date is ingested."""
    teams = defaultdict(lambda: defaultdict(list))
    leagues = defaultdict(lambda: {"sum":0, "n":0})
    output = []
    for date, day in df.groupby("date", sort=True):
        for row in day.itertuples(index=False):
            f = _features_one(row.league,row.home,row.away,leagues[row.league],teams)
            output.append({"date":row.date,"league":row.league,"home":row.home,"away":row.away,
                           "total":int(row.total),"home_corners":int(row.home_corners),
                           "away_corners":int(row.away_corners),**f,"eligible":min(f["n_home"],f["n_away"])>=min_history})
        for row in day.itertuples(index=False):
            h = teams[(row.league,row.home)];a = teams[(row.league,row.away)]
            h["for"].append(row.home_corners);h["against"].append(row.away_corners)
            h["home_for"].append(row.home_corners);h["home_against"].append(row.away_corners)
            a["for"].append(row.away_corners);a["against"].append(row.home_corners)
            a["away_for"].append(row.away_corners);a["away_against"].append(row.home_corners)
            leagues[row.league]["sum"]+=int(row.total);leagues[row.league]["n"]+=1
    return pd.DataFrame.from_records(output)


def make_match_features(df: pd.DataFrame, date: str, league: str, home: str, away: str) -> dict:
    target = pd.Timestamp(date).normalize()
    hist = df[df.date<target].copy()
    if hist.empty:
        raise DataError("Não há partidas anteriores à data escolhida.")
    if home==away:
        raise DataError("Selecione equipes diferentes.")
    ld = hist[hist.league==league]
    if ld.empty:
        raise DataError("Não existe histórico anterior para essa liga.")
    s = {"sum":int(ld.total.sum()),"n":len(ld)}
    teams = defaultdict(lambda: defaultdict(list))
    for row in ld.itertuples(index=False):
        h=teams[(league,row.home)];a=teams[(league,row.away)]
        h["for"].append(row.home_corners);h["against"].append(row.away_corners)
        h["home_for"].append(row.home_corners);h["home_against"].append(row.away_corners)
        a["for"].append(row.away_corners);a["against"].append(row.home_corners)
        a["away_for"].append(row.away_corners);a["away_against"].append(row.home_corners)
    if (league,home) not in teams or (league,away) not in teams:
        raise DataError("Uma das equipes não possui partidas anteriores nessa liga.")
    f=_features_one(league,home,away,s,teams)
    if min(f["n_home"],f["n_away"])<4:
        raise DataError("O modelo requer pelo menos quatro jogos anteriores de cada equipe.")
    return {"date":str(target.date()),"league":league,"home":home,"away":away,**f}


def _matrix(data: pd.DataFrame | list[dict]) -> np.ndarray:
    """NB log link with league-prior offset and two regularized matchup covariates."""
    if isinstance(data,pd.DataFrame):
        base=data.baseline.to_numpy(dtype=float)
        a=data.form10.to_numpy(dtype=float)
        b=data.venue10.to_numpy(dtype=float)
    else:
        base=np.array([r["baseline"] for r in data],dtype=float)
        a=np.array([r["form10"] for r in data],dtype=float)
        b=np.array([r["venue10"] for r in data],dtype=float)
    return np.column_stack([np.ones(len(base)),np.clip((a-base)/3,-4,4),np.clip((b-base)/3,-4,4)])


def estimate_alpha(train: pd.DataFrame,mu_col: str="form10") -> float:
    y = train.total.to_numpy(dtype=float)
    mu = train[mu_col].to_numpy(dtype=float)
    a = (np.sum((y-mu)**2-y)/np.sum(mu**2)) if np.sum(mu**2)>0 else .01
    return float(np.clip(a,0.002,0.6))


def fit_nb_regression(train: pd.DataFrame) -> dict:
    if len(train)<28:
        return {"betas":[0.,0.,0.],"alpha":estimate_alpha(train),"fitted":False,"reason":"Amostra pequena"}
    X=_matrix(train)
    y=train.total.to_numpy(dtype=int)
    base=np.log(np.maximum(.01,train.baseline.to_numpy(float)))
    def objective(params):
        beta=params[:3];alpha=np.exp(params[3]);mu=np.exp(np.clip(base+X@beta,-5,5))
        r=1/alpha
        nll=-np.sum(nbinom.logpmf(y,r,r/(r+mu)))
        penalty=.7*sum(beta[1:]**2)+.15*beta[0]**2
        return float(nll+penalty)
    init=np.array([0.,.25,.10,math.log(max(.003,estimate_alpha(train)))])
    res=minimize(objective,init,method="L-BFGS-B",
                 bounds=[(-.7,.7),(-2,2),(-2,2),(math.log(.001),math.log(.7))],
                 options={"maxiter":300,"ftol":1e-10})
    if not res.success or not np.isfinite(res.fun):
        return {"betas":[0.,0.,0.],"alpha":estimate_alpha(train),"fitted":False,"reason":str(res.message)}
    return {"betas":res.x[:3].tolist(),"alpha":float(np.exp(res.x[3])),"fitted":True}


def predict_params(rows:pd.DataFrame|list[dict], name:str, reg:dict, alpha:float) -> tuple[np.ndarray,float]:
    if not isinstance(rows,pd.DataFrame):rows=pd.DataFrame(rows)
    if name=="nb_regression":
        mu=np.exp(np.clip(np.log(np.maximum(.01,rows.baseline.to_numpy(float)))+_matrix(rows)@np.asarray(reg["betas"]),-3.5,4.5))
        return np.clip(mu,.2,35),float(reg["alpha"])
    col="baseline" if name.startswith("baseline") else name.split("_")[0]
    mu=rows[col].to_numpy(float)
    return np.clip(mu,.2,35),float(alpha if name.endswith("_nb") else 0.0)


def over_prob(mu:float|np.ndarray,alpha:float,line:float):
    if line<0 or line>40 or line*2%1!=0:
        raise DataError("Linha deve ser múltipla de 0,5 e estar entre 0,5 e 40.")
    # only x.5 lines; for integer lines, pushes need explicit handling
    if float(line).is_integer():
        raise DataError("Nesta versão use linhas terminadas em ,5, sem possibilidade de push.")
    k=math.floor(line)
    mu=np.asarray(mu,dtype=float)
    if alpha>=0.000001:
        r=1/alpha
        p=nbinom.sf(k,r,r/(r+mu))
    else:
        p=poisson.sf(k,mu)
    return p.item() if p.ndim==0 else p


def count_nll(y:np.ndarray,mu:np.ndarray,alpha:float) -> float:
    if alpha >= 1e-6:
        r=1/alpha
        prob=nbinom.logpmf(y,r,r/(r+mu))
    else:
        prob=poisson.logpmf(y,mu)
    return float(np.mean(-prob))


def evaluate(rows:pd.DataFrame, name:str, reg:dict, alpha:float,line:float)->dict:
    if len(rows)==0:return {"n":0}
    mu,a=predict_params(rows,name,reg,alpha)
    p=over_prob(mu,a,line)
    actual=(rows.total.to_numpy(float)>line).astype(float)
    pred=(p>=.5).astype(float)
    return {"n":len(rows),"hits":int((pred==actual).sum()),
            "accuracy":float((pred==actual).mean()),
            "brier":float(np.mean((p-actual)**2)),
            "mae":float(np.mean(np.abs(mu-rows.total.to_numpy(float)))),
            "nll":count_nll(rows.total.to_numpy(int),mu,a),
            "over_actual":float(actual.mean()),"over_predicted":float(pred.mean()),
            "p_avg":float(p.mean())}


def split_dates(features:pd.DataFrame,training_pct=.60, validation_pct=.20):
    eligible=features.loc[features.eligible].copy()
    days=np.sort(eligible.date.unique())
    if len(eligible)<60 or len(days)<12:
        raise DataError("Para o backtest são necessários pelo menos 60 jogos elegíveis e 12 datas diferentes.")
    # Split on dates; do not let matches on same calendar date straddle splits.
    t1=days[int(len(days)*training_pct)]
    t2=days[int(len(days)*(training_pct+validation_pct))]
    return (eligible[eligible.date<t1],eligible[(eligible.date>=t1)&(eligible.date<t2)],eligible[eligible.date>=t2])



def uncertainty_by_date(test: pd.DataFrame, model:str, regression:dict, alpha:float, line:float, boots:int=1600) -> dict:
    """Paired bootstrap by calendar date, on selected model minus constant baseline Brier.
    Positive delta means selected model is WORSE; negative means better.
    """
    mu, a = predict_params(test,model,regression,alpha)
    mub,ab=predict_params(test,"baseline_pois",regression,alpha)
    p=over_prob(mu,a,line)
    pb=over_prob(mub,ab,line)
    y=(test.total.to_numpy(float)>line).astype(float)
    delta=np.asarray((p-y)**2-(pb-y)**2)
    date_keys=test.date.dt.strftime("%Y-%m-%d").to_numpy()
    # Per-date sums and counts: the mean of a resample of whole dates equals
    # sum(chosen sums) / sum(chosen counts), so the loop can be vectorized.
    _, group_idx = np.unique(date_keys, return_inverse=True)
    n_groups = int(group_idx.max()) + 1 if len(group_idx) else 0
    sums = np.bincount(group_idx, weights=delta, minlength=n_groups)
    counts = np.bincount(group_idx, minlength=n_groups).astype(float)
    generator=np.random.default_rng(20261104)
    lo=hi=None
    if n_groups:
        chosen=generator.integers(0,n_groups,size=(boots,n_groups))
        boot=sums[chosen].sum(axis=1)/counts[chosen].sum(axis=1)
        lo,hi=(float(x) for x in np.quantile(boot,[.025,.975]))
    edges=[0,.2,.4,.6,.8,1.0000001]
    calibration=[]
    for i in range(5):
        mask=(p>=edges[i])&(p<edges[i+1])
        calibration.append({'range':f'{int(edges[i]*100)}–{int(min(edges[i+1],1)*100)}%',
                            'n':int(mask.sum()),
                            'predicted':float(p[mask].mean()) if mask.any() else None,
                            'observed':float(y[mask].mean()) if mask.any() else None})
    return {'date_blocks':n_groups,'delta_brier':float(delta.mean()),
            'ci95':[lo,hi],
            'interpretation':'Melhor que média histórica em Brier' if hi is not None and hi<0 else
                             'Pior que média histórica em Brier' if lo is not None and lo>0 else 'Inconclusivo',
            'calibration':calibration}


def run_backtest(df:pd.DataFrame,line:float=9.5,min_history:int=4,include_rows:bool=True,
                 features:pd.DataFrame|None=None)->dict:
    over_prob(10.,0.,line)
    f=build_features(df,min_history=min_history) if features is None else features
    train,val,test=split_dates(f)
    alpha=estimate_alpha(train)
    reg=fit_nb_regression(train)
    val_scores={n:evaluate(val,n,reg,alpha,line) for n in METHODS}
    selected=min(METHODS,key=lambda n: (val_scores[n]["brier"],val_scores[n]["nll"]))
    test_scores={n:evaluate(test,n,reg,alpha,line) for n in METHODS}
    m,a=predict_params(test,selected,reg,alpha)
    p=over_prob(m,a,line)
    truth=(test.total.to_numpy(float)>line)
    item=[]
    if include_rows:
        for i,rec in enumerate(test.itertuples(index=False)):
            item.append({"date":str(rec.date.date()),"league":rec.league,"home":rec.home,"away":rec.away,
                         "actual":int(rec.total),"mu":float(round(m[i],3)),"p_over":float(round(p[i],5)),
                         "prediction":"Over" if p[i]>=.5 else "Under",
                         "actual_side":"Over" if truth[i] else "Under", "correct":bool((p[i]>=.5)==truth[i])})
    model_rows=[]
    for n in METHODS:
        model_rows.append({"code":n,"name":METHODS[n],"validation":val_scores[n],"test":test_scores[n],"selected":n==selected})
    result={"line":line,"total":len(df),"eligible":len(f[f.eligible]),"split":{"train":len(train),"validation":len(val),"test":len(test),
            "test_first":str(test.date.min().date()),"test_last":str(test.date.max().date())},
            "selected":selected,"selected_name":METHODS[selected],"alpha_train":alpha,
            "regression":{"fitted":reg.get("fitted",False),"alpha":reg["alpha"],"betas":reg["betas"]},
            "models":model_rows,"predictions":item,
            "uncertainty":uncertainty_by_date(test,selected,reg,alpha,line),
            "notes":["Seleção baseada somente na validação; teste final separado por data.",
                     "Sem odds pré-jogo documentadas, ROI e lucratividade não podem ser calculados.",
                     f"O conjunto de teste ({len(test)} partidas) da base de demonstração já foi examinado em versões anteriores; não é um teste inédito.",
"Probabilidades não foram validadas contra as casas de apostas."]}
    result["_internal"]={"features":f,"train":train,"validation":val,"test":test,"alpha":alpha,"regression":reg}
    return result


def new_prediction(df:pd.DataFrame,date:str,league:str,home:str,away:str,line:float,method:str|None=None) -> dict:
    f=make_match_features(df,date,league,home,away)
    earlier=df[df.date<pd.Timestamp(date)].copy()
    feat=build_features(earlier)
    eligible=feat[feat.eligible]
    if len(eligible)<30:
        raise DataError("Histórico insuficiente para ajustar a distribuição (mínimo de 30 partidas elegíveis anteriores).")
    # The pre-selected method comes from the validation on the existing dataset.
    # Fit coefficients with all past data, as is allowed for an actual future event.
    if method is None:
        try:
            method=run_backtest(earlier,include_rows=False,features=feat)["selected"]
        except DataError:
            method="form10_nb"
    if method not in METHODS:
        raise DataError("Modelo indisponível.")
    alpha=estimate_alpha(eligible)
    reg=fit_nb_regression(eligible)
    mu,a=predict_params([f],method,reg,alpha)
    mu=float(mu[0]);p=float(over_prob(mu,a,line))
    # Robustness through perturbation of the expected count, not a formal CI.
    low=float(over_prob(mu*.9,a,line));high=float(over_prob(mu*1.1,a,line))
    return {"match":f,"model":method,"model_name":METHODS[method],"mu":mu,"alpha":a,
            "line":line,"p_over":p,"p_under":1-p,"p_over_low_count":low,"p_over_high_count":high,
            "source_date_last":str(earlier.date.max().date()),"n_matches_prior":len(earlier),
            "warning":"Projeção exploratória: não incorpora escalações, clima, arbitragem ou odds em tempo real."}


def market_eval(pred:dict,over_odds:float,under_odds:float)->dict:
    if not (1.01<=over_odds<=100 and 1.01<=under_odds<=100):
        raise DataError("Odds decimais devem estar entre 1,01 e 100.")
    p=pred["p_over"]; p_low=pred["p_over_low_count"];p_hi=pred["p_over_high_count"]
    def value(side,prob,conservative,odd):
        return {"side":side,"odd":odd,"p":prob,"p_implied":1/odd,
                "ev":prob*odd-1,"ev_conservative":conservative*odd-1,
                "fair_odd":1/prob if prob else None}
    a=value("Over",p,p_low,over_odds)
    b=value("Under",1-p,1-p_hi,under_odds)
    margin=1/over_odds+1/under_odds-1
    a["market_no_vig"]=(1/over_odds)/(1/over_odds+1/under_odds)
    b["market_no_vig"]=(1/under_odds)/(1/over_odds+1/under_odds)
    for x in (a,b):
        x["decision"]=("VALOR TEÓRICO (NÃO VALIDADO)" if x["ev"]>=.03 and x["ev_conservative"]>0 else
                        "FRÁGIL" if x["ev"]>0 else "SEM VALOR")
    return {"margin":margin,"over":a,"under":b,"note":"Preços inseridos manualmente: não foram confirmados em casa de apostas."}


def roi_backtest(result:dict,odds:pd.DataFrame,min_ev:float=.03)->dict:
    """Only user-supplied timestamped odds on chronological test; 1u per fixture.

    When a fixture has several valid pre-match quotes (several bookmakers, or
    several snapshots), exactly one is used: the most recent observation before
    kickoff, chosen BEFORE looking at EV. Picking whichever quote passes the EV
    filter would be line shopping in hindsight and inflate the ROI.
    """
    internal=result["_internal"]
    test=internal["test"]
    model=result["selected"]
    mu,a=predict_params(test,model,internal["regression"],internal["alpha"])
    test2=test[["date","league","home","away","total"]].copy()
    test2["mu"]=mu
    test2["alpha"]=a
    key=["date","league","home","away"]
    d=pd.merge(test2,odds,on=key,how="inner")
    rejected={"odds_ausentes":len(test2)-len(d.drop_duplicates(key)),
              "timestamp_invalido":0,"fora_do_pre_jogo":0,"linha_inteira":0,
              "cotacoes_duplicadas":0,"sem_valor_robusto":0}
    bad_ts=d.observed_at.isna()|d.kickoff_at.isna()
    rejected["timestamp_invalido"]=int(bad_ts.sum())
    d=d[~bad_ts]
    lead=(d.kickoff_at-d.observed_at).dt.total_seconds()
    outside=(lead<=0)|(lead>7*86400)
    rejected["fora_do_pre_jogo"]=int(outside.sum())
    d=d[~outside]
    # only half-lines; integer lines would need push handling
    integer_line=(d.line%1)==0
    rejected["linha_inteira"]=int(integer_line.sum())
    d=d[~integer_line]
    # stable sort keeps file order among equal timestamps; keep the latest quote
    d=d.sort_values("observed_at",kind="stable")
    latest=d.drop_duplicates(key,keep="last")
    rejected["cotacoes_duplicadas"]=len(d)-len(latest)
    d=latest.sort_values(key,kind="stable")
    accepted=[]
    for row in d.itertuples(index=False):
        m=float(row.mu); p=float(over_prob(m,row.alpha,row.line));lo=float(over_prob(m*.9,row.alpha,row.line));hi=float(over_prob(m*1.1,row.alpha,row.line))
        a={"side":"Over","ev":p*row.over_odds-1,"ev_worst":lo*row.over_odds-1,"odds":float(row.over_odds)}
        b={"side":"Under","ev":(1-p)*row.under_odds-1,"ev_worst":(1-hi)*row.under_odds-1,"odds":float(row.under_odds)}
        cand=max((a,b),key=lambda x:x["ev_worst"])
        if cand["ev"]<min_ev or cand["ev_worst"]<=0:
            rejected["sem_valor_robusto"]+=1;continue
        win = row.total>row.line if cand["side"]=="Over" else row.total<row.line
        accepted.append({"date":str(row.date.date()),"league":row.league,"home":row.home,"away":row.away,
                         "line":float(row.line),"side":cand["side"],"odd":cand["odds"],
                         "ev":cand["ev"],"ev_conservative":cand["ev_worst"],
                         "actual":int(row.total),"won":bool(win),"profit":cand["odds"]-1 if win else -1})
    n=len(accepted);profit=sum(x["profit"] for x in accepted)
    return {"n":n,"profit":profit,"roi":profit/n if n else None,"wins":sum(x["won"] for x in accepted),
            "bets":accepted,"rejected":rejected,"sample_warning":n<100,
            "notes":"Retorno retrospectivo hipotético com timestamps declarados pelo usuário (não autenticados externamente), sem taxas, restrições ou liquidez. Aumentar ou diminuir o filtro EV após ver o resultado provoca overfitting."}


def public_backtest(raw:dict)->dict:
    return {k:v for k,v in raw.items() if k!="_internal"}
