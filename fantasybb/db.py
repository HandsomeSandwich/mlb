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

-- Official season batting totals (from the stats endpoint).
CREATE TABLE IF NOT EXISTS batting_season (
    player_id INTEGER PRIMARY KEY REFERENCES players(player_id),
    season INTEGER, team_id INTEGER,
    g INTEGER, pa INTEGER, ab INTEGER, r INTEGER, h INTEGER,
    doubles INTEGER, triples INTEGER, hr INTEGER, rbi INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER,
    sb INTEGER, cs INTEGER, gidp INTEGER, tb INTEGER,
    sac_bunts INTEGER, sac_flies INTEGER,
    avg REAL, obp REAL, slg REAL, ops REAL, babip REAL
);

-- Official season pitching totals.
CREATE TABLE IF NOT EXISTS pitching_season (
    player_id INTEGER PRIMARY KEY REFERENCES players(player_id),
    season INTEGER, team_id INTEGER,
    g INTEGER, gs INTEGER, w INTEGER, l INTEGER, sv INTEGER, hld INTEGER, bs INTEGER,
    outs INTEGER, h INTEGER, r INTEGER, er INTEGER, hr INTEGER,
    bb INTEGER, ibb INTEGER, so INTEGER, hbp INTEGER, bf INTEGER,
    era REAL, whip REAL, k9 REAL, bb9 REAL, kbb REAL
);

-- Statcast batted-ball aggregates per hitter (optional enrichment).
CREATE TABLE IF NOT EXISTS statcast_batting (
    player_id INTEGER PRIMARY KEY REFERENCES players(player_id),
    season INTEGER,
    bbe INTEGER,            -- batted-ball events
    avg_ev REAL,            -- average exit velocity (mph)
    max_ev REAL,            -- max exit velocity
    avg_la REAL,            -- average launch angle
    barrel_pct REAL,        -- barrels / BBE
    hard_hit_pct REAL,      -- 95+ mph / BBE
    xwoba REAL,             -- expected wOBA on contact
    xba REAL                -- expected batting avg on contact
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


def init_db(db_path: str = DB_PATH) -> None:
    """Create all tables/indexes if they do not yet exist."""
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA)
    conn.close()
