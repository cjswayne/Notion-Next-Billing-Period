# Notion Next Billing Period

A small always-on-top Windows desktop widget that tracks how much billable time you still need this billing period, paced per day from your Notion **Time Tracker**.

## What it shows

One status line, for example:

```text
10 days left | 4:27/day | 3:57PM
```

- **Days left** in the current billing period (ends on the 14th and last day of each month by default)
- **Hours per day** still needed to hit the period target (default 54h), in `H:MM`
- **Finish-by clock** if you keep working from now until that daily goal is met

Hover the line for more detail:

- Hours left to the period target
- Time left to meet the **current** daily goal (and finish-by time)
- Time worked today (non–JadePuma Apps)
- Live **active Running timer** credit (`−H:MM active timer`) when a timer is open
- Optional **off 1 / 2 / 3 day** paces when enough days remain in the period

### Goal bumping

The “min” daily pace starts at the base split of remaining hours across days left. After you hit that for today, the widget bumps the displayed goal to the off-1-day pace, then off-2, then off-3. Higher off-day rows in the tooltip only appear for tiers you have not reached yet and that still fit in the period.

### What counts as hours

- Notion **Time Tracker** database entries for your Agent
- Sums `Time (Hrs)` (or Start/End duration) with local **Pacific** calendar dates
- Excludes client **JadePuma Apps**
- Period pace uses hours through **yesterday**; today is tracked separately
- Running timers apply live Start→now elapsed (refreshed every 60s on the UI; Notion refetch on the configured interval)

## Requirements

- Windows
- Python 3 with stdlib only (tkinter, urllib, zoneinfo)
- Notion internal integration token in `.env` as `NOTION_SECRET=...`
- Time Tracker database shared with that integration

`.env` is gitignored; do not commit secrets.

## Files

| File | Role |
|------|------|
| `widget.py` | Desktop UI, pacing math, tooltips |
| `notion_client.py` | Notion API fetch, timezone detection, filters |
| `config.json` | Billing ends, target hours, Notion property names |
| `test_notion_client.py` | Offline unit tests |
| `start_widget.vbs` | Silent launcher (no console) |
| `install_startup.ps1` / `uninstall_startup.ps1` | Run at Windows logon |

## Config highlights

```json
{
  "billing_period_end_days": [14, "last"],
  "timezone": "America/Los_Angeles",
  "target_hours": 54,
  "notion_database_id": "...",
  "notion_hours_property": "Time (Hrs)",
  "notion_date_property": "Start",
  "notion_exclude_today": true,
  "notion_exclude_client_names": ["JadePuma Apps"],
  "notion_refresh_minutes": 15
}
```

Timezone is resolved dynamically from Windows when possible (`Pacific Standard Time` → `America/Los_Angeles`); `timezone` in config is a fallback.

`billing_period_end_days` lists day-of-month period ends. Use `"last"` for month end.

## Usage

```bash
pythonw widget.py
```

Or double-click `start_widget.vbs`.

Smoke-test Notion hours:

```bash
python notion_client.py --dry-run
python notion_client.py --test --start YYYY-MM-DD --end YYYY-MM-DD
python -m unittest test_notion_client.py -v
```

Install at startup:

```powershell
powershell -ExecutionPolicy Bypass -File install_startup.ps1
```

## Interaction

- **Drag** — move the widget; position saved to `.position`
- **Right-click** — Refresh hours, Reload config, Open config, Toggle always-on-top, Exit
- Active-timer UI updates every **60 seconds**
- Notion data refreshes every `notion_refresh_minutes` (default 15)
