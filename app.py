"""Flask dashboards for the fantasy-baseball database.

    python app.py            # http://127.0.0.1:5000

Read-only: the app only queries the SQLite DB built by `fantasybb.ingest`.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Flask, Response, g, render_template, request

from fantasybb import db, queries as Q

app = Flask(__name__)

# Optional password gate. When APP_PASSWORD is set (e.g. before exposing the app
# through a tunnel), every request requires HTTP basic auth. Left unset for
# frictionless local use.
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _require_auth():
    if not APP_PASSWORD:
        return  # no password configured -> open (local only)
    auth = request.authorization
    if not auth or auth.username != APP_USER or auth.password != APP_PASSWORD:
        return Response(
            "Authentication required.", 401,
            {"WWW-Authenticate": 'Basic realm="DiamondDB"'})


def _sort_link(base_args: dict, col: str, new_dir: str) -> str:
    """Build a leaderboard URL: current filters + the new sort column/dir."""
    d = {k: v for k, v in base_args.items() if v not in (None, "", 0)}
    d["sort"], d["dir"] = col, new_dir
    return "?" + urlencode(d)


# Registered as a Jinja global (not a context processor) so it is visible
# inside the imported `th` macro, which runs outside the request render context.
app.jinja_env.globals["sort_link"] = _sort_link


def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def selected_season(conn, allowed=None):
    """The season chosen via ?season=, falling back to the latest available."""
    allowed = allowed if allowed is not None else Q.seasons(conn)
    s = request.args.get("season", type=int)
    return s if s in allowed else (allowed[0] if allowed else None)


@app.context_processor
def inject_helpers():
    conn = get_db()
    seasons = Q.seasons(conn)
    return {
        "ip_str": Q.ip_str,
        "status": Q.db_status(get_db()),
        "seasons": seasons,
        "season": selected_season(conn, seasons),
    }


@app.route("/")
def index():
    conn = get_db()
    yr = selected_season(conn)
    ctx = {
        "hr_leaders": Q.leaders(conn, "batting_season", "hr", yr),
        "rbi_leaders": Q.leaders(conn, "batting_season", "rbi", yr),
        "sb_leaders": Q.leaders(conn, "batting_season", "sb", yr),
        "avg_leaders": Q.leaders(conn, "batting_season", "avg", yr, where="pa >= 300"),
        "win_leaders": Q.leaders(conn, "pitching_season", "w", yr),
        "sv_leaders": Q.leaders(conn, "pitching_season", "sv", yr),
        "k_leaders": Q.leaders(conn, "pitching_season", "so", yr),
        "era_leaders": Q.leaders(conn, "pitching_season", "era", yr, asc=True, where="outs >= 450"),
    }
    return render_template("index.html", **ctx)


@app.errorhandler(404)
def page_not_found(_e):
    return render_template("not_found.html"), 404


@app.route("/search")
def search():
    conn = get_db()
    yr = selected_season(conn)
    q = request.args.get("q") or None
    rows = Q.search_players(conn, q, yr) if q else []
    return render_template("search.html", q=q, rows=rows)


def _limit(default=50):
    """Row-count selector; 'all' (or a big number) lifts the cap."""
    raw = request.args.get("limit")
    if raw in ("all", "0"):
        return 5000
    try:
        return min(int(raw), 5000)
    except (TypeError, ValueError):
        return default


@app.route("/hitters")
def hitters():
    conn = get_db()
    yr = selected_season(conn)
    sort = request.args.get("sort", "hr")
    direction = request.args.get("dir", "desc")
    team = request.args.get("team") or None
    pos = request.args.get("pos") or None
    q = request.args.get("q") or None
    min_pa = request.args.get("min_pa", type=int) or 0
    limit = _limit()
    rows = Q.hitters(conn, sort=sort, direction=direction, team=team, pos=pos,
                     min_pa=min_pa, q=q, season=yr, limit=limit)
    return render_template(
        "hitters.html", rows=rows, sort=sort, dir=direction, team=team, pos=pos,
        q=q, min_pa=min_pa, limit=limit, teams=Q.teams_list(conn),
        positions=Q.positions_list(conn),
    )


@app.route("/pitchers")
def pitchers():
    conn = get_db()
    yr = selected_season(conn)
    sort = request.args.get("sort", "so")
    direction = request.args.get("dir", "desc")
    team = request.args.get("team") or None
    q = request.args.get("q") or None
    min_ip = request.args.get("min_ip", type=int) or 0
    limit = _limit()
    rows = Q.pitchers(conn, sort=sort, direction=direction, team=team,
                      min_outs=min_ip * 3, q=q, season=yr, limit=limit)
    return render_template(
        "pitchers.html", rows=rows, sort=sort, dir=direction, team=team, q=q,
        min_ip=min_ip, limit=limit, teams=Q.teams_list(conn),
    )


@app.route("/trends")
def trends():
    conn = get_db()
    yr = selected_season(conn)
    sort = request.args.get("sort", "ops")
    direction = request.args.get("dir", "desc")
    days = request.args.get("days", type=int) or 15
    min_pa = request.args.get("min_pa", type=int)
    if min_pa is None:
        min_pa = 20
    rows = Q.recent_hitters(conn, days=days, min_pa=min_pa, sort=sort,
                            direction=direction, season=yr)
    start, end = Q.recent_window(conn, days, yr)
    return render_template(
        "trends.html", rows=rows, sort=sort, dir=direction, days=days,
        min_pa=min_pa, start=start, end=end,
    )


def _is_pitcher(row) -> bool:
    sel = (row["selected_position"] or "")
    pos = (row["positions"] or "")
    if sel in ("SP", "RP", "P"):
        return True
    if sel in ("C", "1B", "2B", "3B", "SS", "OF", "CI", "MI", "Util"):
        return False
    # bench/IL slots: fall back to eligibility, then to which stat line exists
    if "SP" in pos or "RP" in pos:
        return True
    if pos:
        return False
    return row["outs"] is not None and row["b_pa"] is None


@app.route("/myteam")
def myteam():
    conn = get_db()
    teams = Q.my_teams(conn)
    if not teams:
        return render_template("myteam.html", teams=[], rows=None)
    team_key = request.args.get("team_key") or teams[0]["team_key"]
    yr = selected_season(conn)
    roster = Q.my_team(conn, team_key, yr)

    hitters_, pitchers_ = [], []
    for r in roster:
        (pitchers_ if _is_pitcher(r) else hitters_).append(r)

    # 5x5 category roll-ups over matched players who actually have a line
    def s(rows, key):
        return sum((row[key] or 0) for row in rows)
    h_with = [r for r in hitters_ if r["b_ab"]]
    p_with = [r for r in pitchers_ if r["outs"]]
    ab, h = s(h_with, "b_ab"), s(h_with, "b_h")
    outs, er = s(p_with, "outs"), s(p_with, "p_er")
    walks_hits = s(p_with, "p_h") + s(p_with, "p_bb")
    cats = {
        "r": s(h_with, "r"), "hr": s(h_with, "hr"), "rbi": s(h_with, "rbi"),
        "sb": s(h_with, "sb"), "avg": (h / ab) if ab else None,
        "w": s(p_with, "w"), "sv": s(p_with, "sv"), "so": s(p_with, "p_so"),
        "era": (27 * er / outs) if outs else None,
        "whip": (walks_hits / (outs / 3)) if outs else None,
        "ip": outs / 3 if outs else 0,
    }
    team_name = next((t["team_name"] for t in teams if t["team_key"] == team_key), team_key)
    unmatched = [r["yahoo_name"] for r in roster if r["player_id"] is None]
    return render_template(
        "myteam.html", teams=teams, team_key=team_key, team_name=team_name,
        hitters=hitters_, pitchers=pitchers_, cats=cats, unmatched=unmatched,
    )


@app.route("/streaming")
def streaming():
    conn = get_db()
    yr = selected_season(conn)
    dates = Q.streaming_dates(conn)
    if not dates:
        return render_template("streaming.html", dates=[], rows=None)
    date = request.args.get("date") or dates[0]
    league = Q.streaming_league(conn)
    rows = Q.streaming_board(conn, date, yr, league)
    mine = [r for r in rows if r["avail"] == "mine"]
    available = [r for r in rows if r["avail"] == "available"]
    return render_template(
        "streaming.html", dates=dates, date=date, rows=rows,
        mine=mine, available=available,
    )


@app.route("/matchup")
def matchup():
    conn = get_db()
    leagues = Q.league_keys(conn)
    if not leagues:
        return render_template("matchup.html", leagues=[], meta=None)
    league_key = request.args.get("league") or leagues[0]
    meta, mine = Q.league_info(conn, league_key)
    return render_template(
        "matchup.html", leagues=leagues, league_key=league_key, meta=meta, mine=mine,
        mv=Q.matchup_view(conn, league_key), ranks=Q.league_ranks(conn, league_key),
        h2h=Q.h2h_history(conn, league_key), managers=Q.team_managers(conn, league_key),
    )


@app.route("/transactions")
def transactions():
    conn = get_db()
    leagues = Q.league_keys(conn)
    if not leagues:
        return render_template("transactions.html", leagues=[], a=None, meta=None)
    league_key = request.args.get("league") or leagues[0]
    meta, _ = Q.league_info(conn, league_key)
    return render_template(
        "transactions.html", leagues=leagues, league_key=league_key, meta=meta,
        a=Q.transactions_analysis(conn, league_key),
        opp=Q.opponent_behavior(conn, league_key),
    )


@app.route("/collusion")
def collusion():
    conn = get_db()
    leagues = Q.league_keys(conn)
    if not leagues:
        return render_template("collusion.html", leagues=[], cv=None, meta=None)
    league_key = request.args.get("league") or leagues[0]
    focus = None
    a, b = request.args.get("a"), request.args.get("b")
    if a and b:
        focus = [(a, b)]
    meta, _ = Q.league_info(conn, league_key)
    return render_template(
        "collusion.html", leagues=leagues, league_key=league_key, meta=meta,
        cv=Q.collusion_view(conn, league_key, focus),
    )


@app.route("/weekly")
def weekly():
    conn = get_db()
    roster = Q.roster_players(conn)
    q = request.args.get("q") or None
    pid = request.args.get("player_id", type=int)
    if not pid and q:
        row = conn.execute(
            "SELECT player_id FROM players WHERE full_name LIKE ? ORDER BY full_name LIMIT 1",
            (f"%{q}%",)).fetchone()
        pid = row[0] if row else None
    if not pid and roster:
        pid = roster[0]["player_id"]

    p = Q.player(conn, pid) if pid else None
    kind = "bat"
    if p:
        has_bat = conn.execute(
            "SELECT 1 FROM batting_games WHERE player_id=? LIMIT 1", (pid,)).fetchone()
        if p["position"] == "P" or not has_bat:
            kind = "pit"
    data = Q.weekly_splits(conn, pid, kind) if pid else {}
    seasons = sorted(data.keys())
    week_nums = sorted({w["week"] for ws in data.values() for w in ws})
    idx = {(s, w["week"]): w for s, ws in data.items() for w in ws}
    rows = [{"week": wn, "cells": {s: idx.get((s, wn)) for s in seasons}} for wn in week_nums]
    return render_template(
        "weekly.html", roster=roster, p=p, pid=pid, kind=kind,
        seasons=seasons, rows=rows, q=q,
    )


@app.route("/player/<int:player_id>")
def player(player_id):
    conn = get_db()
    p = Q.player(conn, player_id)
    if p is None:
        return render_template("not_found.html"), 404
    pseasons = Q.player_seasons(conn, player_id)
    yr = selected_season(conn, pseasons) or selected_season(conn)
    return render_template(
        "player.html",
        p=p,
        player_season=yr,
        player_seasons=pseasons,
        bat=Q.batting_season_row(conn, player_id, yr),
        pit=Q.pitching_season_row(conn, player_id, yr),
        statcast=Q.statcast_row(conn, player_id, yr),
        bat_log=Q.batting_log(conn, player_id, yr),
        pit_log=Q.pitching_log(conn, player_id, yr),
    )


if __name__ == "__main__":
    # debug stays OFF unless explicitly asked for -- never expose the debugger
    # through a tunnel. Use serve.sh (waitress) for the tunnel/production path.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
