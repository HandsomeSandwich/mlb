"""Read-only query helpers used by the Flask dashboards.

Sorting is done server-side against a whitelist of columns (never raw user
input) so the sort param can't be used for SQL injection.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
import re
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


def search_players(conn, q: str, season=None, limit=60):
    """Find any player (hitter or pitcher) by name, for the global search box."""
    return conn.execute(
        """SELECT p.player_id, p.full_name, p.position, p.bat_side, p.pitch_hand,
                  t.abbreviation AS team,
                  (b.player_id IS NOT NULL) AS has_bat,
                  (ps.player_id IS NOT NULL) AS has_pit
           FROM players p
           LEFT JOIN teams t ON t.team_id = p.team_id
           LEFT JOIN batting_season b  ON b.player_id = p.player_id AND b.season = ?
           LEFT JOIN pitching_season ps ON ps.player_id = p.player_id AND ps.season = ?
           WHERE p.full_name LIKE ?
           ORDER BY (b.pa IS NULL AND ps.outs IS NULL), p.full_name
           LIMIT ?""",
        (season, season, f"%{q}%", limit),
    ).fetchall()


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


def rolling_form(conn, player_id: int, season: int, kind: str, window=15):
    """A smoothed game-by-game form line: rolling-`window` OPS (bat) or ERA (pit)."""
    if kind == "pit":
        rows = conn.execute(
            "SELECT game_date, outs, er, h, bb FROM pitching_games "
            "WHERE player_id=? AND substr(game_date,1,4)=? ORDER BY game_date",
            (player_id, str(season))).fetchall()
    else:
        rows = conn.execute(
            "SELECT game_date, ab, h, bb, hbp, sac_flies, tb FROM batting_games "
            "WHERE player_id=? AND substr(game_date,1,4)=? ORDER BY game_date",
            (player_id, str(season))).fetchall()
    series = []
    for i in range(len(rows)):
        w = rows[max(0, i - window + 1): i + 1]
        if kind == "pit":
            outs = sum(r["outs"] or 0 for r in w)
            er = sum(r["er"] or 0 for r in w)
            val = round(9 * er / (outs / 3), 2) if outs else None
        else:
            ab = sum(r["ab"] or 0 for r in w)
            h = sum(r["h"] or 0 for r in w)
            bb = sum(r["bb"] or 0 for r in w)
            hbp = sum(r["hbp"] or 0 for r in w)
            sf = sum(r["sac_flies"] or 0 for r in w)
            tb = sum(r["tb"] or 0 for r in w)
            den = ab + bb + hbp + sf
            val = round((h + bb + hbp) / den + (tb / ab), 3) if (ab and den) else None
        series.append({"date": rows[i]["game_date"][5:], "v": val})
    return series


def weekly_overlay(conn, player_id: int, kind: str):
    """{season: [{week, v}]} of weekly OPS/ERA, for the year-over-year chart."""
    data = weekly_splits(conn, player_id, kind)
    key = "era" if kind == "pit" else "ops"
    return {s: [{"week": w["week"], "v": w.get(key)} for w in weeks]
            for s, weeks in data.items()}


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
        "SELECT DISTINCT team_key, team_name FROM fantasy_roster WHERE is_mine=1 ORDER BY team_name"
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
                  ps.g AS p_g, ps.gs, ps.w, ps.sv, ps.so AS p_so, ps.outs, ps.era, ps.whip,
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


def default_streaming_date(dates):
    """Pick which date the streaming board opens to: today if we have it,
    otherwise the most recent past date, else the earliest upcoming one.
    `dates` is the ascending ISO-date list from streaming_dates()."""
    if not dates:
        return None
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if today in dates:
        return today
    past = [d for d in dates if d < today]
    if past:
        return past[-1]      # most recent day we have data for
    return dates[0]          # all dates are in the future -> soonest


def streaming_league(conn):
    row = conn.execute("SELECT team_key FROM fantasy_roster WHERE is_mine=1 LIMIT 1").fetchone()
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
           LEFT JOIN fantasy_roster fr ON fr.player_id = ps.player_id AND fr.is_mine = 1
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
           WHERE fr.is_mine = 1
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


def matchup_view(conn, league_key, week=None):
    """My H2H matchup, category by category vs my opponent.

    `week=None` uses the latest week present in matchup_team (the standalone
    matchup page). The dashboard passes the calendar's current week so the
    scored categories line up with the headline opponent — and gets None
    (section hidden) when that week hasn't been scored/synced yet, instead of
    showing last week's result under this week's header."""
    if week is None:
        mine = conn.execute(
            "SELECT * FROM matchup_team WHERE league_key=? AND is_mine=1 "
            "ORDER BY week DESC LIMIT 1", (league_key,)).fetchone()
    else:
        mine = conn.execute(
            "SELECT * FROM matchup_team WHERE league_key=? AND is_mine=1 AND week=? "
            "LIMIT 1", (league_key, week)).fetchone()
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


def commissioner_teams(conn, league_key):
    """Team names whose manager (or a co-manager) is the league commissioner."""
    return {r[0] for r in conn.execute(
        "SELECT lt.name FROM league_managers lm "
        "JOIN league_teams lt ON lt.league_key=lm.league_key AND lt.team_key=lm.team_key "
        "WHERE lm.league_key=? AND lm.is_commissioner=1", (league_key,))}


def all_play_standings(conn, league_key):
    """Luck-adjusted standings: each week, score every team against ALL others,
    not just its one H2H opponent. The actual record depends on the schedule
    (who you happened to draw); the all-play record is schedule-independent true
    strength. The gap between them is luck.
    """
    cats = _scoring_cats(conn, league_key)
    hi = {c["stat_id"]: (c["sort_order"] == 1) for c in cats}  # True => higher wins
    teams = {r["team_key"]: r for r in conn.execute(
        "SELECT team_key, name, manager, is_mine, wins, losses, ties, rank "
        "FROM league_teams WHERE league_key=?", (league_key,))}
    if not teams:
        return None
    weeks = [r[0] for r in conn.execute(
        "SELECT DISTINCT week FROM matchup_category WHERE league_key=? ORDER BY week",
        (league_key,))]

    def fnum(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    ap = {tk: {"w": 0, "l": 0, "t": 0, "cw": 0, "cl": 0, "ct": 0} for tk in teams}
    for wk in weeks:
        vals = defaultdict(dict)
        for r in conn.execute(
                "SELECT team_key, stat_id, value FROM matchup_category "
                "WHERE league_key=? AND week=?", (league_key, wk)):
            v = fnum(r["value"])
            if v is not None:
                vals[r["team_key"]][r["stat_id"]] = v
        present = [tk for tk in teams if tk in vals]
        for a in present:
            for b in present:
                if a == b:
                    continue
                cw = cl = ct = 0
                for sid, better_hi in hi.items():
                    va, vb = vals[a].get(sid), vals[b].get(sid)
                    if va is None or vb is None:
                        continue
                    if va == vb:
                        ct += 1
                    elif (va > vb) == better_hi:
                        cw += 1
                    else:
                        cl += 1
                ap[a]["cw"] += cw
                ap[a]["cl"] += cl
                ap[a]["ct"] += ct
                if cw > cl:
                    ap[a]["w"] += 1
                elif cw < cl:
                    ap[a]["l"] += 1
                else:
                    ap[a]["t"] += 1

    commish = commissioner_teams(conn, league_key)
    rows = []
    for tk, t in teams.items():
        d = ap[tk]
        ap_g = d["w"] + d["l"] + d["t"]
        cat_g = d["cw"] + d["cl"] + d["ct"]
        ap_pct = (d["w"] + 0.5 * d["t"]) / ap_g if ap_g else None
        cat_pct = (d["cw"] + 0.5 * d["ct"]) / cat_g if cat_g else None
        actual_w, actual_t = t["wins"] or 0, t["ties"] or 0
        actual_g = actual_w + (t["losses"] or 0) + actual_t
        exp_w = ap_pct * actual_g if ap_pct is not None else None
        rows.append({
            "team": t["name"], "mgr": t["manager"], "is_mine": t["is_mine"],
            "is_commish": t["name"] in commish,
            "actual_w": actual_w, "actual_l": t["losses"] or 0, "actual_t": actual_t,
            "actual_rank": t["rank"],
            "ap_w": d["w"], "ap_l": d["l"], "ap_t": d["t"],
            "ap_pct": ap_pct, "cat_pct": cat_pct,
            "exp_w": round(exp_w, 1) if exp_w is not None else None,
            "luck_w": round(actual_w - exp_w, 1) if exp_w is not None else None,
        })
    rows.sort(key=lambda r: (r["ap_pct"] is not None, r["ap_pct"] or 0), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["ap_rank"] = i
        # + => standings rank is worse (higher number) than talent => buried by luck
        r["rank_gap"] = (r["actual_rank"] - i) if r["actual_rank"] else None
    me = next((r for r in rows if r["is_mine"]), None)
    return {"rows": rows, "weeks": len(weeks), "n": len(rows), "me": me}


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


FIP_CONST = 3.15


def player_factors(conn, pid, season):
    """Multi-factor outlook for one player: production now, luck, skill, form."""
    row = conn.execute(
        "SELECT p.full_name, p.position, t.abbreviation AS team "
        "FROM players p LEFT JOIN teams t ON t.team_id = p.team_id WHERE p.player_id=?",
        (pid,)).fetchone()
    if not row:
        return {"name": "?", "kind": None, "team": None}
    name = row["full_name"]
    team = row["team"]
    is_pit = row["position"] == "P"

    if is_pit:
        ps = conn.execute("SELECT * FROM pitching_season WHERE player_id=? AND season=?",
                          (pid, season)).fetchone()
        if not ps or not ps["outs"]:
            return {"name": name, "team": team, "kind": "pit", "thin": True}
        ip = ps["outs"] / 3
        fip = round((13 * (ps["hr"] or 0) + 3 * ((ps["bb"] or 0) + (ps["hbp"] or 0))
                     - 2 * (ps["so"] or 0)) / ip + FIP_CONST, 2)
        era = ps["era"]
        luck = round(era - fip, 2) if era is not None else None   # + => ERA worse than skill (unlucky)
        value_now = round((ps["w"] or 0) * 4 + (ps["sv"] or 0) * 3 + (ps["so"] or 0) * 0.4
                          - ((era or 4) - 4) * 8 - ((ps["whip"] or 1.25) - 1.25) * 30, 1)
        grade = "A" if fip <= 3.0 else "B" if fip <= 3.75 else "C" if fip <= 4.5 else "D" if fip <= 5.25 else "F"
        roll = rolling_form(conn, pid, season, "pit")
        form = _form_arrow(roll[-1]["v"] if roll else None, era, lower_better=True)
        return {"name": name, "pid": pid, "team": team, "kind": "pit", "value_now": value_now,
                "prod": f"{ps['w']}W {ps['sv']}SV · {ps['so']}K · {era} ERA · {ps['whip']} WHIP",
                "skill": f"FIP {fip} · K/BB {ps['kbb'] or '—'}", "fip": fip,
                "luck": luck, "luck_dir": _luck_dir(luck, 0.4),
                "luck_txt": _pit_luck_txt(luck), "grade": grade, "form": form}

    b = conn.execute("SELECT * FROM batting_season WHERE player_id=? AND season=?",
                     (pid, season)).fetchone()
    if not b or not b["ab"]:
        return {"name": name, "team": team, "kind": "bat", "thin": True}
    s = conn.execute("SELECT * FROM statcast_batting WHERE player_id=? AND season=?",
                     (pid, season)).fetchone()
    xavg = luck = None
    if s and s["xba"] is not None and b["ab"]:
        xavg = round(s["xba"] * (b["ab"] - (b["so"] or 0)) / b["ab"], 3)
        luck = round(xavg - (b["avg"] or 0), 3)
    value_now = round((b["hr"] or 0) * 3 + (b["r"] or 0) + (b["rbi"] or 0)
                      + (b["sb"] or 0) * 2 + ((b["ops"] or 0.7) - 0.7) * 120, 1)
    xw = s["xwoba"] if s else None
    grade = ("A" if xw and xw >= 0.370 else "B" if xw and xw >= 0.340
             else "C" if xw and xw >= 0.310 else "D" if xw and xw >= 0.290 else "F") if xw else "—"
    roll = rolling_form(conn, pid, season, "bat")
    form = _form_arrow(roll[-1]["v"] if roll else None, b["ops"], lower_better=False)
    return {"name": name, "pid": pid, "team": team, "kind": "bat", "value_now": value_now,
            "prod": f"{b['hr']} HR · {b['r']} R · {b['rbi']} RBI · {b['sb']} SB · {b['avg']} AVG",
            "skill": (f"xwOBA {xw} · {s['barrel_pct']}% brl · {s['avg_ev']} EV" if s else "no Statcast"),
            "luck": luck, "luck_dir": _luck_dir(luck, 0.020),
            "luck_txt": _bat_luck_txt(luck, b["avg"], xavg), "grade": grade, "form": form}


def _luck_dir(luck, thresh):
    if luck is None:
        return 0
    return 1 if luck > thresh else (-1 if luck < -thresh else 0)


def _bat_luck_txt(luck, avg, xavg):
    if luck is None:
        return "—"
    if luck > 0.020:
        return f"unlucky: {avg} AVG vs {xavg} expected → due up"
    if luck < -0.020:
        return f"lucky: {avg} AVG vs {xavg} expected → due down"
    return "performing to expectation"


def _pit_luck_txt(luck):
    if luck is None:
        return "—"
    if luck > 0.40:
        return "ERA worse than FIP → due to improve"
    if luck < -0.40:
        return "ERA flattering his FIP → due to regress"
    return "ERA in line with skill"


def _form_arrow(recent, season_rate, lower_better):
    if recent is None or season_rate is None:
        return "→"
    better = (recent < season_rate) if lower_better else (recent > season_rate)
    worse = (recent > season_rate) if lower_better else (recent < season_rate)
    return "▲" if better else ("▼" if worse else "→")


# --- Prospective trade calculator -----------------------------------------
# Score a HYPOTHETICAL "I give X, I get Y" deal before making it. Reuses
# player_factors (production value + luck + skill grade), then re-weights toward
# the underlying so a lucky hot streak doesn't fool the verdict.

# --- Position-fair, category-weighted player rating ------------------------
# A player's rating is z-scored per category against the pool of regulars, then
# averaged across the league's scoring cats (weighted by where MY team is weak).
# Scaled so 50 = an average regular, +15 per standard deviation (~80 = elite).
# Because each category is measured in SDs relative to its own pool, a hitter and
# a pitcher land on the same scale — apples-to-apples.

_RATE_BASE, _RATE_PER_SD = 50.0, 15.0


def _cat_need_weights(conn, league_key):
    """Per-category weight from MY team's standings rank: weak cats weighted up
    (~1.4), strong cats down (~0.6), so the rating favors players who help where
    I actually need help. 1.0 (neutral) when rank is unknown."""
    weights = {}
    if not league_key:
        return weights
    my_keys = {r[0] for r in conn.execute(
        "SELECT team_key FROM league_teams WHERE league_key=? AND is_mine=1", (league_key,))}
    for c in _scoring_cats(conn, league_key):
        sid, hi = c["stat_id"], (c["sort_order"] == 1)
        vals = conn.execute(
            "SELECT team_key, value FROM team_category "
            "WHERE league_key=? AND stat_id=? AND value IS NOT NULL", (league_key, sid)).fetchall()
        ranked = sorted(vals, key=lambda v: v["value"], reverse=hi)
        n = len(ranked)
        my_rank = next((i for i, v in enumerate(ranked, 1) if v["team_key"] in my_keys), None)
        weights[sid] = round(0.6 + 0.8 * (my_rank - 1) / (n - 1), 3) if (my_rank and n > 1) else 1.0
    return weights


def _cat_rating_dists(conn, season, bat_floor=100, pit_floor=45):
    """Mean/std of each category's track-record-adjusted per-game rate across the
    pool of regulars, so a player can be z-scored against his peers. Returns
    {stat_id: (mean, std, higher_is_better)}."""
    dists = {}

    def build(tbl, sids, floor_col, floor):
        rows = defaultdict(dict)   # pid -> {season: row}
        for r in conn.execute(f"SELECT * FROM {tbl} WHERE season IN (?,?)", (season, season - 1)):
            rows[r["player_id"]][r["season"]] = r
        pool = [pid for pid, yr in rows.items()
                if season in yr and (yr[season][floor_col] or 0) >= floor]
        for sid in sids:
            _grp, _is_rate, hi, numf, denf, _scale = _CAT_EXT[sid]
            vals = []
            for pid in pool:
                r26, r25 = rows[pid].get(season), rows[pid].get(season - 1)
                num, den = numf(r26), denf(r26)
                if r25:
                    num += 0.5 * numf(r25); den += 0.5 * denf(r25)
                if den > 0:
                    vals.append(num / den)
            if len(vals) >= 5:
                m = sum(vals) / len(vals)
                sd = math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals)) or 1e-9
                dists[sid] = (m, sd, hi)

    bat_sids = [s for s, spec in _CAT_EXT.items() if spec[0] == "bat"]
    pit_sids = [s for s, spec in _CAT_EXT.items() if spec[0] == "pit"]
    build("batting_season", bat_sids, "pa", bat_floor)
    build("pitching_season", pit_sids, "outs", pit_floor)
    return dists


def _player_rating(conn, pid, season, kind, dists, weights):
    """50 + 15·(need-weighted average z) across the league's scoring cats for this
    player's side of the ball. None if no category data."""
    grp = "bat" if kind == "bat" else "pit"
    num = den = 0.0
    for sid, (m, sd, hi) in dists.items():
        if _CAT_EXT[sid][0] != grp:
            continue
        if weights and sid not in weights:    # restrict to the league's scoring cats
            continue
        b = _blended_cat(conn, pid, sid, season)
        if not b:
            continue
        z = (b["rate"] - m) / sd
        if not hi:
            z = -z
        w = weights.get(sid, 1.0)
        num += w * z
        den += w
    if den == 0:
        return None
    return round(_RATE_BASE + _RATE_PER_SD * (num / den), 1)


def _adj_rating(rating, pf):
    """Nudge the rating toward the underlying: an unlucky hitter (AVG below xBA)
    or pitcher (ERA above FIP) is worth a touch more than today's line, a lucky
    one a touch less. Capped at ±6 points so luck informs, never dominates."""
    if rating is None:
        return None
    luck = pf.get("luck")            # bat: xavg-avg; pit: era-fip (both +=due up)
    if not luck:
        return rating
    nudge = max(-6.0, min(6.0, luck * (150 if pf.get("kind") == "bat" else 4)))
    return round(rating + nudge, 1)


# --- Positional scarcity (VORP-style) --------------------------------------
# A good SS is worth more than an equally-productive OF because the SS you'd
# replace him with is worse. We bucket players (hitters by MLB position; pitchers
# split SP/RP by start ratio, so closers — scarce save sources — get their own
# replacement level), find each bucket's REPLACEMENT-LEVEL rating (the marginal
# rostered player at depth = teams × slots), and give a premium = how far below
# the average replacement that bucket sits. Scarce spots (C, SS, RP) get a boost;
# deep ones (OF, 1B, SP) a small haircut. Centered WITHIN each side of the ball so
# scarcity never shifts hitters vs pitchers as a whole (the rating already balances
# those).
_POS_BUCKET = {
    "C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
    "LF": "OF", "RF": "OF", "CF": "OF", "OF": "OF",
    "DH": "DH", "TWP": "DH", "IF": "1B",
}
_POS_SLOTS = {"C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3, "DH": 1,
              "SP": 5, "RP": 2, "CL": 1}
_SCARCITY_CAP = 5.0
_CLOSER_SV = 5   # saves that mark a closing role (vs middle relief)


def _pitcher_bucket(row):
    """SP if he started at least half his games; otherwise a reliever — CL if he's
    racking up saves (a closing role), else RP. Closers get their own bucket so
    their replacement level reflects the save cliff, not middle relievers' ratios."""
    g, gs = row["g"] or 0, row["gs"] or 0
    if not g:
        return None
    if gs >= 0.5 * g:
        return "SP"
    return "CL" if (row["sv"] or 0) >= _CLOSER_SV else "RP"


def _bulk_ratings(conn, season, dists, weights, group, floor):
    """{pid: (rating, bucket)} for every qualified player on one side of the ball,
    computed in bulk (two queries + arithmetic) so a full-league replacement-level
    pass is cheap. Uses the SAME blend/z math as _player_rating so the pool and the
    trade participants are scored identically."""
    rows = defaultdict(dict)
    bucket_of = {}
    if group == "bat":
        cur = conn.execute(
            "SELECT b.*, p.position AS _pos FROM batting_season b "
            "JOIN players p ON p.player_id = b.player_id WHERE b.season IN (?,?)",
            (season, season - 1))
        floor_col = "pa"
    else:
        cur = conn.execute("SELECT * FROM pitching_season WHERE season IN (?,?)",
                           (season, season - 1))
        floor_col = "outs"
    for r in cur:
        rows[r["player_id"]][r["season"]] = r
        if r["season"] == season:
            bucket_of[r["player_id"]] = (_POS_BUCKET.get(r["_pos"]) if group == "bat"
                                         else _pitcher_bucket(r))
    sids = [s for s in dists if _CAT_EXT[s][0] == group]
    out = {}
    for pid, yrs in rows.items():
        r26 = yrs.get(season)
        if not r26 or (r26[floor_col] or 0) < floor:
            continue
        bucket = bucket_of.get(pid)
        if not bucket:
            continue
        r25 = yrs.get(season - 1)
        num = den = 0.0
        for sid in sids:
            if weights and sid not in weights:
                continue
            _g, _ir, hi, numf, denf, _sc = _CAT_EXT[sid]
            n, d = numf(r26), denf(r26)
            if r25:
                n += 0.5 * numf(r25); d += 0.5 * denf(r25)
            if d <= 0:
                continue
            m, sd, _hi = dists[sid]
            z = (n / d - m) / sd
            if not hi:
                z = -z
            w = weights.get(sid, 1.0)
            num += w * z; den += w
        if den:
            out[pid] = (round(_RATE_BASE + _RATE_PER_SD * (num / den), 1), bucket)
    return out


def _position_scarcity(conn, season, dists, weights, n_teams):
    """{bucket: premium} — points to add to a player's rating for his position's
    scarcity. Hitter and pitcher buckets are each centered among their own kind,
    so premiums net to ~0 within hitters and within pitchers."""
    out = {}
    for group, floor in (("bat", 100), ("pit", 60)):
        by_bucket = defaultdict(list)
        for _pid, (rt, bucket) in _bulk_ratings(conn, season, dists, weights, group, floor).items():
            by_bucket[bucket].append(rt)
        repl = {}
        for bucket, rts in by_bucket.items():
            rts.sort(reverse=True)
            depth = max(1, n_teams * _POS_SLOTS.get(bucket, 1))
            repl[bucket] = rts[min(depth, len(rts)) - 1]
        if not repl:
            continue
        base = sum(repl.values()) / len(repl)
        for b, v in repl.items():
            out[b] = max(-_SCARCITY_CAP, min(_SCARCITY_CAP, round(base - v, 1)))
    return out


def _player_bucket(conn, pid, kind, season):
    if kind == "pit":
        r = conn.execute("SELECT g, gs, sv FROM pitching_season WHERE player_id=? AND season=?",
                         (pid, season)).fetchone()
        return _pitcher_bucket(r) if r else None
    row = conn.execute("SELECT position FROM players WHERE player_id=?", (pid,)).fetchone()
    return _POS_BUCKET.get(row["position"]) if row else None


# The raw value_now formulas put hitters on a ~2.5x larger scale than pitchers,
# so naively summing them across a trade overrates the bat-heavy side. A flat
# multiplier distorts the tails (it would balloon an elite, ERA-lucky closer), so
# we normalize each value to a position Z-SCORE mapped to a common 0–100 rating
# (50 = average contributor, ~80 = +2 SD). That makes a 98th-percentile arm and a
# 95th-percentile bat read as the near-equals they are.
_POS_NORM_CACHE = {}


def _pos_norm(conn, season):
    if season not in _POS_NORM_CACHE:
        def stats(sql):
            xs = [r[0] for r in conn.execute(sql, (season,)) if r[0] is not None]
            if not xs:
                return (0.0, 1.0)
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
            return (m, sd or 1.0)
        _POS_NORM_CACHE[season] = {
            "bat": stats("SELECT hr*3+r+rbi+sb*2+(COALESCE(ops,0.7)-0.7)*120 "
                         "FROM batting_season WHERE season=? AND pa>=150"),
            "pit": stats("SELECT w*4+sv*3+so*0.4-(COALESCE(era,4)-4)*8-(COALESCE(whip,1.25)-1.25)*30 "
                         "FROM pitching_season WHERE season=? AND outs>=90"),
        }
    return _POS_NORM_CACHE[season]


def _tv(value, kind, norm):
    """Position-normalized trade rating (50 = average contributor at the position,
    +15 per standard deviation). Comparable across hitters and pitchers."""
    if value is None:
        return None
    m, sd = norm["pit" if kind == "pit" else "bat"]
    return round(50 + 15 * (value - m) / sd, 1)


# stat_id -> (label, group, is_rate, lower_better, value(row))
_TRADE_CATS = {
    7:  ("R",   "bat", False, False, lambda r: r["r"] or 0),
    12: ("HR",  "bat", False, False, lambda r: r["hr"] or 0),
    13: ("RBI", "bat", False, False, lambda r: r["rbi"] or 0),
    62: ("SB",  "bat", False, False, lambda r: (r["sb"] or 0) - (r["cs"] or 0)),
    21: ("K",   "bat", False, True,  lambda r: r["so"] or 0),
    3:  ("AVG", "bat", True,  False, lambda r: r["avg"]),
    55: ("OPS", "bat", True,  False, lambda r: r["ops"]),
    28: ("W",   "pit", False, False, lambda r: r["w"] or 0),
    42: ("K",   "pit", False, False, lambda r: r["so"] or 0),
    50: ("IP",  "pit", False, False, lambda r: round((r["outs"] or 0) / 3, 1)),
    38: ("HRA", "pit", False, True,  lambda r: r["hr"] or 0),
    90: ("SV+H", "pit", False, False, lambda r: (r["sv"] or 0) + (r["hld"] or 0)),
    26: ("ERA", "pit", True,  True,  lambda r: r["era"]),
    27: ("WHIP", "pit", True,  True,  lambda r: r["whip"]),
}


def _trade_cat_swing(conn, league_key, give_ids, get_ids, season):
    """Per-category swing of the deal, in the league's own scoring cats. Counting
    cats show a true net (get total − give total); rate cats show incoming vs
    outgoing averages (a rough side-by-side, not a team total)."""
    cats = _scoring_cats(conn, league_key)
    if not cats:
        return []

    def rows(ids, group):
        tbl = "batting_season" if group == "bat" else "pitching_season"
        out = []
        for pid in ids:
            r = conn.execute(f"SELECT * FROM {tbl} WHERE player_id=? AND season=?",
                             (pid, season)).fetchone()
            if r:
                out.append(r)
        return out

    cache = {("give", "bat"): rows(give_ids, "bat"), ("give", "pit"): rows(give_ids, "pit"),
             ("get", "bat"): rows(get_ids, "bat"), ("get", "pit"): rows(get_ids, "pit")}
    out = []
    for c in cats:
        spec = _TRADE_CATS.get(c["stat_id"])
        if not spec:
            continue
        label, group, is_rate, lower, valf = spec
        gvals = [v for r in cache[("give", group)] if (v := valf(r)) is not None]
        tvals = [v for r in cache[("get", group)] if (v := valf(r)) is not None]
        if not gvals and not tvals:
            continue
        if is_rate:
            g = round(sum(gvals) / len(gvals), 3) if gvals else None
            t = round(sum(tvals) / len(tvals), 3) if tvals else None
            net = None if (g is None or t is None) else round(t - g, 3)
        else:
            g, t = round(sum(gvals), 1), round(sum(tvals), 1)
            net = round(t - g, 1)
        good = None
        if net:
            good = (net < 0) if lower else (net > 0)
        out.append({"label": c["display_name"] or label, "is_rate": is_rate,
                    "lower_better": lower, "give": g, "get": t, "net": net, "good": good})
    return out


def _trade_verdict(edge_now, edge_adj, flips):
    def lab(e):
        if e > 8:
            return ("clearly favors you", "good")
        if e > 2:
            return ("leans your way", "good")
        if e >= -2:
            return ("roughly even", "flat")
        if e >= -8:
            return ("leans against you", "bad")
        return ("clearly favors them", "bad")
    n, a = lab(edge_now), lab(edge_adj)
    return {"now": n[0], "now_cls": n[1], "adj": a[0], "adj_cls": a[1], "flips": flips}


def evaluate_trade(conn, league_key, give_ids, get_ids, season):
    """Score a hypothetical deal on a position-fair, category-need-weighted rating
    (50 = average regular, +15 per SD) AND a luck-adjusted version, plus a
    per-category swing. Each player is z-scored per category against the pool of
    regulars, then averaged with weight on the cats MY team is weak in, so the
    rating favors players who help where I need it. Edges net each side against an
    average roster spot, so uneven-count deals are scored fairly. Flags when
    regression flips who wins."""
    dists = _cat_rating_dists(conn, season)
    weights = _cat_need_weights(conn, league_key) if league_key else {}
    n_teams = conn.execute("SELECT COUNT(*) FROM league_teams WHERE league_key=?",
                           (league_key,)).fetchone()[0] if league_key else 0
    scarcity = _position_scarcity(conn, season, dists, weights, n_teams or 12)
    pos = _pos_norm(conn, season)   # fallback scale when a player lacks category data

    def rate(pf, pid):
        """Weighted rating + positional-scarcity premium, falling back to the
        position-normalized heuristic for players with too little category data."""
        if pf.get("thin") or not pf.get("kind"):
            return None, None
        r = _player_rating(conn, pid, season, pf["kind"], dists, weights)
        if r is None:    # too little category data — fall back to the heuristic scale
            r = _tv(pf.get("value_now"), pf.get("kind"), pos)
        if r is not None:
            pf["pos_bucket"] = _player_bucket(conn, pid, pf["kind"], season)
            pf["scarcity"] = scarcity.get(pf["pos_bucket"], 0.0)
            r = round(r + pf["scarcity"], 1)
        return r, _adj_rating(r, pf)

    def side(ids):
        players = []
        for pid in ids:
            pf = player_factors(conn, pid, season)
            pf["value_now"], pf["value_adj"] = rate(pf, pid)
            players.append(pf)
        rated = [p for p in players if p.get("value_now") is not None]
        adj_of = lambda p: p["value_adj"] if p["value_adj"] is not None else p["value_now"]
        return {
            "players": players,
            "now": round(sum(p["value_now"] for p in rated), 1),
            "adj": round(sum(adj_of(p) for p in rated), 1),
            # surplus over an average roster spot (50) — count-fair basis for the edge
            "surp_now": round(sum(p["value_now"] - _RATE_BASE for p in rated), 1),
            "surp_adj": round(sum(adj_of(p) - _RATE_BASE for p in rated), 1),
        }

    give, get = side(give_ids), side(get_ids)
    edge_now = round(get["surp_now"] - give["surp_now"], 1)
    edge_adj = round(get["surp_adj"] - give["surp_adj"], 1)
    flips = abs(edge_now) > 1 and abs(edge_adj) > 1 and (edge_now > 0) != (edge_adj > 0)
    return {
        "give": give, "get": get,
        "edge_now": edge_now, "edge_adj": edge_adj, "flips": flips,
        "cats": _trade_cat_swing(conn, league_key, give_ids, get_ids, season),
        "verdict": _trade_verdict(edge_now, edge_adj, flips),
        "weighted": bool(weights),
        "empty": not give_ids and not get_ids,
    }


def manager_trade_scoreboard(conn, league_key, season, trades=None):
    """League table of who's net-winning their trades (since each trade date)."""
    trades = trades if trades is not None else trade_factors(conn, league_key, season)
    agg = defaultdict(lambda: {"trades": 0, "wins": 0, "net_since": 0.0, "net_now": 0.0})
    for t in trades:
        if len(t["sides"]) != 2:
            continue
        a, b = t["sides"]
        for me, opp in ((a, b), (b, a)):
            r = agg[me["mgr"]]
            r["trades"] += 1
            r["net_since"] += me["since"] - opp["since"]
            r["net_now"] += me["now"] - opp["now"]
            if me["since"] >= opp["since"]:
                r["wins"] += 1
    rows = [{"mgr": m, "trades": v["trades"], "wins": v["wins"],
             "losses": v["trades"] - v["wins"], "net_since": round(v["net_since"], 1),
             "net_now": round(v["net_now"], 1)} for m, v in agg.items()]
    rows.sort(key=lambda x: x["net_since"], reverse=True)
    return rows


def _since_trade(conn, pid, kind, since_date):
    """A player's accumulated game-log production from `since_date` onward."""
    if kind == "pit":
        r = conn.execute(
            "SELECT COUNT(*) g, SUM(outs) outs, SUM(er) er, SUM(h) h, SUM(bb) bb, "
            "SUM(w) w, SUM(sv) sv, SUM(so) so FROM pitching_games "
            "WHERE player_id=? AND game_date>=?", (pid, since_date)).fetchone()
        if not r or not r["g"]:
            return None
        ip = (r["outs"] or 0) / 3
        era = round(9 * (r["er"] or 0) / ip, 2) if ip else None
        whip = round(((r["h"] or 0) + (r["bb"] or 0)) / ip, 2) if ip else None
        val = round((r["w"] or 0) * 4 + (r["sv"] or 0) * 3 + (r["so"] or 0) * 0.4
                    - ((era or 4) - 4) * 8 - ((whip or 1.25) - 1.25) * 30, 1)
        return {"value": val, "g": r["g"],
                "line": f"{r['w']}W {r['sv']}SV {r['so']}K · {era if era is not None else '—'} ERA · {ip:.0f} IP"}
    r = conn.execute(
        "SELECT COUNT(*) g, SUM(ab) ab, SUM(h) h, SUM(r) r, SUM(hr) hr, SUM(rbi) rbi, "
        "SUM(sb) sb, SUM(bb) bb, SUM(hbp) hbp, SUM(sac_flies) sf, SUM(tb) tb "
        "FROM batting_games WHERE player_id=? AND game_date>=?", (pid, since_date)).fetchone()
    if not r or not r["g"]:
        return None
    ab = r["ab"] or 0
    avg = round((r["h"] or 0) / ab, 3) if ab else None
    den = ab + (r["bb"] or 0) + (r["hbp"] or 0) + (r["sf"] or 0)
    ops = (round(((r["h"] or 0) + (r["bb"] or 0) + (r["hbp"] or 0)) / den + (r["tb"] or 0) / ab, 3)
           if (ab and den) else None)
    val = round((r["hr"] or 0) * 3 + (r["r"] or 0) + (r["rbi"] or 0) + (r["sb"] or 0) * 2
                + ((ops or 0.7) - 0.7) * 120, 1)
    return {"value": val, "g": r["g"],
            "line": f"{r['hr']}HR {r['r']}R {r['rbi']}RBI {r['sb']}SB · {avg if avg is not None else '—'} · {r['g']}G"}


def _window_prod(conn, pid, kind, start_date, end_date):
    """Accumulated game-log production in [start_date, end_date). Same value
    formula as _since_trade, so before/after splits are directly comparable."""
    if kind == "pit":
        r = conn.execute(
            "SELECT COUNT(*) g, SUM(outs) outs, SUM(er) er, SUM(h) h, SUM(bb) bb, "
            "SUM(w) w, SUM(sv) sv, SUM(so) so FROM pitching_games "
            "WHERE player_id=? AND game_date>=? AND game_date<?",
            (pid, start_date, end_date)).fetchone()
        if not r or not r["g"]:
            return None
        ip = (r["outs"] or 0) / 3
        era = round(9 * (r["er"] or 0) / ip, 2) if ip else None
        whip = round(((r["h"] or 0) + (r["bb"] or 0)) / ip, 2) if ip else None
        val = round((r["w"] or 0) * 4 + (r["sv"] or 0) * 3 + (r["so"] or 0) * 0.4
                    - ((era or 4) - 4) * 8 - ((whip or 1.25) - 1.25) * 30, 1)
        # Rate stats (ERA/WHIP) are noise over a few innings; only "scoreable"
        # with enough sample. Below that we keep the line but not a trusted value.
        ok = ip >= 8
        return {"value": val, "g": r["g"], "ip": round(ip, 1), "ab": None,
                "per_g": round(val / r["g"], 1), "scoreable": ok,
                "line": f"{r['w']}W {r['sv']}SV {r['so']}K · {era if era is not None else '—'} ERA · {ip:.0f} IP"}
    r = conn.execute(
        "SELECT COUNT(*) g, SUM(ab) ab, SUM(h) h, SUM(r) r, SUM(hr) hr, SUM(rbi) rbi, "
        "SUM(sb) sb, SUM(bb) bb, SUM(hbp) hbp, SUM(sac_flies) sf, SUM(tb) tb "
        "FROM batting_games WHERE player_id=? AND game_date>=? AND game_date<?",
        (pid, start_date, end_date)).fetchone()
    if not r or not r["g"]:
        return None
    ab = r["ab"] or 0
    avg = round((r["h"] or 0) / ab, 3) if ab else None
    den = ab + (r["bb"] or 0) + (r["hbp"] or 0) + (r["sf"] or 0)
    ops = (round(((r["h"] or 0) + (r["bb"] or 0) + (r["hbp"] or 0)) / den + (r["tb"] or 0) / ab, 3)
           if (ab and den) else None)
    val = round((r["hr"] or 0) * 3 + (r["r"] or 0) + (r["rbi"] or 0) + (r["sb"] or 0) * 2
                + ((ops or 0.7) - 0.7) * 120, 1)
    # OPS over a handful of AB swings wildly (1 HR in 2 AB -> 2.5 OPS); require a
    # real sample before the value is trusted for scoring/ranking.
    ok = ab >= 25
    return {"value": val, "g": r["g"], "ab": ab, "ip": None,
            "per_g": round(val / r["g"], 1), "scoreable": ok,
            "line": f"{r['hr']}HR {r['r']}R {r['rbi']}RBI {r['sb']}SB · {avg if avg is not None else '—'}"}


def handoff_outcomes(conn, league_key, season, gap_days=7, window_days=21):
    """Drop->add 'handoffs': one manager drops a player, another adds him within
    `gap_days`. For each, the player's production in the `window_days` BEFORE vs
    AFTER the pickup -- so you can see whether he regressed or took off -- and a
    tally of which managers keep landing players who then produce.

    Production is real MLB game-log output around the date (per game, so short
    windows stay comparable). delta = after - before per-game value: positive
    means the player improved after the handoff (the adder benefited)."""
    import datetime as _d
    txns = behavior.load_txns(conn, league_key)
    mgr = team_managers(conn, league_key)
    gap = gap_days * 86400

    drops, adds_by_pid = [], defaultdict(list)
    for t in txns:
        for mv in t["moves"]:
            pid = mv["player_id"]
            if not pid:
                continue
            if mv["move_type"] == "drop" and mv["source_team"] not in behavior.POOLS:
                drops.append((pid, mv["player_name"], mv["source_team"], t["ts"]))
            if mv["move_type"] == "add" and mv["dest_team"] not in behavior.POOLS:
                adds_by_pid[pid].append((mv["dest_team"], t["ts"]))

    kind_cache = {}

    def kind_of(pid):
        if pid not in kind_cache:
            row = conn.execute("SELECT position FROM players WHERE player_id=?", (pid,)).fetchone()
            kind_cache[pid] = "pit" if (row and row["position"] == "P") else "bat"
        return kind_cache[pid]

    seen, events = set(), []
    for pid, pname, dteam, dts in drops:
        for ateam, ats in adds_by_pid.get(pid, []):
            if ateam == dteam or not (0 <= ats - dts <= gap):
                continue
            key = (pid, dteam, ateam, dts, ats)
            if key in seen:
                continue
            seen.add(key)
            add_date = _d.datetime.fromtimestamp(ats).date()
            kind = kind_of(pid)
            before = _window_prod(conn, pid, kind,
                                  (add_date - _d.timedelta(days=window_days)).isoformat(),
                                  add_date.isoformat())
            after = _window_prod(conn, pid, kind, add_date.isoformat(),
                                 (add_date + _d.timedelta(days=window_days)).isoformat())
            # Δ only when BOTH windows have enough sample to be meaningful.
            scored = bool(before and after and before["scoreable"] and after["scoreable"])
            delta = round(after["per_g"] - before["per_g"], 1) if scored else None
            after_ok = bool(after and after["scoreable"])
            events.append({
                "pid": pid, "player": pname, "kind": kind,
                "dropper": dteam, "dropper_mgr": mgr.get(dteam) or dteam,
                "adder": ateam, "adder_mgr": mgr.get(ateam) or ateam,
                "date": add_date.isoformat(), "gap_h": round((ats - dts) / 3600, 1),
                "before": before, "after": after, "delta": delta,
                "after_ok": after_ok,
                # post-pickup value counts toward tallies only with enough sample
                "after_val": after["value"] if after_ok else None,
            })
    events.sort(key=lambda e: e["date"], reverse=True)

    agg = defaultdict(lambda: {"handoffs": 0, "after_val": 0.0, "deltas": [], "improved": 0})
    for e in events:
        a = agg[e["adder_mgr"]]
        a["handoffs"] += 1
        if e["after_val"] is not None:
            a["after_val"] += e["after_val"]
        if e["delta"] is not None:
            a["deltas"].append(e["delta"])
            a["improved"] += 1 if e["delta"] > 0 else 0
    benef = [{"mgr": m, "handoffs": v["handoffs"], "after_val": round(v["after_val"], 1),
              "avg_delta": round(sum(v["deltas"]) / len(v["deltas"]), 1) if v["deltas"] else None,
              "improved": v["improved"], "scored": len(v["deltas"])}
             for m, v in agg.items()]
    benef.sort(key=lambda x: (x["after_val"]), reverse=True)

    # Flip side — by DROPPER: whose cuts keep producing for someone else.
    dagg = defaultdict(lambda: {"handoffs": 0, "after_val": 0.0, "deltas": [], "blew_up": 0})
    for e in events:
        d = dagg[e["dropper_mgr"]]
        d["handoffs"] += 1
        if e["after_val"] is not None:
            d["after_val"] += e["after_val"]
        if e["delta"] is not None:
            d["deltas"].append(e["delta"])
            d["blew_up"] += 1 if e["delta"] > 0 else 0
    droppers = [{"mgr": m, "handoffs": v["handoffs"], "after_val": round(v["after_val"], 1),
                 "avg_delta": round(sum(v["deltas"]) / len(v["deltas"]), 1) if v["deltas"] else None,
                 "blew_up": v["blew_up"], "scored": len(v["deltas"])}
                for m, v in dagg.items()]
    droppers.sort(key=lambda x: x["after_val"], reverse=True)

    # The specific drops that came back to bite: dropped players who then produced
    # the most for their new team (highest post-pickup value).
    blowups = sorted((e for e in events if e["after_ok"]),
                     key=lambda e: e["after_val"], reverse=True)[:15]

    return {"events": events, "beneficiaries": benef, "droppers": droppers,
            "blowups": blowups,
            "window_days": window_days, "gap_days": gap_days, "n": len(events)}


def my_drops_scorecard(conn, league_key, season, data=None, ok_value=15):
    """Evaluate MY drops only: of the players I cut who were claimed, how many
    turned out to be GOOD decisions (the player did little for the new team) vs
    ones I dropped too early (they produced). `ok_value` = post-pickup value
    at/below which the drop is judged justified (~replacement over the window)."""
    data = data or handoff_outcomes(conn, league_key, season)
    me = conn.execute("SELECT name FROM league_teams WHERE league_key=? AND is_mine=1",
                      (league_key,)).fetchone()
    if not me:
        return None
    mine = me["name"]
    rows, good, bad, pending = [], 0, 0, 0
    for e in data["events"]:
        if e["dropper"] != mine:
            continue
        if not e["after_ok"]:
            verdict = "pending"
            pending += 1
        elif e["after_val"] <= ok_value:
            verdict = "good"
            good += 1
        else:
            verdict = "stung"
            bad += 1
        rows.append({**e, "verdict": verdict})
    scored = good + bad
    return {"team": mine, "rows": rows, "good": good, "bad": bad, "pending": pending,
            "scored": scored, "total": len(rows),
            "good_pct": round(100 * good / scored) if scored else None,
            "ok_value": ok_value, "window_days": data["window_days"]}


def my_adds_scorecard(conn, league_key, season, ok_value=15, window_days=21):
    """Evaluate MY pickups: every player I added (any source — waivers/FA, not
    just handoffs) and how much he produced in the `window_days` after. 'Hit' =
    produced above ~replacement; 'bust' = didn't; 'pending' = too few games yet."""
    import datetime as _d
    txns = behavior.load_txns(conn, league_key)
    me = conn.execute("SELECT name FROM league_teams WHERE league_key=? AND is_mine=1",
                      (league_key,)).fetchone()
    if not me:
        return None
    mine = me["name"]
    kind_cache = {}

    def kind_of(pid):
        if pid not in kind_cache:
            row = conn.execute("SELECT position FROM players WHERE player_id=?", (pid,)).fetchone()
            kind_cache[pid] = "pit" if (row and row["position"] == "P") else "bat"
        return kind_cache[pid]

    seen, adds = set(), []
    for t in txns:
        for mv in t["moves"]:
            if mv["move_type"] == "add" and mv["dest_team"] == mine and mv["player_id"]:
                key = (mv["player_id"], t["ts"])
                if key not in seen:
                    seen.add(key)
                    adds.append((mv["player_id"], mv["player_name"], t["ts"]))

    rows, hits, busts, pending = [], 0, 0, 0
    for pid, pname, ts in adds:
        add_date = _d.datetime.fromtimestamp(ts).date()
        kind = kind_of(pid)
        before = _window_prod(conn, pid, kind,
                              (add_date - _d.timedelta(days=window_days)).isoformat(),
                              add_date.isoformat())
        after = _window_prod(conn, pid, kind, add_date.isoformat(),
                             (add_date + _d.timedelta(days=window_days)).isoformat())
        after_ok = bool(after and after["scoreable"])
        after_val = after["value"] if after_ok else None
        if not after_ok:
            verdict = "pending"
            pending += 1
        elif after_val > ok_value:
            verdict = "hit"
            hits += 1
        else:
            verdict = "bust"
            busts += 1
        rows.append({"pid": pid, "player": pname, "kind": kind, "date": add_date.isoformat(),
                     "before": before, "after": after, "after_ok": after_ok,
                     "after_val": after_val, "verdict": verdict})
    rows.sort(key=lambda r: r["date"], reverse=True)
    scored = hits + busts
    return {"team": mine, "rows": rows, "hits": hits, "busts": busts, "pending": pending,
            "scored": scored, "total": len(rows),
            "hit_pct": round(100 * hits / scored) if scored else None,
            "ok_value": ok_value, "window_days": window_days}


def churn_history(conn, league_key, min_hold_days=3):
    """Players I both added AND dropped (churn) — an auditable hold list and the
    source for 'you've had this guy before' warnings. Per player: how many times
    I added/dropped him, the longest stretch I held him, and the last drop date.
    Every entry is tied to real Yahoo add/drop transactions so it can be checked.

    `min_hold_days` filters out claim-and-cut noise: a player I never held longer
    than that (often a same-day failed/auto claim) isn't real churn.
    """
    import datetime as _d
    me = conn.execute("SELECT name FROM league_teams WHERE league_key=? AND is_mine=1",
                      (league_key,)).fetchone()
    if not me:
        return {"items": [], "by_pid": {}}
    mine = me["name"]
    rows = conn.execute(
        """SELECT tm.player_id, tm.player_name, tm.move_type, tm.source_team,
                  tm.dest_team, t.ts
           FROM transaction_moves tm JOIN transactions t ON t.txn_key = tm.txn_key
           WHERE t.league_key=? AND tm.player_id IS NOT NULL
                 AND (tm.source_team=? OR tm.dest_team=?)
           ORDER BY t.ts""", (league_key, mine, mine)).fetchall()
    by = defaultdict(lambda: {"player": None, "adds": [], "drops": []})
    for r in rows:
        rec = by[r["player_id"]]
        rec["player"] = r["player_name"]
        if r["move_type"] == "add" and r["dest_team"] == mine:
            rec["adds"].append(r["ts"])
        elif r["move_type"] == "drop" and r["source_team"] == mine:
            rec["drops"].append(r["ts"])
    items, quick = [], 0
    for pid, rec in by.items():
        if not (rec["adds"] and rec["drops"]):
            continue
        # held = for each drop, time since the most recent add before it
        held = [(d - max([a for a in rec["adds"] if a <= d], default=d)) / 86400
                for d in rec["drops"]]
        held = [h for h in held if h >= 0]
        max_held = round(max(held)) if held else 0
        if max_held < min_hold_days:   # claim-and-cut, not real churn
            quick += 1
            continue
        items.append({
            "pid": pid, "player": rec["player"],
            "adds": len(rec["adds"]), "drops": len(rec["drops"]),
            "held_days": max_held,
            "last_drop": _d.datetime.fromtimestamp(max(rec["drops"])).date().isoformat(),
        })
    items.sort(key=lambda x: x["last_drop"], reverse=True)
    return {"items": items, "by_pid": {it["pid"]: it for it in items},
            "quick": quick, "min_hold_days": min_hold_days}


def trade_factors(conn, league_key, season):
    """Each trade evaluated on multiple factors, side by side, plus the outcome
    each side has actually banked since the trade date."""
    import datetime as _d
    txns = behavior.load_txns(conn, league_key)
    mgr = team_managers(conn, league_key)
    norm = _pos_norm(conn, season)
    out = []
    for t in txns:
        if t["type"] != "trade":
            continue
        since_date = _d.datetime.fromtimestamp(t["ts"]).date().isoformat()
        sides = {}
        for mv in t["moves"]:
            dest = mv["dest_team"]
            if dest in behavior.POOLS:
                continue
            # Use the stored player_id (matched at ingest with the MLB team, so
            # shared names like "Luis Garcia" resolve to the right player).
            pid = mv["player_id"]
            f = player_factors(conn, pid, season) if pid else {
                "name": mv["player_name"], "team": mv.get("team_abbr"), "kind": None, "thin": True}
            f["since"] = _since_trade(conn, pid, f.get("kind"), since_date) if pid and f.get("kind") else None
            sides.setdefault(dest, []).append(f)
        side_list = []
        for team, players in sides.items():
            side_list.append({
                "team": team, "mgr": mgr.get(team) or team, "players": players,
                "now": round(sum(_tv(p.get("value_now"), p.get("kind"), norm) or 0 for p in players), 1),
                "since": round(sum(_tv(p["since"]["value"], p.get("kind"), norm) if p.get("since") else 0 for p in players), 1),
                "luck_tilt": sum(p.get("luck_dir") or 0 for p in players),
            })
        verdict = None
        if len(side_list) == 2:
            a, b = side_list
            fwd_w = a if a["luck_tilt"] >= b["luck_tilt"] else b
            since_w = a if a["since"] >= b["since"] else b
            verdict = {
                "now": (a if a["now"] >= b["now"] else b)["mgr"],
                "now_gap": round(abs(a["now"] - b["now"]), 1),
                "fwd": fwd_w["mgr"] if a["luck_tilt"] != b["luck_tilt"] else None,
                "since": since_w["mgr"], "since_gap": round(abs(a["since"] - b["since"]), 1),
            }
        out.append({"ts": t["ts"],
                    "since_date": since_date,
                    "when": _d.datetime.fromtimestamp(t["ts"]).strftime("%b %d, %Y"),
                    "sides": side_list, "verdict": verdict})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


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


def h2h_history(conn, league_key):
    """My week-by-week H2H results: opponent, score, and which categories flipped."""
    names = dict(conn.execute(
        "SELECT team_key, name FROM league_teams WHERE league_key=?", (league_key,)).fetchall())
    mgrs = team_managers(conn, league_key)
    cats = {c["stat_id"]: c["display_name"] for c in _scoring_cats(conn, league_key)}
    mine = conn.execute(
        "SELECT week, team_key, opp_team_key FROM matchup_team "
        "WHERE league_key=? AND is_mine=1 ORDER BY week", (league_key,)).fetchall()
    out = []
    for m in mine:
        wk, my_key = m["week"], m["team_key"]
        wins = conn.execute(
            "SELECT stat_id, win FROM matchup_category WHERE league_key=? AND week=? "
            "AND team_key=?", (league_key, wk, my_key)).fetchall()
        won = [cats.get(r["stat_id"]) for r in wins if r["win"] == 1]
        lost = [cats.get(r["stat_id"]) for r in wins if r["win"] == 0]
        opp = names.get(m["opp_team_key"])
        out.append({
            "week": wk, "opp": opp, "opp_mgr": mgrs.get(opp),
            "won": len(won), "lost": len(lost), "tied": sum(1 for r in wins if r["win"] is None),
            "won_cats": [w for w in won if w], "lost_cats": [l for l in lost if l],
            "result": "W" if len(won) > len(lost) else ("L" if len(won) < len(lost) else "T"),
        })
    return out


def opponent_behavior(conn, league_key):
    """How each opponent's transaction activity changes the week they play me."""
    txns = behavior.load_txns(conn, league_key)
    sched = [dict(r) for r in conn.execute(
        "SELECT week, week_start, week_end, opp_name, status FROM my_schedule "
        "WHERE league_key=? ORDER BY week", (league_key,))]
    rows, weeks_with_data = behavior.opponent_vs_me(txns, sched)
    return {"rows": rows, "weeks_with_data": weeks_with_data}


# Scored pitching counting cats (skip ratio cats ERA/WHIP and HR-allowed, which
# don't read as "did they try"), plus raw IP for context. IP (50) is stored in
# Yahoo thirds notation ("31.1" = 31 1/3); the rest are plain numbers.
_VS_COMMISH_CATS = [
    (28, "W"), (42, "K"), (83, "QS"), (90, "SVH"), (50, "IP"),
]


def _commissioner_team_key(conn, league_key):
    """The team we treat as 'Chad' for the vs-commissioner view: the league
    commissioner if flagged, else a team managed by someone named Chad."""
    row = conn.execute(
        "SELECT lt.team_key FROM league_managers lm "
        "JOIN league_teams lt ON lt.league_key=lm.league_key AND lt.team_key=lm.team_key "
        "WHERE lm.league_key=? AND lm.is_commissioner=1 LIMIT 1", (league_key,)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT team_key FROM league_teams WHERE league_key=? AND manager LIKE '%Chad%' "
        "LIMIT 1", (league_key,)).fetchone()
    return row[0] if row else None


def vs_commissioner_pitching(conn, league_key):
    """For every other team, compare its scored pitching cats (W/K/QS/SV+H) and
    innings in the week(s) it faced the commissioner ('Chad') against all its
    other weeks. A drop vs Chad is the 'did they tank the matchup' signal."""
    focus = _commissioner_team_key(conn, league_key)
    teams = {r["team_key"]: dict(r) for r in conn.execute(
        "SELECT team_key, name, manager, is_mine FROM league_teams WHERE league_key=?",
        (league_key,))}
    if not focus or focus not in teams:
        return {"has_focus": False, "cats": [c[1] for c in _VS_COMMISH_CATS], "rows": []}

    opp = {(r["week"], r["team_key"]): r["opp_team_key"] for r in conn.execute(
        "SELECT week, team_key, opp_team_key FROM matchup_team WHERE league_key=?",
        (league_key,))}

    def _num(s, sid):
        if s is None:
            return None
        if sid == 50:  # innings: thirds notation
            w, _, f = str(s).partition(".")
            try:
                return int(w) + (int(f[0]) if f else 0) / 3.0
            except ValueError:
                return None
        try:
            return float(s)
        except ValueError:
            return None

    want = {sid for sid, _ in _VS_COMMISH_CATS}
    val = {}
    for r in conn.execute(
            "SELECT week, team_key, stat_id, value FROM matchup_category "
            "WHERE league_key=? AND stat_id IN (%s)" %
            ",".join("?" * len(want)), (league_key, *want)):
        v = _num(r["value"], r["stat_id"])
        if v is not None:
            val[(r["week"], r["team_key"], r["stat_id"])] = v

    rows = []
    for tk, t in teams.items():
        if tk == focus:
            continue
        weeks = {w for (w, x) in opp if x == tk}
        vs_w = {w for w in weeks if opp.get((w, tk)) == focus}
        ot_w = weeks - vs_w
        if not vs_w:
            continue
        cells = {}
        for sid, label in _VS_COMMISH_CATS:
            vs = [val[(w, tk, sid)] for w in vs_w if (w, tk, sid) in val]
            ot = [val[(w, tk, sid)] for w in ot_w if (w, tk, sid) in val]
            avs = sum(vs) / len(vs) if vs else None
            aot = sum(ot) / len(ot) if ot else None
            diff = (avs - aot) if (avs is not None and aot is not None) else None
            cells[label] = {"vs": avs, "oth": aot, "diff": diff}
        rows.append({"team": t["name"], "mgr": t["manager"], "is_mine": t["is_mine"],
                     "n_vs": len(vs_w), "n_oth": len(ot_w), "cells": cells})
    # Sort by strikeout drop (most negative = biggest tank signal) first.
    rows.sort(key=lambda r: (r["cells"]["K"]["diff"]
                             if r["cells"]["K"]["diff"] is not None else 0))
    return {"has_focus": True, "focus_team": teams[focus]["name"],
            "focus_mgr": teams[focus]["manager"],
            "cats": [c[1] for c in _VS_COMMISH_CATS], "rows": rows}


# Suspected coordination, in team terms (managers shown in the UI):
#   Chad (Forgive Them Judge) ↔ Coop (Okamoto Murakami Sushi Express)
#   Chad (Forgive Them Judge) ↔ Jason (The Straight of Collins)
SUSPECTED_PAIRS = [
    ("Forgive Them Judge", "Okamoto Murakami Sushi Express"),
    ("Forgive Them Judge", "The Straight of Collins"),
]
SUSPECTED_HUB = "Forgive Them Judge"


def team_managers(conn, league_key):
    return {r["name"]: r["manager"] for r in conn.execute(
        "SELECT name, manager FROM league_teams WHERE league_key=?", (league_key,))}


def duplicate_managers(conn, league_key):
    """Manager nicknames that appear on more than one team (same-person flag)."""
    seen = defaultdict(list)
    for r in conn.execute("SELECT name, manager FROM league_teams WHERE league_key=?",
                          (league_key,)):
        if r["manager"]:
            for nick in r["manager"].split(" / "):
                seen[nick.strip()].append(r["name"])
    return {nick: teams for nick, teams in seen.items() if len(teams) > 1}


def co_managers(conn, league_key):
    """Teams in this league with more than one manager attached (co-managed)."""
    names = dict(conn.execute(
        "SELECT team_key, name FROM league_teams WHERE league_key=?", (league_key,)))
    by_team = defaultdict(list)
    for r in conn.execute(
            "SELECT team_key, nickname, guid, is_commissioner, is_comanager "
            "FROM league_managers WHERE league_key=? ORDER BY team_key, is_comanager",
            (league_key,)):
        by_team[r["team_key"]].append(r)
    out = []
    for tk, mgs in by_team.items():
        if len(mgs) > 1:
            out.append({
                "team": names.get(tk, tk),
                "managers": [{"nickname": m["nickname"] or "(hidden)", "guid": m["guid"],
                              "is_commissioner": m["is_commissioner"],
                              "is_comanager": m["is_comanager"]} for m in mgs],
            })
    return out


def _real_guid(g):
    """Yahoo returns the sentinel '--hidden--' for managers with private profiles.
    That is NOT an identity — treat it (and blanks) as 'no guid'."""
    return g if (g and g != "--hidden--") else None


def shared_accounts(conn, league_key):
    """The definitive 'one person, two rosters' check, using Yahoo's stable guid
    rather than fuzzy nicknames. A guid on >1 team *in the same league* is the red
    flag. We also surface cross-league overlaps for context (usually innocent —
    the same friend group in two leagues — but useful to know who's who).

    Many leagues have managers with private profiles, in which case Yahoo masks
    the guid as '--hidden--' and no definitive check is possible (masked=True)."""
    rows = conn.execute(
        "SELECT lm.guid, lm.league_key, lm.team_key, lm.nickname, "
        "       lt.name AS team_name, lme.name AS league_name "
        "FROM league_managers lm "
        "LEFT JOIN league_teams lt ON lt.league_key=lm.league_key AND lt.team_key=lm.team_key "
        "LEFT JOIN league_meta  lme ON lme.league_key=lm.league_key").fetchall()
    by_guid = defaultdict(list)
    for r in rows:
        g = _real_guid(r["guid"])
        if g is not None:
            by_guid[g].append(r)

    same_league, cross_league = [], []
    for guid, recs in by_guid.items():
        teams = {}  # (league_key, team_key) -> entry, de-duped
        for r in recs:
            teams[(r["league_key"], r["team_key"])] = {
                "league_key": r["league_key"], "league": r["league_name"],
                "team": r["team_name"] or r["team_key"], "nickname": r["nickname"] or "(hidden)"}
        entries = list(teams.values())
        nick = next((e["nickname"] for e in entries if e["nickname"] != "(hidden)"),
                    entries[0]["nickname"])
        here = [e for e in entries if e["league_key"] == league_key]
        if len(here) > 1:   # same guid, multiple teams in THIS league — serious
            same_league.append({"guid": guid, "nickname": nick, "teams": here})
        elif len(entries) > 1 and here:  # same person also runs a team elsewhere
            cross_league.append({"guid": guid, "nickname": nick, "teams": entries})
    same_league.sort(key=lambda x: -len(x["teams"]))
    cross_league.sort(key=lambda x: x["nickname"].lower())
    league_rows = [r for r in rows if r["league_key"] == league_key]
    real_in_league = any(_real_guid(r["guid"]) for r in league_rows)
    return {"same_league": same_league, "cross_league": cross_league,
            "has_guids": real_in_league,
            "masked": bool(league_rows) and not real_in_league}


def name_changes(conn, league_key):
    """Teams/managers we've observed under more than one name over time.
    Returns the rename list plus an {current_team_name: [former names]} map for
    inline 'formerly X' tooltips."""
    rows = conn.execute(
        "SELECT kind, entity_id, name, last_seen FROM name_history "
        "WHERE league_key=? ORDER BY first_seen", (league_key,)).fetchall()
    by_ent = defaultdict(list)
    for r in rows:
        by_ent[(r["kind"], r["entity_id"])].append(r)
    team_names = dict(conn.execute(
        "SELECT team_key, name FROM league_teams WHERE league_key=?", (league_key,)))
    guid_nick, guid_team = {}, {}
    for r in conn.execute(
            "SELECT lm.guid, lm.nickname, lt.name AS team FROM league_managers lm "
            "LEFT JOIN league_teams lt ON lt.league_key=lm.league_key "
            "AND lt.team_key=lm.team_key "
            "WHERE lm.league_key=? AND lm.guid IS NOT NULL", (league_key,)):
        if r["nickname"]:
            guid_nick[r["guid"]] = r["nickname"]
        guid_team[r["guid"]] = r["team"]

    changes, aka_by_team = [], {}
    for (kind, eid), recs in by_ent.items():
        if kind == "manager" and _real_guid(eid) is None:
            continue  # masked guid isn't a real identity — don't merge managers
        names = [r["name"] for r in recs]
        if len(names) < 2:
            continue
        if kind == "team":
            current = team_names.get(eid) or max(recs, key=lambda r: r["last_seen"])["name"]
        else:
            current = guid_nick.get(eid) or max(recs, key=lambda r: r["last_seen"])["name"]
        former = [n for n in names if n != current]
        if not former:
            continue
        changes.append({"kind": kind, "current": current, "former": former,
                        "team": current if kind == "team" else guid_team.get(eid)})
        if kind == "team":
            aka_by_team[current] = former
    return {"changes": changes, "aka_by_team": aka_by_team}


# Accounts suspected of being run by one operator (the slip detector watches these).
LINKED_ACCOUNTS = [
    "Forgive Them Judge",            # Chad (commissioner)
    "Okamoto Murakami Sushi Express",  # Coop
    "Sho-guns",                      # Peter
]


def slip_detector(conn, league_key, watched=None):
    import datetime as _d
    watched = watched or LINKED_ACCOUNTS
    txns = behavior.load_txns(conn, league_key)
    flags = behavior.slip_signals(txns, watched)
    mgr = team_managers(conn, league_key)
    for f in flags:
        f["a_mgr"] = mgr.get(f["a"]) or f["a"]
        f["b_mgr"] = mgr.get(f["b"]) or f["b"]
        f["when"] = _d.datetime.fromtimestamp(f["ts"]).strftime("%b %d, %Y %H:%M:%S")
    isolated = [f for f in flags if f["isolated"]]
    return {"flags": flags, "isolated": isolated, "watched": watched,
            "n_isolated": len(isolated)}


def roster_cycling(conn, league_key, window_h=36):
    """Tripwire for quick drop→add hand-offs: A drops a player, the SAME other
    manager adds him within `window_h` hours. Repeated same-pair hits are the
    roster-stashing tell. (Usually empty — waivers make fast hand-offs hard.)"""
    import datetime as _d
    from collections import Counter, defaultdict as _dd
    pools = behavior.POOLS
    rows = conn.execute(
        "SELECT m.player_name pn, t.ts, m.move_type mt, m.source_team src, m.dest_team dst "
        "FROM transactions t JOIN transaction_moves m ON m.txn_key=t.txn_key "
        "WHERE t.league_key=? ORDER BY t.ts", (league_key,)).fetchall()
    ev = _dd(list)
    for r in rows:
        ev[r["pn"]].append(r)
    pair = Counter()
    samples = _dd(list)
    recent = []
    for pn, es in ev.items():
        for i in range(len(es) - 1):
            a, b = es[i], es[i + 1]
            if (a["mt"] == "drop" and b["mt"] == "add" and a["src"] not in pools
                    and b["dst"] not in pools and a["src"] != b["dst"]
                    and 0 <= (b["ts"] - a["ts"]) <= window_h * 3600):
                pair[(a["src"], b["dst"])] += 1
                samples[(a["src"], b["dst"])].append(pn)
                recent.append((b["ts"], a["src"], b["dst"], pn, round((b["ts"] - a["ts"]) / 3600, 1)))
    mgr = team_managers(conn, league_key)
    flags = [{"dropper": mgr.get(a) or a, "adder": mgr.get(b) or b, "count": n,
              "players": samples[(a, b)][:5]}
             for (a, b), n in pair.most_common() if n >= 2]
    recent.sort(reverse=True)
    recent_list = [{"when": _d.datetime.fromtimestamp(ts).strftime("%b %d, %H:%M"),
                    "dropper": mgr.get(a) or a, "adder": mgr.get(b) or b,
                    "player": pn, "hrs": hrs} for ts, a, b, pn, hrs in recent[:6]]
    return {"flags": flags, "total": sum(pair.values()), "window_h": window_h,
            "recent": recent_list}


def collusion_view(conn, league_key, focus=None):
    txns = behavior.load_txns(conn, league_key)
    lookup = match.build_lookup(conn)
    pairs, hubs = behavior.collusion_lens(txns, lambda n: _player_value(conn, lookup, n))

    def find(a, b):
        key = tuple(sorted((a, b)))
        return next((p for p in pairs if tuple(sorted((p["a"], p["b"]))) == key), None)

    focus = focus or SUSPECTED_PAIRS
    focus_cards = [{"a": a, "b": b, "pair": find(a, b)} for a, b in focus]
    hub_pairs = [p for p in pairs if SUSPECTED_HUB in (p["a"], p["b"])]
    def _best_z(p):
        return max(p.get("sig_z") or 0, p.get("timing_z") or 0)
    sig_pairs = sorted([p for p in pairs if _best_z(p) >= 2],
                       key=lambda p: -_best_z(p))
    mgr = team_managers(conn, league_key)
    hub_score = {h["team"]: h["score"] for h in hubs}
    nodes = [{"id": t, "label": (mgr.get(t) or t), "value": hub_score.get(t, 1),
              "hub": (t == SUSPECTED_HUB)} for t in mgr]
    edges = []
    for p in pairs:
        a, b = p["a"], p["b"]
        if p["feed_ab"]:
            edges.append({"from": a, "to": b, "value": p["feed_ab"], "kind": "feed",
                          "title": f"{mgr.get(a)} → {mgr.get(b)}: fed {p['feed_ab']} (×{p['oi_ab']})"})
        if p["feed_ba"]:
            edges.append({"from": b, "to": a, "value": p["feed_ba"], "kind": "feed",
                          "title": f"{mgr.get(b)} → {mgr.get(a)}: fed {p['feed_ba']} (×{p['oi_ba']})"})
        if p["trades"]:
            edges.append({"from": a, "to": b, "value": p["trades"] * 2, "kind": "trade",
                          "title": f"{mgr.get(a)} ↔ {mgr.get(b)}: {p['trades']} trade(s)"})
    return {"pairs": pairs, "hubs": hubs, "focus": focus_cards,
            "hub_name": SUSPECTED_HUB, "hub_pairs": hub_pairs,
            "managers": mgr, "dupes": duplicate_managers(conn, league_key),
            "comanagers": co_managers(conn, league_key),
            "shared": shared_accounts(conn, league_key),
            "renames": name_changes(conn, league_key),
            "slips": slip_detector(conn, league_key),
            "cycling": roster_cycling(conn, league_key),
            "regression": sig_pairs,
            "graph": {"nodes": nodes, "edges": edges}}


# Yahoo category stat_id -> (label, group, SQL expr, higher_is_better, min filter)
CAT_MAP = {
    7:  ("R", "bat", "b.r", True, "b.pa>=50"),
    12: ("HR", "bat", "b.hr", True, "b.pa>=50"),
    13: ("RBI", "bat", "b.rbi", True, "b.pa>=50"),
    3:  ("AVG", "bat", "b.avg", True, "b.pa>=80"),
    55: ("OPS", "bat", "b.ops", True, "b.pa>=80"),
    62: ("Net SB", "bat", "(b.sb-b.cs)", True, "b.pa>=40"),
    21: ("K (bat)", "bat", "b.so", False, "b.pa>=80"),
    28: ("W", "pit", "ps.w", True, "ps.outs>=30"),
    42: ("K", "pit", "ps.so", True, "ps.outs>=30"),
    26: ("ERA", "pit", "ps.era", False, "ps.outs>=60"),
    27: ("WHIP", "pit", "ps.whip", False, "ps.outs>=60"),
    50: ("IP", "pit", "ps.outs", True, "ps.outs>=30"),
    38: ("HR allowed", "pit", "ps.hr", False, "ps.outs>=60"),
    90: ("Net SV+H", "pit", "(ps.sv+ps.hld)", True, "ps.outs>=15"),
}
_RATE_CATS = {"AVG", "OPS", "ERA", "WHIP"}


def _my_team_key(conn, league_key):
    row = conn.execute(
        "SELECT team_key FROM fantasy_roster WHERE is_mine=1 AND team_key LIKE ? LIMIT 1",
        (league_key + ".t.%",)).fetchone()
    return row[0] if row else None


def _player_age(conn, pid, season):
    r = conn.execute("SELECT birth_date FROM players WHERE player_id=?", (pid,)).fetchone()
    if not r or not r["birth_date"]:
        return None
    try:
        return season - int(str(r["birth_date"])[:4])  # approx age during the season
    except (ValueError, TypeError):
        return None


# Per-category value extraction for the "established value" calc. Each entry:
#   (group, is_rate, hi, numerator(row), denominator(row), display_scale)
# The comparable value is a rate = blended numerator / blended denominator
# (per game for counting cats; the stat itself for rate cats). display_scale
# turns that rate back into a familiar number (ERA = rate*27, WHIP = rate*3).
_CAT_EXT = {
    7:  ("bat", False, True,  lambda r: r["r"] or 0,   lambda r: r["g"] or 0, 1),
    12: ("bat", False, True,  lambda r: r["hr"] or 0,  lambda r: r["g"] or 0, 1),
    13: ("bat", False, True,  lambda r: r["rbi"] or 0, lambda r: r["g"] or 0, 1),
    62: ("bat", False, True,  lambda r: (r["sb"] or 0) - (r["cs"] or 0), lambda r: r["g"] or 0, 1),
    21: ("bat", False, False, lambda r: r["so"] or 0,  lambda r: r["g"] or 0, 1),
    3:  ("bat", True,  True,  lambda r: r["h"] or 0,   lambda r: r["ab"] or 0, 1),
    55: ("bat", True,  True,  lambda r: (r["ops"] or 0) * (r["pa"] or 0), lambda r: r["pa"] or 0, 1),
    28: ("pit", False, True,  lambda r: r["w"] or 0,   lambda r: r["g"] or 0, 1),
    42: ("pit", False, True,  lambda r: r["so"] or 0,  lambda r: r["g"] or 0, 1),
    50: ("pit", False, True,  lambda r: r["outs"] or 0, lambda r: r["g"] or 0, 1),
    38: ("pit", False, False, lambda r: r["hr"] or 0,  lambda r: r["g"] or 0, 1),
    90: ("pit", False, True,  lambda r: (r["sv"] or 0) + (r["hld"] or 0), lambda r: r["g"] or 0, 1),
    26: ("pit", True,  False, lambda r: r["er"] or 0,  lambda r: r["outs"] or 0, 27),
    27: ("pit", True,  False, lambda r: (r["h"] or 0) + (r["bb"] or 0), lambda r: r["outs"] or 0, 3),
}


def _blended_cat(conn, pid, sid, season, alpha=0.5):
    """A player's track-record-adjusted value in one category: this season pooled
    with `alpha` of last season (regresses small samples toward the prior, so a
    proven hitter isn't judged on a slow few weeks and a part-timer isn't judged
    on a tiny counting total). Returns a per-game/rate `value` plus this season's
    actual stat for display."""
    grp, is_rate, hi, numf, denf, scale = _CAT_EXT[sid]
    tbl = "batting_season" if grp == "bat" else "pitching_season"
    rows = {r["season"]: r for r in conn.execute(
        f"SELECT * FROM {tbl} WHERE player_id=? AND season IN (?,?)",
        (pid, season, season - 1))}
    r26, r25 = rows.get(season), rows.get(season - 1)
    if not r26 and not r25:
        return None
    num = den = 0.0
    if r26:
        num += numf(r26); den += denf(r26)
    if r25:
        num += alpha * numf(r25); den += alpha * denf(r25)
    if den <= 0:
        return None
    rate = num / den
    cur = None
    if r26 and (denf(r26) or 0) > 0:
        cur = round(numf(r26) / denf(r26) * scale, 3) if is_rate else numf(r26)
    return {"rate": rate, "is_rate": is_rate, "hi": hi,
            "samp26": (r26["pa"] if grp == "bat" else r26["outs"]) if r26 else 0,
            "g26": (r26["g"] if r26 else 0), "cur": cur, "has_prior": r25 is not None}


def _cat_goodness(b, form=None):
    """One number where HIGHER is always better (handles lower-is-better cats),
    nudged slightly by recent form (▲ hot / ▼ cold)."""
    if not b or b["rate"] <= 0:
        return None
    g = b["rate"] if b["hi"] else 1.0 / b["rate"]
    if form == "▲":
        g *= 1.05
    elif form == "▼":
        g *= 0.95
    return g


# Minimum sample to count as an "established regular" who can be the floor —
# keeps a hot part-timer (few PA) from being mislabeled your weakest.
_FLOOR_PA, _FLOOR_OUTS = 150, 90


def upgrade_targets(conn, league_key, season, per_cat=5):
    """Best available players in my weak categories, framed as a REAL upgrade.
    Each is compared to the WEAKEST *established* regular I roster in that
    category — where "value" is this season blended with last (so a slumping vet
    isn't judged on a small sample and a part-timer isn't judged on a low total),
    nudged by recent form. A verdict (upgrade / marginal / worse) replaces a raw
    stat gap; upside chips (age, form, regression) sit alongside. It's a
    per-category baseline, NOT a drop recommendation.
    """
    my_key = _my_team_key(conn, league_key)
    ranks = league_ranks(conn, league_key)
    out = []
    for c in ranks:
        sid = next((s for s, m in CAT_MAP.items() if m[0] == c["label"]), None)
        if sid is None or not c["my_rank"] or c["my_rank"] <= (2 * c["n"] / 3.0):
            continue
        label, group, expr, hi, minf = CAT_MAP[sid]
        alias = "b" if group == "bat" else "ps"
        tbl = "batting_season b" if group == "bat" else "pitching_season ps"
        line = ("b.hr||' HR · '||b.r||' R · '||b.sb||' SB · '||COALESCE(b.avg,'')||' AVG'"
                if group == "bat" else
                "ps.w||'W '||ps.sv||'SV · '||ps.so||'K · '||COALESCE(ps.era,'')||' ERA'")
        pt_floor = f"b.pa>={_FLOOR_PA}" if group == "bat" else f"ps.outs>={_FLOOR_OUTS}"

        # Floor = my weakest ESTABLISHED regular by track-record-adjusted value.
        bench = None
        floor_good = None
        if my_key:
            cands = conn.execute(
                f"""SELECT fr.player_id, p.full_name
                    FROM fantasy_roster fr
                    JOIN players p ON p.player_id = fr.player_id
                    JOIN {tbl} ON {alias}.player_id = fr.player_id AND {alias}.season = ?
                    WHERE fr.team_key = ? AND {pt_floor}""",
                (season, my_key)).fetchall()
            for cd in cands:
                b = _blended_cat(conn, cd["player_id"], sid, season)
                if not b:
                    continue
                pf = player_factors(conn, cd["player_id"], season)
                good = _cat_goodness(b, pf.get("form"))
                if good is None:
                    continue
                if floor_good is None or good < floor_good:
                    floor_good = good
                    bench = {"name": cd["full_name"], "cur": b["cur"],
                             "g": b["g26"], "form": pf.get("form")}

        rows = conn.execute(
            f"""SELECT p.player_id, p.full_name, p.position, t.abbreviation AS team,
                       {expr} AS val, {line} AS line
                FROM availability av
                JOIN players p ON p.player_id = av.player_id
                LEFT JOIN teams t ON t.team_id = p.team_id
                JOIN {tbl} ON {alias}.player_id = p.player_id AND {alias}.season = ?
                WHERE av.league_key = ? AND {minf} AND {expr} IS NOT NULL
                ORDER BY {expr} {'DESC' if hi else 'ASC'} LIMIT ?""",
            (season, league_key, per_cat)).fetchall()

        targets = []
        for r in rows:
            pf = player_factors(conn, r["player_id"], season)
            verdict, is_up = None, None
            if floor_good:
                tb = _blended_cat(conn, r["player_id"], sid, season)
                tg = _cat_goodness(tb, pf.get("form")) if tb else None
                if tg:
                    ratio = tg / floor_good
                    verdict = "up" if ratio >= 1.15 else ("down" if ratio < 0.9 else "marginal")
                    is_up = ratio > 1.0
            targets.append({
                "player_id": r["player_id"], "full_name": r["full_name"],
                "position": r["position"], "team": r["team"],
                "val": r["val"], "line": r["line"], "verdict": verdict, "is_upgrade": is_up,
                "age": _player_age(conn, r["player_id"], season),
                "form": pf.get("form"), "luck_dir": pf.get("luck_dir"),
                "luck_txt": pf.get("luck_txt"), "grade": pf.get("grade"),
                "value_now": pf.get("value_now"),
            })
        out.append({"label": label, "my_rank": c["my_rank"], "n": c["n"],
                    "is_rate": label in _RATE_CATS, "lower_better": not hi,
                    "bench": bench, "targets": targets})
    return out


# --- Hold / drop / upgrade board ------------------------------------------
# For every player I roster: keep him, or would I be better off dropping him for
# the best free agent who plays his position? Each rostered player AND each free
# agent is scored on the SAME category rating the trade tools use (50 = average
# regular, +15 per SD). The bar to clear is the position's REPLACEMENT level —
# the rating of the marginal rostered player at that spot (depth = teams × slots)
# — which is exactly "the best the waiver wire can offer" and bakes in scarcity:
# a scarce spot's replacement is lower, so you hold its players longer. A wire
# option must beat my guy by a BUFFER to trigger a swap (so we don't churn on
# noise); the buffer widens for an unlucky/improving hold — don't sell low — and
# shrinks for a lucky/fading one.
_HD_KEEPER = 50.0    # rating at/above which a player is a clear hold
_HD_BUFFER = 4.0     # rating points a free agent must beat my guy by to upgrade
_HD_LUCK_ADJ = 2.0   # buffer widens (due-up) / shrinks (due-down) by this much
_HD_STASH = {"IL", "IL10", "IL15", "IL60", "NA"}
_HD_PRIO = {"drop": 5, "upgrade": 4, "stash": 3, "unknown": 2, "hold": 1}


def _hd_rating(conn, pid, kind, season, base_map, pos_norm):
    """A player's category rating (50 + 15·z), falling back to the position-
    normalized heuristic when he's below the qualifying sample. None if he can't
    be valued at all."""
    hit = base_map.get(pid)
    if hit:
        return hit[0]
    pf = player_factors(conn, pid, season)
    if pf.get("kind") and not pf.get("thin"):
        return _tv(pf.get("value_now"), pf["kind"], pos_norm)
    return None


def hold_drop_board(conn, league_key, season):
    """Per-roster hold / drop / upgrade verdicts vs the waiver wire. None if no
    synced team. Each of my players is graded against his position's replacement
    level and the best available free agent at that spot."""
    my_key = _my_team_key(conn, league_key) if league_key else None
    if not my_key:
        return None
    dists = _cat_rating_dists(conn, season)
    weights = _cat_need_weights(conn, league_key)
    n_teams = conn.execute("SELECT COUNT(*) FROM league_teams WHERE league_key=?",
                           (league_key,)).fetchone()[0] or 12
    pos_norm = _pos_norm(conn, season)

    # One bulk rating pass per side, reused for my players, FAs, and replacement.
    base_map, repl = {}, {}
    for group, floor in (("bat", 100), ("pit", 60)):
        ratings = _bulk_ratings(conn, season, dists, weights, group, floor)
        base_map.update(ratings)
        by_bucket = defaultdict(list)
        for _pid, (rt, bucket) in ratings.items():
            by_bucket[bucket].append(rt)
        for bucket, rts in by_bucket.items():
            rts.sort(reverse=True)
            depth = max(1, n_teams * _POS_SLOTS.get(bucket, 1))
            repl[bucket] = rts[min(depth, len(rts)) - 1]

    kind_cache = {}

    def kind_of(pid):
        if pid not in kind_cache:
            row = conn.execute("SELECT position FROM players WHERE player_id=?", (pid,)).fetchone()
            kind_cache[pid] = "pit" if (row and row["position"] == "P") else "bat"
        return kind_cache[pid]

    # Best available free agent per position bucket.
    best_fa = {}
    for r in conn.execute(
            "SELECT av.player_id, p.full_name FROM availability av "
            "JOIN players p ON p.player_id = av.player_id "
            "WHERE av.league_key=? AND av.player_id IS NOT NULL", (league_key,)):
        pid, kind = r["player_id"], kind_of(r["player_id"])
        bucket = _player_bucket(conn, pid, kind, season)
        if not bucket:
            continue
        rt = _hd_rating(conn, pid, kind, season, base_map, pos_norm)
        if rt is None:
            continue
        cur = best_fa.get(bucket)
        if cur is None or rt > cur["rating"]:
            best_fa[bucket] = {"pid": pid, "name": r["full_name"], "rating": rt}

    churn = churn_history(conn, league_key)["by_pid"]
    fa_pf = {}   # cache player_factors for the surfaced FAs (one per used bucket)
    rows = []
    for r in conn.execute(
            "SELECT fr.player_id, fr.yahoo_name, fr.selected_position, fr.status, p.full_name "
            "FROM fantasy_roster fr LEFT JOIN players p ON p.player_id = fr.player_id "
            "WHERE fr.team_key=?", (my_key,)):
        pid = r["player_id"]
        name = r["full_name"] or r["yahoo_name"]
        status = (r["status"] or "").upper()
        pos = r["selected_position"]
        if not pid:
            rows.append({"pid": None, "name": name, "pos": pos, "bucket": None,
                         "status": status, "rating": None, "repl": None, "prod": None,
                         "form": None, "luck_dir": 0, "luck_txt": None, "grade": None,
                         "wire": None, "edge": None, "verdict": "unknown",
                         "reason": "not matched to a stats line — can't value", "churn": None})
            continue
        kind = kind_of(pid)
        bucket = _player_bucket(conn, pid, kind, season)
        pf = player_factors(conn, pid, season)
        mine = _hd_rating(conn, pid, kind, season, base_map, pos_norm)
        R = repl.get(bucket)
        wire = best_fa.get(bucket)
        if wire and wire["pid"] == pid:   # he's wrongly listed as available too
            wire = None
        edge = round(wire["rating"] - mine, 1) if (wire and mine is not None) else None

        buf = _HD_BUFFER
        if pf.get("luck_dir", 0) > 0 or pf.get("form") == "▲":
            buf += _HD_LUCK_ADJ
        if pf.get("luck_dir", 0) < 0 or pf.get("form") == "▼":
            buf -= _HD_LUCK_ADJ

        if mine is None:
            verdict, reason = "unknown", "not enough data to value him yet"
        elif status in _HD_STASH:
            verdict, reason = "stash", "injured — stash if you have an IL slot, else a drop candidate"
        elif R is not None and mine < R:
            verdict = "drop"
            reason = f"below replacement at {bucket} ({mine} vs {round(R, 1)})"
        elif mine >= _HD_KEEPER:
            verdict, reason = "hold", "startable asset — clear keeper"
        elif wire and edge is not None and edge >= buf:
            verdict = "upgrade"
            reason = f"{wire['name']} grades {edge:+.0f} higher (clears the {round(buf, 1)} buffer)"
        else:
            verdict = "hold"
            reason = "roster-worthy depth — no wire option clears the buffer"

        wire_out = None
        if wire and verdict in ("upgrade", "drop"):
            wpf = fa_pf.get(wire["pid"])
            if wpf is None:
                wpf = fa_pf[wire["pid"]] = player_factors(conn, wire["pid"], season)
            wire_out = {"pid": wire["pid"], "name": wire["name"], "rating": wire["rating"],
                        "prod": wpf.get("prod"), "grade": wpf.get("grade")}

        rows.append({
            "pid": pid, "name": name, "pos": pos, "bucket": bucket, "status": status,
            "rating": mine, "repl": round(R, 1) if R is not None else None,
            "prod": pf.get("prod"), "form": pf.get("form"), "luck_dir": pf.get("luck_dir", 0),
            "luck_txt": pf.get("luck_txt"), "grade": pf.get("grade"),
            "wire": wire_out, "edge": edge, "verdict": verdict, "reason": reason,
            "churn": churn.get(pid),
        })

    rows.sort(key=lambda x: (_HD_PRIO.get(x["verdict"], 0),
                             x["edge"] if x["edge"] is not None else -99), reverse=True)
    counts = {v: sum(1 for x in rows if x["verdict"] == v)
              for v in ("drop", "upgrade", "stash", "hold", "unknown")}
    fa_loaded = bool(conn.execute(
        "SELECT 1 FROM availability WHERE league_key=? LIMIT 1", (league_key,)).fetchone())
    return {"rows": rows, "counts": counts, "buffer": _HD_BUFFER, "keeper": _HD_KEEPER,
            "n_teams": n_teams, "fa_loaded": fa_loaded}


def buy_sell(conn, season, min_pa=150, limit=12):
    """Expected AVG vs actual AVG: who's been unlucky (buy) vs lucky (sell).

    Statcast xBA is *on contact*, so we deflate it by strikeout rate
    (xBA * (AB-SO)/AB) to get a full-season expected average comparable to the
    real AVG. luck = expected - actual: positive => unlucky => BUY LOW.
    """
    rows = conn.execute(
        """SELECT p.player_id, p.full_name, t.abbreviation AS team,
                  b.avg, s.xwoba, b.pa, b.ops, b.hr, s.barrel_pct,
                  ROUND(s.xba * (b.ab - b.so) * 1.0 / NULLIF(b.ab, 0), 3) AS xavg,
                  ROUND(s.xba * (b.ab - b.so) * 1.0 / NULLIF(b.ab, 0) - b.avg, 3) AS luck
           FROM statcast_batting s
           JOIN batting_season b ON b.player_id = s.player_id AND b.season = s.season
           JOIN players p USING(player_id)
           LEFT JOIN teams t ON t.team_id = p.team_id
           WHERE s.season = ? AND b.pa >= ? AND s.xba IS NOT NULL
                 AND b.avg IS NOT NULL AND b.ab > 0
           ORDER BY luck DESC""",
        (season, min_pa)).fetchall()
    rows = [r for r in rows if r["luck"] is not None]
    buy = rows[:limit]
    sell = sorted(rows, key=lambda r: r["luck"])[:limit]
    points = [{"x": r["xavg"], "y": r["avg"], "n": r["full_name"], "pa": r["pa"]}
              for r in rows]
    return {"points": points, "buy": buy, "sell": sell, "n": len(rows)}


def league_options(conn):
    """All leagues stored, newest season first, for the league switcher."""
    return conn.execute(
        "SELECT league_key, name, current_week FROM league_meta "
        "ORDER BY league_key DESC").fetchall()


def owner_engagement(conn, league_key):
    """Per-manager transaction activity: total moves, last active, days idle."""
    import datetime as _d
    rows = conn.execute(
        """SELECT m.team AS team, COUNT(DISTINCT t.txn_key) AS moves,
                  MAX(t.ts) AS last_ts, MIN(t.ts) AS first_ts
           FROM transactions t
           JOIN (SELECT txn_key, source_team AS team FROM transaction_moves
                 WHERE source_team NOT IN ('waivers','freeagents','commish')
                 UNION ALL
                 SELECT txn_key, dest_team FROM transaction_moves
                 WHERE dest_team NOT IN ('waivers','freeagents','commish')) m
             ON m.txn_key = t.txn_key
           WHERE t.league_key = ? AND m.team IS NOT NULL
           GROUP BY m.team""", (league_key,)).fetchall()
    mgr = team_managers(conn, league_key)
    league_last = max((r["last_ts"] for r in rows), default=0)
    out = []
    for r in rows:
        out.append({
            "team": r["team"], "mgr": mgr.get(r["team"]) or r["team"],
            "moves": r["moves"],
            "last": _d.datetime.fromtimestamp(r["last_ts"]).strftime("%b %d"),
            "idle": round((league_last - r["last_ts"]) / 86400) if league_last else 0,
        })
    out.sort(key=lambda x: x["moves"], reverse=True)
    return out


# ===========================================================================
#  Dashboard — the ADHD-friendly "what do I do this week?" command center.
#  Everything below answers one of: who am I playing, how many games/starts/
#  save-chances do my players have left this week, and what should I act on.
# ===========================================================================

# Counting (volume) categories — the ones more games this week can still move.
# Used to flag matchup categories that are "still in play" vs. locked rate stats.
_COUNTING_CATS = {"R", "HR", "RBI", "SB", "NSB", "W", "K", "QS", "SV", "NSVH", "HLD"}


def dashboard_leagues(conn):
    """Leagues to offer in the dashboard switcher, with whether a roster exists."""
    out = []
    for lk in league_keys(conn):
        meta = conn.execute(
            "SELECT name FROM league_meta WHERE league_key=?", (lk,)).fetchone()
        has_roster = conn.execute(
            "SELECT 1 FROM fantasy_roster WHERE team_key LIKE ? AND is_mine=1 LIMIT 1",
            (lk + ".t.%",)).fetchone() is not None
        out.append({"league_key": lk, "name": meta["name"] if meta else lk,
                    "has_roster": has_roster})
    return out


def default_dashboard_league(conn):
    """The league we land on: prefer one that has a synced roster."""
    row = conn.execute("SELECT team_key FROM fantasy_roster WHERE is_mine=1 LIMIT 1").fetchone()
    if row:
        return row[0].rsplit(".t.", 1)[0]
    ks = league_keys(conn)
    return ks[0] if ks else None


def default_league(conn):
    """Default league for any league-scoped page: prefer the league we have a
    synced roster for (the user's active league), else the newest league."""
    lk = default_dashboard_league(conn)
    if lk:
        return lk
    opts = league_options(conn)
    return opts[0]["league_key"] if opts else None


def league_season(conn, league_key):
    """The stats season a league belongs to, so player lines join correctly.

    A 2025 league and a 2026 league can share a name; using the latest season
    for both would score an old league's trades against this year's stats. We
    infer from the schedule's date range, then the latest transaction, then the
    newest season with data."""
    row = conn.execute(
        "SELECT MAX(week_end) FROM my_schedule WHERE league_key=?", (league_key,)).fetchone()
    if row and row[0]:
        return int(row[0][:4])
    row = conn.execute(
        "SELECT MAX(ts) FROM transactions WHERE league_key=?", (league_key,)).fetchone()
    if row and row[0]:
        return _dt.datetime.fromtimestamp(row[0]).year
    return latest_season(conn)


def league_switcher(conn):
    """League options with their season, so same-named leagues are distinguishable."""
    return [{"league_key": r["league_key"], "name": r["name"],
             "season": league_season(conn, r["league_key"])}
            for r in league_options(conn)]


def current_week(conn, league_key, today):
    """The in-progress fantasy week: the week whose date range contains `today`,
    else the one Yahoo marks 'midevent', else league_meta.current_week.

    The calendar is ground truth and is checked first: a 'midevent' status is a
    point-in-time hint captured at sync, so it goes stale the moment a week rolls
    over and would otherwise pin the dashboard to last week's opponent until the
    next sync."""
    rows = conn.execute(
        "SELECT * FROM my_schedule WHERE league_key=? ORDER BY week", (league_key,)
    ).fetchall()
    if not rows:
        return None
    for r in rows:
        if r["week_start"] <= today <= r["week_end"]:
            return r
    for r in rows:
        if r["status"] == "midevent":
            return r
    cur = conn.execute(
        "SELECT current_week FROM league_meta WHERE league_key=?", (league_key,)
    ).fetchone()
    if cur and cur[0]:
        for r in rows:
            if r["week"] == cur[0]:
                return r
    return rows[-1]


def _team_week_games(conn, season, start, end, today):
    """{team_id: {'total': n, 'left': n}} over [start, end]; 'left' = on/after today."""
    out = {}
    for r in conn.execute(
        "SELECT team_id, game_date FROM team_schedule "
        "WHERE season=? AND game_date BETWEEN ? AND ?", (season, start, end)):
        d = out.setdefault(r["team_id"], {"total": 0, "left": 0})
        d["total"] += 1
        if r["game_date"] >= today:
            d["left"] += 1
    return out


def _roster_team_id(conn, row, ab2id):
    """Best MLB team_id for a roster row: matched player's team, else Yahoo abbr."""
    if row["p_team_id"]:
        return row["p_team_id"]
    return ab2id.get(row["yahoo_team"])


def _is_pitcher_row(row):
    sel, pos = (row["selected_position"] or ""), (row["positions"] or "")
    if sel in ("SP", "RP", "P"):
        return True
    if sel in ("C", "1B", "2B", "3B", "SS", "OF", "CI", "MI", "Util"):
        return False
    if "SP" in pos or "RP" in pos:
        return True
    return False  # default: treat unknown as a hitter


def dashboard(conn, league_key, today):
    """Assemble the whole command-center view for one league as of `today`."""
    season = int(today[:4])
    wk = current_week(conn, league_key, today)
    team_key = _my_team_key(conn, league_key)
    id2ab = {r["team_id"]: r["abbreviation"]
             for r in conn.execute("SELECT team_id, abbreviation FROM teams")}
    ab2id = {v: k for k, v in id2ab.items()}

    out = {
        "week": wk, "today": today, "league_key": league_key,
        "matchup": matchup_view(conn, league_key, wk["week"] if wk else None),
        "pitchers": [], "hitters": [], "actions": [],
        "streamers": [], "closers": [],
        "totals": {"sp_starts_left": 0, "confirmed_left": 0,
                   "save_ceiling": 0, "hitter_games_left": 0},
        "has_roster": team_key is not None,
    }
    if not wk or not team_key:
        return out

    start, end = wk["week_start"], wk["week_end"]
    days_left = max(0, (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(today)).days + 1) \
        if today <= end else 0
    out["days_left"] = days_left
    tg = _team_week_games(conn, season, start, end, today)

    roster = conn.execute(
        """SELECT fr.slot, fr.yahoo_name, fr.selected_position, fr.positions,
                  fr.status, fr.player_id, fr.yahoo_team,
                  p.full_name, p.team_id AS p_team_id,
                  ps.sv, ps.w, ps.gs, ps.so AS p_so, ps.era, ps.whip,
                  bs.avg, bs.hr, bs.r, bs.rbi, bs.sb
           FROM fantasy_roster fr
           LEFT JOIN players p          ON p.player_id = fr.player_id
           LEFT JOIN pitching_season ps ON ps.player_id = fr.player_id AND ps.season=?
           LEFT JOIN batting_season bs  ON bs.player_id = fr.player_id AND bs.season=?
           WHERE fr.team_key=? ORDER BY fr.slot""",
        (season, season, team_key)).fetchall()

    for r in roster:
        sel = r["selected_position"] or ""
        on_il = sel == "IL" or (r["status"] or "").upper().startswith("IL")
        active = sel not in ("BN", "IL", "NA", "")
        team_id = _roster_team_id(conn, r, ab2id)
        games = tg.get(team_id, {"total": 0, "left": 0})
        # An IL player can't play his team's remaining games, so he has no
        # opportunities left regardless of how many games his MLB team has.
        games_left = 0 if on_il else games["left"]
        abbr = id2ab.get(team_id, r["yahoo_team"] or "?")
        name = r["full_name"] or r["yahoo_name"]

        if _is_pitcher_row(r):
            pos = r["positions"] or ""
            is_sp, is_rp = "SP" in pos, "RP" in pos
            role = "SP/RP" if (is_sp and is_rp) else ("SP" if is_sp else ("RP" if is_rp else "P"))
            sv = r["sv"] or 0
            is_closer = is_rp and not is_sp and sv >= 3
            confirmed = []
            est_left = 0
            if is_sp and r["player_id"] and not on_il:
                confirmed = conn.execute(
                    "SELECT game_date, opp, is_home FROM probable_starts "
                    "WHERE player_id=? AND game_date BETWEEN ? AND ? ORDER BY game_date",
                    (r["player_id"], today, end)).fetchall()
                est_week = max(1, round(games["total"] / 5)) if games["total"] else 0
                made = conn.execute(
                    "SELECT COUNT(*) FROM pitching_games WHERE player_id=? AND gs=1 "
                    "AND game_date>=? AND game_date<?",
                    (r["player_id"], start, today)).fetchone()[0]
                est_left = max(len(confirmed), max(0, est_week - made))
            save_left = games["left"] if (is_closer and not on_il) else 0
            p = {
                "name": name, "player_id": r["player_id"], "role": role,
                "team": abbr, "active": active, "on_il": on_il, "il_status": r["status"],
                "games_total": games["total"], "games_left": games_left,
                "est_starts_left": est_left,
                "confirmed": [{"date": c["game_date"], "opp": c["opp"], "home": c["is_home"]}
                              for c in confirmed],
                "is_closer": is_closer, "save_left": save_left, "sv": sv,
                "era": r["era"], "whip": r["whip"], "so": r["p_so"],
            }
            out["pitchers"].append(p)
            if not on_il:
                out["totals"]["sp_starts_left"] += est_left
                out["totals"]["confirmed_left"] += len(confirmed)
                out["totals"]["save_ceiling"] += save_left
        else:
            h = {
                "name": name, "player_id": r["player_id"],
                "pos": (r["positions"] or sel or ""), "team": abbr,
                "active": active, "on_il": on_il, "il_status": r["status"],
                "games_total": games["total"], "games_left": games_left,
                "avg": r["avg"], "hr": r["hr"], "r": r["r"], "rbi": r["rbi"], "sb": r["sb"],
            }
            out["hitters"].append(h)
            if not on_il:
                out["totals"]["hitter_games_left"] += games["left"]

    out["pitchers"].sort(key=lambda p: (p["on_il"], not p["active"], -p["games_left"]))
    out["hitters"].sort(key=lambda h: (h["on_il"], not h["active"], h["games_left"]))

    # --- pickups: available probable starters (next days) + available closers ---
    up_dates = [d for d in streaming_dates(conn) if d >= today][:2]
    if up_dates:
        qm = ",".join("?" * len(up_dates))
        out["streamers"] = conn.execute(
            f"""SELECT ps.name, ps.team, ps.opp, ps.is_home, ps.player_id, ps.game_date,
                       pl.tier, pl.tier_rank, pit.era, pit.whip, pit.so
                FROM probable_starts ps
                JOIN availability av
                     ON av.player_id=ps.player_id AND av.league_key=? AND av.status='A'
                LEFT JOIN pl_streamers pl
                     ON pl.player_id=ps.player_id AND pl.game_date=ps.game_date
                LEFT JOIN pitching_season pit
                     ON pit.player_id=ps.player_id AND pit.season=?
                WHERE ps.game_date IN ({qm})
                ORDER BY pl.tier_rank IS NULL, pl.tier_rank, ps.game_date
                LIMIT 6""",
            (league_key, season, *up_dates)).fetchall()
    out["closers"] = conn.execute(
        """SELECT av.name, av.player_id, p.team_id, ps.sv, ps.era, ps.whip, ps.so
           FROM availability av
           JOIN pitching_season ps ON ps.player_id=av.player_id AND ps.season=?
           LEFT JOIN players p ON p.player_id=av.player_id
           WHERE av.league_key=? AND av.status='A' AND ps.sv>=3
           ORDER BY ps.sv DESC LIMIT 5""", (season, league_key)).fetchall()

    out["next_week"] = _next_week_outlook(conn, league_key, wk, season, roster, ab2id)
    out["actions"] = _dashboard_actions(conn, out, id2ab)
    return out


def _week_outlook(conn, season, start, end, roster, ab2id):
    """How much a roster is set to produce over a full week window: estimated
    SP starts, closer save-chance ceiling, and hitter games."""
    # `today=start` makes 'left' == every game in the window (none played yet).
    tg = _team_week_games(conn, season, start, end, start)
    o = {"sp_starts": 0, "save_ceiling": 0, "hitter_games": 0}
    for r in roster:
        sel = r["selected_position"] or ""
        if sel == "IL" or (r["status"] or "").upper().startswith("IL"):
            continue
        team_id = _roster_team_id(conn, r, ab2id)
        total = tg.get(team_id, {"total": 0})["total"]
        if _is_pitcher_row(r):
            pos = r["positions"] or ""
            if "SP" in pos:
                o["sp_starts"] += max(1, round(total / 5)) if total else 0
            elif "RP" in pos and (r["sv"] or 0) >= 3:
                o["save_ceiling"] += total
        else:
            o["hitter_games"] += total
    return o


def _outlook_roster(conn, team_key, season):
    """Minimal roster rows (just the fields _week_outlook needs) for any team."""
    return conn.execute(
        """SELECT fr.selected_position, fr.positions, fr.status, fr.player_id,
                  fr.yahoo_team, p.team_id AS p_team_id, ps.sv
           FROM fantasy_roster fr
           LEFT JOIN players p          ON p.player_id = fr.player_id
           LEFT JOIN pitching_season ps ON ps.player_id = fr.player_id AND ps.season=?
           WHERE fr.team_key=?""", (season, team_key)).fetchall()


def _next_week_outlook(conn, league_key, wk, season, roster, ab2id):
    """Next week: opponent + my outlook numbers and (if their roster is synced)
    the opponent's, so the two can be compared side by side."""
    nxt = conn.execute(
        "SELECT * FROM my_schedule WHERE league_key=? AND week>? ORDER BY week LIMIT 1",
        (league_key, wk["week"])).fetchone()
    if not nxt:
        return None
    start, end = nxt["week_start"], nxt["week_end"]
    o = _week_outlook(conn, season, start, end, roster, ab2id)
    o.update({"week": nxt["week"], "opp_name": nxt["opp_name"],
              "start": start, "end": end})
    opp_roster = _outlook_roster(conn, nxt["opp_team_key"], season)
    o["opp"] = _week_outlook(conn, season, start, end, opp_roster, ab2id) if opp_roster else None
    return o


def _dashboard_actions(conn, d, id2ab):
    """Ranked, concrete to-do items derived from the assembled dashboard state."""
    acts = []

    # 1. Injured players sitting in an active slot (silently scoring nothing).
    bad_il = [p for p in d["pitchers"] + d["hitters"] if p["active"] and p["on_il"]]
    for p in bad_il:
        acts.append({"urgency": "high", "kind": "Injured in lineup",
                     "text": f"{p['name']} ({p.get('il_status') or 'IL'}) is in your active "
                             f"lineup — move to IL/bench and slot someone who plays."})

    # 2. Active starters with no start left this week = a dead roster spot.
    for p in d["pitchers"]:
        if p["active"] and not p["on_il"] and p["role"] in ("SP", "SP/RP") \
                and p["est_starts_left"] == 0:
            acts.append({"urgency": "mid", "kind": "Idle starter",
                         "text": f"{p['name']} likely has no start left this week — "
                                 f"stream a starting pitcher into that slot."})

    # 3. Matchup categories you're losing that more games can still flip.
    mv = d["matchup"]
    if mv:
        losing = [r["label"] for r in mv["rows"]
                  if r["win"] == 0 and r["label"] in _COUNTING_CATS]
        n_close = len(d["closers"])
        n_str = len(d["streamers"])
        for lab in losing:
            if lab in ("SV", "NSVH", "HLD") and n_close:
                acts.append({"urgency": "mid", "kind": f"Losing {lab}",
                             "text": f"You're losing {lab} — {n_close} closer(s) sit on "
                                     f"waivers (see pickups below)."})
            elif lab in ("W", "QS", "K") and n_str:
                acts.append({"urgency": "mid", "kind": f"Losing {lab}",
                             "text": f"You're losing {lab} — {n_str} available arm(s) start "
                                     f"in the next couple days (see pickups)."})
            else:
                acts.append({"urgency": "low", "kind": f"Losing {lab}",
                             "text": f"You're losing {lab} — still a counting category, "
                                     f"games remain to chip away."})

    # 4. Top streamer available right now.
    if d["streamers"]:
        s = d["streamers"][0]
        tag = f" ({s['tier']})" if s["tier"] else ""
        acts.append({"urgency": "low", "kind": "Best add today",
                     "text": f"{s['name']}{tag} is available and starts "
                             f"{s['game_date'][5:]} {'vs' if s['is_home'] else '@'} {s['opp']}."})

    order = {"high": 0, "mid": 1, "low": 2}
    acts.sort(key=lambda a: order[a["urgency"]])
    return acts


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


# ---------------------------------------------------------------------------
# Trade analyzer — standard 5x5 roto z-score valuation.
#
# A player's fantasy value is the sum of how many standard deviations above /
# below the league pool they are in each scoring category:
#   Hitters:  R, HR, RBI, SB, AVG
#   Pitchers: W, SV, K(SO), ERA, WHIP
# Counting cats (R/HR/RBI/SB, W/SV/SO) are z-scored directly. Rate cats
# (AVG/ERA/WHIP) are first converted to a playing-time-weighted "impact" — so a
# .300 hitter over 600 AB is worth far more than a .300 hitter over 50 AB — then
# z-scored. This is the textbook fantasy-valuation method; the "pool" of
# rosterable players below stands in for league average. Two-way players (Ohtani)
# get both a hitter and a pitcher value, summed. Values are based on realized
# season production in the DB, not projections.
# ---------------------------------------------------------------------------
HITTER_POOL_MIN_PA = 250        # ~ the hitters a standard league would roster
PITCHER_POOL_MIN_OUTS = 90      # 30 IP — low enough to keep closers (saves) in


def _mean_std(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    if n == 0:
        return 0.0, 1.0
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return mean, (math.sqrt(var) or 1.0)


def hitter_pool(conn, season, min_pa: int = HITTER_POOL_MIN_PA) -> dict:
    """Per-category mean/std (and league AVG) over the rosterable hitter pool."""
    rows = conn.execute(
        "SELECT r, hr, rbi, sb, ab, h FROM batting_season "
        "WHERE season = ? AND pa >= ? AND ab > 0",
        (season, min_pa),
    ).fetchall()
    tot_ab = sum(r["ab"] for r in rows) or 1
    lg_avg = sum(r["h"] for r in rows) / tot_ab
    meta = {c: _mean_std([r[c] or 0 for r in rows]) for c in ("r", "hr", "rbi", "sb")}
    # AVG impact = hits above what a league-average hitter makes in those ABs.
    meta["avg"] = _mean_std([(r["h"] or 0) - lg_avg * (r["ab"] or 0) for r in rows])
    meta["lg_avg"], meta["n"] = lg_avg, len(rows)
    return meta


def pitcher_pool(conn, season, min_outs: int = PITCHER_POOL_MIN_OUTS) -> dict:
    """Per-category mean/std (and league ERA/WHIP) over the rosterable arms."""
    rows = conn.execute(
        "SELECT w, sv, so, er, outs, h, bb FROM pitching_season "
        "WHERE season = ? AND outs >= ?",
        (season, min_outs),
    ).fetchall()
    tot_ip = (sum(r["outs"] or 0 for r in rows) or 1) / 3
    lg_era = sum(r["er"] or 0 for r in rows) * 9 / tot_ip
    lg_whip = sum((r["h"] or 0) + (r["bb"] or 0) for r in rows) / tot_ip
    meta = {c: _mean_std([r[c] or 0 for r in rows]) for c in ("w", "sv", "so")}
    # ERA/WHIP impact: runs / baserunners saved vs. a league-average arm over the
    # same innings. Positive = better than league (i.e. lower ERA/WHIP).
    meta["era"] = _mean_std(
        [lg_era * ((r["outs"] or 0) / 3) - 9 * (r["er"] or 0) for r in rows])
    meta["whip"] = _mean_std(
        [lg_whip * ((r["outs"] or 0) / 3) - ((r["h"] or 0) + (r["bb"] or 0)) for r in rows])
    meta["lg_era"], meta["lg_whip"], meta["n"] = lg_era, lg_whip, len(rows)
    return meta


def value_hitter(line, meta: dict) -> dict | None:
    if line is None or not line["ab"]:
        return None
    comps = {c.upper(): ((line[c] or 0) - meta[c][0]) / meta[c][1]
             for c in ("r", "hr", "rbi", "sb")}
    ai = (line["h"] or 0) - meta["lg_avg"] * (line["ab"] or 0)
    comps["AVG"] = (ai - meta["avg"][0]) / meta["avg"][1]
    return {"total": sum(comps.values()), "comps": comps}


def value_pitcher(line, meta: dict) -> dict | None:
    if line is None or not line["outs"]:
        return None
    comps = {c.upper(): ((line[c] or 0) - meta[c][0]) / meta[c][1]
             for c in ("w", "sv", "so")}
    ip = (line["outs"] or 0) / 3
    era_imp = meta["lg_era"] * ip - 9 * (line["er"] or 0)
    whip_imp = meta["lg_whip"] * ip - ((line["h"] or 0) + (line["bb"] or 0))
    comps["ERA"] = (era_imp - meta["era"][0]) / meta["era"][1]
    comps["WHIP"] = (whip_imp - meta["whip"][0]) / meta["whip"][1]
    return {"total": sum(comps.values()), "comps": comps}


def parse_player_names(text: str | None) -> list[str]:
    """Split a free-text side ('Soto, Soriano & Schmitt') into player names."""
    if not text:
        return []
    parts = re.split(r"[\n,]+|\s&\s|\s+and\s+", text)
    return [p.strip() for p in parts if p.strip()]


def evaluate_side(conn, names: list[str], season, hmeta: dict, pmeta: dict) -> dict:
    """Resolve each name to its best match and value it. Returns side summary.

    Uses the shared season-aware search_players() and *_season_row() helpers so
    the free-text trade analyzer values players for the selected season."""
    players, unmatched, total = [], [], 0.0
    for name in names:
        matches = search_players(conn, name, season, limit=6)
        if not matches:
            unmatched.append(name)
            continue
        best = matches[0]
        bat = batting_season_row(conn, best["player_id"], season)
        pit = pitching_season_row(conn, best["player_id"], season)
        hv = value_hitter(bat, hmeta)
        pv = value_pitcher(pit, pmeta)
        val = (hv["total"] if hv else 0.0) + (pv["total"] if pv else 0.0)
        total += val
        players.append({
            "row": best, "query": name, "value": val, "hit": hv, "pit": pit and pv,
            # alternative matches help the user spot a wrong auto-pick
            "alts": [m for m in matches[1:] if m["full_name"] != best["full_name"]],
        })
    return {"players": players, "unmatched": unmatched, "total": total}


def evaluate_text_trade(conn, a_names: list[str], b_names: list[str], season) -> dict:
    """Value both sides of a free-text trade and render a verdict.

    Name-based counterpart to the league-aware evaluate_trade(): takes player
    names + a season (used by the /trade page). Pools are computed once."""
    hmeta, pmeta = hitter_pool(conn, season), pitcher_pool(conn, season)
    side_a = evaluate_side(conn, a_names, season, hmeta, pmeta)
    side_b = evaluate_side(conn, b_names, season, hmeta, pmeta)
    delta = side_a["total"] - side_b["total"]      # + => side A gets more value
    mag = abs(delta)
    if mag < 1.0:
        verdict, winner = "Even trade", None
    elif mag < 3.0:
        verdict, winner = "Slight edge", ("A" if delta > 0 else "B")
    elif mag < 6.0:
        verdict, winner = "Clear edge", ("A" if delta > 0 else "B")
    else:
        verdict, winner = "Lopsided", ("A" if delta > 0 else "B")
    # Balance bar: share of total positive value (only meaningful when both > 0).
    share_a = None
    if side_a["total"] > 0 and side_b["total"] > 0:
        share_a = side_a["total"] / (side_a["total"] + side_b["total"]) * 100
    return {
        "a": side_a, "b": side_b, "delta": delta, "verdict": verdict,
        "winner": winner, "share_a": share_a,
        "pool": {"hitters": hmeta["n"], "pitchers": pmeta["n"],
                 "min_pa": HITTER_POOL_MIN_PA, "min_ip": PITCHER_POOL_MIN_OUTS // 3},
    }


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


# ---------------------------------------------------------------------------
# Category levels: where a player ranks across the roto 5x5 categories,
# expressed as a 0-100 percentile vs. that season's qualified pool.
# Used by the player page (one player) and My Team (whole roster).
# ---------------------------------------------------------------------------

# (db_key, label, lower_is_better, value_format)
BAT_CATS = [
    ("r", "R", False, "{:.0f}"),
    ("hr", "HR", False, "{:.0f}"),
    ("rbi", "RBI", False, "{:.0f}"),
    ("sb", "SB", False, "{:.0f}"),
    ("avg", "AVG", False, "{:.3f}"),
]
PIT_CATS = [
    ("w", "W", False, "{:.0f}"),
    ("sv", "SV", False, "{:.0f}"),
    ("so", "K", False, "{:.0f}"),
    ("era", "ERA", True, "{:.2f}"),
    ("whip", "WHIP", True, "{:.2f}"),
]

# Qualified-pool floors. Both scale to the season — half the leader's volume
# with a sane minimum — so the pool isn't empty mid-season (a completed season
# lands near a normal qualifier; June lands lower), mirroring the /leaders
# rate-stat qualifier. A fixed bar would leave every percentile NULL until
# players accumulate enough playing time.
def _hitter_pa_floor(conn, season):
    top = conn.execute(
        "SELECT MAX(pa) FROM batting_season WHERE season=?", (season,)
    ).fetchone()[0] or 0
    return max(100, round(0.5 * top))


def pitcher_role(g, gs):
    """SP if most appearances are starts, else RP.

    Drives which pool a pitcher is judged against: ranking a closer's W/K
    volume against innings-eating starters reads as 'bad' when he's simply
    in a different role, so starters and relievers get separate pools.
    """
    g, gs = (g or 0), (gs or 0)
    return "SP" if gs >= 0.5 * g and gs >= 1 else "RP"


def _percentile(sorted_vals, v, lower_better):
    """Midrank percentile (0-100) of v within an ascending value list.

    Ties share the middle of their rank band. For 'lower is better' stats
    (ERA/WHIP) the scale is flipped so 100 always means elite.
    """
    n = len(sorted_vals)
    if not n or v is None:
        return None
    below = bisect.bisect_left(sorted_vals, v)
    equal = bisect.bisect_right(sorted_vals, v) - below
    pct = 100.0 * (below + 0.5 * equal) / n
    if lower_better:
        pct = 100.0 - pct
    return round(pct)


def _tier(pct):
    """Map a percentile to a (label, css-class) badge."""
    if pct is None:
        return ("—", "na")
    if pct >= 90:
        return ("Elite", "elite")
    if pct >= 70:
        return ("Above avg", "good")
    if pct >= 45:
        return ("Average", "avg")
    if pct >= 25:
        return ("Below avg", "low")
    return ("Poor", "poor")


def _pool_from_rows(rows, specs):
    """Sorted (ascending) value list per category from a set of rows."""
    return {
        key: sorted(r[key] for r in rows if r[key] is not None)
        for key, *_ in specs
    }


def _batter_pools(conn, season):
    """Per-category value lists for the season's qualified hitters."""
    rows = conn.execute(
        "SELECT r, hr, rbi, sb, avg FROM batting_season WHERE season=? AND pa>=?",
        (season, _hitter_pa_floor(conn, season)),
    ).fetchall()
    return _pool_from_rows(rows, BAT_CATS)


def _pitcher_pools(conn, season):
    """Separate qualified pools for starters and relievers, keyed 'SP'/'RP'.

    Each role's volume floor scales to that role (half the role leader's outs,
    sane minimum) so relievers aren't washed out by starter innings and
    neither pool is empty mid-season.
    """
    rows = conn.execute(
        "SELECT g, gs, w, sv, so, era, whip, outs FROM pitching_season WHERE season=?",
        (season,),
    ).fetchall()
    by_role = {"SP": [], "RP": []}
    for r in rows:
        by_role[pitcher_role(r["g"], r["gs"])].append(r)
    pools = {}
    for role, rrows in by_role.items():
        top = max((r["outs"] or 0) for r in rrows) if rrows else 0
        floor = max(90 if role == "SP" else 30, round(0.5 * top))
        qual = [r for r in rrows if (r["outs"] or 0) >= floor]
        pools[role] = _pool_from_rows(qual, PIT_CATS)
    return pools


def _level_cells(specs, pools, getter):
    """Build the per-category level cells for one player."""
    cells = []
    for key, label, lower_better, fmt in specs:
        v = getter(key)
        pct = _percentile(pools[key], v, lower_better)
        tier, cls = _tier(pct)
        cells.append({
            "key": key,
            "label": label,
            "value": fmt.format(v) if v is not None else "—",
            "pct": pct,
            "tier": tier,
            "cls": cls,
        })
    return cells


def category_levels(conn, player_id, season, kind):
    """One player's roto-category levels (percentile vs the season's pool).

    Pitchers are compared within their role — starter vs reliever.
    """
    row = (batting_season_row(conn, player_id, season) if kind == "bat"
           else pitching_season_row(conn, player_id, season))
    if row is None:
        return None
    if kind == "bat":
        specs, pools = BAT_CATS, _batter_pools(conn, season)
    else:
        specs = PIT_CATS
        pools = _pitcher_pools(conn, season)[pitcher_role(row["g"], row["gs"])]
    keys = row.keys()
    return _level_cells(specs, pools, lambda k: row[k] if k in keys else None)


def roster_levels(conn, hitters, pitchers, season):
    """Category levels for a whole synced roster, pools computed once.

    `hitters`/`pitchers` are the split my_team() rows. Pitcher strikeouts and
    games arrive aliased as `p_so`/`p_g`; everything else matches the pool
    column names. Pitchers are ranked within their role (starter vs reliever).
    """
    bat_pools = _batter_pools(conn, season)
    pit_pools = _pitcher_pools(conn, season)

    def hrow(row):
        keys = row.keys()
        return {
            "name": row["full_name"] or row["yahoo_name"],
            "player_id": row["player_id"],
            "cells": _level_cells(
                BAT_CATS, bat_pools,
                lambda k, _r=row, _k=keys: _r[k] if k in _k else None),
        }

    def prow(row):
        keys = row.keys()
        role = pitcher_role(row["p_g"] if "p_g" in keys else None, row["gs"])

        def getter(k, _r=row, _k=keys):
            col = "p_so" if k == "so" else k
            return _r[col] if col in _k else None

        return {
            "name": row["full_name"] or row["yahoo_name"],
            "player_id": row["player_id"],
            "role": role,
            "cells": _level_cells(PIT_CATS, pit_pools[role], getter),
        }

    return {
        "bat_cats": [c[1] for c in BAT_CATS],
        "pit_cats": [c[1] for c in PIT_CATS],
        "hitters": sorted((hrow(r) for r in hitters if r["b_ab"]),
                          key=lambda p: p["name"] or ""),
        "pitchers": sorted((prow(r) for r in pitchers if r["outs"]),
                           key=lambda p: p["name"] or ""),
    }
