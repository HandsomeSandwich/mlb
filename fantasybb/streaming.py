"""Refresh the data behind the Streaming board.

Pulls three time-sensitive sources for the upcoming days and stores them so the
web page is a fast local join:
  * probable starters         (MLB Stats API)
  * Pitcher List streamer tiers (scraped)
  * free-agent / waiver pool   (Yahoo, for one league)

    python -m fantasybb.streaming refresh                 # uses synced team's league
    python -m fantasybb.streaming refresh 469.l.9254      # explicit league key
"""
from __future__ import annotations

import sys

from . import db, match, pitcherlist
from .mlb_api import MLBClient


def refresh_probables(conn, dates: list[str]) -> int:
    client = MLBClient()
    n = 0
    with conn:
        for d in dates:
            conn.execute("DELETE FROM probable_starts WHERE game_date=?", (d,))
            for p in client.probables(d):
                conn.execute(
                    "INSERT OR REPLACE INTO probable_starts "
                    "(game_date, player_id, name, team, opp, is_home) VALUES (?,?,?,?,?,?)",
                    (p["game_date"], p["player_id"], p["name"], p["team"], p["opp"], p["is_home"]))
                n += 1
    return n


def refresh_availability(conn, league_key: str) -> int:
    from . import yahoo
    avail = {}
    for pos in ("B", "P"):  # batters and pitchers
        for a in yahoo.get_available(league_key, position=pos):
            avail[a["name"]] = a  # dedupe by name
    lookup = match.build_lookup(conn)
    with conn:
        conn.execute("DELETE FROM availability WHERE league_key=?", (league_key,))
        for a in avail.values():
            conn.execute(
                "INSERT OR REPLACE INTO availability "
                "(league_key, name, player_id, status, position) VALUES (?,?,?,?,?)",
                (league_key, a["name"], match.match(lookup, a["name"], a["team_abbr"]),
                 "A", a["position"]))
    return len(avail)


def default_league_key(conn) -> str | None:
    row = conn.execute("SELECT team_key FROM fantasy_roster LIMIT 1").fetchone()
    return row[0].rsplit(".t.", 1)[0] if row else None


def refresh(league_key: str | None = None) -> dict:
    conn = db.connect()
    league_key = league_key or default_league_key(conn)

    n_pl, dates = pitcherlist.refresh(conn)          # scrape tiers (defines the dates)
    if not dates:                                    # fall back to today + tomorrow
        import datetime as _dt
        today = _dt.date.today()
        dates = [(today).isoformat(), (today + _dt.timedelta(days=1)).isoformat()]
    n_prob = refresh_probables(conn, dates)
    n_av = refresh_availability(conn, league_key) if league_key else 0
    conn.close()
    return {"pl_rows": n_pl, "dates": dates, "probables": n_prob,
            "available": n_av, "league_key": league_key}


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    db.init_db()
    league = args[1] if len(args) > 1 else None
    if args[:1] == ["refresh"]:
        r = refresh(league)
        print(f"dates {r['dates']} | PL {r['pl_rows']} | probables {r['probables']} "
              f"| available {r['available']} | league {r['league_key']}")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
