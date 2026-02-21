# injury-report-monitor

**Never get blindsided by a late scratch again.**

A real-time injury aggregation tool that pulls from multiple public sources,
tracks status changes, and tells you which injuries actually matter for
tonight's games.

## The Problem

Injury news is scattered across dozens of sources. By the time you check ESPN,
the official NBA injury report, team Twitter accounts, and beat reporters, the
game has already started and your fantasy lineup is locked with a player who was
ruled out 20 minutes ago.

## The Solution

`injury-report-monitor` continuously aggregates injury data from:

- **ESPN** injury pages (NBA, MLB, Soccer)
- **CBS Sports** injury reports
- **Official NBA** injury report filings
- **MLB transaction wire** (IL placements and activations)
- **Soccer** injury reports from public league sources

All sources are combined into a single feed. When a player's status changes --
Questionable to Out, IL to Active, Injured to Available -- you see it
immediately with full context about whether their game is today.

## Who This Is For

- **Fantasy managers** who need lineup-lock alerts
- **Sports bettors** who need to know about late scratches before lines adjust
- **Fans** who want to know if their favorite player is suiting up tonight
- **Analysts** tracking team health trends over a season

## Setup

### 1. API Key for Game Schedules

You need an API key for game schedule context (knowing which games are today
so the tool can flag relevant injuries):

```bash
export SHIPP_API_KEY="your-api-key-here"
```

### 2. Install Dependencies

```bash
pip install requests beautifulsoup4
```

No additional API keys are required. All injury sources are scraped from
publicly available pages.

### 3. (Optional) State Persistence

By default, the monitor stores last-known injury states in a local JSON file
(`~/.injury_monitor_state.json`). This enables change detection between runs.
To customize the location:

```bash
export INJURY_STATE_PATH="/your/preferred/path/state.json"
```

## Usage

### Full Report (All Sports)

```python
from scripts.injury_monitor import InjuryMonitor

monitor = InjuryMonitor()
report = monitor.get_full_report()

# Structured JSON output
print(report.to_json())

# Human-readable summary
print(report.summary())
```

### Single Sport

```python
report = monitor.get_report(sport="nba")
```

### Changes Only

```python
changes = monitor.get_status_changes()
for change in changes:
    print(f"{change['player']} ({change['team']}): "
          f"{change['old_status']} -> {change['new_status']} "
          f"{'** GAME TODAY **' if change['game_today'] else ''}")
```

### Today's Games Impact

```python
# Only injuries for players whose teams play today
today_injuries = monitor.get_today_impact()
```

## Output Format

### JSON Structure

```json
{
  "generated_at": "2026-02-18T14:30:00Z",
  "sports": {
    "nba": {
      "injuries": [
        {
          "player": "Anthony Davis",
          "team": "Los Angeles Lakers",
          "status": "Questionable",
          "injury": "Left knee soreness",
          "updated": "2026-02-18T10:00:00Z",
          "source": "espn",
          "game_today": {
            "opponent": "Golden State Warriors",
            "time": "19:30 ET",
            "game_id": "nba-20260218-lal-gsw"
          },
          "status_changed": true,
          "previous_status": "Probable"
        }
      ],
      "total_injuries": 47,
      "games_today": 8,
      "affected_games": 6
    }
  }
}
```

### Human-Readable Summary

```
=== INJURY REPORT — February 18, 2026 ===

--- NBA (47 injuries, 6 of 8 games affected) ---

** STATUS CHANGES **
  Anthony Davis (LAL) — Probable -> Questionable — Left knee soreness
    GAME TODAY: vs GSW at 7:30 PM ET

  Ja Morant (MEM) — Questionable -> Out — Right ankle sprain
    GAME TODAY: vs PHX at 9:00 PM ET

** ALL INJURIES (Teams Playing Today) **
  ...
```

## Architecture

```
injury_monitor.py          Main orchestrator
    |
    +---> injury_sources.py    Individual source parsers (ESPN, CBS, etc.)
    |
    +---> shipp_wrapper.py     Game schedule context
    |
    +---> state.json           Last-known injury states for change detection
```

## Error Handling

Each source is fetched independently. If ESPN is down, CBS Sports data still
comes through. The report always tells you which sources succeeded and which
failed, so you know the completeness of your data.

## Rate Limiting

All sources are public HTML pages scraped with polite intervals:

- Minimum 2 seconds between requests to the same domain
- Requests timeout after 15 seconds
- Failed sources are retried once after a 5-second delay

## License

MIT
