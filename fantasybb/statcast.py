"""Statcast (Baseball Savant) enrichment via pybaseball.

Pulls pitch-level data for a season, keeps batted-ball events, and rolls them
up per hitter into the advanced metrics fantasy managers care about:
exit velocity, barrel rate, hard-hit rate, and expected wOBA/BA on contact.

This is the one source pybaseball can still reach (FanGraphs/Baseball-Reference
are blocked), and it complements the MLB Stats API box-score data nicely.

    python -m fantasybb.ingest statcast --season 2025
"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")


def ingest_statcast(conn, season: int, start: str | None = None, end: str | None = None) -> int:
    import pandas as pd
    import pybaseball as pb  # imported lazily; heavy + only needed here

    pb.cache.enable()  # cache day pulls on disk so reruns are fast

    def _num(v, nd):
        return round(float(v), nd) if not pd.isna(v) else None

    start = start or f"{season}-03-01"
    end = end or f"{season}-11-30"
    print(f"pulling Statcast {start}..{end} (this is large and may take a few minutes)", flush=True)
    df = pb.statcast(start_dt=start, end_dt=end)
    if df is None or df.empty:
        print("no statcast rows returned")
        return 0

    # Balls in play only (type == 'X'); these are the batted-ball events.
    bip = df[df["type"] == "X"].copy()
    bip = bip.dropna(subset=["launch_speed"])
    # launch_speed_angle (6 == barrel) can be null on untracked balls; treat
    # null as "not a barrel" so the cast to int doesn't choke on pandas NA.
    bip["barrel"] = bip["launch_speed_angle"].eq(6).fillna(False).astype(int)
    bip["hard_hit"] = bip["launch_speed"].ge(95).fillna(False).astype(int)

    grp = bip.groupby("batter")
    agg = grp.agg(
        bbe=("launch_speed", "size"),
        avg_ev=("launch_speed", "mean"),
        max_ev=("launch_speed", "max"),
        avg_la=("launch_angle", "mean"),
        barrels=("barrel", "sum"),
        hard=("hard_hit", "sum"),
        xwoba=("estimated_woba_using_speedangle", "mean"),
        xba=("estimated_ba_using_speedangle", "mean"),
    ).reset_index()

    # Only keep batters we already have in `players` (the box-score ingest is
    # the source of truth); this avoids FK failures on the handful of Statcast
    # batter IDs that never recorded an MLB box-score line in our window.
    valid = {r[0] for r in conn.execute("SELECT player_id FROM players").fetchall()}

    rows = []
    skipped = 0
    for r in agg.itertuples(index=False):
        if int(r.batter) not in valid:
            skipped += 1
            continue
        bbe = int(r.bbe)
        rows.append((
            int(r.batter), season, bbe,
            _num(r.avg_ev, 1),
            _num(r.max_ev, 1),
            _num(r.avg_la, 1),
            round(100 * r.barrels / bbe, 1) if bbe else None,
            round(100 * r.hard / bbe, 1) if bbe else None,
            _num(r.xwoba, 3),
            _num(r.xba, 3),
        ))
    if skipped:
        print(f"skipped {skipped} batters not in players table", flush=True)

    with conn:
        conn.executemany(
            """INSERT INTO statcast_batting
               (player_id, season, bbe, avg_ev, max_ev, avg_la, barrel_pct,
                hard_hit_pct, xwoba, xba)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(player_id) DO UPDATE SET
                season=excluded.season, bbe=excluded.bbe, avg_ev=excluded.avg_ev,
                max_ev=excluded.max_ev, avg_la=excluded.avg_la,
                barrel_pct=excluded.barrel_pct, hard_hit_pct=excluded.hard_hit_pct,
                xwoba=excluded.xwoba, xba=excluded.xba""",
            rows,
        )
    return len(rows)
