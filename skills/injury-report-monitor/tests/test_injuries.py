"""
Comprehensive tests for the Injury Report Monitor skill.

Covers: status normalization, injury record creation, RSS/HTML parsing,
source aggregation, deduplication, change detection, schedule correlation,
error handling, and edge cases.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import requests

# We need to patch the ShippClient before importing InjuryMonitor,
# because ShippClient.__init__ raises ValueError when no API key is set.
# We set the env var for imports, then control behaviour per-test via mocks.
os.environ.setdefault("SHIPP_API_KEY", "test-key-for-unit-tests")

from scripts.injury_sources import (
    _normalize_status,
    _make_injury_record,
    _fetch_html,
    parse_espn_injuries,
    parse_cbs_injuries,
    parse_nba_injury_report,
    parse_mlb_transactions,
    parse_soccer_injuries,
    fetch_all_injuries,
)
from scripts.injury_monitor import InjuryReport, InjuryMonitor
from scripts.shipp_wrapper import ShippClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html_page(body_html: str) -> str:
    """Wrap a body fragment in a minimal HTML document."""
    return f"<html><body>{body_html}</body></html>"


def _mock_response(text="", status_code=200, json_data=None):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=resp
        )
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# 1. Status Normalization
# ---------------------------------------------------------------------------

class TestNormalizeStatus:
    """Tests for _normalize_status mapping."""

    def test_standard_statuses(self):
        assert _normalize_status("Out") == "out"
        assert _normalize_status("Doubtful") == "doubtful"
        assert _normalize_status("Questionable") == "questionable"
        assert _normalize_status("Probable") == "probable"

    def test_abbreviations(self):
        assert _normalize_status("O") == "out"
        assert _normalize_status("D") == "doubtful"
        assert _normalize_status("Q") == "questionable"
        assert _normalize_status("P") == "probable"

    def test_day_to_day_variants(self):
        assert _normalize_status("Day-to-Day") == "day-to-day"
        assert _normalize_status("DTD") == "day-to-day"
        assert _normalize_status("day to day") == "day-to-day"

    def test_injured_list_variants(self):
        assert _normalize_status("10-Day IL") == "il-10"
        assert _normalize_status("15-Day IL") == "il-15"
        assert _normalize_status("60-Day IL") == "il-60"
        assert _normalize_status("IL") == "il-15"
        assert _normalize_status("Injured List") == "il-15"
        assert _normalize_status("10-day injured list") == "il-10"
        assert _normalize_status("15-day injured list") == "il-15"
        assert _normalize_status("60-day injured list") == "il-60"

    def test_suspended_variants(self):
        assert _normalize_status("Suspended") == "suspended"
        assert _normalize_status("SUSP") == "suspended"

    def test_unknown_falls_through(self):
        assert _normalize_status("something weird") == "unknown"
        assert _normalize_status("") == "unknown"
        assert _normalize_status("   ") == "unknown"


# ---------------------------------------------------------------------------
# 2. Injury Record Construction
# ---------------------------------------------------------------------------

class TestMakeInjuryRecord:
    """Tests for _make_injury_record factory."""

    def test_basic_record(self):
        rec = _make_injury_record(
            player="LeBron James",
            team="Los Angeles Lakers",
            status="Out",
            injury="Left ankle sprain",
            source="espn",
            sport="nba",
        )
        assert rec["player"] == "LeBron James"
        assert rec["team"] == "Los Angeles Lakers"
        assert rec["status"] == "out"  # normalized
        assert rec["raw_status"] == "Out"
        assert rec["injury"] == "Left ankle sprain"
        assert rec["source"] == "espn"
        assert rec["sport"] == "nba"
        assert "fetched_at" in rec

    def test_strips_whitespace(self):
        rec = _make_injury_record(
            player="  Stephen Curry  ",
            team="  Warriors  ",
            status="  Q  ",
            injury="  Knee  ",
            source="cbs",
            sport="nba",
        )
        assert rec["player"] == "Stephen Curry"
        assert rec["team"] == "Warriors"
        assert rec["status"] == "questionable"
        assert rec["raw_status"] == "Q"
        assert rec["injury"] == "Knee"

    def test_empty_injury_defaults_to_undisclosed(self):
        rec = _make_injury_record(
            player="Player X",
            team="Team Y",
            status="Out",
            injury="",
            source="espn",
            sport="nba",
        )
        assert rec["injury"] == "Undisclosed"

    def test_none_injury_defaults_to_undisclosed(self):
        rec = _make_injury_record(
            player="Player X",
            team="Team Y",
            status="Out",
            injury=None,
            source="espn",
            sport="nba",
        )
        assert rec["injury"] == "Undisclosed"

    def test_custom_updated_timestamp(self):
        rec = _make_injury_record(
            player="Player X",
            team="Team Y",
            status="Out",
            injury="Knee",
            source="espn",
            sport="nba",
            updated="2026-02-18T12:00:00Z",
        )
        assert rec["updated"] == "2026-02-18T12:00:00Z"


# ---------------------------------------------------------------------------
# 3. HTML Fetching
# ---------------------------------------------------------------------------

class TestFetchHtml:
    """Tests for the _fetch_html helper."""

    @patch("scripts.injury_sources.requests.get")
    def test_successful_fetch(self, mock_get):
        mock_get.return_value = _mock_response(text="<html><body>hi</body></html>")
        soup = _fetch_html("https://example.com")
        assert soup is not None
        assert soup.body.text == "hi"

    @patch("scripts.injury_sources.requests.get")
    def test_returns_none_on_persistent_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("fail")
        soup = _fetch_html("https://example.com", retry=False)
        assert soup is None

    @patch("scripts.injury_sources.requests.get")
    @patch("scripts.injury_sources.time.sleep")
    def test_retries_once_on_failure(self, mock_sleep, mock_get):
        # First call fails, second succeeds
        mock_get.side_effect = [
            requests.exceptions.Timeout("timeout"),
            _mock_response(text="<html><body>ok</body></html>"),
        ]
        soup = _fetch_html("https://example.com", retry=True)
        assert soup is not None
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5)

    @patch("scripts.injury_sources.requests.get")
    def test_returns_none_when_http_error(self, mock_get):
        mock_get.return_value = _mock_response(text="", status_code=500)
        soup = _fetch_html("https://example.com", retry=False)
        assert soup is None


# ---------------------------------------------------------------------------
# 4. ESPN Parsing
# ---------------------------------------------------------------------------

class TestParseEspnInjuries:
    """Tests for parse_espn_injuries with mocked HTML."""

    @patch("scripts.injury_sources._fetch_html")
    def test_parses_table_structure(self, mock_fetch):
        html = _html_page("""
        <div class="ResponsiveTable">
            <h2>Los Angeles Lakers</h2>
            <table>
                <tr><td>LeBron James</td><td>Out</td><td>Ankle</td><td>Feb 18</td></tr>
                <tr><td>Anthony Davis</td><td>Questionable</td><td>Knee</td></tr>
            </table>
        </div>
        """)
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup(html, "html.parser")

        injuries = parse_espn_injuries("nba")
        assert len(injuries) == 2
        assert injuries[0]["player"] == "LeBron James"
        assert injuries[0]["status"] == "out"
        assert injuries[0]["team"] == "Los Angeles Lakers"
        assert injuries[0]["source"] == "espn"
        assert injuries[1]["player"] == "Anthony Davis"
        assert injuries[1]["status"] == "questionable"

    @patch("scripts.injury_sources._fetch_html")
    def test_returns_empty_on_none_soup(self, mock_fetch):
        mock_fetch.return_value = None
        injuries = parse_espn_injuries("nba")
        assert injuries == []

    def test_returns_empty_for_unknown_sport(self):
        injuries = parse_espn_injuries("cricket")
        assert injuries == []

    @patch("scripts.injury_sources._fetch_html")
    def test_skips_header_rows(self, mock_fetch):
        html = _html_page("""
        <div class="ResponsiveTable">
            <h2>Team</h2>
            <table>
                <tr><td>Name</td><td>Status</td><td>Injury</td></tr>
                <tr><td>Player A</td><td>Out</td><td>Knee</td></tr>
            </table>
        </div>
        """)
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup(html, "html.parser")

        injuries = parse_espn_injuries("nba")
        assert len(injuries) == 1
        assert injuries[0]["player"] == "Player A"

    @patch("scripts.injury_sources._fetch_html")
    def test_fallback_flat_table_parsing(self, mock_fetch):
        """When no ResponsiveTable divs are found, falls back to flat tables."""
        html = _html_page("""
        <h3>Boston Celtics</h3>
        <table>
            <tr><td>Jaylen Brown</td><td>DTD</td><td>Hamstring</td></tr>
        </table>
        """)
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup(html, "html.parser")

        injuries = parse_espn_injuries("nba")
        assert len(injuries) == 1
        assert injuries[0]["player"] == "Jaylen Brown"
        assert injuries[0]["status"] == "day-to-day"
        assert injuries[0]["team"] == "Boston Celtics"


# ---------------------------------------------------------------------------
# 5. CBS Parsing
# ---------------------------------------------------------------------------

class TestParseCbsInjuries:
    """Tests for parse_cbs_injuries."""

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources._fetch_html")
    def test_parses_5_column_layout(self, mock_fetch, mock_sleep):
        html = _html_page("""
        <h4>Golden State Warriors</h4>
        <table>
            <tr><td>Curry</td><td>PG</td><td>Feb 17</td><td>Knee</td><td>Questionable</td></tr>
        </table>
        """)
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup(html, "html.parser")

        injuries = parse_cbs_injuries("nba")
        assert len(injuries) == 1
        assert injuries[0]["player"] == "Curry"
        assert injuries[0]["status"] == "questionable"
        assert injuries[0]["injury"] == "Knee"
        assert injuries[0]["source"] == "cbs"

    def test_returns_empty_for_unsupported_sport(self):
        injuries = parse_cbs_injuries("soccer")
        assert injuries == []

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources._fetch_html")
    def test_returns_empty_on_none_soup(self, mock_fetch, mock_sleep):
        mock_fetch.return_value = None
        injuries = parse_cbs_injuries("nba")
        assert injuries == []


# ---------------------------------------------------------------------------
# 6. MLB Transaction Parsing
# ---------------------------------------------------------------------------

class TestParseMlbTransactions:
    """Tests for parse_mlb_transactions."""

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources.requests.get")
    def test_parses_il_placement(self, mock_get, mock_sleep):
        mock_get.return_value = _mock_response(json_data={
            "transactions": [
                {
                    "description": "Los Angeles Dodgers placed LHP Clayton Kershaw on the 15-Day IL with left elbow inflammation.",
                    "typeDesc": "Placed on 15-Day IL",
                    "player": {"fullName": "Clayton Kershaw"},
                    "team": {"name": "Los Angeles Dodgers"},
                    "effectiveDate": "2026-02-18",
                }
            ]
        })

        injuries = parse_mlb_transactions()
        assert len(injuries) == 1
        assert injuries[0]["player"] == "Clayton Kershaw"
        assert injuries[0]["status"] == "il-15"
        assert injuries[0]["team"] == "Los Angeles Dodgers"
        assert injuries[0]["source"] == "mlb_transactions"

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources.requests.get")
    def test_parses_activation(self, mock_get, mock_sleep):
        mock_get.return_value = _mock_response(json_data={
            "transactions": [
                {
                    "description": "Activated from 10-Day IL",
                    "typeDesc": "Activated from 10-Day IL",
                    "player": {"fullName": "Mike Trout"},
                    "team": {"name": "Los Angeles Angels"},
                    "effectiveDate": "2026-02-18",
                }
            ]
        })

        injuries = parse_mlb_transactions()
        assert len(injuries) == 1
        assert injuries[0]["status"] == "active"

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources.requests.get")
    def test_skips_non_il_transactions(self, mock_get, mock_sleep):
        mock_get.return_value = _mock_response(json_data={
            "transactions": [
                {
                    "description": "Traded to the Yankees",
                    "typeDesc": "Trade",
                    "player": {"fullName": "Some Player"},
                    "team": {"name": "New York Yankees"},
                    "effectiveDate": "2026-02-18",
                }
            ]
        })

        injuries = parse_mlb_transactions()
        assert len(injuries) == 0

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources.requests.get")
    def test_handles_request_failure(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("fail")
        injuries = parse_mlb_transactions()
        assert injuries == []

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources.requests.get")
    def test_extracts_injury_description_from_with_clause(self, mock_get, mock_sleep):
        mock_get.return_value = _mock_response(json_data={
            "transactions": [
                {
                    "description": "Team placed Player on 15-Day IL with right shoulder inflammation.",
                    "typeDesc": "Placed on 15-Day IL",
                    "player": {"fullName": "Player X"},
                    "team": {"name": "Team A"},
                    "effectiveDate": "2026-02-18",
                }
            ]
        })

        injuries = parse_mlb_transactions()
        assert len(injuries) == 1
        assert injuries[0]["injury"] == "right shoulder inflammation"


# ---------------------------------------------------------------------------
# 7. Soccer Parsing
# ---------------------------------------------------------------------------

class TestParseSoccerInjuries:
    """Tests for parse_soccer_injuries."""

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources._fetch_html")
    def test_parses_premier_league_injuries(self, mock_fetch, mock_sleep):
        html = _html_page("""
        <h2>Arsenal</h2>
        <table>
            <tr><td>Bukayo Saka</td><td>Injured</td><td>Hamstring</td><td>Mar 2026</td></tr>
        </table>
        """)
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup(html, "html.parser")

        injuries = parse_soccer_injuries("premier-league")
        assert len(injuries) == 1
        assert injuries[0]["player"] == "Bukayo Saka"
        assert injuries[0]["status"] == "injured"
        assert injuries[0]["league"] == "premier-league"
        assert injuries[0]["sport"] == "soccer"
        assert injuries[0].get("expected_return") == "Mar 2026"

    @patch("scripts.injury_sources.time.sleep")
    @patch("scripts.injury_sources._fetch_html")
    def test_unknown_league_falls_back(self, mock_fetch, mock_sleep):
        """Unknown league should still attempt a fetch using the fallback URL."""
        mock_fetch.return_value = None
        injuries = parse_soccer_injuries("bundesliga")
        assert injuries == []
        # Verify it was called (fallback URL used)
        mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# 8. fetch_all_injuries Aggregation
# ---------------------------------------------------------------------------

class TestFetchAllInjuries:
    """Tests for the aggregate fetch_all_injuries function."""

    @patch("scripts.injury_sources.parse_soccer_injuries", return_value=[])
    @patch("scripts.injury_sources.parse_mlb_transactions", return_value=[])
    @patch("scripts.injury_sources.parse_cbs_injuries", return_value=[])
    @patch("scripts.injury_sources.parse_nba_injury_report", return_value=[])
    @patch("scripts.injury_sources.parse_espn_injuries", return_value=[])
    def test_returns_structure_with_all_sports(self, *mocks):
        result = fetch_all_injuries(sports=["nba", "mlb", "soccer"])
        assert "injuries" in result
        assert "sources" in result
        assert "fetched_at" in result
        assert "nba" in result["injuries"]
        assert "mlb" in result["injuries"]
        assert "soccer" in result["injuries"]

    @patch("scripts.injury_sources.parse_espn_injuries")
    @patch("scripts.injury_sources.parse_cbs_injuries")
    @patch("scripts.injury_sources.parse_nba_injury_report")
    def test_source_failure_isolated(self, mock_nba, mock_cbs, mock_espn):
        """One source raising an exception should not break others."""
        mock_espn.return_value = [
            _make_injury_record("A", "Team", "Out", "Knee", "espn", "nba")
        ]
        mock_cbs.side_effect = RuntimeError("CBS is down")
        mock_nba.return_value = []

        result = fetch_all_injuries(sports=["nba"])
        # ESPN data still present
        assert len(result["injuries"]["nba"]) == 1
        # CBS marked as error
        assert result["sources"]["cbs_nba"]["status"] == "error"
        # ESPN marked as ok
        assert result["sources"]["espn_nba"]["status"] == "ok"

    @patch("scripts.injury_sources.parse_espn_injuries", return_value=[])
    @patch("scripts.injury_sources.parse_cbs_injuries", return_value=[])
    @patch("scripts.injury_sources.parse_nba_injury_report", return_value=[])
    def test_single_sport_filter(self, *mocks):
        result = fetch_all_injuries(sports=["nba"])
        assert "nba" in result["injuries"]
        assert "mlb" not in result["injuries"]
        assert "soccer" not in result["injuries"]


# ---------------------------------------------------------------------------
# 9. Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Tests for InjuryMonitor._deduplicate_injuries."""

    def _make_monitor(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor.state_path = MagicMock()
            monitor._team_game_map = None
        return monitor

    def test_keeps_higher_priority_source(self):
        monitor = self._make_monitor()
        injuries = [
            {"player": "LeBron James", "team": "Lakers", "source": "cbs",
             "status": "out", "updated": "2026-02-18T10:00:00Z"},
            {"player": "LeBron James", "team": "Lakers", "source": "nba_official",
             "status": "questionable", "updated": "2026-02-18T10:00:00Z"},
        ]
        deduped = monitor._deduplicate_injuries(injuries)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "nba_official"

    def test_prefers_more_recent_same_priority(self):
        monitor = self._make_monitor()
        injuries = [
            {"player": "LeBron James", "team": "Lakers", "source": "espn",
             "status": "out", "updated": "2026-02-18T08:00:00Z"},
            {"player": "LeBron James", "team": "Lakers", "source": "espn",
             "status": "questionable", "updated": "2026-02-18T12:00:00Z"},
        ]
        deduped = monitor._deduplicate_injuries(injuries)
        assert len(deduped) == 1
        assert deduped[0]["status"] == "questionable"  # more recent

    def test_different_players_not_deduped(self):
        monitor = self._make_monitor()
        injuries = [
            {"player": "LeBron James", "team": "Lakers", "source": "espn",
             "status": "out", "updated": ""},
            {"player": "Anthony Davis", "team": "Lakers", "source": "espn",
             "status": "questionable", "updated": ""},
        ]
        deduped = monitor._deduplicate_injuries(injuries)
        assert len(deduped) == 2

    def test_case_insensitive_dedup(self):
        monitor = self._make_monitor()
        injuries = [
            {"player": "LEBRON JAMES", "team": "LAKERS", "source": "espn",
             "status": "out", "updated": "2026-02-18T08:00:00Z"},
            {"player": "lebron james", "team": "lakers", "source": "cbs",
             "status": "questionable", "updated": "2026-02-18T12:00:00Z"},
        ]
        deduped = monitor._deduplicate_injuries(injuries)
        assert len(deduped) == 1

    def test_unknown_source_gets_lowest_priority(self):
        monitor = self._make_monitor()
        injuries = [
            {"player": "Player A", "team": "Team A", "source": "random_blog",
             "status": "out", "updated": "2026-02-18T10:00:00Z"},
            {"player": "Player A", "team": "Team A", "source": "cbs",
             "status": "questionable", "updated": "2026-02-18T10:00:00Z"},
        ]
        deduped = monitor._deduplicate_injuries(injuries)
        assert len(deduped) == 1
        assert deduped[0]["source"] == "cbs"


# ---------------------------------------------------------------------------
# 10. Change Detection
# ---------------------------------------------------------------------------

class TestChangeDetection:
    """Tests for InjuryMonitor._detect_changes."""

    def _make_monitor(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor.state_path = MagicMock()
            monitor._team_game_map = None
        return monitor

    def test_detects_status_change(self):
        monitor = self._make_monitor()
        current = [
            {"player": "LeBron James", "team": "Lakers", "status": "out"},
        ]
        previous = {
            "lebron james|lakers": {"status": "questionable"},
        }
        result = monitor._detect_changes(current, previous)
        assert result[0]["status_changed"] is True
        assert result[0]["previous_status"] == "questionable"

    def test_no_change_when_status_same(self):
        monitor = self._make_monitor()
        current = [
            {"player": "LeBron James", "team": "Lakers", "status": "out"},
        ]
        previous = {
            "lebron james|lakers": {"status": "out"},
        }
        result = monitor._detect_changes(current, previous)
        assert result[0]["status_changed"] is False
        assert result[0]["previous_status"] is None

    def test_new_player_no_change(self):
        monitor = self._make_monitor()
        current = [
            {"player": "New Player", "team": "New Team", "status": "out"},
        ]
        result = monitor._detect_changes(current, {})
        assert result[0]["status_changed"] is False
        assert result[0]["previous_status"] is None


# ---------------------------------------------------------------------------
# 11. Schedule Correlation (Game Context)
# ---------------------------------------------------------------------------

class TestAnnotateWithGameContext:
    """Tests for InjuryMonitor._annotate_with_game_context."""

    def _make_monitor(self, team_game_map):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor.state_path = MagicMock()
            monitor._team_game_map = team_game_map
        return monitor

    def test_exact_match(self):
        monitor = self._make_monitor({
            "los angeles lakers": {
                "opponent": "Warriors",
                "time": "19:30",
                "game_id": "g1",
            }
        })
        injuries = [{"player": "LeBron", "team": "Los Angeles Lakers"}]
        result = monitor._annotate_with_game_context(injuries)
        assert result[0]["game_today"] is not None
        assert result[0]["game_today"]["opponent"] == "Warriors"

    def test_partial_match(self):
        monitor = self._make_monitor({
            "los angeles lakers": {
                "opponent": "Warriors",
                "time": "19:30",
                "game_id": "g1",
            }
        })
        injuries = [{"player": "LeBron", "team": "Lakers"}]
        result = monitor._annotate_with_game_context(injuries)
        # Should match via partial/nickname matching
        assert result[0]["game_today"] is not None

    def test_no_game_today(self):
        monitor = self._make_monitor({})
        injuries = [{"player": "LeBron", "team": "Lakers"}]
        result = monitor._annotate_with_game_context(injuries)
        assert result[0]["game_today"] is None


# ---------------------------------------------------------------------------
# 12. InjuryReport Formatting
# ---------------------------------------------------------------------------

class TestInjuryReport:
    """Tests for the InjuryReport class."""

    def test_to_json(self):
        data = {"generated_at": "2026-02-18T00:00:00Z", "sports": {}}
        report = InjuryReport(data)
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["generated_at"] == "2026-02-18T00:00:00Z"

    def test_to_dict(self):
        data = {"sports": {}, "source_status": {}}
        report = InjuryReport(data)
        assert report.to_dict() is data

    def test_summary_with_status_changes(self):
        data = {
            "sports": {
                "nba": {
                    "injuries": [
                        {
                            "player": "LeBron James",
                            "team": "Lakers",
                            "status": "out",
                            "status_changed": True,
                            "previous_status": "questionable",
                            "injury": "Ankle",
                            "game_today": {
                                "opponent": "Warriors",
                                "time": "19:30",
                            },
                        }
                    ],
                    "games_today": 5,
                    "affected_games": 1,
                }
            },
            "source_status": {
                "espn_nba": {"status": "ok"},
            },
        }
        report = InjuryReport(data)
        summary = report.summary()
        assert "STATUS CHANGES" in summary
        assert "LeBron James" in summary
        assert "questionable -> out" in summary
        assert "GAME TODAY" in summary
        assert "Sources: 1 succeeded, 0 failed" in summary

    def test_summary_with_failed_sources(self):
        data = {
            "sports": {},
            "source_status": {
                "espn_nba": {"status": "ok"},
                "cbs_nba": {"status": "error", "error": "timeout"},
            },
        }
        report = InjuryReport(data)
        summary = report.summary()
        assert "1 succeeded, 1 failed" in summary
        assert "cbs_nba" in summary

    def test_summary_caps_other_injuries_at_20(self):
        """If there are more than 20 non-game-day, non-changed injuries, cap display."""
        injuries = []
        for i in range(25):
            injuries.append({
                "player": f"Player {i}",
                "team": f"Team {i}",
                "status": "out",
                "status_changed": False,
                "game_today": None,
                "injury": "Knee",
            })
        data = {
            "sports": {
                "nba": {
                    "injuries": injuries,
                    "games_today": 0,
                    "affected_games": 0,
                }
            },
            "source_status": {},
        }
        report = InjuryReport(data)
        summary = report.summary()
        assert "and 5 more" in summary


# ---------------------------------------------------------------------------
# 13. State Persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """Tests for _load_state and _save_state."""

    def test_load_state_returns_empty_when_no_file(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor._team_game_map = None
            with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as f:
                # File is deleted on close, so path won't exist
                from pathlib import Path
                monitor.state_path = Path(f.name + "_nonexistent")
            assert monitor._load_state() == {}

    def test_save_and_load_state_roundtrip(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor._team_game_map = None
            with tempfile.TemporaryDirectory() as tmpdir:
                from pathlib import Path
                monitor.state_path = Path(tmpdir) / "state.json"
                state = {"player|team": {"status": "out", "injury": "Knee"}}
                monitor._save_state(state)
                loaded = monitor._load_state()
                assert loaded == state

    def test_load_state_handles_corrupt_json(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor._team_game_map = None
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                f.write("NOT VALID JSON {{{")
                f.flush()
                from pathlib import Path
                monitor.state_path = Path(f.name)
            try:
                result = monitor._load_state()
                assert result == {}
            finally:
                os.unlink(f.name)


# ---------------------------------------------------------------------------
# 14. Build Current State
# ---------------------------------------------------------------------------

class TestBuildCurrentState:
    """Tests for InjuryMonitor._build_current_state."""

    def test_builds_state_from_injuries(self):
        with patch.object(ShippClient, "__init__", lambda self, **kw: None):
            monitor = InjuryMonitor.__new__(InjuryMonitor)
            monitor.shipp = MagicMock()
            monitor.state_path = MagicMock()
            monitor._team_game_map = None

        injuries_by_sport = {
            "nba": [
                {"player": "LeBron James", "team": "Lakers", "status": "out", "injury": "Ankle"},
                {"player": "Anthony Davis", "team": "Lakers", "status": "questionable", "injury": "Knee"},
            ]
        }
        state = monitor._build_current_state(injuries_by_sport)
        assert "lebron james|lakers" in state
        assert state["lebron james|lakers"]["status"] == "out"
        assert "anthony davis|lakers" in state
        assert state["anthony davis|lakers"]["status"] == "questionable"


# ---------------------------------------------------------------------------
# 15. ShippClient
# ---------------------------------------------------------------------------

class TestShippClient:
    """Tests for the ShippClient wrapper."""

    def test_raises_without_api_key(self):
        # Temporarily remove the env var
        with patch.dict(os.environ, {}, clear=True):
            # Also explicitly remove SHIPP_API_KEY if present
            env_copy = os.environ.copy()
            for k in list(env_copy.keys()):
                if k == "SHIPP_API_KEY":
                    del os.environ[k]
            with pytest.raises(ValueError, match="SHIPP_API_KEY is required"):
                ShippClient()

    def test_accepts_explicit_api_key(self):
        client = ShippClient(api_key="my-test-key")
        assert client.api_key == "my-test-key"

    @patch("scripts.shipp_wrapper.requests.Session")
    def test_build_team_game_map(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        client = ShippClient(api_key="test-key")
        client.session = mock_session

        # Mock get_all_todays_games
        with patch.object(client, "get_all_todays_games") as mock_games:
            mock_games.return_value = {
                "nba": [
                    {
                        "game_id": "g1",
                        "home_team": "Los Angeles Lakers",
                        "away_team": "Golden State Warriors",
                        "start_time": "19:30",
                        "status": "scheduled",
                    }
                ]
            }
            team_map = client.build_team_game_map()
            assert "los angeles lakers" in team_map
            assert "golden state warriors" in team_map
            assert team_map["los angeles lakers"]["opponent"] == "Golden State Warriors"
            assert team_map["golden state warriors"]["opponent"] == "Los Angeles Lakers"
            assert team_map["los angeles lakers"]["home"] is True
            assert team_map["golden state warriors"]["home"] is False


# ---------------------------------------------------------------------------
# 16. Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge-case and boundary tests."""

    def test_normalize_status_with_extra_whitespace(self):
        assert _normalize_status("  out  ") == "out"
        assert _normalize_status("\tquestionable\n") == "questionable"

    def test_empty_transactions_list(self):
        """MLb parser handles empty transactions array."""
        with patch("scripts.injury_sources.requests.get") as mock_get, \
             patch("scripts.injury_sources.time.sleep"):
            mock_get.return_value = _mock_response(json_data={"transactions": []})
            injuries = parse_mlb_transactions()
            assert injuries == []

    def test_mlb_60_day_il(self):
        """60-Day IL placement should be detected correctly."""
        with patch("scripts.injury_sources.requests.get") as mock_get, \
             patch("scripts.injury_sources.time.sleep"):
            mock_get.return_value = _mock_response(json_data={
                "transactions": [
                    {
                        "description": "Transferred to 60-Day IL due to torn UCL",
                        "typeDesc": "Transferred to 60-Day IL",
                        "player": {"fullName": "Player Z"},
                        "team": {"name": "Team Z"},
                        "effectiveDate": "2026-02-18",
                    }
                ]
            })
            injuries = parse_mlb_transactions()
            assert len(injuries) == 1
            assert injuries[0]["status"] == "il-60"

    @patch("scripts.injury_sources._fetch_html")
    def test_espn_empty_page(self, mock_fetch):
        """An empty page (no tables) should return empty list without error."""
        from bs4 import BeautifulSoup
        mock_fetch.return_value = BeautifulSoup("<html><body></body></html>", "html.parser")
        injuries = parse_espn_injuries("nba")
        assert injuries == []

    @patch("scripts.injury_sources._fetch_html")
    def test_malformed_html_does_not_crash(self, mock_fetch):
        """Badly formed HTML should still be handled without crashing."""
        from bs4 import BeautifulSoup
        # BeautifulSoup is quite forgiving; verify we don't crash
        mock_fetch.return_value = BeautifulSoup(
            "<div><table><tr><td>unclosed", "html.parser"
        )
        injuries = parse_espn_injuries("nba")
        # Might or might not parse anything, but should not raise
        assert isinstance(injuries, list)
