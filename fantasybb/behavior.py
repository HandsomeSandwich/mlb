"""Behavioral analysis of league transactions.

Pure functions over the stored transaction log. The goal is to surface
*patterns worth a human look* -- synchronized waiver timing, recurring
drop->add "feeding", repeat trade partners, lopsided trades -- NOT to accuse
anyone of collusion. Treat every signal as a prompt to investigate, not proof.
"""
from __future__ import annotations

import bisect
from collections import defaultdict

POOLS = {None, "", "waivers", "freeagents", "commish"}


def load_txns(conn, league_key) -> list[dict]:
    txns = {}
    for t in conn.execute(
        "SELECT txn_key, type, status, ts FROM transactions WHERE league_key=? "
        "ORDER BY ts", (league_key,)):
        txns[t["txn_key"]] = {"ts": t["ts"], "type": t["type"], "moves": []}
    for m in conn.execute(
        "SELECT txn_key, player_name, player_id, move_type, source_team, dest_team "
        "FROM transaction_moves"):
        if m["txn_key"] in txns:
            txns[m["txn_key"]]["moves"].append(dict(m))
    out = []
    for t in txns.values():
        teams = set()
        for mv in t["moves"]:
            for x in (mv["source_team"], mv["dest_team"]):
                if x not in POOLS:
                    teams.add(x)
        t["acting_teams"] = teams
        out.append(t)
    return out


def team_activity(txns) -> list[dict]:
    act = defaultdict(lambda: {"adds": 0, "drops": 0, "trades": 0})
    for t in txns:
        if t["type"] == "trade":
            for tm in t["acting_teams"]:
                act[tm]["trades"] += 1
            continue
        for mv in t["moves"]:
            if mv["move_type"] == "add" and mv["dest_team"] not in POOLS:
                act[mv["dest_team"]]["adds"] += 1
            elif mv["move_type"] == "drop" and mv["source_team"] not in POOLS:
                act[mv["source_team"]]["drops"] += 1
    rows = [{"team": k, **v, "total": v["adds"] + v["drops"] + v["trades"]}
            for k, v in act.items()]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def timing_similarity(txns, window=1800) -> list[dict]:
    """Pairs of teams whose transactions repeatedly land within `window` secs.

    score = fraction of the less-active team's moves that coincide with the
    other team's -- high score means unusually synchronized activity.
    """
    times = defaultdict(list)
    for t in txns:
        if t["type"] == "trade":
            continue
        for tm in t["acting_teams"]:
            times[tm].append(t["ts"])
    for tm in times:
        times[tm].sort()
    teams = sorted(times)
    pairs = []
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            a, b = teams[i], teams[j]
            tb = times[b]
            co = 0
            for x in times[a]:
                lo = bisect.bisect_left(tb, x - window)
                hi = bisect.bisect_right(tb, x + window)
                if hi > lo:
                    co += 1
            if co >= 2:
                denom = min(len(times[a]), len(tb))
                pairs.append({"a": a, "b": b, "co": co,
                              "a_total": len(times[a]), "b_total": len(tb),
                              "score": round(co / denom, 2) if denom else 0})
    pairs.sort(key=lambda p: (p["score"], p["co"]), reverse=True)
    return pairs


def drop_add_feeding(txns, max_gap_days=7) -> list[dict]:
    """How often team B picks up a player team A just dropped (B != A)."""
    gap = max_gap_days * 86400
    drops, adds = [], []
    for t in txns:
        for mv in t["moves"]:
            if mv["move_type"] == "drop" and mv["source_team"] not in POOLS:
                drops.append((mv["player_name"], mv["source_team"], t["ts"]))
            if mv["move_type"] == "add" and mv["dest_team"] not in POOLS:
                adds.append((mv["player_name"], mv["dest_team"], t["ts"]))
    adds_by_player = defaultdict(list)
    for pn, team, ts in adds:
        adds_by_player[pn].append((team, ts))
    pair = defaultdict(int)
    examples = defaultdict(list)
    for pn, dteam, dts in drops:
        for ateam, ats in adds_by_player.get(pn, []):
            if ateam != dteam and 0 <= ats - dts <= gap:
                pair[(dteam, ateam)] += 1
                if pn not in examples[(dteam, ateam)]:
                    examples[(dteam, ateam)].append(pn)
    out = [{"dropper": k[0], "adder": k[1], "count": v, "players": examples[k][:6]}
           for k, v in pair.items() if v >= 2]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def trades(txns, value_fn) -> list[dict]:
    """Each trade with players grouped by who received them + heuristic value."""
    out = []
    for t in txns:
        if t["type"] != "trade":
            continue
        sides = defaultdict(list)
        for mv in t["moves"]:
            dest = mv["dest_team"]
            if dest in POOLS:
                continue
            v, line = value_fn(mv["player_name"])
            sides[dest].append({"name": mv["player_name"], "value": v, "line": line})
        side_list = [{"team": team, "players": pls,
                      "value": round(sum(p["value"] or 0 for p in pls), 1)}
                     for team, pls in sides.items()]
        gap = None
        if len(side_list) == 2:
            gap = round(abs(side_list[0]["value"] - side_list[1]["value"]), 1)
        out.append({"ts": t["ts"], "sides": side_list, "value_gap": gap})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def trade_partner_counts(trade_rows) -> list[dict]:
    pair = defaultdict(int)
    for tr in trade_rows:
        teams = sorted(s["team"] for s in tr["sides"])
        if len(teams) == 2:
            pair[(teams[0], teams[1])] += 1
    out = [{"a": k[0], "b": k[1], "count": v} for k, v in pair.items()]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
