"""Scrape Pitcher List's daily SP streamer tiers (Nick Pollack).

The daily post (pitcherlist.com/.../sp-streamers/) contains one ranked table
per day with columns [Rank, Pitcher, Matchup, Rostership]; tier names appear as
separator rows ('Auto Start', 'Probably Start', ...). The 1st two days are
public; the 3rd is PL-Pro-only and simply absent here.

    python -m fantasybb.pitcherlist refresh    # scrape latest + store

For personal use; best-effort -- if PL changes their layout the parser may need
a tweak.
"""
from __future__ import annotations

import datetime as _dt
import sys

import requests
from bs4 import BeautifulSoup

from . import db, match

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CATEGORY = "https://pitcherlist.com/category/fantasy/starting-pitchers/sp-streamers/"

# tier label (as it appears in the table) -> (canonical name, rank)
TIERS = {
    "auto start": ("Auto Start", 0),
    "probably start": ("Probably Start", 1),
    "questionable start": ("Questionable Start", 2),
    "questionable": ("Questionable Start", 2),
    "do not start": ("Do Not Start", 3),
}
STREAMER_HEADER = ["rank", "pitcher", "matchup", "rostership"]


def latest_article_url() -> str:
    r = requests.get(CATEGORY, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        if "starting-pitcher-streamer-ranks" in a["href"]:
            return a["href"]
    raise RuntimeError("no streamer article found on the category page")


def _dates_from_url(url: str, year: int) -> list[str]:
    """'...-6-5-6-6-6-7/' -> ['YYYY-06-05','YYYY-06-06','YYYY-06-07']."""
    tail = url.rstrip("/").split("fantasy-baseball-")[-1]
    parts = [int(x) for x in tail.split("-") if x.isdigit()]
    out = []
    for i in range(0, len(parts) - 1, 2):
        out.append(f"{year:04d}-{parts[i]:02d}-{parts[i+1]:02d}")
    return out


def _opp_from_matchup(matchup: str) -> str:
    m = (matchup or "").replace("vs.", "").replace("@", "").strip()
    return m.split()[0] if m else ""


def scrape(url: str | None = None, year: int | None = None) -> list[dict]:
    url = url or latest_article_url()
    year = year or _dt.date.today().year
    dates = _dates_from_url(url, year)
    html = requests.get(url, headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    # streamer tables are the ones whose header row is Rank/Pitcher/Matchup/Rostership
    streamer_tables = []
    for t in soup.find_all("table"):
        first = [c.get_text(strip=True).lower() for c in t.find_all("tr")[0].find_all(["td", "th"])]
        if first[:4] == STREAMER_HEADER:
            streamer_tables.append(t)

    rows: list[dict] = []
    for di, table in enumerate(streamer_tables):
        game_date = dates[di] if di < len(dates) else None
        tier, tier_rank = None, None
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 4:
                continue
            rank, name, matchup, roster = cells[0], cells[1], cells[2], cells[3]
            key = name.strip().lower()
            if not rank and key in TIERS:           # tier separator row
                tier, tier_rank = TIERS[key]
                continue
            if not name or not rank:
                continue
            name = name.split("(")[0].strip()   # drop '(Opener)' etc.
            rows.append({
                "game_date": game_date, "name": name, "tier": tier,
                "tier_rank": tier_rank,
                "rank": int(rank) if rank.isdigit() else None,
                "matchup": matchup, "opp": _opp_from_matchup(matchup),
                "rostership": roster,
            })
    return rows


def refresh(conn=None) -> tuple[int, list[str]]:
    """Scrape the latest article and replace stored streamer rows."""
    own = conn is None
    conn = conn or db.connect()
    rows = scrape()
    lookup = match.build_lookup(conn)
    for r in rows:
        r["player_id"] = match.match(lookup, r["name"], r["opp"])
    dates = sorted({r["game_date"] for r in rows if r["game_date"]})
    with conn:
        for d in dates:
            conn.execute("DELETE FROM pl_streamers WHERE game_date=?", (d,))
        conn.executemany(
            "INSERT OR REPLACE INTO pl_streamers (game_date, name, player_id, tier, "
            "tier_rank, rank, matchup, opp, rostership) VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["game_date"], r["name"], r["player_id"], r["tier"], r["tier_rank"],
              r["rank"], r["matchup"], r["opp"], r["rostership"]) for r in rows],
        )
    if own:
        conn.close()
    return len(rows), dates


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:])[:1]
    db.init_db()
    if cmd == ["refresh"]:
        n, dates = refresh()
        print(f"stored {n} streamer rows for {', '.join(dates)}")
    else:
        for r in scrape():
            print(f"  {r['game_date']} [{r['tier']}] {r['rank']}. {r['name']} {r['matchup']} ({r['rostership']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
