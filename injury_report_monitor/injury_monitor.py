"""
Injury Report Monitor — Main orchestrator.

Aggregates injury data from multiple public sources, cross-references with
today's game schedule, tracks status changes, and produces structured reports.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .injury_sources import fetch_all_injuries
from .shipp_wrapper import ShippClient

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = os.path.expanduser("~/.injury_monitor_state.json")


class InjuryReport:
    """Container for a complete injury report with formatting helpers."""

    def __init__(self, data: dict):
        self.data = data
        self.generated_at = data.get("generated_at", datetime.now(timezone.utc).isoformat())

    def to_json(self, indent: int = 2) -> str:
        """Return the full report as formatted JSON."""
        return json.dumps(self.data, indent=indent, default=str)

    def to_dict(self) -> dict:
        """Return the report as a dict."""
        return self.data

    def summary(self) -> str:
        """Return a human-readable summary of the injury report."""
        lines = []
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        lines.append(f"=== INJURY REPORT -- {date_str} ===")
        lines.append("")

        sports = self.data.get("sports", {})
        for sport, sport_data in sports.items():
            injuries = sport_data.get("injuries", [])
            games_today = sport_data.get("games_today", 0)
            affected_games = sport_data.get("affected_games", 0)

            lines.append(
                f"--- {sport.upper()} ({len(injuries)} injuries, "
                f"{affected_games} of {games_today} games affected) ---"
            )
            lines.append("")

            # Status changes first (most important)
            changes = [i for i in injuries if i.get("status_changed")]
            if changes:
                lines.append("** STATUS CHANGES **")
                for inj in changes:
                    old = inj.get("previous_status", "?")
                    new = inj.get("status", "?")
                    lines.append(
                        f"  {inj['player']} ({inj['team']}) -- "
                        f"{old} -> {new} -- {inj.get('injury', 'Undisclosed')}"
                    )
                    game = inj.get("game_today")
                    if game:
                        opp = game.get("opponent", "TBD")
                        time_str = game.get("time", "TBD")
                        lines.append(f"    GAME TODAY: vs {opp} at {time_str}")
                lines.append("")

            # All injuries for teams playing today
            today_injuries = [i for i in injuries if i.get("game_today")]
            if today_injuries:
                lines.append("** INJURIES (Teams Playing Today) **")
                # Group by team
                teams_seen = {}
                for inj in today_injuries:
                    team = inj["team"]
                    if team not in teams_seen:
                        teams_seen[team] = []
                    teams_seen[team].append(inj)

                for team, team_injuries in sorted(teams_seen.items()):
                    game = team_injuries[0].get("game_today", {})
                    opp = game.get("opponent", "TBD")
                    time_str = game.get("time", "TBD")
                    lines.append(f"  {team} (vs {opp} at {time_str}):")
                    for inj in team_injuries:
                        status = inj.get("status", "Unknown").upper()
                        lines.append(
                            f"    [{status}] {inj['player']} -- {inj.get('injury', 'Undisclosed')}"
                        )
                lines.append("")

            # Remaining injuries (teams not playing today)
            other_injuries = [i for i in injuries if not i.get("game_today") and not i.get("status_changed")]
            if other_injuries:
                lines.append(f"** OTHER INJURIES ({len(other_injuries)} players) **")
                for inj in other_injuries[:20]:  # cap at 20 for readability
                    status = inj.get("status", "Unknown").upper()
                    lines.append(
                        f"  [{status}] {inj['player']} ({inj['team']}) -- "
                        f"{inj.get('injury', 'Undisclosed')}"
                    )
                if len(other_injuries) > 20:
                    lines.append(f"  ... and {len(other_injuries) - 20} more")
                lines.append("")

        # Source status
        sources = self.data.get("source_status", {})
        ok_sources = [s for s, v in sources.items() if v.get("status") == "ok"]
        err_sources = [s for s, v in sources.items() if v.get("status") == "error"]
        lines.append(f"Sources: {len(ok_sources)} succeeded, {len(err_sources)} failed")
        if err_sources:
            lines.append(f"  Failed: {', '.join(err_sources)}")

        return "\n".join(lines)


class InjuryMonitor:
    """
    Main injury monitoring orchestrator.

    Fetches injury data from multiple sources, cross-references with today's
    game schedule, detects status changes, and produces reports.
    """

    def __init__(
        self,
        shipp_api_key: Optional[str] = None,
        state_path: Optional[str] = None,
    ):
        """
        Initialize the injury monitor.

        Args:
            shipp_api_key: Shipp API key for game schedule data.
                          Falls back to SHIPP_API_KEY env var.
            state_path: Path to store injury state for change detection.
                       Falls back to INJURY_STATE_PATH env var or default.
        """
        self.shipp = ShippClient(api_key=shipp_api_key)
        self.state_path = Path(
            state_path or os.environ.get("INJURY_STATE_PATH", DEFAULT_STATE_PATH)
        )
        self._team_game_map = None

    def _load_state(self) -> dict:
        """Load last-known injury states from disk."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load state from %s: %s", self.state_path, e)
        return {}

    def _save_state(self, state: dict):
        """Save current injury states to disk."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except IOError as e:
            logger.error("Failed to save state to %s: %s", self.state_path, e)

    def _get_team_game_map(self) -> dict:
        """Get or build the team-to-game mapping for today."""
        if self._team_game_map is None:
            try:
                self._team_game_map = self.shipp.build_team_game_map()
            except Exception as e:
                logger.error("Failed to build team game map: %s", e)
                self._team_game_map = {}
        return self._team_game_map

    def _deduplicate_injuries(self, injuries: list) -> list:
        """
        Deduplicate injuries from multiple sources.

        When the same player appears in multiple sources, keep the record
        with the most recent update time. If tied, prefer more authoritative
        sources (official > ESPN > CBS).
        """
        source_priority = {
            "nba_official": 3,
            "mlb_transactions": 3,
            "espn": 2,
            "cbs": 1,
        }

        # Key by lowercase player name + team
        seen = {}
        for inj in injuries:
            key = f"{inj['player'].lower()}|{inj['team'].lower()}"
            existing = seen.get(key)
            if existing is None:
                seen[key] = inj
            else:
                # Prefer higher-priority source
                existing_priority = source_priority.get(existing["source"], 0)
                new_priority = source_priority.get(inj["source"], 0)
                if new_priority > existing_priority:
                    seen[key] = inj
                elif new_priority == existing_priority:
                    # Prefer more recent update
                    if inj.get("updated", "") > existing.get("updated", ""):
                        seen[key] = inj

        return list(seen.values())

    def _annotate_with_game_context(self, injuries: list) -> list:
        """Add game_today info to injuries whose teams play today."""
        team_game_map = self._get_team_game_map()

        for inj in injuries:
            team_lower = inj["team"].lower()
            game = team_game_map.get(team_lower)

            if game is None:
                # Try partial matching (e.g., "Lakers" matches "Los Angeles Lakers")
                for team_key, game_info in team_game_map.items():
                    if team_lower in team_key or team_key in team_lower:
                        game = game_info
                        break
                    # Also try matching just the last word (team nickname)
                    team_parts = team_lower.split()
                    key_parts = team_key.split()
                    if team_parts and key_parts and team_parts[-1] == key_parts[-1]:
                        game = game_info
                        break

            if game:
                inj["game_today"] = {
                    "opponent": game["opponent"],
                    "time": game["time"],
                    "game_id": game["game_id"],
                }
            else:
                inj["game_today"] = None

        return injuries

    def _detect_changes(self, current_injuries: list, previous_state: dict) -> list:
        """
        Detect status changes compared to the previous state.

        Annotates each injury record with:
        - status_changed (bool)
        - previous_status (str or None)
        """
        for inj in current_injuries:
            key = f"{inj['player'].lower()}|{inj['team'].lower()}"
            prev = previous_state.get(key)
            if prev and prev.get("status") != inj["status"]:
                inj["status_changed"] = True
                inj["previous_status"] = prev["status"]
            else:
                inj["status_changed"] = False
                inj["previous_status"] = None

        return current_injuries

    def _build_current_state(self, injuries_by_sport: dict) -> dict:
        """Build a state dict from current injuries for persistence."""
        state = {}
        for sport, injuries in injuries_by_sport.items():
            for inj in injuries:
                key = f"{inj['player'].lower()}|{inj['team'].lower()}"
                state[key] = {
                    "status": inj["status"],
                    "injury": inj.get("injury", ""),
                    "sport": sport,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
        return state

    def get_full_report(self, sports: Optional[list] = None) -> InjuryReport:
        """
        Generate a complete injury report for all requested sports.

        Args:
            sports: List of sports ('nba', 'mlb', 'soccer'). Defaults to all.

        Returns:
            InjuryReport object with full data, summary, and JSON output.
        """
        if sports is None:
            sports = ["nba", "mlb", "soccer"]

        # Load previous state for change detection
        previous_state = self._load_state()

        # Fetch all injuries from all sources
        raw_data = fetch_all_injuries(sports=sports)

        # Process each sport
        report_sports = {}
        all_injuries_for_state = {}

        for sport in sports:
            sport_injuries = raw_data["injuries"].get(sport, [])

            # Deduplicate across sources
            deduped = self._deduplicate_injuries(sport_injuries)

            # Add game context
            annotated = self._annotate_with_game_context(deduped)

            # Detect changes
            with_changes = self._detect_changes(annotated, previous_state)

            # Sort: status changes first, then game-today injuries, then rest
            with_changes.sort(
                key=lambda x: (
                    not x.get("status_changed", False),
                    x.get("game_today") is None,
                    x.get("player", ""),
                )
            )

            # Count affected games
            games_today = len([
                g for g in self.shipp.get_todays_games(sport)
            ]) if sport in sports else 0

            affected_teams = set()
            for inj in with_changes:
                if inj.get("game_today"):
                    affected_teams.add(inj["team"].lower())
            affected_games = len(affected_teams) // 2 + len(affected_teams) % 2

            report_sports[sport] = {
                "injuries": with_changes,
                "total_injuries": len(with_changes),
                "games_today": games_today,
                "affected_games": affected_games,
                "status_changes": len([i for i in with_changes if i.get("status_changed")]),
            }

            all_injuries_for_state[sport] = with_changes

        # Save current state for next run
        new_state = self._build_current_state(all_injuries_for_state)
        merged_state = {**previous_state, **new_state}
        self._save_state(merged_state)

        report_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sports": report_sports,
            "source_status": raw_data["sources"],
        }

        return InjuryReport(report_data)

    def get_report(self, sport: str) -> InjuryReport:
        """
        Generate an injury report for a single sport.

        Args:
            sport: One of 'nba', 'mlb', 'soccer'.

        Returns:
            InjuryReport for the requested sport.
        """
        return self.get_full_report(sports=[sport])

    def get_status_changes(self, sports: Optional[list] = None) -> list:
        """
        Get only the injuries that have changed status since last check.

        Args:
            sports: List of sports to check. Defaults to all.

        Returns:
            List of injury dicts that have status_changed=True.
        """
        report = self.get_full_report(sports=sports)
        changes = []
        for sport_data in report.data.get("sports", {}).values():
            for inj in sport_data.get("injuries", []):
                if inj.get("status_changed"):
                    changes.append(inj)
        return changes

    def get_today_impact(self, sports: Optional[list] = None) -> list:
        """
        Get only injuries affecting today's games.

        Args:
            sports: List of sports to check. Defaults to all.

        Returns:
            List of injury dicts where game_today is not None.
        """
        report = self.get_full_report(sports=sports)
        impact = []
        for sport_data in report.data.get("sports", {}).values():
            for inj in sport_data.get("injuries", []):
                if inj.get("game_today"):
                    impact.append(inj)
        return impact


def main():
    """CLI entry point for the injury monitor."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Injury Report Monitor -- Aggregate injury data across sports"
    )
    parser.add_argument(
        "--sport",
        choices=["nba", "mlb", "soccer", "all"],
        default="all",
        help="Sport to monitor (default: all)",
    )
    parser.add_argument(
        "--changes-only",
        action="store_true",
        help="Show only status changes since last check",
    )
    parser.add_argument(
        "--today-only",
        action="store_true",
        help="Show only injuries affecting today's games",
    )
    parser.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="Output format (default: summary)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sports = None if args.sport == "all" else [args.sport]
    monitor = InjuryMonitor()

    if args.changes_only:
        changes = monitor.get_status_changes(sports=sports)
        if args.format == "json":
            print(json.dumps(changes, indent=2, default=str))
        else:
            if not changes:
                print("No status changes detected since last check.")
            else:
                for c in changes:
                    game_str = ""
                    if c.get("game_today"):
                        game_str = f" ** GAME TODAY vs {c['game_today']['opponent']} **"
                    print(
                        f"{c['player']} ({c['team']}): "
                        f"{c.get('previous_status', '?')} -> {c['status']} "
                        f"-- {c.get('injury', 'Undisclosed')}{game_str}"
                    )
    elif args.today_only:
        impact = monitor.get_today_impact(sports=sports)
        if args.format == "json":
            print(json.dumps(impact, indent=2, default=str))
        else:
            if not impact:
                print("No injuries affecting today's games.")
            else:
                for inj in impact:
                    status = inj.get("status", "Unknown").upper()
                    game = inj.get("game_today", {})
                    print(
                        f"[{status}] {inj['player']} ({inj['team']}) "
                        f"-- {inj.get('injury', 'Undisclosed')} "
                        f"-- vs {game.get('opponent', 'TBD')} at {game.get('time', 'TBD')}"
                    )
    else:
        report = monitor.get_full_report(sports=sports)
        if args.format == "json":
            print(report.to_json())
        else:
            print(report.summary())


if __name__ == "__main__":
    main()
