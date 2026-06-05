"""Read-only query helpers used by the Flask dashboards.

Sorting is done server-side against a whitelist of columns (never raw user
input) so the sort param can't be used for SQL injection.
"""
from __future__ import annotations

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
