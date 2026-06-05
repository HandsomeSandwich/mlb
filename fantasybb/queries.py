"""Read-only query helpers used by the Flask dashboards.

Sorting is done server-side against a whitelist of columns (never raw user
input) so the sort param can't be used for SQL injection.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict

from . import behavior, match

# column -> SQL expression, for each leaderboard. Keys are what the UI sends.
HITTER_SORTS = {
    "name": "p.full_name", "team": "t.abbreviation", "g": "b.g", "pa": "b.pa",
    "ab": "b.ab", "r": "b.r", "h": "b.h", "doubles": "b.doubles",
    "triples": "b.triples", "hr": "b.hr", "rbi": "b.rbi", "sb": "b.sb",
    "bb": "b.bb", "so": "b.so", "avg": "b.avg", "obp": "b.obp", "slg": "b.slg",
    "ops": "b.ops", "babip": "b.babip", "avg_ev": "s.avg_ev",
    "barrel_pct": "s.barrel_pct", "hard_hit_pct": "s.hard_hit_pct",
    "xwoba": "s.xwoba",
}
PITCHER_SORTS = {
    "name": "p.full_name", "team": "t.abbreviation", "g": "ps.g", "gs": "ps.gs",
    "w": "ps.w", "l": "ps.l", "sv": "ps.sv", "hld": "ps.hld", "ip": "ps.outs",
    "so": "ps.so", "bb": "ps.bb", "h": "ps.h", "hr": "ps.hr", "era": "ps.era",
    "whip": "ps.whip", "k9": "ps.k9", "bb9": "ps.bb9", "kbb": "ps.kbb",
}
# Recent-form (last-N-days) leaderboard. Computed aggregates are sorted by their
# SELECT aliases, so the whitelist values are the alias names.
RECENT_SORTS = {
    "name": "full_name", "team": "team", "g": "g", "pa": "pa", "ab": "ab",
    "h": "h", "r": "r", "hr": "hr", "rbi": "rbi", "sb": "sb", "bb": "bb",
    "so": "so", "avg": "avg", "obp": "obp", "slg": "slg", "ops": "ops",
    "season_ops": "season_ops", "delta": "delta",
}


def ip_str(outs: int | None) -> str:
    """Convert stored outs back to the familiar 'X.Y' innings notation."""
    if not outs:
        return "0.0"
    return f"{outs // 3}.{outs % 3}"


def _order_clause(sort: str, direction: str, allowed: dict, default: str) -> str:
    col = allowed.get(sort, allowed[default])
    dir_sql = "ASC" if direction == "asc" else "DESC"
    # stable tiebreak on name so equal values don't jump around between loads
    return f"{col} {dir_sql} NULLS LAST, p.full_name ASC"


def hitters(conn, *, sort="hr", direction="desc", team=None, pos=None,
            min_pa=0, q=None, limit=300, season=None):
    where = ["b.pa >= ?", "b.season = ?"]
    params: list = [min_pa, season]
    if team:
        where.append("t.abbreviation = ?")
        params.append(team)
    if pos:
        where.append("p.position = ?")
        params.append(pos)
    if q:
        where.append("p.full_name LIKE ?")
        params.append(f"%{q}%")
    order = _order_clause(sort, direction, HITTER_SORTS, "hr")
    sql = f"""
        SELECT p.player_id, p.full_name, p.position, p.bat_side,
               t.abbreviation AS team,
               b.g, b.pa, b.ab, b.r, b.h, b.doubles, b.triples, b.hr, b.rbi,
               b.sb, b.cs, b.bb, b.so, b.avg, b.obp, b.slg, b.ops, b.babip,
               s.avg_ev, s.max_ev, s.barrel_pct, s.hard_hit_pct, s.xwoba
        FROM batting_season b
        JOIN players p USING(player_id)
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN statcast_batting s
               ON s.player_id = b.player_id AND s.season = b.season
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT ?"""
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def pitchers(conn, *, sort="so", direction="desc", team=None,
             min_outs=0, q=None, limit=300, season=None):
    where = ["ps.outs >= ?", "ps.season = ?"]
    params: list = [min_outs, season]
    if team:
        where.append("t.abbreviation = ?")
        params.append(team)
    if q:
        where.append("p.full_name LIKE ?")
        params.append(f"%{q}%")
    order = _order_clause(sort, direction, PITCHER_SORTS, "so")
    sql = f"""
        SELECT p.player_id, p.full_name, p.pitch_hand, t.abbreviation AS team,
               ps.g, ps.gs, ps.w, ps.l, ps.sv, ps.hld, ps.outs, ps.h, ps.r,
               ps.er, ps.hr, ps.bb, ps.so, ps.bf, ps.era, ps.whip, ps.k9,
               ps.bb9, ps.kbb
        FROM pitching_season ps
        JOIN players p USING(player_id)
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT ?"""
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def seasons(conn) -> list[int]:
    """Seasons present in the DB, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT season FROM batting_season ORDER BY season DESC").fetchall()
    return [r[0] for r in rows]


def latest_season(conn):
    r = conn.execute("SELECT MAX(season) FROM batting_season").fetchone()[0]
    return r


def player(conn, player_id: int):
    return conn.execute(
        """SELECT p.*, t.abbreviation AS team, t.name AS team_name
           FROM players p LEFT JOIN teams t ON t.team_id = p.team_id
           WHERE p.player_id = ?""",
        (player_id,),
    ).fetchone()


def batting_season_row(conn, player_id: int, season: int):
    return conn.execute(
        "SELECT * FROM batting_season WHERE player_id=? AND season=?",
        (player_id, season)).fetchone()


def pitching_season_row(conn, player_id: int, season: int):
    return conn.execute(
        "SELECT * FROM pitching_season WHERE player_id=? AND season=?",
        (player_id, season)).fetchone()


def statcast_row(conn, player_id: int, season: int):
    return conn.execute(
        "SELECT * FROM statcast_batting WHERE player_id=? AND season=?",
        (player_id, season)).fetchone()


def batting_log(conn, player_id: int, season: int, limit=200):
    return conn.execute(
        """SELECT bg.*, ht.abbreviation AS team_abbr, ot.abbreviation AS opp_abbr
           FROM batting_games bg
           LEFT JOIN teams ht ON ht.team_id = bg.team_id
           LEFT JOIN teams ot ON ot.team_id = bg.opp_team_id
           WHERE bg.player_id=? AND substr(bg.game_date,1,4)=?
           ORDER BY bg.game_date DESC LIMIT ?""",
        (player_id, str(season), limit),
    ).fetchall()


def pitching_log(conn, player_id: int, season: int, limit=200):
    return conn.execute(
        """SELECT pg.*, ht.abbreviation AS team_abbr, ot.abbreviation AS opp_abbr
           FROM pitching_games pg
           LEFT JOIN teams ht ON ht.team_id = pg.team_id
           LEFT JOIN teams ot ON ot.team_id = pg.opp_team_id
           WHERE pg.player_id=? AND substr(pg.game_date,1,4)=?
           ORDER BY pg.game_date DESC LIMIT ?""",
        (player_id, str(season), limit),
    ).fetchall()


def player_seasons(conn, player_id: int) -> list[int]:
    """Seasons this player has a batting or pitching line for, newest first."""
    rows = conn.execute(
        """SELECT season FROM batting_season WHERE player_id=?
           UNION SELECT season FROM pitching_season WHERE player_id=?
           ORDER BY season DESC""",
        (player_id, player_id)).fetchall()
    return [r[0] for r in rows]


def teams_list(conn):
    return conn.execute(
        "SELECT abbreviation, name FROM teams ORDER BY abbreviation").fetchall()


def positions_list(conn):
    rows = conn.execute(
        "SELECT DISTINCT position FROM players WHERE position IS NOT NULL "
        "AND position NOT IN ('P') ORDER BY position").fetchall()
    return [r[0] for r in rows]


def leaders(conn, table_join, col, season, limit=5, asc=False, where="1=1"):
    """Small helper for the home-page leader cards (for one season)."""
    direction = "ASC" if asc else "DESC"
    sql = f"""
        SELECT p.player_id, p.full_name, x.{col} AS val
        FROM {table_join} x JOIN players p USING(player_id)
        WHERE x.{col} IS NOT NULL AND x.season = ? AND {where}
        ORDER BY x.{col} {direction} LIMIT ?"""
    return conn.execute(sql, (season, limit)).fetchall()


def my_teams(conn):
    """Synced Yahoo rosters available to display."""
    return conn.execute(
        "SELECT DISTINCT team_key, team_name FROM fantasy_roster ORDER BY team_name"
    ).fetchall()


def my_team(conn, team_key: str, season: int):
    """A synced roster joined with that season's batting/pitching/Statcast lines."""
    return conn.execute(
        """SELECT fr.slot, fr.selected_position, fr.positions, fr.yahoo_name,
                  fr.yahoo_team, fr.status, fr.player_id,
                  p.full_name,
                  b.pa AS b_pa, b.ab AS b_ab, b.h AS b_h, b.r, b.hr, b.rbi,
                  b.sb, b.avg, b.ops,
                  sc.barrel_pct, sc.avg_ev,
                  ps.gs, ps.w, ps.sv, ps.so AS p_so, ps.outs, ps.era, ps.whip,
                  ps.er AS p_er, ps.h AS p_h, ps.bb AS p_bb
           FROM fantasy_roster fr
           LEFT JOIN players p          ON p.player_id = fr.player_id
           LEFT JOIN batting_season b   ON b.player_id = fr.player_id AND b.season = ?
           LEFT JOIN pitching_season ps ON ps.player_id = fr.player_id AND ps.season = ?
           LEFT JOIN statcast_batting sc ON sc.player_id = fr.player_id AND sc.season = ?
           WHERE fr.team_key = ?
           ORDER BY fr.slot""",
        (season, season, season, team_key),
    ).fetchall()


def streaming_dates(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT game_date FROM probable_starts ORDER BY game_date")]


def streaming_league(conn):
    row = conn.execute("SELECT team_key FROM fantasy_roster LIMIT 1").fetchone()
    return row[0].rsplit(".t.", 1)[0] if row else None


def streaming_board(conn, game_date: str, season: int, league_key: str | None):
    """Probable starters for a date with Pollack tier, availability, and stats."""
    return conn.execute(
        """SELECT ps.name, ps.team, ps.opp, ps.is_home, ps.player_id,
                  pl.tier, pl.tier_rank, pl.rank AS pl_rank, pl.matchup, pl.rostership,
                  pit.era, pit.whip, pit.k9, pit.so, pit.gs, pit.outs, pit.w,
                  CASE WHEN fr.player_id IS NOT NULL THEN 'mine'
                       WHEN av.name IS NOT NULL THEN 'available'
                       ELSE 'rostered' END AS avail
           FROM probable_starts ps
           LEFT JOIN pl_streamers pl
                  ON pl.player_id = ps.player_id AND pl.game_date = ps.game_date
           LEFT JOIN pitching_season pit
                  ON pit.player_id = ps.player_id AND pit.season = ?
           LEFT JOIN fantasy_roster fr ON fr.player_id = ps.player_id
           LEFT JOIN availability av
                  ON av.player_id = ps.player_id AND av.league_key = ?
           WHERE ps.game_date = ?
           ORDER BY pl.tier_rank IS NULL, pl.tier_rank, pl.rank, ps.name""",
        (season, league_key, game_date),
    ).fetchall()


def roster_players(conn):
    """Matched players on any synced roster (for the weekly-compare picker)."""
    return conn.execute(
        """SELECT DISTINCT fr.player_id, p.full_name, p.position
           FROM fantasy_roster fr JOIN players p ON p.player_id = fr.player_id
           ORDER BY p.full_name"""
    ).fetchall()


def _monday(d):
    """Monday (date) of the ISO week containing date string d (YYYY-MM-DD)."""
    import datetime as _dt
    dt = _dt.date.fromisoformat(d)
    return dt - _dt.timedelta(days=dt.weekday())


def weekly_splits(conn, player_id: int, kind: str):
    """Per-fantasy-week (Mon-Sun) aggregates for a player, grouped by season.

    Returns {season: [ {week, start, end, ...stats} ]}. `kind` is 'bat' or 'pit'.
    Weeks are numbered within each season (week 1 = opening week).
    """
    if kind == "pit":
        sql = ("SELECT game_date, outs, w, sv, so, er, h, bb, hr "
               "FROM pitching_games WHERE player_id=? ORDER BY game_date")
        fields = ("outs", "w", "sv", "so", "er", "h", "bb", "hr")
    else:
        sql = ("SELECT game_date, pa, ab, r, h, doubles, triples, hr, rbi, "
               "bb, so, sb, tb, hbp, sac_flies FROM batting_games "
               "WHERE player_id=? ORDER BY game_date")
        fields = ("pa", "ab", "r", "h", "doubles", "triples", "hr", "rbi",
                  "bb", "so", "sb", "tb", "hbp", "sac_flies")

    buckets: dict = {}  # (season, monday) -> sums
    for row in conn.execute(sql, (player_id,)):
        season = int(row["game_date"][:4])
        mon = _monday(row["game_date"])
        b = buckets.setdefault((season, mon), {f: 0 for f in fields})
        for f in fields:
            b[f] += row[f] or 0

    out: dict = {}
    for (season, mon), b in buckets.items():
        out.setdefault(season, []).append((mon, b))
    for season, weeks in out.items():
        weeks.sort(key=lambda x: x[0])
        result = []
        for i, (mon, b) in enumerate(weeks, start=1):
            import datetime as _dt
            rec = {"week": i, "start": mon.isoformat(),
                   "end": (mon + _dt.timedelta(days=6)).isoformat(), **b}
            if kind == "pit":
                ip = b["outs"] / 3
                rec["ip"] = ip
                rec["era"] = round(9 * b["er"] / ip, 2) if ip else None
                rec["whip"] = round((b["h"] + b["bb"]) / ip, 2) if ip else None
            else:
                rec["avg"] = round(b["h"] / b["ab"], 3) if b["ab"] else None
                obp_den = b["ab"] + b["bb"] + b["hbp"] + b["sac_flies"]
                obp = (b["h"] + b["bb"] + b["hbp"]) / obp_den if obp_den else 0
                slg = b["tb"] / b["ab"] if b["ab"] else 0
                rec["ops"] = round(obp + slg, 3) if b["ab"] else None
            result.append(rec)
        out[season] = result
    return out


def league_keys(conn):
    return [r[0] for r in conn.execute("SELECT league_key FROM league_meta")]


def league_info(conn, league_key):
    meta = conn.execute("SELECT * FROM league_meta WHERE league_key=?", (league_key,)).fetchone()
    mine = conn.execute(
        "SELECT * FROM league_teams WHERE league_key=? AND is_mine=1", (league_key,)).fetchone()
    return meta, mine


def _scoring_cats(conn, league_key):
    return conn.execute(
        "SELECT stat_id, display_name, is_pitching, sort_order FROM league_categories "
        "WHERE league_key=? ORDER BY ord", (league_key,)).fetchall()


def matchup_view(conn, league_key):
    """My current-week H2H matchup, category by category vs my opponent."""
    mine = conn.execute(
        "SELECT * FROM matchup_team WHERE league_key=? AND is_mine=1 "
        "ORDER BY week DESC LIMIT 1", (league_key,)).fetchone()
    if not mine:
        return None
    week, my_key, opp_key = mine["week"], mine["team_key"], mine["opp_team_key"]
    names = dict(conn.execute(
        "SELECT team_key, name FROM league_teams WHERE league_key=?", (league_key,)).fetchall())

    def vals(team_key):
        return {r["stat_id"]: (r["value"], r["win"]) for r in conn.execute(
            "SELECT stat_id, value, win FROM matchup_category WHERE league_key=? "
            "AND week=? AND team_key=?", (league_key, week, team_key))}

    mv, ov = vals(my_key), vals(opp_key)
    rows, won, lost, tied = [], 0, 0, 0
    for c in _scoring_cats(conn, league_key):
        sid = c["stat_id"]
        my_v, win = mv.get(sid, (None, None))
        opp_v = ov.get(sid, (None, None))[0]
        if win == 1:
            won += 1
        elif win == 0:
            lost += 1
        elif sid in mv:
            tied += 1
        rows.append({"label": c["display_name"], "is_pitching": c["is_pitching"],
                     "my_value": my_v, "opp_value": opp_v, "win": win})
    return {
        "week": week, "my_name": names.get(my_key), "opp_name": names.get(opp_key),
        "my_points": mine["points"], "rows": rows, "won": won, "lost": lost, "tied": tied,
    }


def league_ranks(conn, league_key):
    """For each scoring category: every team's total, with my team's rank."""
    teams = {r["team_key"]: (r["name"], r["is_mine"]) for r in conn.execute(
        "SELECT team_key, name, is_mine FROM league_teams WHERE league_key=?", (league_key,))}
    out = []
    for c in _scoring_cats(conn, league_key):
        sid, hi_better = c["stat_id"], (c["sort_order"] == 1)
        vals = conn.execute(
            "SELECT team_key, value, value_str FROM team_category "
            "WHERE league_key=? AND stat_id=?", (league_key, sid)).fetchall()
        ranked = sorted(
            [v for v in vals if v["value"] is not None],
            key=lambda v: v["value"], reverse=hi_better)
        n = len(ranked)
        cells, my_rank, my_str = [], None, None
        for i, v in enumerate(ranked, start=1):
            name, is_mine = teams.get(v["team_key"], ("?", 0))
            if is_mine:
                my_rank, my_str = i, v["value_str"]
            cells.append({"rank": i, "name": name, "value": v["value_str"],
                          "is_mine": is_mine, "pct": (n - i) / (n - 1) if n > 1 else 0.5})
        out.append({"label": c["display_name"], "is_pitching": c["is_pitching"],
                    "n": n, "my_rank": my_rank, "my_value": my_str, "cells": cells})
    return out


def _player_value(conn, lookup, name):
    """Rough one-number 2026 fantasy value + a short stat line, for trade reads."""
    pid = match.match(lookup, name or "")
    if not pid:
        return None, None
    pos = conn.execute("SELECT position FROM players WHERE player_id=?", (pid,)).fetchone()
    is_pit = bool(pos and pos[0] == "P")
    if is_pit:
        p = conn.execute("SELECT w,sv,so,era,whip FROM pitching_season WHERE player_id=? "
                         "AND season=2026", (pid,)).fetchone()
        if p and (p["so"] or p["w"]):
            val = ((p["w"] or 0) * 4 + (p["sv"] or 0) * 3 + (p["so"] or 0) * 0.4
                   - ((p["era"] or 4) - 4) * 8 - ((p["whip"] or 1.25) - 1.25) * 30)
            return round(val, 1), f"{p['w']}W {p['sv']}SV {p['so']}K {p['era']}ERA"
    b = conn.execute("SELECT hr,r,rbi,sb,ops FROM batting_season WHERE player_id=? "
                     "AND season=2026", (pid,)).fetchone()
    if b and b["ops"] is not None:
        val = ((b["hr"] or 0) * 3 + (b["r"] or 0) + (b["rbi"] or 0) + (b["sb"] or 0) * 2
               + ((b["ops"] or 0.7) - 0.7) * 120)
        return round(val, 1), f"{b['hr']}HR {b['r']}R {b['rbi']}RBI {b['sb']}SB"
    return None, None


def _txn_desc(t):
    adds = [m["player_name"] for m in t["moves"] if m["move_type"] == "add"]
    drops = [m["player_name"] for m in t["moves"] if m["move_type"] == "drop"]
    if t["type"] == "trade":
        return "TRADE: " + " ↔ ".join(sorted(t["acting_teams"]))
    team = next(iter(t["acting_teams"]), "?")
    parts = []
    if adds:
        parts.append("added " + ", ".join(adds))
    if drops:
        parts.append("dropped " + ", ".join(drops))
    return f"{team} — " + "; ".join(parts)


def transactions_analysis(conn, league_key):
    txns = behavior.load_txns(conn, league_key)
    lookup = match.build_lookup(conn)
    activity = behavior.team_activity(txns)
    timing = behavior.timing_similarity(txns)
    feeding = behavior.drop_add_feeding(txns)
    trade_rows = behavior.trades(txns, lambda n: _player_value(conn, lookup, n))
    partners = behavior.trade_partner_counts(trade_rows)

    daily = defaultdict(int)
    for t in txns:
        daily[_dt.datetime.fromtimestamp(t["ts"]).date().isoformat()] += 1
    timeline = sorted(daily.items())

    feed = [{"when": _dt.datetime.fromtimestamp(t["ts"]).strftime("%b %d, %H:%M"),
             "type": t["type"], "desc": _txn_desc(t)}
            for t in sorted(txns, key=lambda x: x["ts"], reverse=True)[:40]]

    signals = []
    for p in partners:
        if p["count"] >= 2:
            signals.append({"kind": "Repeat trade partners", "level": "high",
                            "text": f"{p['a']} and {p['b']} have traded {p['count']} times."})
    for tr in trade_rows:
        if tr["value_gap"] and tr["value_gap"] >= 35 and len(tr["sides"]) == 2:
            hi = max(tr["sides"], key=lambda s: s["value"])
            lo = min(tr["sides"], key=lambda s: s["value"])
            when = _dt.datetime.fromtimestamp(tr["ts"]).strftime("%b %d")
            signals.append({"kind": "Lopsided trade (heuristic)", "level": "mid",
                            "text": f"{when}: {hi['team']} got ~{hi['value']} vs "
                                    f"{lo['team']}'s ~{lo['value']} in rough value."})
    for f in feeding:
        if f["count"] >= 3:
            signals.append({"kind": "Drop→add feeding", "level": "mid",
                            "text": f"{f['adder']} has picked up {f['count']} players "
                                    f"dropped by {f['dropper']} ({', '.join(f['players'][:3])}…)."})
    for tm in timing:
        if tm["score"] >= 0.5 and tm["co"] >= 4:
            signals.append({"kind": "Synchronized timing", "level": "low",
                            "text": f"{tm['a']} and {tm['b']} transacted within 30 min "
                                    f"of each other {tm['co']} times."})

    return {
        "n_txns": len(txns), "activity": activity, "timing": timing[:10],
        "feeding": feeding[:10], "trades": trade_rows, "partners": partners,
        "timeline": timeline, "feed": feed, "signals": signals,
        "max_day": max((c for _, c in timeline), default=1),
    }


def db_status(conn):
    """Counts for the footer / status line so the user can see ingest progress."""
    g = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
    return {
        "games": g("SELECT COUNT(*) FROM games"),
        "batting_games": g("SELECT COUNT(*) FROM batting_games"),
        "pitching_games": g("SELECT COUNT(*) FROM pitching_games"),
        "hitters": g("SELECT COUNT(*) FROM batting_season"),
        "pitchers": g("SELECT COUNT(*) FROM pitching_season"),
        "statcast": g("SELECT COUNT(*) FROM statcast_batting"),
    }


def latest_game_date(conn, season=None):
    if season is not None:
        return conn.execute(
            "SELECT MAX(game_date) FROM batting_games WHERE substr(game_date,1,4)=?",
            (str(season),)).fetchone()[0]
    return conn.execute("SELECT MAX(game_date) FROM batting_games").fetchone()[0]


def recent_window(conn, days: int, season=None):
    """(start_date, end_date) for the last `days` of data in a season.

    'Recent' is relative to the most recent game in that season (a completed
    season ends in the fall; an in-progress one ends at the latest game played).
    """
    end = latest_game_date(conn, season)
    start = conn.execute("SELECT date(?, ?)", (end, f"-{int(days)} days")).fetchone()[0]
    return start, end


def recent_hitters(conn, *, days=15, min_pa=20, sort="ops", direction="desc",
                   limit=300, season=None):
    """Hot/cold hitters over the last `days` of the season.

    Rate stats (AVG/OBP/SLG/OPS) are computed over the window; `delta` is the
    window OPS minus the player's full-season OPS — positive = heating up.
    """
    start, _ = recent_window(conn, days, season)
    order = RECENT_SORTS.get(sort, "ops")
    dir_sql = "ASC" if direction == "asc" else "DESC"
    sql = f"""
        SELECT p.player_id, p.full_name, p.position, t.abbreviation AS team,
               COUNT(DISTINCT bg.game_pk) AS g,
               SUM(bg.pa) AS pa, SUM(bg.ab) AS ab, SUM(bg.h) AS h,
               SUM(bg.r) AS r, SUM(bg.hr) AS hr, SUM(bg.rbi) AS rbi,
               SUM(bg.sb) AS sb, SUM(bg.bb) AS bb, SUM(bg.so) AS so,
               ROUND(SUM(bg.h) * 1.0 / NULLIF(SUM(bg.ab), 0), 3) AS avg,
               ROUND((SUM(bg.h) + SUM(bg.bb) + SUM(bg.hbp)) * 1.0
                     / NULLIF(SUM(bg.ab) + SUM(bg.bb) + SUM(bg.hbp) + SUM(bg.sac_flies), 0), 3) AS obp,
               ROUND(SUM(bg.tb) * 1.0 / NULLIF(SUM(bg.ab), 0), 3) AS slg,
               ROUND(
                 (SUM(bg.h) + SUM(bg.bb) + SUM(bg.hbp)) * 1.0
                   / NULLIF(SUM(bg.ab) + SUM(bg.bb) + SUM(bg.hbp) + SUM(bg.sac_flies), 0)
                 + SUM(bg.tb) * 1.0 / NULLIF(SUM(bg.ab), 0), 3) AS ops,
               bs.ops AS season_ops,
               ROUND(
                 (SUM(bg.h) + SUM(bg.bb) + SUM(bg.hbp)) * 1.0
                   / NULLIF(SUM(bg.ab) + SUM(bg.bb) + SUM(bg.hbp) + SUM(bg.sac_flies), 0)
                 + SUM(bg.tb) * 1.0 / NULLIF(SUM(bg.ab), 0)
                 - bs.ops, 3) AS delta
        FROM batting_games bg
        JOIN players p USING(player_id)
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN batting_season bs
               ON bs.player_id = bg.player_id AND bs.season = ?
        WHERE bg.game_date >= ? AND bg.game_date <= ?
        GROUP BY bg.player_id
        HAVING SUM(bg.pa) >= ?
        ORDER BY {order} {dir_sql} NULLS LAST, p.full_name ASC
        LIMIT ?"""
    end = latest_game_date(conn, season)
    return conn.execute(sql, (season, start, end, min_pa, limit)).fetchall()
