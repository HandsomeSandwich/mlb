"""Seed a SAMPLE database so the dashboards (and the trade analyzer) can be
demoed without the network ingest.

The real `fantasybb.ingest` pipeline pulls from statsapi.mlb.com, which is not
reachable from every environment. This script writes a self-contained DB with
realistic, approximate 2025-shaped season lines for a pool of stars — including
the players from the league trade dispute — so every page renders with data.

    python scripts/seed_sample.py        # writes data/baseball.db

NOTE: these numbers are illustrative approximations entered by hand, NOT an
official feed. Use the real ingest for accurate data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fantasybb import db  # noqa: E402

TEAMS = [
    (108, "LAA", "Los Angeles Angels"), (109, "ARI", "Arizona Diamondbacks"),
    (110, "BAL", "Baltimore Orioles"), (111, "BOS", "Boston Red Sox"),
    (112, "CHC", "Chicago Cubs"), (113, "CIN", "Cincinnati Reds"),
    (114, "CLE", "Cleveland Guardians"), (115, "COL", "Colorado Rockies"),
    (116, "DET", "Detroit Tigers"), (117, "HOU", "Houston Astros"),
    (118, "KC", "Kansas City Royals"), (119, "LAD", "Los Angeles Dodgers"),
    (120, "WSH", "Washington Nationals"), (121, "NYM", "New York Mets"),
    (133, "ATH", "Athletics"), (134, "PIT", "Pittsburgh Pirates"),
    (135, "SD", "San Diego Padres"), (136, "SEA", "Seattle Mariners"),
    (137, "SF", "San Francisco Giants"), (138, "STL", "St. Louis Cardinals"),
    (139, "TB", "Tampa Bay Rays"), (140, "TEX", "Texas Rangers"),
    (141, "TOR", "Toronto Blue Jays"), (142, "MIN", "Minnesota Twins"),
    (143, "PHI", "Philadelphia Phillies"), (144, "ATL", "Atlanta Braves"),
    (145, "CWS", "Chicago White Sox"), (146, "MIA", "Miami Marlins"),
    (147, "NYY", "New York Yankees"), (158, "MIL", "Milwaukee Brewers"),
]

# id, name, pos, team, g, pa, ab, r, h, 2b, 3b, hr, rbi, bb, so, sb, cs, hbp, sf
HITTERS = [
    (665742, "Juan Soto", "RF", 121, 157, 685, 560, 121, 158, 28, 2, 41, 105, 118, 119, 11, 4, 5, 5),
    (660271, "Shohei Ohtani", "DH", 119, 158, 700, 600, 134, 180, 33, 6, 52, 112, 90, 165, 22, 4, 6, 4),
    (665487, "Fernando Tatis Jr.", "RF", 135, 145, 620, 545, 99, 152, 30, 3, 29, 82, 60, 130, 28, 6, 8, 4),
    (660670, "Ronald Acuna Jr.", "RF", 144, 124, 540, 470, 88, 138, 26, 2, 24, 66, 58, 100, 31, 5, 4, 3),
    (683002, "Gunnar Henderson", "SS", 110, 152, 660, 580, 105, 159, 34, 5, 31, 88, 65, 145, 19, 4, 6, 5),
    (668227, "Randy Arozarena", "LF", 136, 156, 670, 575, 90, 148, 27, 2, 26, 80, 78, 160, 22, 7, 9, 4),
    (592450, "Aaron Judge", "CF", 147, 152, 660, 540, 125, 175, 30, 1, 53, 130, 110, 155, 9, 2, 6, 5),
    (665489, "Vladimir Guerrero Jr.", "1B", 141, 159, 690, 600, 95, 185, 35, 1, 30, 103, 80, 90, 3, 1, 7, 6),
    (606192, "Bobby Witt Jr.", "SS", 142, 161, 700, 630, 115, 200, 42, 9, 32, 98, 55, 110, 35, 6, 4, 5),
    (677951, "Corbin Carroll", "RF", 135, 155, 660, 580, 105, 165, 35, 12, 28, 75, 65, 120, 38, 7, 5, 4),
    (665161, "Elly De La Cruz", "SS", 113, 158, 680, 590, 110, 160, 30, 6, 27, 86, 70, 175, 55, 12, 6, 3),
    (663656, "Kyle Tucker", "RF", 112, 150, 640, 545, 100, 158, 30, 2, 28, 90, 80, 95, 24, 4, 6, 5),
    (605141, "Mookie Betts", "SS", 119, 145, 630, 540, 105, 160, 32, 3, 22, 78, 75, 80, 14, 3, 5, 4),
    (571448, "Nolan Arenado", "3B", 144, 150, 630, 570, 70, 155, 30, 2, 20, 78, 50, 85, 2, 1, 5, 6),
    (514888, "Jose Altuve", "2B", 117, 154, 660, 600, 110, 175, 33, 2, 22, 70, 50, 90, 18, 5, 6, 3),
    (608336, "Corey Seager", "SS", 140, 130, 560, 500, 85, 150, 30, 1, 28, 78, 55, 95, 2, 0, 4, 4),
    (519317, "Bryce Harper", "1B", 143, 145, 620, 530, 95, 152, 35, 1, 30, 90, 78, 120, 8, 2, 6, 5),
    (596019, "Francisco Lindor", "SS", 121, 160, 700, 620, 110, 168, 32, 3, 31, 88, 65, 110, 25, 4, 5, 5),
    (650391, "Yordan Alvarez", "DH", 117, 120, 500, 430, 80, 130, 28, 1, 30, 92, 65, 90, 1, 0, 4, 5),
    (663728, "Julio Rodriguez", "CF", 136, 158, 680, 610, 100, 170, 32, 4, 26, 85, 30, 145, 28, 6, 5, 4),
    (700022, "Jackson Merrill", "CF", 135, 150, 620, 565, 85, 162, 28, 4, 24, 88, 35, 110, 16, 4, 5, 4),
    (672695, "Anthony Volpe", "SS", 147, 158, 660, 590, 90, 145, 30, 5, 20, 70, 55, 130, 24, 5, 6, 4),
    (677594, "William Contreras", "C", 158, 155, 660, 560, 88, 158, 32, 1, 22, 85, 75, 110, 4, 1, 8, 5),
    (663457, "Lawrence Butler", "RF", 133, 150, 630, 570, 88, 150, 30, 4, 25, 78, 50, 140, 22, 5, 4, 3),
    (607208, "Marcus Semien", "2B", 140, 160, 700, 630, 100, 158, 30, 2, 23, 75, 60, 105, 12, 3, 5, 4),
    (657077, "Cal Raleigh", "C", 136, 155, 640, 540, 92, 130, 25, 0, 38, 100, 80, 150, 10, 1, 7, 5),
    (656941, "Kyle Schwarber", "DH", 143, 158, 670, 560, 100, 135, 22, 1, 42, 98, 100, 195, 8, 1, 6, 4),
    (502671, "Pete Alonso", "1B", 121, 152, 650, 575, 92, 150, 30, 1, 35, 108, 65, 130, 3, 1, 6, 6),
    (700935, "Casey Schmitt", "3B", 137, 92, 300, 270, 33, 73, 14, 1, 8, 36, 22, 60, 3, 1, 3, 2),
    (682928, "Wyatt Langford", "LF", 140, 140, 600, 540, 82, 148, 28, 5, 22, 74, 45, 130, 19, 4, 4, 3),
]

# id, name, team, gs, w, l, sv, hld, ip_outs, h, er, hr, bb, so, bf
PITCHERS = [
    (694973, "Paul Skenes", 134, 30, 14, 6, 0, 0, 600, 150, 56, 14, 45, 240, 780),
    (668678, "Tarik Skubal", 116, 31, 17, 5, 0, 0, 615, 145, 52, 16, 38, 245, 790),
    (605483, "Zack Wheeler", 143, 30, 15, 7, 0, 0, 600, 155, 60, 18, 40, 230, 790),
    (656605, "George Soriano", 135, 0, 4, 3, 2, 12, 200, 55, 25, 6, 22, 70, 270),
    (701542, "Zebby Matthews", 142, 18, 6, 7, 0, 0, 300, 95, 45, 12, 28, 95, 410),
    (543037, "Gerrit Cole", 147, 30, 13, 8, 0, 0, 590, 160, 62, 20, 42, 215, 780),
    (664285, "Garrett Crochet", 111, 32, 12, 9, 0, 0, 620, 150, 58, 16, 40, 250, 800),
    (676979, "Hunter Greene", 113, 28, 11, 7, 0, 0, 550, 140, 55, 18, 45, 220, 730),
    (642203, "Logan Webb", 137, 33, 14, 9, 0, 0, 640, 175, 60, 14, 42, 200, 850),
    (660271, "Shohei Ohtani", 119, 14, 8, 2, 0, 0, 300, 80, 35, 9, 30, 130, 400),  # two-way
    (453286, "Max Fried", 147, 29, 15, 5, 0, 0, 580, 150, 50, 12, 38, 175, 760),
    (663556, "Tanner Bibee", 114, 31, 12, 7, 0, 0, 590, 155, 58, 18, 40, 205, 770),
    (621111, "Emmanuel Clase", 114, 0, 5, 4, 42, 5, 210, 50, 14, 3, 15, 80, 280),
    (664299, "Mason Miller", 133, 0, 3, 5, 30, 8, 195, 42, 16, 5, 25, 100, 260),
    (608566, "Edwin Diaz", 121, 0, 4, 3, 36, 4, 195, 45, 18, 6, 24, 95, 265),
    (518886, "Josh Hader", 117, 0, 5, 3, 38, 2, 200, 40, 15, 7, 20, 95, 255),
    (676083, "Cade Smith", 114, 0, 6, 2, 5, 30, 220, 50, 18, 5, 22, 100, 290),
    (656302, "Jhoan Duran", 142, 0, 4, 4, 33, 6, 195, 48, 17, 4, 20, 85, 260),
    (605400, "Aaron Nola", 143, 31, 11, 9, 0, 0, 590, 165, 68, 24, 40, 195, 780),
    (664351, "Bryan Woo", 136, 30, 13, 6, 0, 0, 570, 150, 52, 16, 30, 180, 740),
]


def derived_hitter(ab, h, dbl, tpl, hr, bb, so, hbp, sf):
    singles = h - dbl - tpl - hr
    tb = singles + 2 * dbl + 3 * tpl + 4 * hr
    avg = round(h / ab, 3)
    pa_denom = ab + bb + hbp + sf
    obp = round((h + bb + hbp) / pa_denom, 3)
    slg = round(tb / ab, 3)
    ops = round(obp + slg, 3)
    bip = ab - so - hr + sf
    babip = round((h - hr) / bip, 3) if bip > 0 else None
    return tb, avg, obp, slg, ops, babip


def derived_pitcher(outs, h, er, bb, so):
    ip = outs / 3
    era = round(er * 9 / ip, 2)
    whip = round((h + bb) / ip, 2)
    k9 = round(so * 9 / ip, 2)
    bb9 = round(bb * 9 / ip, 2)
    kbb = round(so / bb, 2) if bb else None
    return era, whip, k9, bb9, kbb


def main():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    conn = db.connect()
    conn.executemany(
        "INSERT INTO teams(team_id, abbreviation, name) VALUES (?,?,?)", TEAMS)

    seen = set()
    for row in HITTERS:
        pid, name, pos, team = row[0], row[1], row[2], row[3]
        g, pa, ab, r, h, dbl, tpl, hr, rbi, bb, so, sb, cs, hbp, sf = row[4:]
        tb, avg, obp, slg, ops, babip = derived_hitter(ab, h, dbl, tpl, hr, bb, so, hbp, sf)
        conn.execute(
            "INSERT INTO players(player_id, full_name, position, bat_side, team_id) "
            "VALUES (?,?,?,?,?)", (pid, name, pos, "R", team))
        seen.add(pid)
        conn.execute(
            """INSERT INTO batting_season(player_id, season, team_id, g, pa, ab, r, h,
               doubles, triples, hr, rbi, bb, so, sb, cs, hbp, sac_flies, tb,
               avg, obp, slg, ops, babip)
               VALUES (?,2025,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, team, g, pa, ab, r, h, dbl, tpl, hr, rbi, bb, so, sb, cs, hbp, sf,
             tb, avg, obp, slg, ops, babip))

    for row in PITCHERS:
        pid, name, team = row[0], row[1], row[2]
        gs, w, l, sv, hld, outs, h, er, hr, bb, so, bf = row[3:]
        g = gs if gs > 0 else round(outs / 3)   # relievers: ~1 IP per appearance
        era, whip, k9, bb9, kbb = derived_pitcher(outs, h, er, bb, so)
        if pid not in seen:
            conn.execute(
                "INSERT INTO players(player_id, full_name, position, pitch_hand, team_id) "
                "VALUES (?,?,?,?,?)", (pid, name, "P", "R", team))
            seen.add(pid)
        conn.execute(
            """INSERT INTO pitching_season(player_id, season, team_id, g, gs, w, l, sv,
               hld, outs, h, er, hr, bb, so, bf, era, whip, k9, bb9, kbb)
               VALUES (?,2025,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, team, g, gs, w, l, sv, hld, outs, h, er, hr, bb, so, bf,
             era, whip, k9, bb9, kbb))

    conn.commit()
    nb = conn.execute("SELECT COUNT(*) FROM batting_season").fetchone()[0]
    npi = conn.execute("SELECT COUNT(*) FROM pitching_season").fetchone()[0]
    conn.close()
    print(f"Seeded sample DB at {db.DB_PATH}: {nb} hitters, {npi} pitchers")


if __name__ == "__main__":
    main()
