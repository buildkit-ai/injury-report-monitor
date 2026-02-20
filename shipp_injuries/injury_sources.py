"""
Individual source parsers for injury data.

Each parser fetches from a single public source, extracts structured injury
information, and returns a standardized list of injury records. Parsers are
independent -- one source failing does not affect others.
"""

import logging
import time
from typing import Optional
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
POLITE_DELAY = 2.0  # seconds between requests to the same domain

# Standard headers to mimic a normal browser request
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ESPN injury page URLs by sport
ESPN_INJURY_URLS = {
    "nba": "https://www.espn.com/nba/injuries",
    "mlb": "https://www.espn.com/mlb/injuries",
    "soccer": "https://www.espn.com/soccer/injuries",
}

# CBS Sports injury page URLs
CBS_INJURY_URLS = {
    "nba": "https://www.cbssports.com/nba/injuries/",
    "mlb": "https://www.cbssports.com/mlb/injuries/",
}

# Normalized injury statuses
VALID_STATUSES = {
    "out", "doubtful", "questionable", "probable", "day-to-day",
    "il-10", "il-15", "il-60", "suspended", "injured", "unknown",
}


def _normalize_status(raw_status: str) -> str:
    """Normalize a raw injury status string to a standard value."""
    status = raw_status.strip().lower()

    status_map = {
        "out": "out",
        "o": "out",
        "doubtful": "doubtful",
        "d": "doubtful",
        "questionable": "questionable",
        "q": "questionable",
        "probable": "probable",
        "p": "probable",
        "day-to-day": "day-to-day",
        "dtd": "day-to-day",
        "day to day": "day-to-day",
        "10-day il": "il-10",
        "10-day injured list": "il-10",
        "il-10": "il-10",
        "15-day il": "il-15",
        "15-day injured list": "il-15",
        "il-15": "il-15",
        "60-day il": "il-60",
        "60-day injured list": "il-60",
        "il-60": "il-60",
        "injured list": "il-15",
        "il": "il-15",
        "suspended": "suspended",
        "susp": "suspended",
        "injured": "injured",
        "inj": "injured",
    }

    return status_map.get(status, "unknown")


def _make_injury_record(
    player: str,
    team: str,
    status: str,
    injury: str,
    source: str,
    sport: str,
    updated: Optional[str] = None,
) -> dict:
    """Create a standardized injury record."""
    return {
        "player": player.strip(),
        "team": team.strip(),
        "status": _normalize_status(status),
        "raw_status": status.strip(),
        "injury": injury.strip() if injury else "Undisclosed",
        "source": source,
        "sport": sport,
        "updated": updated or datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_html(url: str, retry: bool = True) -> Optional[BeautifulSoup]:
    """
    Fetch a URL and parse it as HTML.

    Args:
        url: The URL to fetch.
        retry: Whether to retry once on failure.

    Returns:
        BeautifulSoup object or None if fetch failed.
    """
    for attempt in range(2 if retry else 1):
        try:
            response = requests.get(
                url, headers=BROWSER_HEADERS, timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.exceptions.RequestException as e:
            logger.warning("Fetch attempt %d failed for %s: %s", attempt + 1, url, e)
            if attempt == 0 and retry:
                time.sleep(5)
    return None


# ---------------------------------------------------------------------------
# ESPN Parsers
# ---------------------------------------------------------------------------

def parse_espn_injuries(sport: str) -> list:
    """
    Fetch and parse the ESPN injury page for a given sport.

    ESPN injury pages are organized by team, with each team section containing
    a table of injured players with their status and injury description.

    Args:
        sport: One of 'nba', 'mlb', 'soccer'

    Returns:
        List of standardized injury record dicts.
    """
    url = ESPN_INJURY_URLS.get(sport)
    if not url:
        logger.error("No ESPN injury URL configured for sport: %s", sport)
        return []

    logger.info("Fetching ESPN %s injuries from %s", sport.upper(), url)
    soup = _fetch_html(url)
    if soup is None:
        logger.error("Failed to fetch ESPN %s injury page", sport.upper())
        return []

    injuries = []
    current_team = "Unknown"

    # ESPN injury pages use a structure with team headers and player tables.
    # The exact HTML structure may change, so we use multiple strategies.

    # Strategy 1: Look for team sections with injury tables
    team_sections = soup.find_all("div", class_=lambda c: c and "injuries" in c.lower()) or \
                    soup.find_all("section") or \
                    soup.find_all("div", class_="ResponsiveTable")

    for section in team_sections:
        # Try to find team name in the section header
        team_header = (
            section.find("h2") or
            section.find("h3") or
            section.find("span", class_=lambda c: c and "team" in c.lower() if c else False)
        )
        if team_header:
            current_team = team_header.get_text(strip=True)

        # Find all table rows in this section
        rows = section.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                player_name = cells[0].get_text(strip=True)
                # Skip header rows
                if player_name.lower() in ("name", "player", ""):
                    continue

                status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                injury_desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Undisclosed"

                # Extract date if present (usually in a 4th column)
                updated = None
                if len(cells) > 3:
                    date_text = cells[3].get_text(strip=True)
                    if date_text:
                        updated = date_text

                injuries.append(_make_injury_record(
                    player=player_name,
                    team=current_team,
                    status=status_text,
                    injury=injury_desc,
                    source="espn",
                    sport=sport,
                    updated=updated,
                ))

    # Strategy 2: If no structured tables found, try flat table parsing
    if not injuries:
        all_tables = soup.find_all("table")
        for table in all_tables:
            # Check preceding header for team name
            prev = table.find_previous(["h2", "h3", "h4"])
            if prev:
                current_team = prev.get_text(strip=True)

            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    player_name = cells[0].get_text(strip=True)
                    if player_name.lower() in ("name", "player", ""):
                        continue
                    status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                    injury_desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Undisclosed"

                    injuries.append(_make_injury_record(
                        player=player_name,
                        team=current_team,
                        status=status_text,
                        injury=injury_desc,
                        source="espn",
                        sport=sport,
                    ))

    logger.info("Parsed %d injuries from ESPN %s", len(injuries), sport.upper())
    return injuries


# ---------------------------------------------------------------------------
# CBS Sports Parsers
# ---------------------------------------------------------------------------

def parse_cbs_injuries(sport: str) -> list:
    """
    Fetch and parse the CBS Sports injury page for a given sport.

    CBS Sports organizes injuries in a similar team-by-team table format.

    Args:
        sport: One of 'nba', 'mlb'

    Returns:
        List of standardized injury record dicts.
    """
    url = CBS_INJURY_URLS.get(sport)
    if not url:
        logger.warning("No CBS injury URL configured for sport: %s", sport)
        return []

    logger.info("Fetching CBS Sports %s injuries from %s", sport.upper(), url)
    time.sleep(POLITE_DELAY)  # polite delay before CBS after ESPN
    soup = _fetch_html(url)
    if soup is None:
        logger.error("Failed to fetch CBS Sports %s injury page", sport.upper())
        return []

    injuries = []
    current_team = "Unknown"

    # CBS Sports uses TableBase components with team headers
    team_sections = soup.find_all("div", class_=lambda c: c and "TableBase" in c if c else False) or \
                    soup.find_all("table")

    for section in team_sections:
        # Find team name
        team_el = (
            section.find_previous("h4") or
            section.find_previous("h3") or
            section.find_previous("a", class_=lambda c: c and "team" in c.lower() if c else False)
        )
        if team_el:
            current_team = team_el.get_text(strip=True)

        rows = section.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                player_name = cells[0].get_text(strip=True)
                if not player_name or player_name.lower() in ("player", "name"):
                    continue

                # CBS typically has: Player | Position | Updated | Injury | Status
                if len(cells) >= 5:
                    injury_desc = cells[3].get_text(strip=True)
                    status_text = cells[4].get_text(strip=True)
                    updated = cells[2].get_text(strip=True)
                elif len(cells) >= 3:
                    status_text = cells[1].get_text(strip=True)
                    injury_desc = cells[2].get_text(strip=True)
                    updated = None
                else:
                    continue

                injuries.append(_make_injury_record(
                    player=player_name,
                    team=current_team,
                    status=status_text,
                    injury=injury_desc,
                    source="cbs",
                    sport=sport,
                    updated=updated,
                ))

    logger.info("Parsed %d injuries from CBS Sports %s", len(injuries), sport.upper())
    return injuries


# ---------------------------------------------------------------------------
# NBA Official Injury Report
# ---------------------------------------------------------------------------

def parse_nba_injury_report() -> list:
    """
    Fetch the official NBA injury report.

    The NBA publishes an official injury report typically by 5 PM ET on game
    days. This parser fetches it from the NBA's public-facing injury report
    page or the official PDF endpoint.

    Returns:
        List of standardized injury record dicts.
    """
    url = "https://www.nba.com/players/injuries"
    logger.info("Fetching official NBA injury report from %s", url)
    time.sleep(POLITE_DELAY)
    soup = _fetch_html(url)
    if soup is None:
        logger.error("Failed to fetch NBA official injury report")
        return []

    injuries = []
    current_team = "Unknown"

    # NBA.com injury page structure
    # Look for player injury cards or table rows
    team_containers = soup.find_all(
        "div", class_=lambda c: c and ("team" in c.lower() or "injury" in c.lower()) if c else False
    )

    for container in team_containers:
        team_el = container.find(["h2", "h3", "h4"])
        if team_el:
            current_team = team_el.get_text(strip=True)

        rows = container.find_all("tr") or container.find_all(
            "div", class_=lambda c: c and "player" in c.lower() if c else False
        )
        for row in rows:
            cells = row.find_all("td") or row.find_all("span")
            if len(cells) >= 2:
                player_name = cells[0].get_text(strip=True)
                if not player_name or player_name.lower() in ("player", "name"):
                    continue
                status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                injury_desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Undisclosed"

                injuries.append(_make_injury_record(
                    player=player_name,
                    team=current_team,
                    status=status_text,
                    injury=injury_desc,
                    source="nba_official",
                    sport="nba",
                ))

    # Fallback: try the general table approach
    if not injuries:
        tables = soup.find_all("table")
        for table in tables:
            prev = table.find_previous(["h2", "h3", "h4"])
            if prev:
                current_team = prev.get_text(strip=True)
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    player_name = cells[0].get_text(strip=True)
                    if not player_name or player_name.lower() in ("player", "name"):
                        continue
                    status_text = cells[1].get_text(strip=True)
                    injury_desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Undisclosed"
                    injuries.append(_make_injury_record(
                        player=player_name,
                        team=current_team,
                        status=status_text,
                        injury=injury_desc,
                        source="nba_official",
                        sport="nba",
                    ))

    logger.info("Parsed %d injuries from NBA official report", len(injuries))
    return injuries


# ---------------------------------------------------------------------------
# MLB Transaction Wire
# ---------------------------------------------------------------------------

def parse_mlb_transactions() -> list:
    """
    Fetch MLB transaction wire for IL placements and activations.

    Uses the MLB Stats API public endpoint for recent transactions,
    filtering for injury-related moves (IL placements, activations,
    designations).

    Returns:
        List of standardized injury record dicts.
    """
    url = "https://statsapi.mlb.com/api/v1/transactions"
    params = {
        "startDate": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
        "endDate": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
    }

    logger.info("Fetching MLB transactions from %s", url)
    time.sleep(POLITE_DELAY)

    try:
        response = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch MLB transactions: %s", e)
        return []

    injuries = []
    transactions = data.get("transactions", [])

    # IL-related transaction type codes
    il_types = {
        "Placed on IL",
        "Placed on 10-Day IL",
        "Placed on 15-Day IL",
        "Placed on 60-Day IL",
        "Activated from IL",
        "Activated from 10-Day IL",
        "Activated from 15-Day IL",
        "Activated from 60-Day IL",
        "Transferred to 60-Day IL",
    }

    for txn in transactions:
        description = txn.get("description", "")
        type_desc = txn.get("typeDesc", "")

        # Filter for IL-related transactions
        is_il_related = (
            type_desc in il_types or
            "injured list" in description.lower() or
            "il" in type_desc.lower() or
            "disabled list" in description.lower()
        )

        if not is_il_related:
            continue

        player_info = txn.get("player", {})
        player_name = player_info.get("fullName", "Unknown")
        team_info = txn.get("team", {})
        team_name = team_info.get("name", "Unknown")
        effective_date = txn.get("effectiveDate", "")

        # Determine status from transaction type
        if "activated" in type_desc.lower() or "activated" in description.lower():
            status = "active"
        elif "60-day" in type_desc.lower() or "60-day" in description.lower():
            status = "il-60"
        elif "15-day" in type_desc.lower() or "15-day" in description.lower():
            status = "il-15"
        elif "10-day" in type_desc.lower() or "10-day" in description.lower():
            status = "il-10"
        else:
            status = "il-15"  # default IL type

        # Extract injury description from transaction description
        injury_desc = description
        # Try to extract just the injury part
        for marker in ["with", "due to", "suffering from"]:
            if marker in description.lower():
                idx = description.lower().index(marker)
                injury_desc = description[idx + len(marker):].strip().rstrip(".")
                break

        injuries.append({
            "player": player_name,
            "team": team_name,
            "status": status,
            "raw_status": type_desc,
            "injury": injury_desc or "Undisclosed",
            "source": "mlb_transactions",
            "sport": "mlb",
            "updated": effective_date,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    logger.info("Parsed %d IL transactions from MLB wire", len(injuries))
    return injuries


# ---------------------------------------------------------------------------
# Soccer Injury Parsers
# ---------------------------------------------------------------------------

def parse_soccer_injuries(league: str = "premier-league") -> list:
    """
    Fetch soccer injury data from public sources.

    Uses ESPN's soccer injury pages as the primary source, organized by
    league/competition.

    Args:
        league: League identifier. Options:
                'premier-league', 'la-liga', 'champions-league', 'mls'

    Returns:
        List of standardized injury record dicts.
    """
    league_urls = {
        "premier-league": "https://www.espn.com/soccer/injuries/_/league/eng.1",
        "la-liga": "https://www.espn.com/soccer/injuries/_/league/esp.1",
        "champions-league": "https://www.espn.com/soccer/injuries/_/league/uefa.champions",
        "mls": "https://www.espn.com/soccer/injuries/_/league/usa.1",
    }

    url = league_urls.get(league)
    if not url:
        # Fallback to generic ESPN soccer injuries
        url = ESPN_INJURY_URLS["soccer"]
        logger.warning("Unknown league '%s', falling back to generic ESPN soccer", league)

    logger.info("Fetching soccer injuries for %s from %s", league, url)
    time.sleep(POLITE_DELAY)
    soup = _fetch_html(url)
    if soup is None:
        logger.error("Failed to fetch soccer injuries for %s", league)
        return []

    injuries = []
    current_team = "Unknown"

    # Parse team-by-team injury tables
    sections = soup.find_all("div", class_=lambda c: c and "Table" in c if c else False) or \
               soup.find_all("table")

    for section in sections:
        team_el = (
            section.find_previous("h2") or
            section.find_previous("h3") or
            section.find_previous("caption")
        )
        if team_el:
            current_team = team_el.get_text(strip=True)

        rows = section.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                player_name = cells[0].get_text(strip=True)
                if not player_name or player_name.lower() in ("player", "name", ""):
                    continue

                status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Injured"
                injury_desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Undisclosed"
                expected_return = cells[3].get_text(strip=True) if len(cells) > 3 else None

                record = _make_injury_record(
                    player=player_name,
                    team=current_team,
                    status=status_text,
                    injury=injury_desc,
                    source="espn",
                    sport="soccer",
                )
                if expected_return:
                    record["expected_return"] = expected_return
                record["league"] = league

                injuries.append(record)

    logger.info("Parsed %d injuries for soccer/%s", len(injuries), league)
    return injuries


# ---------------------------------------------------------------------------
# Aggregate Fetcher
# ---------------------------------------------------------------------------

def fetch_all_injuries(sports: Optional[list] = None) -> dict:
    """
    Fetch injuries from all configured sources for the requested sports.

    Each source is fetched independently. Failures in one source do not
    affect others. The result includes metadata about which sources
    succeeded and which failed.

    Args:
        sports: List of sports to fetch. Defaults to ['nba', 'mlb', 'soccer'].

    Returns:
        dict with structure:
        {
            "injuries": {sport: [injury_records]},
            "sources": {source_name: {"status": "ok"|"error", "count": N}},
            "fetched_at": ISO timestamp
        }
    """
    if sports is None:
        sports = ["nba", "mlb", "soccer"]

    all_injuries = {sport: [] for sport in sports}
    source_status = {}

    # NBA sources
    if "nba" in sports:
        # ESPN NBA
        try:
            espn_nba = parse_espn_injuries("nba")
            all_injuries["nba"].extend(espn_nba)
            source_status["espn_nba"] = {"status": "ok", "count": len(espn_nba)}
        except Exception as e:
            logger.error("ESPN NBA parser failed: %s", e)
            source_status["espn_nba"] = {"status": "error", "error": str(e)}

        # CBS NBA
        try:
            cbs_nba = parse_cbs_injuries("nba")
            all_injuries["nba"].extend(cbs_nba)
            source_status["cbs_nba"] = {"status": "ok", "count": len(cbs_nba)}
        except Exception as e:
            logger.error("CBS NBA parser failed: %s", e)
            source_status["cbs_nba"] = {"status": "error", "error": str(e)}

        # Official NBA injury report
        try:
            nba_official = parse_nba_injury_report()
            all_injuries["nba"].extend(nba_official)
            source_status["nba_official"] = {"status": "ok", "count": len(nba_official)}
        except Exception as e:
            logger.error("NBA official parser failed: %s", e)
            source_status["nba_official"] = {"status": "error", "error": str(e)}

    # MLB sources
    if "mlb" in sports:
        # ESPN MLB
        try:
            espn_mlb = parse_espn_injuries("mlb")
            all_injuries["mlb"].extend(espn_mlb)
            source_status["espn_mlb"] = {"status": "ok", "count": len(espn_mlb)}
        except Exception as e:
            logger.error("ESPN MLB parser failed: %s", e)
            source_status["espn_mlb"] = {"status": "error", "error": str(e)}

        # CBS MLB
        try:
            cbs_mlb = parse_cbs_injuries("mlb")
            all_injuries["mlb"].extend(cbs_mlb)
            source_status["cbs_mlb"] = {"status": "ok", "count": len(cbs_mlb)}
        except Exception as e:
            logger.error("CBS MLB parser failed: %s", e)
            source_status["cbs_mlb"] = {"status": "error", "error": str(e)}

        # MLB Transactions
        try:
            mlb_txns = parse_mlb_transactions()
            all_injuries["mlb"].extend(mlb_txns)
            source_status["mlb_transactions"] = {"status": "ok", "count": len(mlb_txns)}
        except Exception as e:
            logger.error("MLB transactions parser failed: %s", e)
            source_status["mlb_transactions"] = {"status": "error", "error": str(e)}

    # Soccer sources
    if "soccer" in sports:
        for league in ["premier-league", "la-liga", "champions-league", "mls"]:
            source_key = f"espn_soccer_{league}"
            try:
                soccer_injuries = parse_soccer_injuries(league)
                all_injuries["soccer"].extend(soccer_injuries)
                source_status[source_key] = {"status": "ok", "count": len(soccer_injuries)}
            except Exception as e:
                logger.error("Soccer %s parser failed: %s", league, e)
                source_status[source_key] = {"status": "error", "error": str(e)}

    return {
        "injuries": all_injuries,
        "sources": source_status,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
