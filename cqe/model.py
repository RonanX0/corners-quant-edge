"""Corner-count models and probability tools.

Team-strength model (``hier``)
------------------------------
For a match of league l between home team h and away team a::

    log E[home corners] = c_l + H_l + att_h + def_a
    log E[away corners] = c_l       + att_a + def_h

* ``c_l`` league level and ``H_l`` home advantage per league;
* ``att`` (corners won) and ``def`` (corners conceded) per team, shared across
  leagues so promoted/relegated teams keep their history;
* ridge penalties act as Gaussian priors: teams with little data are shrunk to
  the league average (hierarchical shrinkage), home advantages are shrunk to
  zero;
* observations are weighted by ``0.5 ** (age_days / half_life)`` (temporal
  weighting), so recent matches count more without discarding old ones.

Fitted by penalised weighted Poisson likelihood (L-BFGS, analytic gradient);
over-dispersion is estimated afterwards (method of moments) and the totals are
modelled as the convolution of two negative binomials. Parameter uncertainty
comes from the inverse penalised Fisher information.

All probabilities are model estimates. Historical frequencies are reported for
reference only and never used as the "true" probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

MAX_K = 40                       # support 0..40 corners (tail mass folded into 40)
REFERENCE_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5)
MODEL_FLOOR_SE = 0.05            # extra log-scale uncertainty for model misspecification


# --------------------------------------------------------------------------- distributions
def count_pmf(mu, alpha) -> np.ndarray:
    """PMF on 0..MAX_K for NB(mu, alpha) (Poisson when alpha≈0). Shape (n, MAX_K+1)."""
    mu = np.atleast_1d(np.asarray(mu, dtype=float))
    alpha = np.broadcast_to(np.atleast_1d(np.asarray(alpha, dtype=float)), mu.shape)
    k = np.arange(MAX_K + 1)
    out = np.empty((len(mu), MAX_K + 1))
    pois = alpha < 1e-6
    if pois.any():
        out[pois] = poisson.pmf(k[None, :], mu[pois, None])
        out[pois, -1] += poisson.sf(MAX_K, mu[pois])
    nb = ~pois
    if nb.any():
        r = 1 / alpha[nb]
        p = r / (r + mu[nb])
        out[nb] = nbinom.pmf(k[None, :], r[:, None], p[:, None])
        out[nb, -1] += nbinom.sf(MAX_K, r, p)
    return out


def convolve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise convolution of two PMFs, truncated at MAX_K (tail folded)."""
    n, m = a.shape
    out = np.zeros((n, m))
    for i in range(m):
        out[:, i:] += a[:, [i]] * b[:, : m - i]
    out[:, -1] += 1 - out.sum(axis=1)
    return np.clip(out, 0, 1)


def total_pmf(mu_h=None, mu_a=None, alpha=0.0, mu=None) -> np.ndarray:
    """PMF of total corners: single NB(mu) or sum of two NB components."""
    if mu is not None:
        return count_pmf(mu, alpha)
    return convolve(count_pmf(mu_h, alpha), count_pmf(mu_a, alpha))


def _line_parts(line: float) -> list[tuple[float, float]]:
    """Split Asian quarter lines into two half-stakes: 9.75 -> [(9.5,.5),(10,.5)]."""
    frac = round(line * 4) % 4
    if frac in (1, 3):
        return [(line - .25, .5), (line + .25, .5)]
    return [(line, 1.0)]


def side_probs(pmf: np.ndarray, line: float, side: str):
    """(p_win, p_push, p_lose) for one line component, vectorised over rows."""
    k = np.arange(pmf.shape[1])
    over, under, push = pmf[:, k > line].sum(1), pmf[:, k < line].sum(1), pmf[:, k == line].sum(1)
    return (over, push, under) if side == "over" else (under, push, over)


def expected_profit(pmf: np.ndarray, line: float, side: str, odd: float) -> np.ndarray:
    """Expected profit per 1 unit staked (EV), handling pushes and quarter lines."""
    ev = np.zeros(pmf.shape[0])
    for part, weight in _line_parts(line):
        win, _push, lose = side_probs(pmf, part, side)
        ev += weight * (win * (odd - 1) - lose)
    return ev


def win_prob(pmf: np.ndarray, line: float, side: str) -> np.ndarray:
    """Probability of winning the bet ignoring pushes (for display/implied comparison)."""
    total = np.zeros(pmf.shape[0])
    for part, weight in _line_parts(line):
        win, push, _ = side_probs(pmf, part, side)
        denom = np.where(1 - push > 0, 1 - push, 1)
        total += weight * win / denom
    return total


def fair_odd(pmf_row: np.ndarray, line: float, side: str) -> float | None:
    """Odd at which EV = 0 (accounts for pushes)."""
    lo, hi = 1.0001, 1000.0
    if expected_profit(pmf_row[None, :], line, side, hi)[0] <= 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if expected_profit(pmf_row[None, :], line, side, mid)[0] > 0:
            hi = mid
        else:
            lo = mid
    return hi


def no_vig(over_odds: float, under_odds: float) -> tuple[float, float, float]:
    """Proportional margin removal: (p_over, p_under, margin)."""
    io_, iu = 1 / over_odds, 1 / under_odds
    s = io_ + iu
    return io_ / s, iu / s, s - 1


def quantile_interval(pmf_row: np.ndarray, level: float = .8) -> tuple[int, int]:
    cdf = np.cumsum(pmf_row)
    lo = int(np.searchsorted(cdf, (1 - level) / 2))
    hi = int(np.searchsorted(cdf, 1 - (1 - level) / 2))
    return lo, min(hi, MAX_K)


# --------------------------------------------------------------------------- team-strength model
@dataclass
class TeamStrength:
    ref_date: pd.Timestamp
    half_life: float
    leagues: list[str]
    teams: list[str]
    theta: np.ndarray
    cov: np.ndarray
    alpha: float
    n_obs: int
    games: dict = field(default_factory=dict)       # team -> matches in training data
    weight_games: dict = field(default_factory=dict)  # team -> effective (time-weighted) matches
    converged: bool = True

    # parameter slices
    def _idx(self):
        L, T = len(self.leagues), len(self.teams)
        return {"c": 0, "h": L, "att": 2 * L, "def": 2 * L + T}

    def params(self):
        L, T = len(self.leagues), len(self.teams)
        i = self._idx()
        return {"c": dict(zip(self.leagues, self.theta[:L])),
                "h": dict(zip(self.leagues, self.theta[L:2 * L])),
                "att": dict(zip(self.teams, self.theta[i["att"]:i["att"] + T])),
                "def": dict(zip(self.teams, self.theta[i["def"]:i["def"] + T]))}

    def _design(self, league: str, team: str, opp: str, home: bool):
        L, T = len(self.leagues), len(self.teams)
        x = np.zeros(len(self.theta))
        li = self.leagues.index(league)
        x[li] = 1
        if home:
            x[L + li] = 1
        lookup = {t: i for i, t in enumerate(self.teams)}
        if team in lookup:
            x[2 * L + lookup[team]] = 1
        if opp in lookup:
            x[2 * L + T + lookup[opp]] = 1
        return x

    def predict(self, league: str, home: str, away: str) -> dict:
        if league not in self.leagues:
            raise KeyError(league)
        xh = self._design(league, home, away, True)
        xa = self._design(league, away, home, False)
        eh, ea = float(xh @ self.theta), float(xa @ self.theta)
        se_h = math.sqrt(max(float(xh @ self.cov @ xh), 0) + MODEL_FLOOR_SE ** 2)
        se_a = math.sqrt(max(float(xa @ self.cov @ xa), 0) + MODEL_FLOOR_SE ** 2)
        return {"mu_home": math.exp(eh), "mu_away": math.exp(ea), "se_home": se_h, "se_away": se_a,
                "alpha": self.alpha, "games_home": self.games.get(home, 0), "games_away": self.games.get(away, 0),
                "known_home": home in self.games, "known_away": away in self.games}

    def predict_frame(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Vectorised predictions for rows with league/home/away (unknown league -> NaN)."""
        L, T = len(self.leagues), len(self.teams)
        li = {l: i for i, l in enumerate(self.leagues)}
        ti = {t: i for i, t in enumerate(self.teams)}
        c = self.theta[:L]; h = self.theta[L:2 * L]
        att = self.theta[2 * L:2 * L + T]; de = self.theta[2 * L + T:]
        lg = rows.league.map(li)
        ok = lg.notna().to_numpy()
        lgi = lg.fillna(0).astype(int).to_numpy()
        hi = rows.home.map(ti); ai = rows.away.map(ti)
        att_h = np.where(hi.notna(), att[hi.fillna(0).astype(int)], 0)
        att_a = np.where(ai.notna(), att[ai.fillna(0).astype(int)], 0)
        def_h = np.where(hi.notna(), de[hi.fillna(0).astype(int)], 0)
        def_a = np.where(ai.notna(), de[ai.fillna(0).astype(int)], 0)
        mu_h = np.exp(c[lgi] + h[lgi] + att_h + def_a)
        mu_a = np.exp(c[lgi] + att_a + def_h)
        mu_h[~ok] = np.nan; mu_a[~ok] = np.nan
        return pd.DataFrame({"mu_home": mu_h, "mu_away": mu_a}, index=rows.index)


def fit_team_strength(df: pd.DataFrame, ref_date, half_life: float = 240.0, ridge_team: float = 30.0,
                      ridge_home: float = 60.0, max_iter: int = 300) -> TeamStrength:
    """Fit on matches strictly before ``ref_date`` (no look-ahead)."""
    ref = pd.Timestamp(ref_date).normalize()
    hist = df[df.date < ref]
    if len(hist) < 40:
        raise ValueError("Histórico insuficiente para o modelo de força das equipes (mínimo 40 partidas).")
    leagues = sorted(hist.league.unique())
    teams = sorted(set(hist.home) | set(hist.away))
    L, T, n = len(leagues), len(teams), len(hist)
    li = hist.league.map({l: i for i, l in enumerate(leagues)}).to_numpy()
    ti = {t: i for i, t in enumerate(teams)}
    hi = hist.home.map(ti).to_numpy(); ai = hist.away.map(ti).to_numpy()
    age = (ref - hist.date).dt.days.to_numpy(dtype=float)
    w1 = 0.5 ** (age / half_life)
    # stacked observations: home side then away side
    rows = np.arange(2 * n)
    P = 2 * L + 2 * T
    cols_c = np.concatenate([li, li])
    cols_h = L + li
    cols_att = 2 * L + np.concatenate([hi, ai])
    cols_def = 2 * L + T + np.concatenate([ai, hi])
    r_idx = np.concatenate([rows, np.arange(n), rows, rows])
    c_idx = np.concatenate([cols_c, cols_h, cols_att, cols_def])
    X = sparse.csr_matrix((np.ones(len(r_idx)), (r_idx, c_idx)), shape=(2 * n, P))
    y = np.concatenate([hist.home_corners.to_numpy(float), hist.away_corners.to_numpy(float)])
    w = np.concatenate([w1, w1])
    pen = np.zeros(P)
    pen[L:2 * L] = ridge_home
    pen[2 * L:] = ridge_team
    theta0 = np.zeros(P)
    for l, i in zip(leagues, range(L)):
        mask = np.concatenate([li == i, li == i])
        theta0[i] = math.log(max(np.average(y[mask], weights=w[mask]), .5))

    def f(theta):
        eta = np.clip(X @ theta, -8, 8)
        mu = np.exp(eta)
        loss = np.sum(w * (mu - y * eta)) + .5 * np.sum(pen * theta ** 2)
        grad = X.T @ (w * (mu - y)) + pen * theta
        return float(loss), grad

    res = minimize(f, theta0, jac=True, method="L-BFGS-B", options={"maxiter": max_iter, "gtol": 1e-6})
    theta = res.x
    mu = np.exp(np.clip(X @ theta, -8, 8))
    # NB over-dispersion (method of moments, weighted); var = mu + alpha mu^2
    alpha = float(np.clip(np.sum(w * ((y - mu) ** 2 - mu)) / max(np.sum(w * mu ** 2), 1e-9), 1e-4, 1.0))
    # penalised Fisher information, inflated for over-dispersion
    H = (X.T @ sparse.diags(w * mu / (1 + alpha * mu)) @ X).toarray() + np.diag(pen + 1e-6)
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(H)
    games = pd.concat([hist.home, hist.away]).value_counts().to_dict()
    wg = pd.Series(np.concatenate([w1, w1]), index=pd.concat([hist.home, hist.away]).to_numpy()).groupby(level=0).sum().to_dict()
    return TeamStrength(ref, half_life, leagues, teams, theta, cov, alpha, n, games, wg, bool(res.success))


# --------------------------------------------------------------------------- per-match summaries
def summarize(pmf_row: np.ndarray, lines=REFERENCE_LINES) -> dict:
    k = np.arange(len(pmf_row))
    mean = float((k * pmf_row).sum())
    lo, hi = quantile_interval(pmf_row, .8)
    lo95, hi95 = quantile_interval(pmf_row, .95)
    per_line = []
    for ln in lines:
        p_over = float(win_prob(pmf_row[None, :], ln, "over")[0])
        per_line.append({"line": ln, "p_over": p_over, "p_under": 1 - p_over if float(ln * 2) % 2 else float(win_prob(pmf_row[None, :], ln, "under")[0]),
                         "fair_over": fair_odd(pmf_row, ln, "over"), "fair_under": fair_odd(pmf_row, ln, "under")})
    dist = [float(x) for x in pmf_row[:21]] + [float(pmf_row[21:].sum())]
    return {"mean": mean, "interval80": [lo, hi], "interval95": [lo95, hi95], "lines": per_line,
            "distribution": dist, "mode": int(np.argmax(pmf_row))}
