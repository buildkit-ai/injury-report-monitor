"""
Thin wrapper around the Shipp.ai API for game schedule context.

Provides today's schedule so the injury monitor knows which games are
happening and can flag relevant injuries as high-priority.
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SHIPP_BASE_URL = "https://api.shipp.ai/api/v1"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = 2.0


class ShippClient:
    """Client for Shipp.ai schedule and live score data."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("SHIPP_API_KEY")
        if not self.api_key:
            raise ValueError(
                "SHIPP_API_KEY is required. Set it as an environment variable "
                "or pass it to ShippClient(api_key='...'). "
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "injury-report-monitor/1.0",
        })

    def _url(self, endpoint: str) -> str:
        """Build URL with api_key query parameter."""
        sep = "&" if "?" in endpoint else "?"
        return f"{SHIPP_BASE_URL}{endpoint}{sep}api_key={self.api_key}"

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request with retry logic."""
        url = self._url(endpoint)
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(
                        "Rate limited by Shipp API, waiting %d seconds", retry_after
                    )
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                last_error = f"Request timed out after {DEFAULT_TIMEOUT}s"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code < 500:
                    raise
                last_error = f"HTTP error: {e}"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))

        raise RuntimeError(f"Shipp API request failed after {MAX_RETRIES + 1} attempts: {last_error}")

    def get_schedule(self, sport: str, date: Optional[str] = None) -> dict:
        """
        Get the game schedule for a sport.

        Args:
            sport: One of 'nba', 'mlb', 'soccer'
            date: Date string in YYYY-MM-DD format. Defaults to today.

        Returns:
            dict with 'games' list containing scheduled/live/final games.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return self._request("GET", f"/sports/{sport}/schedule", params={"date": date})

    def get_todays_games(self, sport: str) -> list:
        """
        Get today's games for a sport.

        Returns:
            List of game dicts with keys: game_id, home_team, away_team,
            start_time, status.
        """
        try:
            schedule = self.get_schedule(sport)
            return schedule.get("games", [])
        except Exception as e:
            logger.error("Failed to fetch %s schedule: %s", sport, e)
            return []

    def get_all_todays_games(self) -> dict:
        """
        Get today's games for all supported sports.

        Returns:
            dict mapping sport name to list of games.
        """
        all_games = {}
        for sport in ["nba", "mlb", "soccer"]:
            games = self.get_todays_games(sport)
            if games:
                all_games[sport] = games
        return all_games

    def get_live_scores(self, sport: str) -> list:
        """
        Get live scores for currently active games.

        Creates a connection and polls for current state.

        Returns:
            List of live game dicts.
        """
        try:
            filter_map = {
                "nba": "Track all NBA games today with live scores and injury updates",
                "mlb": "Track all MLB games today with live scores and roster transactions",
                "soccer": "Track all soccer matches today with live scores and squad updates",
            }
            connection = self._request("POST", "/connections/create", json={
                "filter_instructions": filter_map.get(sport, f"Track all {sport} games today with live scores"),
            })
            connection_id = connection.get("connection_id")
            if not connection_id:
                logger.error("No connection_id returned for %s", sport)
                return []

            result = self._request("POST", f"/connections/{connection_id}", json={"limit": 50})
            return result.get("data", result.get("events", []))

        except Exception as e:
            logger.error("Failed to get live scores for %s: %s", sport, e)
            return []

    def build_team_game_map(self) -> dict:
        """
        Build a mapping of team names to their game info for today.

        Returns:
            dict mapping lowercase team name -> game info dict.
            Example: {"los angeles lakers": {"opponent": "Warriors", "time": "19:30", ...}}
        """
        team_game_map = {}
        all_games = self.get_all_todays_games()

        for sport, games in all_games.items():
            for game in games:
                home = game.get("home_team", "")
                away = game.get("away_team", "")
                start_time = game.get("start_time", "")
                game_id = game.get("game_id", "")

                if home:
                    team_game_map[home.lower()] = {
                        "sport": sport,
                        "opponent": away,
                        "time": start_time,
                        "game_id": game_id,
                        "home": True,
                    }
                if away:
                    team_game_map[away.lower()] = {
                        "sport": sport,
                        "opponent": home,
                        "time": start_time,
                        "game_id": game_id,
                        "home": False,
                    }

        return team_game_map
