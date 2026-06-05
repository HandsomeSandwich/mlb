"""Build / update the SQLite database from the MLB Stats API.

Usage (from the project root, with the venv active):

    python -m fantasybb.ingest all     --season 2025      # everything below, in order
    python -m fantasybb.ingest teams   --season 2025
    python -m fantasybb.ingest season  --season 2025      # official season totals (fast)
    python -m fantasybb.ingest games   --season 2025      # per-game logs (resumable, slow)
    python -m fantasybb.ingest games   --season 2025 --start 2025-04-01 --end 2025-04-14
    python -m fantasybb.ingest bios                        # fill bat/throw/birthdate
    python -m fantasybb.ingest statcast --season 2025      # advanced metrics (pybaseball)

`games` is resumable: already-ingested gamePks are skipped, so you can stop and
re-run, or backfill in date ranges.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import db
from .mlb_api import MLBClient


# --------------------------------------------------------------------------- #
# small parsing helpers
# --------------------------------------------------------------------------- #
def _i(d: dict, key: str) -> int:
    v = d.get(key, 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _f(d: dict, key: str):
    """Parse a stat that may be a string like '.321', '3.45', or '-.--'."""
    v = d.get(key)
    if v in (None, "", "-.--", ".---", "*.**", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ip_to_outs(ip) -> int:
    """'5.2' innings -> 17 outs. Whole part * 3 + the .0/.1/.2 thirds."""
    if ip in (None, ""):
        return 0
    try:
        whole, _, frac = str(ip).partition(".")
        return int(whole or 0) * 3 + int(frac[:1] or 0)
    except ValueError:
        return 0


def _today() -> str:
    return _dt.date.today().isoformat()


# --------------------------------------------------------------------------- #
# upserts
# --------------------------------------------------------------------------- #
def ensure_player(conn, pid: int, name: str, position=None, team_id=None) -> None:
    """Insert a player if new; fill blanks without clobbering known fields."""
    conn.execute(
        "INSERT INTO players (player_id, full_name, position, team_id) "
        "VALUES (?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET "
        "full_name=excluded.full_name, "
        "position=COALESCE(excluded.position, players.position), "
        "team_id=COALESCE(excluded.team_id, players.team_id)",
        (pid, name, position, team_id),
    )


# --------------------------------------------------------------------------- #
# ingest steps
# --------------------------------------------------------------------------- #
def ingest_teams(client: MLBClient, conn, season: int) -> int:
    rows = []
    for t in client.teams(season):
        rows.append(
            (
                t["id"],
                t.get("abbreviation"),
                t.get("name"),
                t.get("clubName"),
                t.get("league", {}).get("name"),
                t.get("division", {}).get("name"),
            )
        )
    with conn:
        conn.executemany(
            "INSERT INTO teams (team_id, abbreviation, name, club_name, league, division) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(team_id) DO UPDATE SET "
            "abbreviation=excluded.abbreviation, name=excluded.name, "
            "club_name=excluded.club_name, league=excluded.league, division=excluded.division",
            rows,
        )
    return len(rows)


def ingest_season(client: MLBClient, conn, season: int) -> tuple[int, int]:
    hit = client.season_hitting(season)
    pit = client.season_pitching(season)

    with conn:
        for sp in hit:
            pl = sp["player"]
            team_id = (sp.get("team") or {}).get("id")
            ensure_player(conn, pl["id"], pl["fullName"], team_id=team_id)
            s = sp["stat"]
            conn.execute(
                """INSERT INTO batting_season
                   (player_id, season, team_id, g, pa, ab, r, h, doubles, triples, hr,
                    rbi, bb, ibb, so, hbp, sb, cs, gidp, tb, sac_bunts, sac_flies,
                    avg, obp, slg, ops, babip)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(player_id, season) DO UPDATE SET
                    team_id=excluded.team_id, g=excluded.g,
                    pa=excluded.pa, ab=excluded.ab, r=excluded.r, h=excluded.h,
                    doubles=excluded.doubles, triples=excluded.triples, hr=excluded.hr,
                    rbi=excluded.rbi, bb=excluded.bb, ibb=excluded.ibb, so=excluded.so,
                    hbp=excluded.hbp, sb=excluded.sb, cs=excluded.cs, gidp=excluded.gidp,
                    tb=excluded.tb, sac_bunts=excluded.sac_bunts, sac_flies=excluded.sac_flies,
                    avg=excluded.avg, obp=excluded.obp, slg=excluded.slg, ops=excluded.ops,
                    babip=excluded.babip""",
                (
                    pl["id"], season, team_id,
                    _i(s, "gamesPlayed"), _i(s, "plateAppearances"), _i(s, "atBats"),
                    _i(s, "runs"), _i(s, "hits"), _i(s, "doubles"), _i(s, "triples"),
                    _i(s, "homeRuns"), _i(s, "rbi"), _i(s, "baseOnBalls"),
                    _i(s, "intentionalWalks"), _i(s, "strikeOuts"), _i(s, "hitByPitch"),
                    _i(s, "stolenBases"), _i(s, "caughtStealing"),
                    _i(s, "groundIntoDoublePlay"), _i(s, "totalBases"),
                    _i(s, "sacBunts"), _i(s, "sacFlies"),
                    _f(s, "avg"), _f(s, "obp"), _f(s, "slg"), _f(s, "ops"), _f(s, "babip"),
                ),
            )

        for sp in pit:
            pl = sp["player"]
            team_id = (sp.get("team") or {}).get("id")
            ensure_player(conn, pl["id"], pl["fullName"], team_id=team_id)
            s = sp["stat"]
            outs = _i(s, "outs") or _ip_to_outs(s.get("inningsPitched"))
            ip = outs / 3 if outs else 0
            so, bb = _i(s, "strikeOuts"), _i(s, "baseOnBalls")
            k9 = round(so * 9 / ip, 2) if ip else None
            bb9 = round(bb * 9 / ip, 2) if ip else None
            kbb = round(so / bb, 2) if bb else None
            conn.execute(
                """INSERT INTO pitching_season
                   (player_id, season, team_id, g, gs, w, l, sv, hld, bs, outs, h, r, er,
                    hr, bb, ibb, so, hbp, bf, era, whip, k9, bb9, kbb)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(player_id, season) DO UPDATE SET
                    team_id=excluded.team_id, g=excluded.g,
                    gs=excluded.gs, w=excluded.w, l=excluded.l, sv=excluded.sv,
                    hld=excluded.hld, bs=excluded.bs, outs=excluded.outs, h=excluded.h,
                    r=excluded.r, er=excluded.er, hr=excluded.hr, bb=excluded.bb,
                    ibb=excluded.ibb, so=excluded.so, hbp=excluded.hbp, bf=excluded.bf,
                    era=excluded.era, whip=excluded.whip, k9=excluded.k9, bb9=excluded.bb9,
                    kbb=excluded.kbb""",
                (
                    pl["id"], season, team_id,
                    _i(s, "gamesPlayed"), _i(s, "gamesStarted"), _i(s, "wins"),
                    _i(s, "losses"), _i(s, "saves"), _i(s, "holds"), _i(s, "blownSaves"),
                    outs, _i(s, "hits"), _i(s, "runs"), _i(s, "earnedRuns"),
                    _i(s, "homeRuns"), bb, _i(s, "intentionalWalks"), so,
                    _i(s, "hitBatsmen"), _i(s, "battersFaced"),
                    _f(s, "era"), _f(s, "whip"), k9, bb9, kbb,
                ),
            )
    return len(hit), len(pit)


def _batting_row(game_pk, pid, team_id, opp, is_home, date, s):
    return (
        game_pk, pid, team_id, opp, is_home, date,
        _i(s, "plateAppearances"), _i(s, "atBats"), _i(s, "runs"), _i(s, "hits"),
        _i(s, "doubles"), _i(s, "triples"), _i(s, "homeRuns"), _i(s, "rbi"),
        _i(s, "baseOnBalls"), _i(s, "intentionalWalks"), _i(s, "strikeOuts"),
        _i(s, "hitByPitch"), _i(s, "stolenBases"), _i(s, "caughtStealing"),
        _i(s, "groundIntoDoublePlay"), _i(s, "totalBases"),
        _i(s, "sacBunts"), _i(s, "sacFlies"), _i(s, "leftOnBase"),
    )


def _pitching_row(game_pk, pid, team_id, opp, is_home, date, s):
    return (
        game_pk, pid, team_id, opp, is_home, date,
        _i(s, "gamesStarted"), _i(s, "wins"), _i(s, "losses"), _i(s, "saves"),
        _i(s, "holds"), _i(s, "blownSaves"), _i(s, "outs"), _i(s, "hits"),
        _i(s, "runs"), _i(s, "earnedRuns"), _i(s, "homeRuns"), _i(s, "baseOnBalls"),
        _i(s, "intentionalWalks"), _i(s, "strikeOuts"), _i(s, "hitBatsmen"),
        _i(s, "battersFaced"), _i(s, "numberOfPitches"), _i(s, "strikes"),
        _i(s, "balks"), _i(s, "wildPitches"),
    )


_BAT_COLS = (
    "game_pk, player_id, team_id, opp_team_id, is_home, game_date, pa, ab, r, h, "
    "doubles, triples, hr, rbi, bb, ibb, so, hbp, sb, cs, gidp, tb, sac_bunts, "
    "sac_flies, lob"
)
_PIT_COLS = (
    "game_pk, player_id, team_id, opp_team_id, is_home, game_date, gs, w, l, sv, "
    "hld, bs, outs, h, r, er, hr, bb, ibb, so, hbp, bf, pitches, strikes, balks, wp"
)


def ingest_games(client: MLBClient, conn, season: int,
                 start=None, end=None, limit=None) -> int:
    done = {r[0] for r in conn.execute("SELECT game_pk FROM ingest_log").fetchall()}
    schedule = client.schedule(season)

    todo = []
    for g in schedule:
        if (g.get("status", {}).get("abstractGameState")) != "Final":
            continue
        date = g["gameDate"][:10]
        if start and date < start:
            continue
        if end and date > end:
            continue
        if g["gamePk"] in done:
            continue
        todo.append(g)
    if limit:
        todo = todo[:limit]

    total = len(todo)
    print(f"games to ingest: {total}", flush=True)
    n = 0
    for g in todo:
        pk = g["gamePk"]
        date = g["gameDate"][:10]
        home_id = g["teams"]["home"]["team"]["id"]
        away_id = g["teams"]["away"]["team"]["id"]
        try:
            box = client.boxscore(pk)
        except Exception as exc:  # noqa: BLE001 - skip a bad game, keep going
            print(f"  ! skip {pk}: {exc}", file=sys.stderr, flush=True)
            continue

        bat_rows, pit_rows = [], []
        for side, is_home, team_id, opp in (
            ("home", 1, home_id, away_id),
            ("away", 0, away_id, home_id),
        ):
            for p in box["teams"][side]["players"].values():
                pid = p["person"]["id"]
                pos = p.get("position", {}).get("abbreviation")
                ensure_player(conn, pid, p["person"]["fullName"], pos, team_id)
                st = p.get("stats", {})
                if st.get("batting"):
                    bat_rows.append(_batting_row(pk, pid, team_id, opp, is_home, date, st["batting"]))
                if st.get("pitching"):
                    pit_rows.append(_pitching_row(pk, pid, team_id, opp, is_home, date, st["pitching"]))

        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO games (game_pk, game_date, season, game_type, "
                "home_team_id, away_team_id, venue, status) VALUES (?,?,?,?,?,?,?,?)",
                (pk, date, season, g.get("gameType"), home_id, away_id,
                 g.get("venue", {}).get("name"), "Final"),
            )
            conn.executemany(
                f"INSERT OR REPLACE INTO batting_games ({_BAT_COLS}) "
                f"VALUES ({','.join('?' * 25)})", bat_rows)
            conn.executemany(
                f"INSERT OR REPLACE INTO pitching_games ({_PIT_COLS}) "
                f"VALUES ({','.join('?' * 26)})", pit_rows)
            conn.execute("INSERT OR REPLACE INTO ingest_log (game_pk, ingested_at) VALUES (?,?)",
                         (pk, _today()))

        n += 1
        if n % 50 == 0 or n == total:
            print(f"  {n}/{total} games", flush=True)
    return n


def ingest_bios(client: MLBClient, conn) -> int:
    ids = [r[0] for r in conn.execute(
        "SELECT player_id FROM players WHERE bat_side IS NULL").fetchall()]
    if not ids:
        return 0
    people = client.people(ids)
    with conn:
        for p in people:
            conn.execute(
                "UPDATE players SET bat_side=?, pitch_hand=?, birth_date=?, "
                "position=COALESCE(position, ?) WHERE player_id=?",
                (
                    p.get("batSide", {}).get("code"),
                    p.get("pitchHand", {}).get("code"),
                    p.get("birthDate"),
                    p.get("primaryPosition", {}).get("abbreviation"),
                    p["id"],
                ),
            )
    return len(people)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the fantasy-baseball SQLite DB.")
    ap.add_argument("command",
                    choices=["init", "teams", "season", "games", "bios", "statcast", "all"])
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--start", help="YYYY-MM-DD (games command)")
    ap.add_argument("--end", help="YYYY-MM-DD (games command)")
    ap.add_argument("--limit", type=int, help="cap number of games (games command)")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between API calls")
    args = ap.parse_args(argv)

    db.init_db()
    conn = db.connect()
    client = MLBClient(pause=args.pause)

    if args.command == "init":
        print("schema ready ->", db.DB_PATH)

    elif args.command == "teams":
        print("teams:", ingest_teams(client, conn, args.season))

    elif args.command == "season":
        h, p = ingest_season(client, conn, args.season)
        print(f"season totals: {h} hitters, {p} pitchers")

    elif args.command == "games":
        ingest_games(client, conn, args.season, args.start, args.end, args.limit)

    elif args.command == "bios":
        print("bios updated:", ingest_bios(client, conn))

    elif args.command == "statcast":
        from .statcast import ingest_statcast
        print("statcast hitters:", ingest_statcast(conn, args.season))

    elif args.command == "all":
        print("teams:", ingest_teams(client, conn, args.season))
        h, p = ingest_season(client, conn, args.season)
        print(f"season totals: {h} hitters, {p} pitchers")
        print("bios updated:", ingest_bios(client, conn))
        ingest_games(client, conn, args.season, args.start, args.end, args.limit)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
