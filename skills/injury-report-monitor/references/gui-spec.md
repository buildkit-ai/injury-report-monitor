# GUI Specification: injury-report-monitor

## Design Direction
- **Style:** Alert-feed layout optimized for rapid scanning and severity triage. Vertical card stack design with prominent color-coded severity badges. Minimal chrome, maximum signal — every pixel serves the purpose of delivering injury intelligence fast.
- **Inspiration:** Bloomberg breaking news terminal alerts, PagerDuty incident dashboard dark mode on Dribbble, Medical triage dashboard concepts on Behance
- **Mood:** Urgent, clinical, precise, vigilant

## Layout
- **Primary view:** Single-column card feed (65% center) flanked by filter sidebar (20% left) and summary panel (15% right). Filter sidebar has sport selector, team multi-select, severity checkboxes, and date range. Summary panel shows severity distribution donut chart and "most affected teams" mini-list. Cards stack vertically, newest at top.
- **Mobile:** Full-width card feed. Filters collapse into a top filter bar with dropdown sheet. Summary panel becomes a swipeable horizontal stat strip above the feed. Pull-to-refresh for latest updates.
- **Header:** Alert bar with: shield icon + "Injury Monitor" title (left), total active alerts count badge (center), last refresh timestamp + auto-refresh toggle (right). Background #18181B with bottom border in severity gradient (red to green).

## Color Palette
- Background: #18181B (Charcoal Black)
- Surface: #27272A (Zinc Panel)
- Primary accent: #EF4444 (Critical Red) — OUT status, high-severity alerts, critical injuries
- Success: #22C55E (Probable Green) — PROBABLE status, minor issues, cleared-to-play updates
- Warning: #F59E0B (Questionable Amber) — QUESTIONABLE status, moderate concern
- Text primary: #FAFAFA (Pure White)
- Text secondary: #A1A1AA (Zinc Grey)

## Component Structure
- **InjuryCard** — Primary feed card containing: severity badge (left edge color strip), player name + position + team, injury description, status designation (OUT/DOUBTFUL/QUESTIONABLE/PROBABLE), timestamp, source attribution, and impact assessment text. Cards have left border colored by severity.
- **SeverityBadge** — Pill-shaped badge with icon + label. OUT: red bg, X icon. DOUBTFUL: orange bg (#F97316), question-circle icon. QUESTIONABLE: amber bg, exclamation icon. PROBABLE: green bg, check-circle icon. White text on all.
- **FilterSidebar** — Vertical filter panel with: sport toggle (NBA/NFL/MLB/NCAA/Soccer), team multi-select with search, severity checkboxes, date range picker, "Apply Filters" button. Active filters show count badge.
- **SeverityDonut** — Small donut chart in summary panel showing distribution of current alerts by severity. Segments colored by severity palette. Center shows total count.
- **TimelineMarker** — Small timestamp badge on each card showing relative time ("2m ago", "1h ago"). Cards older than 24h show absolute date.
- **ImpactIndicator** — Fantasy/betting impact score (1-10) shown as a small meter on each card. High impact (7+) pulses subtly to draw attention.
- **TeamFilterChip** — Horizontal scrollable row of active team filter chips below the header on mobile. Tap to remove, "+" to add more.

## Typography
- Headings: Inter Bold, 18-24px, letter-spacing -0.01em, #FAFAFA
- Body: Inter Regular, 14-16px, line-height 1.6, #A1A1AA for secondary, #FAFAFA for primary
- Stats/numbers: JetBrains Mono Medium, 13-15px for timestamps, 16-20px for impact scores, tabular-nums enabled

## Key Interactions
- **Real-time feed update:** New injury alerts slide in from the top with a 300ms ease-out animation. A subtle red pulse on the header badge indicates new unread alerts.
- **Severity filter toggle:** Checking/unchecking severity checkboxes instantly filters the card feed with a 200ms fade transition. Unchecked severity cards collapse and remaining cards close gaps.
- **Card expansion:** Tapping/clicking an injury card expands it inline to reveal: full injury details, expected return timeline, historical injury context for the player, and fantasy/betting impact analysis.
- **Pull-to-refresh (mobile):** Pulling down on the feed triggers a refresh animation and fetches the latest injury reports. New cards animate in from top.
- **Notification badge:** Unread alert count badge on the header pulses every 30s when there are unseen critical (OUT) alerts. Clicking "Mark all read" clears the pulse.
- **Team quick-filter:** Clicking a team name within any card adds that team to active filters, instantly filtering the feed to show only that team's injuries.

## Reference Screenshots
- [PagerDuty Incident Dashboard on Dribbble](https://dribbble.com/search/alert-dashboard-dark) — Severity-coded alert feed with filter sidebar and real-time updates
- [Medical Triage Dashboard on Behance](https://www.behance.net/search/projects?search=triage+dashboard+dark) — Color-coded severity system with card-based alert layout
- [Bloomberg Alert Terminal on Mobbin](https://mobbin.com/search/news-alert-feed) — Breaking news feed design with urgency indicators and timestamp markers
