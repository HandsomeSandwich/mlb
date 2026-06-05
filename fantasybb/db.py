"""SQLite connection helpers and schema for the fantasy-baseball database.

Innings pitched are stored as integer `outs` (1 IP = 3 outs) so they sum
cleanly; the display layer converts back to the familiar "X.Y" notation.
"""
from __future__ import annotations

import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.environ.get("FANTASYBB_DB", os.path.join(DATA_DIR, "baseball.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id      INTEGER PRIMARY KEY,
    abbreviation TEXT,
    name         TEXT,
    club_name    TEXT,
    league       TEXT,
    division     TEXT
);

CREATE TABLE IF NOT EXISTS players (
    player_id    INTEGER PRIMARY KEY,
    full_name    TEXT,
    position     TEXT,
    bat_side     TEXT,
    pitch_hand   TEXT,
    birth_date   TEXT,
    team_id      INTEGER REFERENCES teams(team_id)
);

CREATE TABLE IF NOT EXISTS games (
    game_pk       INTEGER PRIMARY KEY,
    game_date     TEXT,
    season        INTEGER,
    game_type     TEXT,
    home_team_id  INTEGER REFERENCES teams(team_id),
    away_team_id  INTEGER REFERENCES teams(team_id),
    venue         TEXT,
    status        TEXT
);

-- One row per batter per game.
CREATE TABLE IF NOT EXISTS batting_games (
    game_pk     INTEGER REFERENCES games(game_pk),
    player_id   INTEGER REFERENCES players(player_id),
    team_id     INTEGER,
    opp_team_id INTEGER,
    is_home     INTEGER,
    game_date   TEXT,
    pa  INTEGER, ab INTEGER, r INTEGER, h INTEGER,
    doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER,
    sb INTEGER, cs INTEGER, gidp INTEGER, tb INTEGER,
    sac_bunts INTEGER, sac_flies INTEGER, lob INTEGER,
    PRIMARY KEY (game_pk, player_id)
);

-- One row per pitcher per game.
CREATE TABLE IF NOT EXISTS pitching_games (
    game_pk     INTEGER REFERENCES games(game_pk),
    player_id   INTEGER REFERENCES players(player_id),
    team_id     INTEGER,
    opp_team_id INTEGER,
    is_home     INTEGER,
    game_date   TEXT,
    gs INTEGER, w INTEGER, l INTEGER, sv INTEGER, hld INTEGER, bs INTEGER,
    outs INTEGER, h INTEGER, r INTEGER, er INTEGER, hr INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER, bf INTEGER,
    pitches INTEGER, strikes INTEGER, balks INTEGER, wp INTEGER,
    PRIMARY KEY (game_pk, player_id)
);

-- Official season batting totals (from the stats endpoint). Keyed by
-- (player_id, season) so multiple seasons can coexist.
CREATE TABLE IF NOT EXISTS batting_season (
    player_id INTEGER REFERENCES players(player_id),
    season INTEGER, team_id INTEGER,
    g INTEGER, pa INTEGER, ab INTEGER, r INTEGER, h INTEGER,
    doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER,
    sb INTEGER, cs INTEGER, gidp INTEGER, tb INTEGER,
    sac_bunts INTEGER, sac_flies INTEGER,
    avg REAL, obp REAL, slg REAL, ops REAL, babip REAL,
    PRIMARY KEY (player_id, season)
);

-- Official season pitching totals.
CREATE TABLE IF NOT EXISTS pitching_season (
    player_id INTEGER REFERENCES players(player_id),
    season INTEGER, team_id INTEGER,
    g INTEGER, gs INTEGER, w INTEGER, l INTEGER, sv INTEGER, hld INTEGER, bs INTEGER,
    outs INTEGER, h INTEGER, r INTEGER, er INTEGER, hr INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER, bf INTEGER,
    era REAL, whip REAL, k9 REAL, bb9 REAL, kbb REAL,
    PRIMARY KEY (player_id, season)
);

-- Statcast batted-ball aggregates per hitter (optional enrichment).
CREATE TABLE IF NOT EXISTS statcast_batting (
    player_id INTEGER REFERENCES players(player_id),
    season INTEGER,
    bbe INTEGER,            -- batted-ball events
    avg_ev REAL,            -- average exit velocity (mph)
    max_ev REAL,            -- max exit velocity
    avg_la REAL,            -- average launch angle
    barrel_pct REAL,        -- barrels / BBE
    hard_hit_pct REAL,      -- 95+ mph / BBE
    xwoba REAL,             -- expected wOBA on contact
    xba REAL,               -- expected batting avg on contact
    PRIMARY KEY (player_id, season)
);

-- Yahoo league header info.
CREATE TABLE IF NOT EXISTS league_meta (
    league_key   TEXT PRIMARY KEY,
    name         TEXT,
    scoring_type TEXT,
    num_teams    INTEGER,
    current_week INTEGER
);

-- My team's matchup schedule (opponent + week dates) for "vs me" analysis.
CREATE TABLE IF NOT EXISTS my_schedule (
    league_key   TEXT,
    week         INTEGER,
    week_start   TEXT,
    week_end     TEXT,
    opp_team_key TEXT,
    opp_name     TEXT,
    status       TEXT,
    PRIMARY KEY (league_key, week)
);

-- Yahoo league scoring categories.
CREATE TABLE IF NOT EXISTS league_categories (
    league_key   TEXT,
    stat_id      INTEGER,
    display_name TEXT,
    name         TEXT,
    sort_order   INTEGER,   -- 1 = higher is better, 0 = lower is better
    is_pitching  INTEGER,
    ord          INTEGER,   -- display order
    PRIMARY KEY (league_key, stat_id, ord)
);

-- League standings: one row per team.
CREATE TABLE IF NOT EXISTS league_teams (
    league_key TEXT,
    team_key   TEXT,
    name       TEXT,
    rank       INTEGER,
    wins       INTEGER,
    losses     INTEGER,
    ties       INTEGER,
    pct        REAL,
    is_mine    INTEGER,
    PRIMARY KEY (league_key, team_key)
);

-- Per-team season totals for each scoring category (for league comparison).
CREATE TABLE IF NOT EXISTS team_category (
    league_key TEXT,
    team_key   TEXT,
    stat_id    INTEGER,
    value      REAL,
    value_str  TEXT,
    PRIMARY KEY (league_key, team_key, stat_id)
);

-- Weekly matchup: each team's category values + win flags for a week.
CREATE TABLE IF NOT EXISTS matchup_team (
    league_key   TEXT,
    week         INTEGER,
    team_key     TEXT,
    opp_team_key TEXT,
    points       REAL,
    is_mine      INTEGER,
    PRIMARY KEY (league_key, week, team_key)
);
CREATE TABLE IF NOT EXISTS matchup_category (
    league_key TEXT,
    week       INTEGER,
    team_key   TEXT,
    stat_id    INTEGER,
    value      TEXT,
    win        INTEGER,   -- 1 won, 0 lost, NULL tie
    PRIMARY KEY (league_key, week, team_key, stat_id)
);

-- League transactions (adds / drops / trades) and the player moves within them.
CREATE TABLE IF NOT EXISTS transactions (
    league_key TEXT,
    txn_key    TEXT,
    type       TEXT,
    status     TEXT,
    ts         INTEGER,    -- unix timestamp
    PRIMARY KEY (txn_key)
);
CREATE TABLE IF NOT EXISTS transaction_moves (
    txn_key     TEXT,
    idx         INTEGER,
    player_name TEXT,
    player_id   INTEGER,
    move_type   TEXT,      -- add / drop
    source_team TEXT,
    dest_team   TEXT,
    PRIMARY KEY (txn_key, idx)
);

-- Pitcher List daily streamer tiers (scraped). One row per pitcher per day.
CREATE TABLE IF NOT EXISTS pl_streamers (
    game_date  TEXT,
    name       TEXT,
    player_id  INTEGER,
    tier       TEXT,       -- Auto Start / Probably Start / Questionable Start / Do Not Start
    tier_rank  INTEGER,    -- ordering of tiers (0 = best)
    rank       INTEGER,    -- rank within the day
    matchup    TEXT,       -- e.g. 'vs. CHW' or '@ DET'
    opp        TEXT,
    rostership TEXT,
    PRIMARY KEY (game_date, name)
);

-- Probable starting pitchers for upcoming days (MLB Stats API).
CREATE TABLE IF NOT EXISTS probable_starts (
    game_date TEXT,
    player_id INTEGER,     -- MLBAM id == our players.player_id
    name      TEXT,
    team      TEXT,
    opp       TEXT,
    is_home   INTEGER,
    PRIMARY KEY (game_date, player_id)
);

-- Availability of players in a Yahoo league (free agent / waiver / owned).
CREATE TABLE IF NOT EXISTS availability (
    league_key TEXT,
    name       TEXT,
    player_id  INTEGER,
    status     TEXT,       -- FA / W / (owned teams omitted)
    position   TEXT,
    PRIMARY KEY (league_key, name)
);

-- Synced Yahoo fantasy roster (matched to our players where possible).
CREATE TABLE IF NOT EXISTS fantasy_roster (
    team_key   TEXT,
    team_name  TEXT,
    season     INTEGER,
    slot       INTEGER,        -- display order
    yahoo_id   TEXT,
    yahoo_name TEXT,
    player_id  INTEGER,        -- matched DB player (NULL if unmatched)
    selected_position TEXT,
    positions  TEXT,
    yahoo_team TEXT,
    status     TEXT,           -- IL / DTD etc.
    PRIMARY KEY (team_key, yahoo_id)
);

-- Tracks ingest progress so game-log backfills are resumable.
CREATE TABLE IF NOT EXISTS ingest_log (
    game_pk INTEGER PRIMARY KEY,
    ingested_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_bg_player ON batting_games(player_id);
CREATE INDEX IF NOT EXISTS idx_bg_date   ON batting_games(game_date);
CREATE INDEX IF NOT EXISTS idx_pg_player ON pitching_games(player_id);
CREATE INDEX IF NOT EXISTS idx_pg_date   ON pitching_games(game_date);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with sensible pragmas and row access by column name."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_SEASON_PK_TABLES = ("batting_season", "pitching_season", "statcast_batting")


def _migrate_season_pk(conn) -> None:
    """Convert legacy season tables (PK = player_id) to PK (player_id, season).

    Columns are unchanged -- only the primary key -- so a straight `SELECT *`
    copy preserves all existing rows (which are all the 2025 season).
    """
    aside = []
    for tbl in _SEASON_PK_TABLES:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if row and "(player_id,season)" not in row[0].replace(" ", "").replace("\n", ""):
            conn.execute(f"ALTER TABLE {tbl} RENAME TO {tbl}__old")
            aside.append(tbl)
    conn.executescript(SCHEMA)  # recreate with new PK + any new tables
    for tbl in aside:
        conn.execute(f"INSERT OR IGNORE INTO {tbl} SELECT * FROM {tbl}__old")
        conn.execute(f"DROP TABLE {tbl}__old")


def init_db(db_path: str = DB_PATH) -> None:
    """Create all tables/indexes if they do not yet exist (migrating if needed)."""
    conn = connect(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")  # let the table swap proceed cleanly
    with conn:
        _migrate_season_pk(conn)
        conn.executescript(SCHEMA)
    conn.close()
