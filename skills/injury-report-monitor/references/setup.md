# Setup Guide — injury-report-monitor Skill

This guide walks you through configuring all required and optional API keys
for the `injury-report-monitor` skill.

## Required: Shipp.ai API Key

The Shipp.ai API key is required for real-time injury alerts, roster status
updates, and game impact analysis across all supported sports.

### Steps

1. **Create an account** at [platform.shipp.ai](https://platform.shipp.ai)
2. **Sign in** and navigate to **Settings > API Keys**
3. **Generate a new API key** — copy it immediately (it won't be shown again)
4. **Set the environment variable**:

```bash
# Add to your shell profile (~/.zshrc, ~/.bashrc, etc.)
export SHIPP_API_KEY="shipp_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

5. **Verify** by running:

```bash
curl -s -H "Authorization: Bearer $SHIPP_API_KEY" \
  "https://api.shipp.ai/api/v1/connections" | python3 -m json.tool
```

You should see a JSON response (even if the connections list is empty).

### API Key Format

Shipp API keys typically start with `shipp_live_` or `shipp_test_`. Use the
`live` key for production sports data.

### Rate Limits

Your rate limit depends on your Shipp.ai plan:

| Plan       | Requests/min | Connections | Notes                    |
|------------|-------------|-------------|--------------------------|
| Free       | 30          | 3           | Great for trying it out  |
| Starter    | 120         | 10          | Suitable for one sport   |
| Pro        | 600         | 50          | All three sports         |
| Enterprise | Custom      | Unlimited   | Contact sales            |

## No Key Required

The following external sources do not require API keys:

- **ESPN Public Injury Reports** — Injury designations and return timelines
  - Base URL: `https://site.api.espn.com`
  - Rate limit: Be courteous (~1 req/sec)
  - Data: Injury status, expected return dates, game-day designations

- **CBS Sports RSS** — Injury news and transaction updates
  - Feed URL: `https://www.cbssports.com/rss/headlines`
  - Rate limit: Standard RSS polling (no more than once per minute)
  - Data: Breaking injury news, roster moves, practice reports

- **Rotowire RSS** — Real-time injury and lineup updates
  - Feed URL: `https://www.rotowire.com/rss/news.htm`
  - Rate limit: Standard RSS polling (no more than once per minute)
  - Data: Injury updates, lineup confirmations, news blurbs

## Python Dependencies

Install the required packages:

```bash
pip install requests beautifulsoup4
```

`beautifulsoup4` is used for parsing RSS feeds and HTML injury report pages.
All other dependencies are from the Python standard library (`os`, `time`,
`logging`, `datetime`, `json`, `typing`, `xml`).

## Environment Variable Summary

| Variable        | Required | Source             | Purpose                              |
|-----------------|----------|--------------------|---------------------------------------|
| `SHIPP_API_KEY` | Yes      | platform.shipp.ai  | Real-time injury alerts, roster data  |

## Verifying Your Setup

Run the built-in smoke test:

```bash
cd skills/community/injury-report-monitor
python3 scripts/injury_monitor.py --once
```

This will attempt to:
1. Fetch real-time injury data (requires `SHIPP_API_KEY`)
2. Parse ESPN public injury reports (no key needed)
3. Pull latest entries from CBS Sports RSS feed (no key needed)
4. Pull latest entries from Rotowire RSS feed (no key needed)

Each section will show either data or an error message indicating which
key is missing or which service is unavailable.

## Troubleshooting

### "SHIPP_API_KEY environment variable is not set"

Your shell session doesn't have the key. Make sure you either:
- Added `export SHIPP_API_KEY=...` to your shell profile and restarted the terminal
- Or ran the export command in the current session

### "Shipp API 401: Unauthorized"

The key is set but invalid. Double-check:
- No extra spaces or newline characters in the key
- The key is from the correct environment (live vs test)
- The key hasn't been revoked

### "Shipp API 402: Payment Required"

Your plan's quota has been exceeded. Check your usage at
[platform.shipp.ai/usage](https://platform.shipp.ai) or upgrade your plan.

### "Shipp API 429: Too Many Requests"

You've hit the rate limit. The monitor automatically retries with backoff,
but if it persists, reduce polling frequency or upgrade your plan.

### RSS feeds returning empty or stale data

ESPN, CBS Sports, and Rotowire feeds are public and generally reliable.
If you receive empty results, check your network connection. The monitor
caches the most recent data and will display it until fresh data arrives.

### beautifulsoup4 import errors

Ensure you installed the package with `pip install beautifulsoup4`. The
import name is `bs4`, not `beautifulsoup4`. If using a virtual environment,
make sure it is activated before running the monitor.

## Documentation Links

- **Shipp.ai Docs**: [docs.shipp.ai](https://docs.shipp.ai)
- **Shipp.ai API Reference**: [docs.shipp.ai/api](https://docs.shipp.ai/api)
- **ESPN API**: Community docs at [gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b](https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b)
- **CBS Sports RSS**: [cbssports.com/rss](https://www.cbssports.com/rss)
- **Rotowire RSS**: [rotowire.com](https://www.rotowire.com)
