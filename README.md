# Fantasy Baseball DB ⚾

A searchable SQLite database of the **2025 MLB season** — every player, their
official season line, and full game-by-game logs — with a Flask dashboard for
fantasy analysis. Hitters get Statcast advanced metrics (exit velo, barrels,
xwOBA) layered on top.

## Why these data sources

The project started from a Retrosheet season page. In practice, the cleanest
**unblocked** sources from a normal machine are:

| Source | Used for | Notes |
| --- | --- | --- |
| **MLB Stats API** (`statsapi.mlb.com`) | season totals, game logs, rosters, bios | Official, free, no key, returns clean box-score lines (R/RBI/SB/W/SV/ERA) so nothing has to be derived. |
| **Baseball Savant / Statcast** (via `pybaseball`) | exit velocity, barrel %, hard-hit %, xwOBA/xBA | Pitch-level data rolled up per hitter. |

> Baseball-Reference and FanGraphs (pybaseball's `*_bref` / `batting_stats`
> functions) are currently Cloudflare-blocked / 403, which is why the box-score
> spine comes from the MLB Stats API instead.

## Setup

```bash
cd fantasy-baseball
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Build the database

```bash
# Everything (teams, season totals, bios, then all game logs — resumable):
.venv/bin/python -m fantasybb.ingest all --season 2025

# …or step by step:
.venv/bin/python -m fantasybb.ingest teams   --season 2025
.venv/bin/python -m fantasybb.ingest season  --season 2025   # fast: official totals
.venv/bin/python -m fantasybb.ingest bios                    # bats/throws/birthdate
.venv/bin/python -m fantasybb.ingest games   --season 2025   # ~2,430 games, resumable
.venv/bin/python -m fantasybb.ingest games   --season 2025 --start 2025-04-01 --end 2025-04-30
.venv/bin/python -m fantasybb.ingest statcast --season 2025  # advanced metrics (slow)
```

`games` records every ingested gamePk in `ingest_log`, so you can stop and
re-run, or backfill by date range, without redoing work. The DB lands at
`data/baseball.db`.

### No-network sample DB

If `statsapi.mlb.com` is unreachable (some networks block it) or you just want
to click around quickly, seed a self-contained sample instead of running the
full ingest:

```bash
.venv/bin/python scripts/seed_sample.py    # writes data/baseball.db
```

It writes realistic, **approximate** 2025-shaped season lines for a pool of
stars so every page (and the trade analyzer) renders with data. These figures
are illustrative, not an official feed — use the real ingest for accuracy.

## Run the dashboard

```bash
.venv/bin/python app.py        # http://127.0.0.1:5000
```

* **Home** — hitting & pitching leader cards (HR, RBI, SB, AVG, W, SV, K, ERA).
* **Hitters / Pitchers** — sortable, filterable leaderboards (click any column
  header to sort; filter by team, position, min PA/IP; search by name).
* **Player page** — bio, season line, Statcast strip, and full game log.
* **Trade** — paste the players each side receives (e.g. `Soto` vs
  `Soriano, Schmitt`) and get a fairness verdict. Each player is valued with a
  standard 5×5 roto **z-score** model: how many standard deviations above/below
  the league pool they are in R/HR/RBI/SB/AVG (hitters) and W/SV/K/ERA/WHIP
  (pitchers), with rate stats weighted by playing time. Side totals, a
  per-category breakdown, and an even/edge/lopsided call settle the argument
  with numbers instead of vibes. Two-way players (Ohtani) get both a hitter and
  a pitcher value. Run `python test_trade_analyzer.py` for a no-network check.

## View it on your phone

The dashboard binds to `127.0.0.1`. To reach it from a phone on the same
network, bind to all interfaces and visit `http://<your-computer-ip>:5000`:

```bash
.venv/bin/flask --app app run --host 0.0.0.0 --port 5000
```

To reach it from anywhere (e.g. cellular), put a tunnel in front of the local
server — this hands back a public HTTPS URL you can open on your phone:

```bash
# Cloudflare (no account needed for a quick tunnel):
cloudflared tunnel --url http://127.0.0.1:5000
# …or ngrok:
ngrok http 5000
```

> Heads-up: a quick tunnel exposes the app publicly with no auth, and only
> lasts while the command runs. Tunnel providers are also unreachable from
> locked-down/allowlisted networks (e.g. some CI/cloud sandboxes) — run the
> tunnel from a machine with open egress. For an always-on URL, deploy the app
> (any host that runs a Flask/WSGI app) and point your phone at it.

## Layout

```
fantasy-baseball/
├── app.py                 # Flask app (routes)
├── fantasybb/
│   ├── db.py              # SQLite schema + connection
│   ├── mlb_api.py         # MLB Stats API client
│   ├── ingest.py          # CLI: build/update the DB (resumable)
│   ├── statcast.py        # pybaseball Statcast enrichment
│   └── queries.py         # read-only query helpers for the app
├── templates/             # Jinja templates
├── static/style.css       # dark "scoreboard" theme
└── data/baseball.db       # generated (gitignored)
```

## Schema (tables)

`teams`, `players`, `games`, `batting_games`, `pitching_games`,
`batting_season`, `pitching_season`, `statcast_batting`, `ingest_log`.

Innings pitched are stored as integer **outs** (1 IP = 3 outs) so they sum
cleanly; the UI converts back to `X.Y` notation.

## Extending

* **Another season:** everything is parameterized by `--season`. Run the ingest
  commands with a different year.
* **Daily refresh:** re-running `games` only fetches games not already in
  `ingest_log`, so a cron job that runs `season` + `games` keeps it current.
* **New dashboards:** add a query in `queries.py` and a route/template — the
  game-log tables support trends, streaks, and matchup splits.
