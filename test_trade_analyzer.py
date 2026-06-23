"""Self-contained smoke test for the trade analyzer.

Builds a tiny DB from the real schema (no network / no full ingest), inserts a
handful of 2025-shaped players, and exercises the /trade route via Flask's test
client. Run: python test_trade_analyzer.py
"""
import os
import tempfile

# Point the app at a throwaway DB before anything imports db/app.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FANTASYBB_DB"] = _tmp.name

from fantasybb import db  # noqa: E402

db.init_db()
conn = db.connect()

TEAMS = [(1, "WSH"), (2, "SD"), (3, "ATL"), (4, "BAL"), (5, "SEA"), (6, "LAD"), (7, "MIN")]
conn.executemany("INSERT INTO teams(team_id, abbreviation, name) VALUES (?,?,?)",
                 [(i, a, a) for i, a in TEAMS])

# (id, name, pos, team)  — the disputed players plus filler to form a real pool.
HITTERS = [
    # id, name, pos, team, g, pa, ab, r, h, hr, rbi, sb
    (101, "Juan Soto", "RF", 1, 150, 650, 540, 110, 160, 38, 100, 12),
    (102, "Fernando Tatis Jr.", "RF", 2, 140, 600, 530, 95, 145, 28, 80, 25),
    (103, "Ronald Acuna Jr.", "RF", 3, 120, 520, 460, 85, 130, 22, 65, 30),
    (104, "Gunnar Henderson", "SS", 4, 145, 620, 550, 100, 150, 30, 85, 18),
    (105, "Randy Arozarena", "LF", 5, 150, 640, 560, 88, 140, 24, 78, 20),
    (106, "Shohei Ohtani", "DH", 6, 155, 680, 580, 130, 175, 50, 110, 18),
    (107, "Filler Hitterone", "1B", 7, 140, 560, 500, 70, 130, 20, 70, 5),
    (108, "Filler Hittertwo", "2B", 7, 130, 520, 470, 60, 120, 12, 55, 8),
    (109, "Filler Hitterthree", "3B", 1, 120, 480, 430, 55, 110, 15, 60, 3),
    (110, "Casey Schmitt", "3B", 2, 90, 300, 270, 30, 70, 8, 35, 2),
]
for h in HITTERS:
    pid, name, pos, team = h[0], h[1], h[2], h[3]
    g, pa, ab, r, hit, hr, rbi, sb = h[4:]
    conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (?,?,?,?)",
                 (pid, name, pos, team))
    conn.execute(
        "INSERT INTO batting_season(player_id, season, g, pa, ab, r, h, hr, rbi, sb) "
        "VALUES (?,2025,?,?,?,?,?,?,?,?)", (pid, g, pa, ab, r, hit, hr, rbi, sb))

PITCHERS = [
    # id, name, team, w, sv, so, er, outs, h, bb
    (201, "George Soriano", 2, 4, 2, 70, 25, 200, 55, 22),   # surging reliever
    (202, "Zebby Matthews", 7, 6, 0, 95, 45, 300, 95, 28),   # back-end SP
    (203, "Ace McFiller", 6, 15, 0, 220, 60, 600, 160, 45),  # ace SP
    (204, "Closer McSaves", 3, 5, 35, 90, 20, 195, 45, 18),  # elite closer
    (205, "Middling Arm", 4, 9, 1, 140, 80, 450, 160, 55),
    (206, "Replacement Guy", 5, 3, 0, 75, 60, 240, 110, 35),
]
for p in PITCHERS:
    pid, name, team, w, sv, so, er, outs, h, bb = p
    conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (?,?,?,?)",
                 (pid, name, "P", team))
    conn.execute(
        "INSERT INTO pitching_season(player_id, season, w, sv, so, er, outs, h, bb) "
        "VALUES (?,2025,?,?,?,?,?,?,?)", (pid, w, sv, so, er, outs, h, bb))

# Ohtani is two-way: give him a pitching line too so we test the combined value.
conn.execute(
    "INSERT INTO pitching_season(player_id, season, w, sv, so, er, outs, h, bb) "
    "VALUES (106,2025,8,0,130,35,300,80,30)")
conn.commit()

from fantasybb import queries as Q  # noqa: E402

SEASON = 2025  # the season the fixtures above are inserted under

print("=== unit: pools ===")
hmeta = Q.hitter_pool(conn, SEASON)
pmeta = Q.pitcher_pool(conn, SEASON)
print(f"hitter pool n={hmeta['n']} lg_avg={hmeta['lg_avg']:.3f}")
print(f"pitcher pool n={pmeta['n']} lg_era={pmeta['lg_era']:.2f} lg_whip={pmeta['lg_whip']:.2f}")
assert hmeta["n"] >= 8 and pmeta["n"] >= 5

print("\n=== unit: name resolution ===")
assert Q.parse_player_names("Soto, Soriano & Schmitt") == ["Soto", "Soriano", "Schmitt"]
assert Q.parse_player_names("Tatis and Acuna") == ["Tatis", "Acuna"]
assert Q.search_players(conn, "Soto", SEASON)[0]["full_name"] == "Juan Soto"
print("parse + search OK")

print("\n=== integration: the disputed Soto trade ===")
res = Q.evaluate_text_trade(conn, ["Juan Soto"], ["George Soriano", "Casey Schmitt"], SEASON)
print(f"Side A (Soto):            {res['a']['total']:+.2f}")
print(f"Side B (Soriano+Schmitt): {res['b']['total']:+.2f}")
print(f"verdict: {res['verdict']}  favors Side {res['winner']}  (delta {res['delta']:+.2f})")
# Soto (elite hitter) should clearly out-value a middling reliever + part-time IF.
assert res["a"]["total"] > res["b"]["total"]
assert res["winner"] == "A"

print("\n=== integration: Henderson+Arozarena vs Tatis+Acuna ===")
res2 = Q.evaluate_text_trade(conn, ["Gunnar Henderson", "Randy Arozarena"],
                             ["Fernando Tatis", "Ronald Acuna"], SEASON)
print(f"Side A: {res2['a']['total']:+.2f}   Side B: {res2['b']['total']:+.2f}")
print(f"verdict: {res2['verdict']}")

print("\n=== integration: two-way Ohtani gets hit + pitch value ===")
oht = Q.evaluate_side(conn, ["Shohei Ohtani"], SEASON, hmeta, pmeta)["players"][0]
assert oht["hit"] is not None and oht["pit"] is not None, "Ohtani should have both"
print(f"Ohtani total {oht['value']:+.2f} (hit {oht['hit']['total']:+.2f} + pit {oht['pit']['total']:+.2f})")

print("\n=== integration: Flask route + template render ===")
import app as flask_app  # noqa: E402

client = flask_app.app.test_client()
r = client.get("/trade")
assert r.status_code == 200 and b"Trade Analyzer" in r.data, "empty form should render"
r = client.get("/trade?a=Juan+Soto&b=George+Soriano,+Casey+Schmitt")
body = r.data.decode()
assert r.status_code == 200
assert "Juan Soto" in body and "George Soriano" in body and "Casey Schmitt" in body
assert "Side A" in body and "favors" in body
# unmatched name should surface, not crash
r = client.get("/trade?a=Notarealplayer&b=Juan+Soto")
assert r.status_code == 200 and "Couldn" in r.data.decode()
print("route renders verdict, players, and handles unmatched names")

print("\nALL CHECKS PASSED ✅")
conn.close()
os.unlink(_tmp.name)
