"""Read-only query helpers used by the Flask dashboards.

Sorting is done server-side against a whitelist of columns (never raw user
input) so the sort param can't be used for SQL injection.
"""
from __future__ import annotations

import math
import re

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
            min_pa=0, q=None, limit=300):
    where = ["b.pa >= ?"]
    params: list = [min_pa]
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
        LEFT JOIN statcast_batting s ON s.player_id = b.player_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT ?"""
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def pitchers(conn, *, sort="so", direction="desc", team=None,
             min_outs=0, q=None, limit=300):
    where = ["ps.outs >= ?"]
    params: list = [min_outs]
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


def player(conn, player_id: int):
    return conn.execute(
        """SELECT p.*, t.abbreviation AS team, t.name AS team_name
           FROM players p LEFT JOIN teams t ON t.team_id = p.team_id
           WHERE p.player_id = ?""",
        (player_id,),
    ).fetchone()


def batting_season_row(conn, player_id: int):
    return conn.execute("SELECT * FROM batting_season WHERE player_id=?", (player_id,)).fetchone()


def pitching_season_row(conn, player_id: int):
    return conn.execute("SELECT * FROM pitching_season WHERE player_id=?", (player_id,)).fetchone()


def statcast_row(conn, player_id: int):
    return conn.execute("SELECT * FROM statcast_batting WHERE player_id=?", (player_id,)).fetchone()


def batting_log(conn, player_id: int, limit=200):
    return conn.execute(
        """SELECT bg.*, ht.abbreviation AS team_abbr, ot.abbreviation AS opp_abbr
           FROM batting_games bg
           LEFT JOIN teams ht ON ht.team_id = bg.team_id
           LEFT JOIN teams ot ON ot.team_id = bg.opp_team_id
           WHERE bg.player_id=? ORDER BY bg.game_date DESC LIMIT ?""",
        (player_id, limit),
    ).fetchall()


def pitching_log(conn, player_id: int, limit=200):
    return conn.execute(
        """SELECT pg.*, ht.abbreviation AS team_abbr, ot.abbreviation AS opp_abbr
           FROM pitching_games pg
           LEFT JOIN teams ht ON ht.team_id = pg.team_id
           LEFT JOIN teams ot ON ot.team_id = pg.opp_team_id
           WHERE pg.player_id=? ORDER BY pg.game_date DESC LIMIT ?""",
        (player_id, limit),
    ).fetchall()


def teams_list(conn):
    return conn.execute(
        "SELECT abbreviation, name FROM teams ORDER BY abbreviation").fetchall()


def positions_list(conn):
    rows = conn.execute(
        "SELECT DISTINCT position FROM players WHERE position IS NOT NULL "
        "AND position NOT IN ('P') ORDER BY position").fetchall()
    return [r[0] for r in rows]


def leaders(conn, table_join, col, label_extra="", limit=5, asc=False, where="1=1"):
    """Small helper for the home-page leader cards."""
    direction = "ASC" if asc else "DESC"
    sql = f"""
        SELECT p.player_id, p.full_name, x.{col} AS val {label_extra}
        FROM {table_join} x JOIN players p USING(player_id)
        WHERE x.{col} IS NOT NULL AND {where}
        ORDER BY x.{col} {direction} LIMIT ?"""
    return conn.execute(sql, (limit,)).fetchall()


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


def latest_game_date(conn):
    return conn.execute("SELECT MAX(game_date) FROM batting_games").fetchone()[0]


def recent_window(conn, days: int):
    """(start_date, end_date) for the last `days` of data we hold.

    The 2025 season is complete, so 'recent' is relative to the most recent
    game in the DB rather than today's date.
    """
    end = latest_game_date(conn)
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


def hitter_pool(conn, min_pa: int = HITTER_POOL_MIN_PA) -> dict:
    """Per-category mean/std (and league AVG) over the rosterable hitter pool."""
    rows = conn.execute(
        "SELECT r, hr, rbi, sb, ab, h FROM batting_season WHERE pa >= ? AND ab > 0",
        (min_pa,),
    ).fetchall()
    tot_ab = sum(r["ab"] for r in rows) or 1
    lg_avg = sum(r["h"] for r in rows) / tot_ab
    meta = {c: _mean_std([r[c] or 0 for r in rows]) for c in ("r", "hr", "rbi", "sb")}
    # AVG impact = hits above what a league-average hitter makes in those ABs.
    meta["avg"] = _mean_std([(r["h"] or 0) - lg_avg * (r["ab"] or 0) for r in rows])
    meta["lg_avg"], meta["n"] = lg_avg, len(rows)
    return meta


def pitcher_pool(conn, min_outs: int = PITCHER_POOL_MIN_OUTS) -> dict:
    """Per-category mean/std (and league ERA/WHIP) over the rosterable arms."""
    rows = conn.execute(
        "SELECT w, sv, so, er, outs, h, bb FROM pitching_season WHERE outs >= ?",
        (min_outs,),
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


def search_players(conn, q: str, limit: int = 6):
    """Name-search candidates, most-played first (best match leads)."""
    return conn.execute(
        """SELECT p.player_id, p.full_name, p.position, t.abbreviation AS team,
                  COALESCE(bs.pa, 0) AS pa, COALESCE(ps.outs, 0) AS outs
           FROM players p
           LEFT JOIN teams t ON t.team_id = p.team_id
           LEFT JOIN batting_season bs ON bs.player_id = p.player_id
           LEFT JOIN pitching_season ps ON ps.player_id = p.player_id
           WHERE p.full_name LIKE ?
           ORDER BY (COALESCE(bs.pa, 0) + COALESCE(ps.outs, 0)) DESC, p.full_name
           LIMIT ?""",
        (f"%{q.strip()}%", limit),
    ).fetchall()


def evaluate_side(conn, names: list[str], hmeta: dict, pmeta: dict) -> dict:
    """Resolve each name to its best match and value it. Returns side summary."""
    players, unmatched, total = [], [], 0.0
    for name in names:
        matches = search_players(conn, name)
        if not matches:
            unmatched.append(name)
            continue
        best = matches[0]
        bat = batting_season_row(conn, best["player_id"])
        pit = pitching_season_row(conn, best["player_id"])
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


def evaluate_trade(conn, a_names: list[str], b_names: list[str]) -> dict:
    """Value both sides and render a verdict. The pools are computed once."""
    hmeta, pmeta = hitter_pool(conn), pitcher_pool(conn)
    side_a = evaluate_side(conn, a_names, hmeta, pmeta)
    side_b = evaluate_side(conn, b_names, hmeta, pmeta)
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


def recent_hitters(conn, *, days=15, min_pa=20, sort="ops", direction="desc", limit=300):
    """Hot/cold hitters over the last `days` of the season.

    Rate stats (AVG/OBP/SLG/OPS) are computed over the window; `delta` is the
    window OPS minus the player's full-season OPS — positive = heating up.
    """
    start, _ = recent_window(conn, days)
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
        LEFT JOIN batting_season bs ON bs.player_id = bg.player_id
        WHERE bg.game_date >= ?
        GROUP BY bg.player_id
        HAVING SUM(bg.pa) >= ?
        ORDER BY {order} {dir_sql} NULLS LAST, p.full_name ASC
        LIMIT ?"""
    return conn.execute(sql, (start, min_pa, limit)).fetchall()
