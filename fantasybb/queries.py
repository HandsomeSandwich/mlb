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


FIP_CONST = 3.15


def player_factors(conn, pid, season):
    """Multi-factor outlook for one player: production now, luck, skill, form."""
    row = conn.execute("SELECT full_name, position FROM players WHERE player_id=?",
                       (pid,)).fetchone()
    if not row:
        return {"name": "?", "kind": None}
    name = row["full_name"]
    is_pit = row["position"] == "P"

    if is_pit:
        ps = conn.execute("SELECT * FROM pitching_season WHERE player_id=? AND season=?",
                          (pid, season)).fetchone()
        if not ps or not ps["outs"]:
            return {"name": name, "kind": "pit", "thin": True}
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
        return {"name": name, "pid": pid, "kind": "pit", "value_now": value_now,
                "prod": f"{ps['w']}W {ps['sv']}SV · {ps['so']}K · {era} ERA · {ps['whip']} WHIP",
                "skill": f"FIP {fip} · K/BB {ps['kbb'] or '—'}", "fip": fip,
                "luck": luck, "luck_dir": _luck_dir(luck, 0.4),
                "luck_txt": _pit_luck_txt(luck), "grade": grade, "form": form}

    b = conn.execute("SELECT * FROM batting_season WHERE player_id=? AND season=?",
                     (pid, season)).fetchone()
    if not b or not b["ab"]:
        return {"name": name, "kind": "bat", "thin": True}
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
    return {"name": name, "pid": pid, "kind": "bat", "value_now": value_now,
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


def trade_factors(conn, league_key, season):
    """Each trade evaluated on multiple factors, side by side, plus the outcome
    each side has actually banked since the trade date."""
    import datetime as _d
    txns = behavior.load_txns(conn, league_key)
    lookup = match.build_lookup(conn)
    mgr = team_managers(conn, league_key)
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
            pid = match.match(lookup, mv["player_name"] or "")
            f = player_factors(conn, pid, season) if pid else {"name": mv["player_name"], "kind": None, "thin": True}
            f["since"] = _since_trade(conn, pid, f.get("kind"), since_date) if pid and f.get("kind") else None
            sides.setdefault(dest, []).append(f)
        side_list = []
        for team, players in sides.items():
            side_list.append({
                "team": team, "mgr": mgr.get(team) or team, "players": players,
                "now": round(sum(p.get("value_now") or 0 for p in players), 1),
                "since": round(sum((p["since"]["value"] if p.get("since") else 0) for p in players), 1),
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


def category_targets(conn, league_key, season, per_cat=6):
    """For each category I'm weak in, the best AVAILABLE players to target."""
    ranks = league_ranks(conn, league_key)
    out = []
    for c in ranks:
        sid = next((s for s, m in CAT_MAP.items() if m[0] == c["label"]), None)
        # only surface categories I'm in the bottom third of, and that we can map
        if sid is None or not c["my_rank"] or c["my_rank"] <= (2 * c["n"] / 3.0):
            continue
        label, group, expr, hi, minf = CAT_MAP[sid]
        tbl = "batting_season b" if group == "bat" else "pitching_season ps"
        order = "DESC" if hi else "ASC"
        line = ("b.hr||' HR · '||b.r||' R · '||b.sb||' SB · '||COALESCE(b.avg,'')||' AVG'"
                if group == "bat" else
                "ps.w||'W '||ps.sv||'SV · '||ps.so||'K · '||COALESCE(ps.era,'')||' ERA'")
        rows = conn.execute(
            f"""SELECT p.player_id, p.full_name, p.position, t.abbreviation AS team,
                       {expr} AS val, {line} AS line
                FROM availability av
                JOIN players p ON p.player_id = av.player_id
                LEFT JOIN teams t ON t.team_id = p.team_id
                JOIN {tbl} ON {'b' if group=='bat' else 'ps'}.player_id = p.player_id
                     AND {'b' if group=='bat' else 'ps'}.season = ?
                WHERE av.league_key = ? AND {minf} AND {expr} IS NOT NULL
                ORDER BY {expr} {order} LIMIT ?""",
            (season, league_key, per_cat)).fetchall()
        out.append({"label": label, "my_rank": c["my_rank"], "n": c["n"],
                    "is_rate": label in _RATE_CATS, "lower_better": not hi,
                    "targets": rows})
    return out


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
