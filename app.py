"""Flask dashboards for the fantasy-baseball database.

    python app.py            # http://127.0.0.1:5000

Read-only: the app only queries the SQLite DB built by `fantasybb.ingest`.
"""
from __future__ import annotations

from urllib.parse import urlencode

from flask import Flask, g, render_template, request

from fantasybb import db, queries as Q

app = Flask(__name__)


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


@app.context_processor
def inject_helpers():
    # available in every template
    return {"ip_str": Q.ip_str, "status": Q.db_status(get_db())}


@app.route("/")
def index():
    conn = get_db()
    ctx = {
        "hr_leaders": Q.leaders(conn, "batting_season", "hr"),
        "rbi_leaders": Q.leaders(conn, "batting_season", "rbi"),
        "sb_leaders": Q.leaders(conn, "batting_season", "sb"),
        "avg_leaders": Q.leaders(conn, "batting_season", "avg", where="pa >= 300"),
        "win_leaders": Q.leaders(conn, "pitching_season", "w"),
        "sv_leaders": Q.leaders(conn, "pitching_season", "sv"),
        "k_leaders": Q.leaders(conn, "pitching_season", "so"),
        "era_leaders": Q.leaders(conn, "pitching_season", "era", asc=True, where="outs >= 450"),
    }
    return render_template("index.html", **ctx)


@app.route("/hitters")
def hitters():
    conn = get_db()
    sort = request.args.get("sort", "hr")
    direction = request.args.get("dir", "desc")
    team = request.args.get("team") or None
    pos = request.args.get("pos") or None
    q = request.args.get("q") or None
    min_pa = request.args.get("min_pa", type=int) or 0
    rows = Q.hitters(conn, sort=sort, direction=direction, team=team, pos=pos,
                     min_pa=min_pa, q=q)
    return render_template(
        "hitters.html", rows=rows, sort=sort, dir=direction, team=team, pos=pos,
        q=q, min_pa=min_pa, teams=Q.teams_list(conn), positions=Q.positions_list(conn),
    )


@app.route("/pitchers")
def pitchers():
    conn = get_db()
    sort = request.args.get("sort", "so")
    direction = request.args.get("dir", "desc")
    team = request.args.get("team") or None
    q = request.args.get("q") or None
    min_ip = request.args.get("min_ip", type=int) or 0
    rows = Q.pitchers(conn, sort=sort, direction=direction, team=team,
                      min_outs=min_ip * 3, q=q)
    return render_template(
        "pitchers.html", rows=rows, sort=sort, dir=direction, team=team, q=q,
        min_ip=min_ip, teams=Q.teams_list(conn),
    )


@app.route("/trends")
def trends():
    conn = get_db()
    sort = request.args.get("sort", "ops")
    direction = request.args.get("dir", "desc")
    days = request.args.get("days", type=int) or 15
    min_pa = request.args.get("min_pa", type=int)
    if min_pa is None:
        min_pa = 20
    rows = Q.recent_hitters(conn, days=days, min_pa=min_pa, sort=sort, direction=direction)
    start, end = Q.recent_window(conn, days)
    return render_template(
        "trends.html", rows=rows, sort=sort, dir=direction, days=days,
        min_pa=min_pa, start=start, end=end,
    )


@app.route("/trade")
def trade():
    conn = get_db()
    a_raw = request.args.get("a", "")
    b_raw = request.args.get("b", "")
    a_names = Q.parse_player_names(a_raw)
    b_names = Q.parse_player_names(b_raw)
    result = None
    if a_names or b_names:
        result = Q.evaluate_trade(conn, a_names, b_names)
    return render_template(
        "trade.html", a_raw=a_raw, b_raw=b_raw, result=result,
    )


@app.route("/player/<int:player_id>")
def player(player_id):
    conn = get_db()
    p = Q.player(conn, player_id)
    if p is None:
        return render_template("not_found.html"), 404
    return render_template(
        "player.html",
        p=p,
        bat=Q.batting_season_row(conn, player_id),
        pit=Q.pitching_season_row(conn, player_id),
        statcast=Q.statcast_row(conn, player_id),
        bat_log=Q.batting_log(conn, player_id),
        pit_log=Q.pitching_log(conn, player_id),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
