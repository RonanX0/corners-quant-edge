"""Competition catalogue shared by the providers.

``code`` is the football-data.co.uk division code (also our internal key);
``odds_api`` is The Odds API sport key for the same competition, when known.
"""
from __future__ import annotations

from datetime import date

LEAGUES: dict[str, dict] = {
    "E0":  {"name": "Premier League", "country": "Inglaterra", "odds_api": "soccer_epl"},
    "E1":  {"name": "Championship", "country": "Inglaterra", "odds_api": "soccer_efl_champ"},
    "E2":  {"name": "League One", "country": "Inglaterra", "odds_api": "soccer_england_league1"},
    "E3":  {"name": "League Two", "country": "Inglaterra", "odds_api": "soccer_england_league2"},
    "SC0": {"name": "Premiership", "country": "Escócia", "odds_api": "soccer_spl"},
    "D1":  {"name": "Bundesliga", "country": "Alemanha", "odds_api": "soccer_germany_bundesliga"},
    "D2":  {"name": "2. Bundesliga", "country": "Alemanha", "odds_api": "soccer_germany_bundesliga2"},
    "SP1": {"name": "La Liga", "country": "Espanha", "odds_api": "soccer_spain_la_liga"},
    "SP2": {"name": "Segunda División", "country": "Espanha", "odds_api": "soccer_spain_segunda_division"},
    "I1":  {"name": "Serie A", "country": "Itália", "odds_api": "soccer_italy_serie_a"},
    "I2":  {"name": "Serie B", "country": "Itália", "odds_api": "soccer_italy_serie_b"},
    "F1":  {"name": "Ligue 1", "country": "França", "odds_api": "soccer_france_ligue_one"},
    "F2":  {"name": "Ligue 2", "country": "França", "odds_api": "soccer_france_ligue_two"},
    "N1":  {"name": "Eredivisie", "country": "Holanda", "odds_api": "soccer_netherlands_eredivisie"},
    "B1":  {"name": "Pro League", "country": "Bélgica", "odds_api": "soccer_belgium_first_div"},
    "P1":  {"name": "Primeira Liga", "country": "Portugal", "odds_api": "soccer_portugal_primeira_liga"},
    "T1":  {"name": "Süper Lig", "country": "Turquia", "odds_api": "soccer_turkey_super_league"},
    "G1":  {"name": "Super League", "country": "Grécia", "odds_api": "soccer_greece_super_league"},
}
DEFAULT_LEAGUES = ("E0", "SP1", "D1", "I1", "F1")


def label(code: str) -> str:
    info = LEAGUES.get(code)
    return f"{info['name']} ({info['country']})" if info else code


def code_for_label(text: str) -> str | None:
    for code in LEAGUES:
        if label(code) == text or code == text:
            return code
    return None


def season_code(day: date) -> str:
    """European season code used by football-data: 2026-11-04 -> '2627'."""
    start = day.year if day.month >= 7 else day.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def recent_seasons(day: date, n: int) -> list[str]:
    start = day.year if day.month >= 7 else day.year - 1
    return [f"{(start - i) % 100:02d}{(start - i + 1) % 100:02d}" for i in range(n)][::-1]


def season_label(code: str) -> str:
    return f"20{code[:2]}/{code[2:]}"
