"""Self-contained test for the shareable trade card (SVG export).

Builds a tiny pool, runs the /trade analyzer, and checks the SVG generator and
the /trade/card.svg route. Run: python test_trade_card.py
"""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FANTASYBB_DB"] = _tmp.name

from fantasybb import db  # noqa: E402

db.init_db()
conn = db.connect()

conn.execute("INSERT INTO teams(team_id, abbreviation, name) VALUES (1,'TST','Test')")
# Enough hitters/pitchers (>= a handful) so the z-score pools are well-defined.
HITTERS = [
    (101, "Juan Soto", "RF", 150, 650, 540, 110, 167, 38, 100, 12, .309),
    (102, "Filler Bat A", "1B", 150, 600, 540, 80, 140, 22, 75, 6, .259),
    (103, "Filler Bat B", "2B", 150, 600, 540, 70, 135, 16, 65, 18, .250),
    (104, "Filler Bat C", "SS", 150, 600, 540, 75, 138, 19, 70, 9, .256),
    (105, "Casey Schmitt", "3B", 95, 320, 290, 32, 70, 8, 35, 2, .241),
]
for pid, name, pos, g, pa, ab, r, h, hr, rbi, sb, avg in HITTERS:
    conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (?,?,?,1)",
                 (pid, name, pos))
    conn.execute(
        "INSERT INTO batting_season(player_id, season, g, pa, ab, r, h, hr, rbi, sb, avg) "
        "VALUES (?,2025,?,?,?,?,?,?,?,?,?)", (pid, g, pa, ab, r, h, hr, rbi, sb, avg))

PITCHERS = [
    (201, "George Soriano", 4, 2, 70, 25, 200, 55, 22, 3.38, 1.16),
    (202, "Filler Arm A", 9, 0, 150, 70, 480, 150, 45, 3.94, 1.22),
    (203, "Filler Arm B", 11, 0, 170, 65, 510, 145, 40, 3.44, 1.09),
    (204, "Closer McSaves", 5, 35, 90, 20, 195, 45, 18, 2.77, 0.97),
    (205, "Filler Arm C", 6, 1, 120, 75, 420, 150, 50, 4.82, 1.43),
]
for pid, name, w, sv, so, er, outs, h, bb, era, whip in PITCHERS:
    conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (?,?,?,1)",
                 (pid, name, "P"))
    conn.execute(
        "INSERT INTO pitching_season(player_id, season, w, sv, so, er, outs, h, bb, era, whip) "
        "VALUES (?,2025,?,?,?,?,?,?,?,?,?)", (pid, w, sv, so, er, outs, h, bb, era, whip))
conn.commit()

from fantasybb import queries as Q, cards  # noqa: E402

print("=== build a verdict + render SVG ===")
result = Q.evaluate_text_trade(conn, ["Juan Soto"], ["George Soriano", "Casey Schmitt"], 2025)
svg = cards.trade_card_svg(result, 2025)
assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
assert 'width="760"' in svg and "viewBox" in svg
assert "Trade Analyzer" in svg
assert result["verdict"] in svg
assert "Juan Soto" in svg and "George Soriano" in svg and "Casey Schmitt" in svg
assert "Side A" in svg and "Side B" in svg
# well-formed XML (would raise on bad escaping / unbalanced tags)
import xml.dom.minidom as _x  # noqa: E402
_x.parseString(svg)
print(f"verdict={result['verdict']}  svg_bytes={len(svg)}  XML well-formed OK")

print("\n=== unmatched name doesn't break the card ===")
r2 = Q.evaluate_text_trade(conn, ["Notarealguy"], ["Juan Soto"], 2025)
svg2 = cards.trade_card_svg(r2, 2025)
_x.parseString(svg2)
assert "not found" in svg2
print("unmatched rendered as a muted row OK")

print("\n=== special chars are escaped ===")
# A name with an ampersand must be XML-escaped, not break parsing.
conn.execute("INSERT INTO players(player_id, full_name, position, team_id) VALUES (901,'A & B Jr.','RF',1)")
conn.execute("INSERT INTO batting_season(player_id, season, g, pa, ab, r, h, hr, rbi, sb, avg) "
             "VALUES (901,2025,150,600,540,70,140,20,70,5,.259)")
conn.commit()
r3 = Q.evaluate_text_trade(conn, ["A & B Jr."], ["Juan Soto"], 2025)
svg3 = cards.trade_card_svg(r3, 2025)
_x.parseString(svg3)
assert "&amp;" in svg3
print("ampersand escaped OK")

print("\n=== Flask route serves SVG ===")
import app as flask_app  # noqa: E402

client = flask_app.app.test_client()
r = client.get("/trade/card.svg?a=Juan+Soto&b=George+Soriano,+Casey+Schmitt&season=2025")
assert r.status_code == 200, r.status_code
assert r.mimetype == "image/svg+xml", r.mimetype
assert b"Trade Analyzer" in r.data and b"Juan Soto" in r.data
# empty trade -> 400, not a 500
assert client.get("/trade/card.svg").status_code == 400
# the trade page exposes the copy/download controls
page = client.get("/trade?a=Juan+Soto&b=Casey+Schmitt").data.decode()
assert "Copy image" in page and "copyTradeImage" in page and "card.svg" in page
print("route serves image/svg+xml, guards empty input, page wires the buttons")

print("\nALL CHECKS PASSED ✅")
conn.close()
os.unlink(_tmp.name)
