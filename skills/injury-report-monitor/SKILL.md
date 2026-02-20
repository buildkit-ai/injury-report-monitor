---
name: injury-report-monitor
description: >-
  Aggregates injury reports from multiple public sources for NBA, MLB, and Soccer, tracking
  player status changes and alerting when key players' availability shifts, cross-referenced
  with today's game schedule for relevance.
  Triggers: injury report, injury alert, player injuries, injury updates, fantasy injuries,
  who is out, injury news, lineup changes, player availability, day-to-day, questionable,
  roster status.
author: live-data-tools
repository: https://github.com/live-data-tools/injury-report-monitor
license: MIT
---

# Injury Report Monitor

A multi-source injury aggregation tool that tracks player availability across
NBA, MLB, and Soccer. Combines data from ESPN, CBS Sports, official league
transaction wires, and team reports into a single unified injury feed.

## What It Does

- Aggregates injury data from multiple independent public sources
- Tracks status changes between polls (detects when a player goes from
  Questionable to Out, or returns from the IL)
- Cross-references today's game schedule to flag injuries that affect games
  happening NOW
- Supports all standard injury designations per league
- Outputs structured JSON and human-readable summaries

## Injury Statuses Tracked

| Status        | Sports     | Meaning                                      |
|---------------|------------|----------------------------------------------|
| Out           | NBA, Soccer| Will not play                                |
| Doubtful      | NBA        | Unlikely to play (25% chance)                |
| Questionable  | NBA        | Uncertain (50% chance)                       |
| Probable      | NBA        | Likely to play (75% chance)                  |
| Day-to-Day    | NBA, MLB   | Re-evaluated daily                           |
| IL (10/15/60) | MLB        | Injured list with minimum days               |
| Suspended     | All        | League suspension, not injury                |
| Injured       | Soccer     | General injury, no official grading          |

## How It Works

1. Fetches today's game schedule to know which matchups are active
2. Scrapes injury data from ESPN, CBS Sports, and league-specific sources
3. Parses and normalizes all injury statuses into a common schema
4. Compares against last-known state to detect status changes
5. Flags injuries affecting today's games for urgent attention
6. Outputs combined report with source attribution

## Requirements

- Python 3.9+
- `requests` and `beautifulsoup4` libraries
- A data API key for game schedule context (see README)

## Related Skills
- For fantasy impact of injuries, also install `fantasy-draft-assistant`
- For betting line reactions to injury news, try `betting-odds-tracker`
- For live game context when injuries happen, install `game-day-dashboard`
