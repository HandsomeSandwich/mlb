"""Thin client for the public MLB Stats API (statsapi.mlb.com).

No API key required. We keep one Session for connection reuse and add a small
retry so a transient hiccup mid-backfill doesn't abort a long ingest.
"""
from __future__ import annotations

import time
from typing import Any

import requests

BASE = "https://statsapi.mlb.com/api/v1"
SPORT_ID = 1  # MLB


class MLBClient:
    def __init__(self, timeout: float = 30.0, max_retries: int = 3, pause: float = 0.0):
        self.timeout = timeout
        self.max_retries = max_retries
        self.pause = pause  # polite delay between calls during bulk loops
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "fantasy-baseball/1.0"})

    def _get(self, path: str, **params: Any) -> dict:
        url = f"{BASE}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                if self.pause:
                    time.sleep(self.pause)
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - retry any transient failure
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after {self.max_retries} tries: {last_exc}")

    # -- bulk reference data -------------------------------------------------

    def teams(self, season: int) -> list[dict]:
        data = self._get("teams", sportId=SPORT_ID, season=season)
        return data.get("teams", [])

    def schedule(self, season: int, game_type: str = "R") -> list[dict]:
        """Return every game (gamePk, date, teams, venue, status) for a season."""
        data = self._get(
            "schedule",
            sportId=SPORT_ID,
            season=season,
            gameType=game_type,
            fields=(
                "dates,date,games,gamePk,gameDate,gameType,season,venue,name,"
                "status,abstractGameState,teams,home,away,team,id"
            ),
        )
        return [g for day in data.get("dates", []) for g in day.get("games", [])]

    def boxscore(self, game_pk: int) -> dict:
        return self._get(f"game/{game_pk}/boxscore")

    def season_hitting(self, season: int, game_type: str = "R") -> list[dict]:
        return self._season_stats("hitting", season, game_type)

    def season_pitching(self, season: int, game_type: str = "R") -> list[dict]:
        return self._season_stats("pitching", season, game_type)

    def _season_stats(self, group: str, season: int, game_type: str) -> list[dict]:
        """Paginated league-wide season totals for a stat group."""
        out: list[dict] = []
        offset = 0
        page = 250
        while True:
            data = self._get(
                "stats",
                stats="season",
                group=group,
                season=season,
                sportId=SPORT_ID,
                gameType=game_type,
                playerPool="all",
                limit=page,
                offset=offset,
            )
            splits = data.get("stats", [{}])[0].get("splits", []) if data.get("stats") else []
            if not splits:
                break
            out.extend(splits)
            if len(splits) < page:
                break
            offset += page
        return out

    def people(self, person_ids: list[int]) -> list[dict]:
        """Bulk bio lookup. The endpoint accepts a comma-separated id list."""
        if not person_ids:
            return []
        out: list[dict] = []
        for i in range(0, len(person_ids), 100):
            chunk = person_ids[i : i + 100]
            data = self._get("people", personIds=",".join(str(p) for p in chunk))
            out.extend(data.get("people", []))
        return out
