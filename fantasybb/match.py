"""Match external player names (Yahoo, Pitcher List) to our DB player_ids.

Names arrive with accents, punctuation, and Jr/Sr suffixes that don't line up
byte-for-byte, so we normalize before comparing and break ties on MLB team.
"""
from __future__ import annotations

import re
import unicodedata

# Pitcher List / Yahoo abbreviations that differ from the MLB Stats API ones.
TEAM_ALIAS = {
    "CHW": "CWS", "WAS": "WSH", "KCR": "KC", "SFG": "SF", "TBR": "TB",
    "SDP": "SD", "WSN": "WSH", "ARI": "AZ",
}


def normalize_name(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[.\-'`]", "", s)
    s = re.sub(r"\s+(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def build_lookup(conn) -> dict:
    """normalized name -> list of (player_id, MLB team abbr)."""
    out: dict[str, list] = {}
    for pid, name, abbr in conn.execute(
        "SELECT p.player_id, p.full_name, t.abbreviation "
        "FROM players p LEFT JOIN teams t ON t.team_id = p.team_id"
    ):
        out.setdefault(normalize_name(name), []).append((pid, (abbr or "").upper()))
    return out


def match(lookup: dict, name: str, team: str | None = None):
    """Return a player_id for `name`, disambiguating shared names by team."""
    cands = lookup.get(normalize_name(name), [])
    if len(cands) == 1:
        return cands[0][0]
    if len(cands) > 1:
        if team:
            t = (team or "").upper().lstrip("@ ").strip()
            t = TEAM_ALIAS.get(t, t)
            hit = [c for c in cands if c[1] == t]
            if hit:
                return hit[0][0]
        return cands[0][0]  # fall back to first; ambiguous but rare
    return None
