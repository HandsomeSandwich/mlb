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
