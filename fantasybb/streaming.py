"""Refresh the data behind the Streaming board.

Pulls the time-sensitive sources for the upcoming days and stores them so the
web pages are a fast local join:
  * probable starters         (MLB Stats API)
  * full-season team schedule (MLB Stats API, for "games this week" counts)
  * Pitcher List streamer tiers (scraped)
  * free-agent / waiver pool   (Yahoo, for one league)
  * every team's roster        (Yahoo, so the dashboard can project opponents)
  * current-week matchup score (Yahoo scoreboard, so the live H2H stays current)

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


def refresh_team_schedule(conn, season: int) -> int:
    """Store the full-season schedule (two rows per game, one per team) so the
    dashboard can count each team's games in a given week — including upcoming
    games that `games` doesn't hold."""
    client = MLBClient()
    games = client.schedule(season)
    with conn:
        conn.execute("DELETE FROM team_schedule WHERE season=?", (season,))
        for g in games:
            pk = g["gamePk"]
            date = g["gameDate"][:10]
            state = (g.get("status") or {}).get("abstractGameState")
            home = g["teams"]["home"]["team"]["id"]
            away = g["teams"]["away"]["team"]["id"]
            conn.executemany(
                "INSERT OR REPLACE INTO team_schedule "
                "(game_pk, season, game_date, team_id, opp_id, is_home, status) "
                "VALUES (?,?,?,?,?,?,?)",
                [(pk, season, date, home, away, 1, state),
                 (pk, season, date, away, home, 0, state)])
    return len(games)


def refresh_availability(conn, league_key: str) -> int:
    from . import yahoo
    avail = {}
    for pos in ("B", "P"):  # batters and pitchers
        for a in yahoo.get_available(league_key, position=pos):
            avail[a["name"]] = a  # dedupe by name
    lookup = match.build_lookup(conn)
    # A rostered player can't also be a free agent. If a name matches someone
    # already on a roster in THIS league, the match is a same-name collision
    # (e.g. an available SP "Cade Smith" colliding with a rostered RP of the
    # same name) — drop the bogus id so we don't surface his stats as available.
    rostered = {r[0] for r in conn.execute(
        "SELECT player_id FROM fantasy_roster "
        "WHERE player_id IS NOT NULL AND team_key LIKE ?", (league_key + ".t.%",))}
    with conn:
        conn.execute("DELETE FROM availability WHERE league_key=?", (league_key,))
        for a in avail.values():
            pid = match.match(lookup, a["name"], a["team_abbr"])
            if pid in rostered:
                pid = None
            conn.execute(
                "INSERT OR REPLACE INTO availability "
                "(league_key, name, player_id, status, position) VALUES (?,?,?,?,?)",
                (league_key, a["name"], pid, "A", a["position"]))
    return len(avail)


def default_league_key(conn) -> str | None:
    row = conn.execute("SELECT team_key FROM fantasy_roster WHERE is_mine=1 LIMIT 1").fetchone()
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
    season = int(dates[0][:4])
    n_sched = refresh_team_schedule(conn, season)
    n_av = refresh_availability(conn, league_key) if league_key else 0
    conn.close()

    # Keep every team's roster current so the dashboard's opponent projections
    # don't go stale (one read-only Yahoo call per team).
    n_rosters = 0
    n_matchup = 0
    if league_key:
        from . import yahoo, league
        n_rosters = sum(1 for _ in yahoo.sync_league_rosters(league_key))
        # Live current-week scoreboard so the matchup score stays current too.
        try:
            n_matchup = league.refresh_current_matchup(league_key).get("matchups") or 0
        except SystemExit:
            pass

    return {"pl_rows": n_pl, "dates": dates, "probables": n_prob,
            "schedule": n_sched, "available": n_av, "rosters": n_rosters,
            "matchup": n_matchup, "league_key": league_key}


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    db.init_db()
    league = args[1] if len(args) > 1 else None
    if args[:1] == ["refresh"]:
        r = refresh(league)
        print(f"dates {r['dates']} | PL {r['pl_rows']} | probables {r['probables']} "
              f"| schedule {r['schedule']} | available {r['available']} "
              f"| rosters {r['rosters']} | matchup {r['matchup']} | league {r['league_key']}")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
