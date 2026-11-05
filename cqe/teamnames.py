"""Map team names between providers (e.g. The Odds API -> football-data.co.uk).

Unresolved names return ``None``; callers must surface that as missing data,
never guess silently.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_DROP = {"fc", "afc", "cf", "sc", "ac", "ssc", "sv", "fk", "sk", "club", "the", "cd", "ud", "rc", "rcd", "sd", "ca", "us", "as", "vfb", "vfl", "tsg", "1", "de"}

# Normalised long name -> normalised football-data name.
ALIASES = {
    "manchester united": "man united", "manchester city": "man city", "newcastle united": "newcastle",
    "tottenham hotspur": "tottenham", "wolverhampton wanderers": "wolves", "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton", "west ham united": "west ham", "nottingham forest": "nott m forest",
    "leeds united": "leeds", "leicester city": "leicester", "ipswich town": "ipswich", "luton town": "luton",
    "sheffield wednesday": "sheffield weds", "west bromwich albion": "west brom", "queens park rangers": "qpr",
    "atletico madrid": "ath madrid", "athletic bilbao": "ath bilbao", "athletic club": "ath bilbao",
    "real sociedad": "sociedad", "real betis": "betis", "celta vigo": "celta", "rayo vallecano": "vallecano",
    "espanyol": "espanol", "deportivo alaves": "alaves", "real oviedo": "oviedo",
    "bayern munich": "bayern munich", "borussia dortmund": "dortmund", "borussia monchengladbach": "m gladbach",
    "bayer leverkusen": "leverkusen", "eintracht frankfurt": "ein frankfurt", "fsv mainz 05": "mainz",
    "mainz 05": "mainz", "hoffenheim": "hoffenheim", "1899 hoffenheim": "hoffenheim", "koln": "fc koln",
    "union berlin": "union berlin", "werder bremen": "werder bremen", "rb leipzig": "rb leipzig",
    "inter milan": "inter", "internazionale": "inter", "milan": "milan", "roma": "roma", "napoli": "napoli",
    "paris saint germain": "paris sg", "psg": "paris sg", "olympique marseille": "marseille",
    "olympique lyonnais": "lyon", "saint etienne": "st etienne", "stade rennais": "rennes",
    "psv eindhoven": "psv eindhoven", "az alkmaar": "az alkmaar", "sporting lisbon": "sp lisbon",
    "sporting cp": "sp lisbon", "fc porto": "porto", "sporting braga": "sp braga",
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and "))
    tokens = [t for t in text.split() if t not in _DROP]
    return " ".join(tokens)


def resolve(name: str, known: list[str] | set[str], cutoff: float = 0.86) -> str | None:
    """Return the entry of ``known`` that denotes ``name``, or None."""
    known = list(known)
    by_norm = {}
    for k in known:
        by_norm.setdefault(normalize(k), k)
    n = normalize(name)
    n = ALIASES.get(n, n)
    if n in by_norm:
        return by_norm[n]
    # football-data short names are often a prefix/suffix of the long name
    candidates = [k for kn, k in by_norm.items() if kn and (kn in n.split() or n.startswith(kn + " ") or kn.startswith(n + " "))]
    if len(candidates) == 1:
        return candidates[0]
    close = difflib.get_close_matches(n, list(by_norm), n=2, cutoff=cutoff)
    if len(close) == 1 or (len(close) == 2 and difflib.SequenceMatcher(None, n, close[0]).ratio() - difflib.SequenceMatcher(None, n, close[1]).ratio() > .06):
        return by_norm[close[0]]
    return None
