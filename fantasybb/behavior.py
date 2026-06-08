"""Behavioral analysis of league transactions.

Pure functions over the stored transaction log. The goal is to surface
*patterns worth a human look* -- synchronized waiver timing, recurring
drop->add "feeding", repeat trade partners, lopsided trades -- NOT to accuse
anyone of collusion. Treat every signal as a prompt to investigate, not proof.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
from collections import defaultdict

POOLS = {None, "", "waivers", "freeagents", "commish"}


def load_txns(conn, league_key) -> list[dict]:
    # team_key -> current team name. A team_key is stable across renames, so we
    # rewrite every move's actor to the team's CURRENT name. Without this, a
    # mid-season rename would split one team into two actors (old name in old
    # transactions, new name in recent ones) and dilute every coordination
    # signal. Moves with no key (free-agent/waiver pools, or rows synced before
    # keys were captured) keep their stored name.
    key2name = {r["team_key"]: r["name"] for r in conn.execute(
        "SELECT team_key, name FROM league_teams WHERE league_key=?", (league_key,))}
    txns = {}
    for t in conn.execute(
        "SELECT txn_key, type, status, ts FROM transactions WHERE league_key=? "
        "ORDER BY ts", (league_key,)):
        txns[t["txn_key"]] = {"ts": t["ts"], "type": t["type"], "moves": []}
    for m in conn.execute(
        "SELECT txn_key, player_name, player_id, team_abbr, move_type, source_team, "
        "dest_team, source_team_key, dest_team_key FROM transaction_moves"):
        if m["txn_key"] in txns:
            mv = dict(m)
            if mv.get("source_team_key") in key2name:
                mv["source_team"] = key2name[mv["source_team_key"]]
            if mv.get("dest_team_key") in key2name:
                mv["dest_team"] = key2name[mv["dest_team_key"]]
            txns[m["txn_key"]]["moves"].append(mv)
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


def permutation_timing(txns, window=900, perms=150):
    """Significance of tight co-timing via a permutation null.

    The naive rate model fails because transactions cluster in time
    league-wide (waiver runs, evenings), so it over-predicts coincidences for
    nobody and under-predicts for everyone. Instead we hold the timestamps
    fixed and shuffle *which manager* made each move -- preserving the real
    temporal clustering -- and measure how far each pair's observed
    coincidences sit above that null. Returns {(a,b) sorted: z}.
    """
    import random
    ev = sorted((t["ts"], tm) for t in txns if t["type"] != "trade"
                for tm in t["acting_teams"])
    times = [e[0] for e in ev]
    labels = [e[1] for e in ev]
    n = len(ev)
    npairs = []
    for i in range(n):
        hi = bisect.bisect_right(times, times[i] + window, i + 1)
        npairs.extend((i, j) for j in range(i + 1, hi))

    def co_counts(lab):
        cc = defaultdict(int)
        for i, j in npairs:
            a, b = lab[i], lab[j]
            if a != b:
                cc[(a, b) if a < b else (b, a)] += 1
        return cc

    obs = co_counts(labels)
    sums, sqs = defaultdict(float), defaultdict(float)
    rng = random.Random(0)  # seeded -> stable across page loads
    arr = labels[:]
    for _ in range(perms):
        rng.shuffle(arr)
        for k, v in co_counts(arr).items():
            sums[k] += v
            sqs[k] += v * v
    out = {}
    for k, o in obs.items():
        mean = sums[k] / perms
        var = max(sqs[k] / perms - mean * mean, 0)
        out[k] = round((o - mean) / (var ** 0.5), 2) if var > 1e-6 else None
    return out


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


def _txn_week(date_iso, weeks):
    for w in weeks:
        if w["start"] and w["end"] and w["start"] <= date_iso <= w["end"]:
            return w["week"]
    return None


def team_week_activity(txns, weeks):
    """{team_name: {week: add/drop count}} bucketed by league week dates."""
    counts = defaultdict(lambda: defaultdict(int))
    for t in txns:
        if t["type"] == "trade":
            continue
        d = _dt.datetime.fromtimestamp(t["ts"]).date().isoformat()
        wk = _txn_week(d, weeks)
        if wk is None:
            continue
        for tm in t["acting_teams"]:
            counts[tm][wk] += 1
    return counts


def opponent_vs_me(txns, schedule):
    """Does each opponent transact more in the week they play me than usual?

    `schedule` rows: {week, week_start, week_end, opp_name, status}. Returns one
    record per opponent comparing their activity in our matchup week(s) to their
    baseline (their other weeks that have transaction data).
    """
    weeks = [{"week": s["week"], "start": s["week_start"], "end": s["week_end"]}
             for s in schedule]
    counts = team_week_activity(txns, weeks)
    weeks_with_data = sorted({wk for tm in counts for wk in counts[tm]})

    opp_weeks = defaultdict(list)
    for s in schedule:
        if s["status"] in ("postevent", "midevent") and s["opp_name"]:
            opp_weeks[s["opp_name"]].append(s["week"])

    out = []
    for oname, wks in opp_weeks.items():
        tc = counts.get(oname, {})
        vs_set = set(wks)
        base_weeks = [w for w in weeks_with_data if w not in vs_set]
        base = (sum(tc.get(w, 0) for w in base_weeks) / len(base_weeks)) if base_weeks else 0
        vs = [(w, tc.get(w, 0)) for w in wks if w in weeks_with_data]
        vs_avg = (sum(c for _, c in vs) / len(vs)) if vs else None
        ramp = (vs_avg / base) if (base and vs_avg is not None) else None
        out.append({
            "opp": oname, "weeks": [w for w, _ in vs], "vs_counts": vs,
            "vs_avg": round(vs_avg, 1) if vs_avg is not None else None,
            "baseline": round(base, 1), "ramp": round(ramp, 2) if ramp else None,
        })
    out.sort(key=lambda x: (x["ramp"] or 0), reverse=True)
    return out, weeks_with_data


def collusion_lens(txns, value_fn, feed_gap_days=7):
    """Directional A↔B coordination metrics: feeding, trade value flow, timing."""
    gap = feed_gap_days * 86400
    feed = defaultdict(int)
    feed_players = defaultdict(list)
    drops, adds_by_player = [], defaultdict(list)
    for t in txns:
        for mv in t["moves"]:
            if mv["move_type"] == "drop" and mv["source_team"] not in POOLS:
                drops.append((mv["player_name"], mv["source_team"], t["ts"]))
            if mv["move_type"] == "add" and mv["dest_team"] not in POOLS:
                adds_by_player[mv["player_name"]].append((mv["dest_team"], t["ts"]))
    for pn, dteam, dts in drops:
        for ateam, ats in adds_by_player.get(pn, []):
            if ateam != dteam and 0 <= ats - dts <= gap:
                feed[(dteam, ateam)] += 1
                if pn not in feed_players[(dteam, ateam)]:
                    feed_players[(dteam, ateam)].append(pn)

    trade_rows = trades(txns, value_fn)
    tp_cnt = defaultdict(int)
    tp_val = defaultdict(lambda: defaultdict(float))
    for tr in trade_rows:
        if len(tr["sides"]) != 2:
            continue
        a, b = sorted(s["team"] for s in tr["sides"])
        tp_cnt[(a, b)] += 1
        for s in tr["sides"]:
            tp_val[(a, b)][s["team"]] += s["value"]

    # Expected feeding from volume alone (contingency-table model): if A gives
    # G_A feeds total and B receives R_B total out of F league-wide, then by
    # chance A->B ~= G_A * R_B / F. over_index = actual / expected cuts the
    # "busiest managers overlap most" confound -- only >1 is beyond chance.
    give, recv, total_feed = defaultdict(int), defaultdict(int), 0
    for (d, a), c in feed.items():
        give[d] += c
        recv[a] += c
        total_feed += c

    def over_index(d, a):
        c = feed.get((d, a), 0)
        exp = (give[d] * recv[a] / total_feed) if total_feed else 0
        return (round(c / exp, 2) if exp > 0 else None), round(exp, 1)

    def residual(d, a):
        """Adjusted residual: std deviations above the activity-expected count.
        Silences the busy-manager noise -- only large positive z is real signal."""
        o = feed.get((d, a), 0)
        exp = (give[d] * recv[a] / total_feed) if total_feed else 0
        denom = math.sqrt(exp * (1 - give[d] / total_feed) * (1 - recv[a] / total_feed)) if (exp > 0 and total_feed) else 0
        return round((o - exp) / denom, 2) if denom > 0 else None

    timing = {}
    for p in timing_similarity(txns, window=900):
        timing[tuple(sorted((p["a"], p["b"])))] = p["co"]
    timing_z = permutation_timing(txns, window=900)  # proper permutation null

    teams = set()
    for (d, a) in feed:
        teams.update((d, a))
    for (a, b) in tp_cnt:
        teams.update((a, b))
    teams = sorted(teams)

    pairs = []
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            A, B = teams[i], teams[j]
            fab, fba = feed.get((A, B), 0), feed.get((B, A), 0)
            tc = tp_cnt.get((A, B), 0)
            tv = tp_val.get((A, B), {})
            vA, vB = round(tv.get(A, 0), 1), round(tv.get(B, 0), 1)
            co = timing.get((A, B), 0)
            tz = timing_z.get((A, B))
            if fab + fba + tc + co == 0:
                continue
            oi_ab, exp_ab = over_index(A, B)
            oi_ba, exp_ba = over_index(B, A)
            max_oi = max([x for x in (oi_ab, oi_ba) if x is not None], default=None)
            z_ab, z_ba = residual(A, B), residual(B, A)
            # "real" signal needs both magnitude (>=4 events) and significance (z>=2)
            sig_z = max([z for z, o in ((z_ab, fab), (z_ba, fba))
                         if z is not None and o >= 4], default=None)
            # rank on coordination *beyond chance*: trades + timing + how far the
            # feeding exceeds its volume-expected baseline.
            excess = max(0, (fab - exp_ab)) + max(0, (fba - exp_ba))
            score = tc * 3 + co * 0.5 + excess * 1.5 + abs(vA - vB) * 0.04
            pairs.append({
                "a": A, "b": B, "feed_ab": fab, "feed_ba": fba,
                "oi_ab": oi_ab, "oi_ba": oi_ba, "max_oi": max_oi,
                "z_ab": z_ab, "z_ba": z_ba, "sig_z": sig_z, "timing_z": tz,
                "exp_ab": exp_ab, "exp_ba": exp_ba,
                "feed_players_ab": feed_players.get((A, B), [])[:5],
                "feed_players_ba": feed_players.get((B, A), [])[:5],
                "trades": tc, "val_a": vA, "val_b": vB,
                "val_gap": round(abs(vA - vB), 1), "timing_co": co,
                "score": round(score, 1),
            })
    pairs.sort(key=lambda p: p["score"], reverse=True)

    hub = defaultdict(float)
    for p in pairs:
        hub[p["a"]] += p["score"] / 2
        hub[p["b"]] += p["score"] / 2
    hubs = sorted(({"team": k, "score": round(v, 1)} for k, v in hub.items()),
                  key=lambda x: x["score"], reverse=True)
    return pairs, hubs


def slip_signals(txns, watched, window=120, context=600):
    """Near-simultaneous moves among `watched` accounts that are NOT league-wide
    waiver batches.

    For every pair of watched-account moves within `window` seconds of each
    other, count how many *other* teams were also active within ±`context`
    seconds. `isolated` (no other team nearby) is the toggling/2-device tell;
    a crowd of other teams means it was just the scheduled waiver run.
    """
    watched = set(watched)
    all_ev, w_ev = [], []
    for t in txns:
        if t["type"] == "trade":
            continue
        for tm in t["acting_teams"]:
            all_ev.append((t["ts"], tm))
            if tm in watched:
                w_ev.append((t["ts"], tm, t))
    all_ev.sort()
    w_ev.sort(key=lambda x: x[0])
    all_ts = [e[0] for e in all_ev]

    def moves_str(t):
        return [f"{m['player_name']} ({m['move_type']})" for m in t["moves"] if m["player_name"]]

    flags, seen = [], set()
    for i in range(len(w_ev)):
        ts1, a, t1 = w_ev[i]
        for j in range(i + 1, len(w_ev)):
            ts2, b, t2 = w_ev[j]
            if ts2 - ts1 > window:
                break
            if a == b:
                continue
            key = (t1["txn_key"] if t1.get("txn_key") else ts1, t2.get("txn_key"), a, b)
            if key in seen:
                continue
            seen.add(key)
            lo = bisect.bisect_left(all_ts, ts1 - context)
            hi = bisect.bisect_right(all_ts, ts2 + context)
            others = {all_ev[k][1] for k in range(lo, hi) if all_ev[k][1] not in watched}
            flags.append({
                "ts": ts1, "gap": ts2 - ts1, "a": a, "b": b,
                "players_a": moves_str(t1), "players_b": moves_str(t2),
                "others": len(others), "isolated": len(others) == 0,
            })
    # isolated first, then tightest gap
    flags.sort(key=lambda f: (0 if f["isolated"] else 1, f["gap"]))
    return flags


def trade_partner_counts(trade_rows) -> list[dict]:
    pair = defaultdict(int)
    for tr in trade_rows:
        teams = sorted(s["team"] for s in tr["sides"])
        if len(teams) == 2:
            pair[(teams[0], teams[1])] += 1
    out = [{"a": k[0], "b": k[1], "count": v} for k, v in pair.items()]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
