"""Self-contained smoke test for the hold/drop board.

Builds a tiny league from the real schema (no network / no full ingest): a pool
of catchers spanning stud→scrub, a synced roster, and a free-agent pool, then
checks that hold_drop_board returns the right verdict for each archetype and that
the Flask /holddrop route renders. Run: python test_hold_drop.py
"""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FANTASYBB_DB"] = _tmp.name

from fantasybb import db  # noqa: E402

db.init_db()
conn = db.connect()

LEAGUE = "999.l.1234"
SEASON = 2025

conn.execute("INSERT INTO teams(team_id, abbreviation, name) VALUES (1,'TST','Test')")

# (id, name, pos, G, R, H, HR, RBI, SB, AB, AVG)  — a catcher pool stud→scrub,
# plus a few non-catchers so the z-score pool is realistic.
HITTERS = [
    (1,  "FA Stud C",      "C",  140, 110, 167, 45, 115, 20, 540, .310),
    (2,  "My Hold C",      "C",  140,  98, 155, 34,  96, 12, 540, .288),
    (3,  "My Upgrade C",   "C",  140,  72, 139, 20,  66,  6, 540, .258),
    (4,  "Filler C Four",  "C",  140,  63, 134, 16,  57,  4, 540, .249),
    (5,  "My Drop C",      "C",  140,  45, 124,  8,  38,  2, 540, .230),
    (6,  "Filler C Six",   "C",  140,  34, 118,  5,  28,  1, 540, .218),
    (7,  "Mid OF One",     "OF", 140,  80, 150, 24,  78, 10, 545, .275),
    (8,  "Mid OF Two",     "OF", 140,  70, 140, 18,  68, 14, 540, .259),
    (9,  "Mid IF One",     "2B", 140,  66, 138, 12,  55, 22, 540, .256),
    (10, "Stash OF",       "OF", 140,  85, 152, 28,  85,  8, 545, .279),  # rostered, IL
]
for h in HITTERS:
    pid, name, pos, g, r, hit, hr, rbi, sb, ab, avg = h
    conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (?,?,?,1)",
                 (pid, name, pos))
    conn.execute(
        "INSERT INTO batting_season(player_id, season, g, pa, ab, r, h, hr, rbi, sb, avg) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pid, SEASON, g, ab + 60, ab, r, hit, hr, rbi, sb, avg))

# League: 4 teams (drives replacement depth = teams x slots), standard 5x5.
for i in range(1, 5):
    conn.execute(
        "INSERT INTO league_teams(league_key, team_key, name, is_mine) VALUES (?,?,?,?)",
        (LEAGUE, f"{LEAGUE}.t.{i}", f"Team {i}", 1 if i == 1 else 0))

CATS = [  # stat_id, name, is_pitching, sort_order (1=higher better)
    (7, "R", 0, 1), (12, "HR", 0, 1), (13, "RBI", 0, 1), (62, "SB", 0, 1), (3, "AVG", 0, 1),
    (28, "W", 1, 1), (42, "K", 1, 1), (90, "SV", 1, 1), (26, "ERA", 1, 0), (27, "WHIP", 1, 0),
]
for ordn, (sid, nm, isp, so) in enumerate(CATS):
    conn.execute(
        "INSERT INTO league_categories(league_key, stat_id, display_name, name, sort_order, is_pitching, ord) "
        "VALUES (?,?,?,?,?,?,?)", (LEAGUE, sid, nm, nm, so, isp, ordn))

# My roster: a keeper, an upgrade candidate, a drop candidate, an injured stash,
# and an unmatched (no stats line) entry.
ROSTER = [
    (2, "My Hold C",    "C",  ""),
    (3, "My Upgrade C", "C",  ""),
    (5, "My Drop C",    "C",  ""),
    (10, "Stash OF",    "OF", "IL"),
    (None, "Mystery Guy", "Util", ""),
]
for slot, (pid, name, pos, status) in enumerate(ROSTER):
    conn.execute(
        "INSERT INTO fantasy_roster(team_key, season, slot, yahoo_id, yahoo_name, player_id, "
        "selected_position, status, is_mine) VALUES (?,?,?,?,?,?,?,?,1)",
        (f"{LEAGUE}.t.1", SEASON, slot, f"y{slot}", name, pid, pos, status))

# Free agents: the stud catcher (drives the upgrade) is available.
for pid, name, pos in [(1, "FA Stud C", "C"), (7, "Mid OF One", "OF")]:
    conn.execute("INSERT INTO availability(league_key, name, player_id, status, position) "
                 "VALUES (?,?,?,?,?)", (LEAGUE, name, pid, "A", pos))
conn.commit()

from fantasybb import queries as Q  # noqa: E402

print("=== hold_drop_board ===")
board = Q.hold_drop_board(conn, LEAGUE, SEASON)
assert board is not None, "board should not be None for a synced team"
by = {r["name"]: r for r in board["rows"]}
for r in board["rows"]:
    wire = f" -> {r['wire']['name']} ({r['wire']['rating']})" if r["wire"] else ""
    edge = f" edge={r['edge']}" if r["edge"] is not None else ""
    print(f"  {r['name']:<14} rating={str(r['rating']):>5} repl={str(r['repl']):>5} "
          f"{r['verdict']:<8}{edge}{wire}")
print(f"  counts: {board['counts']}")

assert by["My Hold C"]["verdict"] == "hold", by["My Hold C"]
assert by["My Upgrade C"]["verdict"] == "upgrade", by["My Upgrade C"]
assert by["My Upgrade C"]["wire"]["name"] == "FA Stud C"
assert by["My Upgrade C"]["edge"] > 0
assert by["My Drop C"]["verdict"] == "drop", by["My Drop C"]
assert by["Stash OF"]["verdict"] == "stash", by["Stash OF"]
assert by["Mystery Guy"]["verdict"] == "unknown", by["Mystery Guy"]

# Drop is below replacement; keeper is at/above the keeper line; upgrade sits
# above replacement but below the keeper line.
assert by["My Drop C"]["rating"] < by["My Drop C"]["repl"]
assert by["My Hold C"]["rating"] >= board["keeper"]
assert by["My Upgrade C"]["repl"] <= by["My Upgrade C"]["rating"] < board["keeper"]

# Most actionable first: a drop (or upgrade) should outrank a plain hold.
assert board["rows"][0]["verdict"] in ("drop", "upgrade")
assert board["counts"]["drop"] == 1 and board["counts"]["upgrade"] == 1
print("verdicts, scale ordering, and priority sort OK")

print("\n=== no synced team -> None ===")
assert Q.hold_drop_board(conn, "888.l.0000", SEASON) is None
print("OK")

print("\n=== Flask route renders ===")
import app as flask_app  # noqa: E402

client = flask_app.app.test_client()
r = client.get(f"/holddrop?league={LEAGUE}&season={SEASON}")
body = r.data.decode()
assert r.status_code == 200, r.status_code
assert "Hold / Drop" in body
assert "My Drop C" in body and "FA Stud C" in body
assert "drop" in body and "upgrade" in body
print("route renders the board with verdicts and wire upgrades")

print("\nALL CHECKS PASSED ✅")
conn.close()
os.unlink(_tmp.name)
