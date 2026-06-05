"""Fantasy baseball database + dashboards.

Data sources:
  * MLB Stats API (statsapi.mlb.com) -- clean game logs & season totals.
  * Statcast / Baseball Savant via pybaseball -- advanced batted-ball metrics.
"""

__all__ = ["db", "mlb_api", "ingest", "queries"]
