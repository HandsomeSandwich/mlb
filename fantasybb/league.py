"""Fetch + store Yahoo league data: settings, standings, matchups, transactions.

    python -m fantasybb.league refresh                # uses the synced team's league
    python -m fantasybb.league refresh 469.l.9254     # explicit league key

Powers the Matchup/League-comparison and Transactions-analysis pages.
"""
from __future__ import annotations

import sys

from . import db, match
from .yahoo import _coll, _flatten, api_get, load_cfg

# Yahoo stat_ids that belong to pitching (used if a stat lacks position_type).
PITCHING_STAT_IDS = {
    50, 28, 29, 32, 42, 26, 27, 83, 89, 90, 38, 39, 48, 1003, 1004, 19, 24,
}


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _league_parts(data: dict):
    return data["fantasy_content"]["league"]


def _find(parts, key):
    for p in parts:
        if isinstance(p, dict) and key in p:
            return p[key]
    return None


def _team_meta(team_node) -> dict:
    """team_node[0] is a list of single-key dicts; merge to one dict."""
    return _flatten(team_node[0])


def _team_stat_map(team_node) -> dict:
    stats = None
    for part in team_node[1:]:
        if isinstance(part, dict) and "team_stats" in part:
            stats = part["team_stats"]["stats"]
    out = {}
    for s in (stats or []):
        st = s["stat"]
        out[int(st["stat_id"])] = st.get("value")
    return out


def _team_points(team_node):
    for part in team_node[1:]:
        if isinstance(part, dict) and "team_points" in part:
            return part["team_points"].get("total")
    return None


def _team_standings(team_node):
    for part in team_node[1:]:
        if isinstance(part, dict) and "team_standings" in part:
            return part["team_standings"]
    return None


# --------------------------------------------------------------------------- #
# fetchers
# --------------------------------------------------------------------------- #
def fetch_settings(lk, cfg):
    parts = _league_parts(api_get(f"league/{lk}/settings", cfg))
    meta = parts[0]
    settings = _find(parts, "settings")
    settings = settings[0] if isinstance(settings, list) else settings
    cats = []
    for i, s in enumerate(settings.get("stat_categories", {}).get("stats", [])):
        st = s["stat"]
        if st.get("is_only_display_stat") == "1":
            continue
        sid = int(st["stat_id"])
        pt = st.get("position_type")
        is_pit = 1 if (pt == "P" or (pt is None and sid in PITCHING_STAT_IDS)) else 0
        cats.append({
            "stat_id": sid, "display_name": st.get("display_name"),
            "name": st.get("name"),
            "sort_order": int(st.get("sort_order", 1)),
            "is_pitching": is_pit, "ord": i,
        })
    return meta, cats


def fetch_standings(lk, cfg, my_keys):
    parts = _league_parts(api_get(f"league/{lk}/standings", cfg))
    teams = _find(parts, "standings")[0]["teams"]
    out = []
    for t in _coll(teams):
        node = t["team"]
        m = _team_meta(node)
        ts = _team_standings(node) or {}
        ot = ts.get("outcome_totals", {})
        out.append({
            "team_key": m.get("team_key"), "name": m.get("name"),
            "rank": int(ts.get("rank") or 0),
            "wins": int(ot.get("wins") or 0), "losses": int(ot.get("losses") or 0),
            "ties": int(ot.get("ties") or 0),
            "pct": float(ot.get("percentage") or 0),
            "is_mine": 1 if m.get("team_key") in my_keys else 0,
            "cats": _team_stat_map(node),
        })
    return out


def fetch_scoreboard(lk, cfg, week=None):
    suffix = f";week={week}" if week else ""
    parts = _league_parts(api_get(f"league/{lk}/scoreboard{suffix}", cfg))
    sb = _find(parts, "scoreboard")
    matchups = _coll(sb["0"]["matchups"])
    out = []
    for mw in matchups:
        m = mw["matchup"]
        winners = {}
        for w in m.get("stat_winners", []):
            sw = w["stat_winner"]
            winners[int(sw["stat_id"])] = sw.get("winner_team_key")
        teams = []
        for t in _coll(m["0"]["teams"]):
            node = t["team"]
            meta = _team_meta(node)
            teams.append({
                "team_key": meta.get("team_key"), "name": meta.get("name"),
                "points": _team_points(node), "cats": _team_stat_map(node),
            })
        out.append({"week": int(m.get("week") or 0), "winners": winners, "teams": teams})
    return out


def fetch_transactions(lk, cfg, count=200):
    parts = _league_parts(api_get(f"league/{lk}/transactions;count={count}", cfg))
    tx = _find(parts, "transactions")
    out = []
    for t in _coll(tx):
        tr = t["transaction"]
        meta = tr[0] if isinstance(tr, list) else tr
        moves = []
        players = tr[1].get("players") if isinstance(tr, list) and len(tr) > 1 and isinstance(tr[1], dict) else None
        for pl in _coll(players or {}):
            pnode = pl["player"]
            pm = _flatten(pnode[0])
            nm = pm.get("name", {})
            name = nm.get("full") if isinstance(nm, dict) else nm
            td = None
            for part in pnode[1:]:
                if isinstance(part, dict) and "transaction_data" in part:
                    td = part["transaction_data"]
                    td = td[0] if isinstance(td, list) else td
            td = td or {}
            moves.append({
                "player_name": name,
                "move_type": td.get("type"),
                "source_team": td.get("source_team_name") or td.get("source_type"),
                "dest_team": td.get("destination_team_name") or td.get("destination_type"),
            })
        out.append({
            "txn_key": meta.get("transaction_key"), "type": meta.get("type"),
            "status": meta.get("status"), "ts": int(meta.get("timestamp") or 0),
            "moves": moves,
        })
    return out


# --------------------------------------------------------------------------- #
# refresh / store
# --------------------------------------------------------------------------- #
def default_league_key(conn):
    row = conn.execute("SELECT team_key FROM fantasy_roster LIMIT 1").fetchone()
    return row[0].rsplit(".t.", 1)[0] if row else None


def refresh(league_key=None) -> dict:
    cfg = load_cfg()
    conn = db.connect()
    league_key = league_key or default_league_key(conn)
    my_keys = {r[0] for r in conn.execute(
        "SELECT team_key FROM fantasy_roster WHERE team_key LIKE ?", (f"{league_key}.t.%",))}

    meta, cats = fetch_settings(league_key, cfg)
    standings = fetch_standings(league_key, cfg, my_keys)
    week = int(meta.get("current_week") or 0) or None
    scoreboard = fetch_scoreboard(league_key, cfg, week)
    txns = fetch_transactions(league_key, cfg)

    lookup = match.build_lookup(conn)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO league_meta (league_key, name, scoring_type, "
            "num_teams, current_week) VALUES (?,?,?,?,?)",
            (league_key, meta.get("name"), meta.get("scoring_type"),
             int(meta.get("num_teams") or 0), week))
        conn.execute("DELETE FROM league_categories WHERE league_key=?", (league_key,))
        conn.executemany(
            "INSERT INTO league_categories (league_key, stat_id, display_name, name, "
            "sort_order, is_pitching, ord) VALUES (?,?,?,?,?,?,?)",
            [(league_key, c["stat_id"], c["display_name"], c["name"], c["sort_order"],
              c["is_pitching"], c["ord"]) for c in cats])

        conn.execute("DELETE FROM league_teams WHERE league_key=?", (league_key,))
        conn.execute("DELETE FROM team_category WHERE league_key=?", (league_key,))
        for t in standings:
            conn.execute(
                "INSERT INTO league_teams (league_key, team_key, name, rank, wins, "
                "losses, ties, pct, is_mine) VALUES (?,?,?,?,?,?,?,?,?)",
                (league_key, t["team_key"], t["name"], t["rank"], t["wins"],
                 t["losses"], t["ties"], t["pct"], t["is_mine"]))
            for sid, val in t["cats"].items():
                num = _num(val)
                conn.execute(
                    "INSERT OR REPLACE INTO team_category (league_key, team_key, stat_id, "
                    "value, value_str) VALUES (?,?,?,?,?)",
                    (league_key, t["team_key"], sid, num, str(val) if val is not None else None))

        if week:
            conn.execute("DELETE FROM matchup_team WHERE league_key=? AND week=?", (league_key, week))
            conn.execute("DELETE FROM matchup_category WHERE league_key=? AND week=?", (league_key, week))
            for mu in scoreboard:
                tks = [t["team_key"] for t in mu["teams"]]
                for i, t in enumerate(mu["teams"]):
                    opp = tks[1 - i] if len(tks) == 2 else None
                    conn.execute(
                        "INSERT OR REPLACE INTO matchup_team (league_key, week, team_key, "
                        "opp_team_key, points, is_mine) VALUES (?,?,?,?,?,?)",
                        (league_key, mu["week"], t["team_key"], opp, _num(t["points"]),
                         1 if t["team_key"] in my_keys else 0))
                    for sid, val in t["cats"].items():
                        winner = mu["winners"].get(sid)
                        win = None if winner is None else (1 if winner == t["team_key"] else 0)
                        conn.execute(
                            "INSERT OR REPLACE INTO matchup_category (league_key, week, "
                            "team_key, stat_id, value, win) VALUES (?,?,?,?,?,?)",
                            (league_key, mu["week"], t["team_key"], sid, str(val), win))

        for tx in txns:
            if not tx["txn_key"]:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO transactions (league_key, txn_key, type, status, ts) "
                "VALUES (?,?,?,?,?)",
                (league_key, tx["txn_key"], tx["type"], tx["status"], tx["ts"]))
            conn.execute("DELETE FROM transaction_moves WHERE txn_key=?", (tx["txn_key"],))
            for i, mv in enumerate(tx["moves"]):
                conn.execute(
                    "INSERT INTO transaction_moves (txn_key, idx, player_name, player_id, "
                    "move_type, source_team, dest_team) VALUES (?,?,?,?,?,?,?)",
                    (tx["txn_key"], i, mv["player_name"],
                     match.match(lookup, mv["player_name"] or ""),
                     mv["move_type"], mv["source_team"], mv["dest_team"]))
    conn.close()
    return {"league": meta.get("name"), "teams": len(standings), "week": week,
            "matchups": len(scoreboard), "transactions": len(txns), "categories": len(cats)}


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    db.init_db()
    if args[:1] == ["refresh"]:
        r = refresh(args[1] if len(args) > 1 else None)
        print(f"{r['league']}: {r['teams']} teams, week {r['week']}, "
              f"{r['matchups']} matchups, {r['transactions']} transactions, "
              f"{r['categories']} categories")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
