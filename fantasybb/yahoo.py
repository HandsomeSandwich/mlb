"""Yahoo Fantasy Sports API client (OAuth2) + roster fetch.

Credentials and tokens live in data/yahoo_oauth.json (gitignored). The flow:

    1. python -m fantasybb.yahoo url        # prints the consent URL to visit
    2. <approve in browser, copy the `code` from the redirected localhost URL>
    3. python -m fantasybb.yahoo code <CODE>  # exchanges it for tokens
    4. python -m fantasybb.yahoo teams       # lists your MLB fantasy teams
    5. python -m fantasybb.yahoo roster <team_key>

After step 3 the refresh token is cached, so the app refreshes access tokens
automatically and you never have to repeat the consent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yahoo_oauth.json")
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


# --------------------------------------------------------------------------- #
# config / token persistence
# --------------------------------------------------------------------------- #
def load_cfg() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"missing {CONFIG_PATH} -- add client_id/client_secret first")
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_cfg(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# --------------------------------------------------------------------------- #
# OAuth2
# --------------------------------------------------------------------------- #
def authorize_url(cfg: dict) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "language": "en-us",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(cfg: dict, code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "redirect_uri": cfg["redirect_uri"],
            "code": code.strip(),
        },
        auth=(cfg["client_id"], cfg["client_secret"]),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"token exchange failed [{resp.status_code}]: {resp.text}")
    tok = resp.json()
    cfg["access_token"] = tok["access_token"]
    cfg["refresh_token"] = tok["refresh_token"]
    cfg["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600)) - 60
    save_cfg(cfg)
    return cfg


def _refresh(cfg: dict) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "redirect_uri": cfg["redirect_uri"],
            "refresh_token": cfg["refresh_token"],
        },
        auth=(cfg["client_id"], cfg["client_secret"]),
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"token refresh failed [{resp.status_code}]: {resp.text} "
                         f"-- re-run `url` + `code` to re-authorize")
    tok = resp.json()
    cfg["access_token"] = tok["access_token"]
    cfg["refresh_token"] = tok.get("refresh_token", cfg["refresh_token"])
    cfg["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600)) - 60
    save_cfg(cfg)
    return cfg


def _valid_token(cfg: dict) -> str:
    if not cfg.get("refresh_token"):
        raise SystemExit("not authorized yet -- run `url` then `code <CODE>`")
    if time.time() >= cfg.get("expires_at", 0):
        cfg = _refresh(cfg)
    return cfg["access_token"]


# --------------------------------------------------------------------------- #
# Fantasy API
# --------------------------------------------------------------------------- #
def api_get(path: str, cfg: dict | None = None) -> dict:
    cfg = cfg or load_cfg()
    token = _valid_token(cfg)
    resp = requests.get(
        f"{API_BASE}/{path.lstrip('/')}",
        params={"format": "json"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Yahoo API {path} -> [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()


def get_teams(cfg: dict | None = None) -> list[dict]:
    """Return [{team_key, name, league_name}] for the logged-in user's MLB teams."""
    data = api_get("users;use_login=1/games;game_keys=mlb/teams", cfg)
    out: list[dict] = []
    users = data["fantasy_content"]["users"]
    user = users["0"]["user"]
    games = _coll(user[1]["games"])
    for game in games:
        g = game["game"]
        teams = _coll(g[1]["teams"]) if len(g) > 1 and "teams" in g[1] else []
        for t in teams:
            tk = t["team"]
            meta = _flatten(tk[0])
            out.append({
                "team_key": meta.get("team_key"),
                "name": meta.get("name"),
                "season": _flatten(g[0]).get("season"),
            })
    return out


def get_available(league_key: str, position: str = "P", cfg: dict | None = None) -> list[dict]:
    """Available (free-agent + waiver) players in a league for a position.

    Yahoo paginates 25 at a time; we walk until a short/empty page.
    """
    cfg = cfg or load_cfg()
    out: list[dict] = []
    start = 0
    while True:
        data = api_get(
            f"league/{league_key}/players;status=A;position={position};"
            f"sort=AR;start={start};count=25", cfg)
        league = data["fantasy_content"]["league"]
        players = None
        for part in league:
            if isinstance(part, dict) and "players" in part:
                players = part["players"]
        items = _coll(players or {})
        if not items:
            break
        for pl in items:
            meta = _flatten(pl["player"][0])
            nm = meta.get("name")
            out.append({
                "name": nm.get("full") if isinstance(nm, dict) else nm,
                "team_abbr": meta.get("editorial_team_abbr"),
                "position": meta.get("display_position"),
                "status": meta.get("status"),
            })
        if len(items) < 25:
            break
        start += 25
    return out


def get_roster(team_key: str, cfg: dict | None = None) -> list[dict]:
    """Return the roster as [{name, yahoo_id, positions, selected_position}]."""
    data = api_get(f"team/{team_key}/roster", cfg)
    team = data["fantasy_content"]["team"]
    roster = None
    for part in team:
        if isinstance(part, dict) and "roster" in part:
            roster = part["roster"]
            break
    if roster is None:
        return []
    players = _coll(roster["0"]["players"])
    out: list[dict] = []
    for p in players:
        pk = p["player"]
        meta = _flatten(pk[0])
        sel = None
        for part in pk[1:]:
            if isinstance(part, dict) and "selected_position" in part:
                sel = _flatten(part["selected_position"]).get("position")
        out.append({
            "name": (meta.get("name") or {}).get("full") if isinstance(meta.get("name"), dict) else meta.get("name"),
            "yahoo_id": meta.get("player_id"),
            "positions": meta.get("display_position"),
            "team_abbr": meta.get("editorial_team_abbr"),
            "selected_position": sel,
            "status": meta.get("status"),  # IL/DTD/etc., if any
        })
    return out


# --------------------------------------------------------------------------- #
# match Yahoo players to our database + persist the roster
# --------------------------------------------------------------------------- #
def sync_roster(team_key: str, cfg: dict | None = None) -> tuple[str, int, int, list]:
    """Fetch a roster, match each player to the DB, and store it.

    Returns (team_name, total, matched, unmatched_names).
    """
    from . import db, match  # local import avoids a circular dependency

    cfg = cfg or load_cfg()
    meta = {t["team_key"]: t for t in get_teams(cfg)}.get(team_key, {})
    team_name = meta.get("name") or team_key
    season = int(meta.get("season") or 0)
    roster = get_roster(team_key, cfg)

    conn = db.connect()
    lookup = match.build_lookup(conn)

    rows, unmatched = [], []
    for i, pl in enumerate(roster):
        pid = match.match(lookup, pl["name"], pl["team_abbr"])
        if pid is None:
            unmatched.append(pl["name"])
        rows.append((team_key, team_name, season, i, pl["yahoo_id"], pl["name"], pid,
                     pl["selected_position"], pl["positions"], pl["team_abbr"], pl["status"]))

    with conn:
        conn.execute("DELETE FROM fantasy_roster WHERE team_key=?", (team_key,))
        conn.executemany(
            "INSERT INTO fantasy_roster (team_key, team_name, season, slot, yahoo_id, "
            "yahoo_name, player_id, selected_position, positions, yahoo_team, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.close()
    return team_name, len(rows), len(rows) - len(unmatched), unmatched


# --------------------------------------------------------------------------- #
# Yahoo's JSON is a quirky mix of numbered-dict "collections" and lists; these
# two helpers normalize it.
# --------------------------------------------------------------------------- #
def _coll(node: Any) -> list:
    """Yahoo collections are dicts keyed '0','1',... plus a 'count'. Listify."""
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        items = []
        for k, v in node.items():
            if k == "count":
                continue
            items.append(v)
        return items
    return []


def _flatten(node: Any) -> dict:
    """Yahoo metadata often arrives as a list of single-key dicts; merge them."""
    if isinstance(node, dict):
        return node
    merged: dict = {}
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                merged.update(item)
    return merged


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Yahoo Fantasy OAuth + roster")
    ap.add_argument("command", choices=["url", "code", "teams", "roster", "sync"])
    ap.add_argument("arg", nargs="?", help="auth code (for `code`) or team_key (for `roster`/`sync`)")
    args = ap.parse_args(argv)
    cfg = load_cfg()

    if args.command == "url":
        print("\n1. Open this URL, sign in, and click Agree:\n")
        print("   " + authorize_url(cfg))
        print("\n2. Your browser will fail to load https://localhost:8000/?code=... "
              "(that's expected).")
        print("3. Copy the `code` value from that URL and run:\n")
        print("   python -m fantasybb.yahoo code <CODE>\n")

    elif args.command == "code":
        if not args.arg:
            raise SystemExit("usage: python -m fantasybb.yahoo code <CODE>")
        exchange_code(cfg, args.arg)
        print("authorized ✅  token cached. Now: python -m fantasybb.yahoo teams")

    elif args.command == "teams":
        for t in get_teams(cfg):
            print(f"  {t['team_key']}  {t['name']}  (season {t['season']})")

    elif args.command == "roster":
        if not args.arg:
            raise SystemExit("usage: python -m fantasybb.yahoo roster <team_key>")
        for p in get_roster(args.arg, cfg):
            print(f"  {p['selected_position'] or '--':4} {p['name']:24} "
                  f"{p['team_abbr'] or '':4} {p['positions'] or ''} "
                  f"{'['+p['status']+']' if p['status'] else ''}")

    elif args.command == "sync":
        if not args.arg:
            raise SystemExit("usage: python -m fantasybb.yahoo sync <team_key>")
        name, total, matched, unmatched = sync_roster(args.arg, cfg)
        print(f"synced '{name}': {matched}/{total} players matched to the DB")
        if unmatched:
            print("  unmatched:", ", ".join(unmatched))

    return 0


if __name__ == "__main__":
    sys.exit(main())
