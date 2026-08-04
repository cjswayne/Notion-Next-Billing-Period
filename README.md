# Billing Period Widget

Tiny always-visible desktop widget showing days remaining in the current billing period.

## Files

- `widget.py` — the tkinter widget
- `config.json` — billing dates, timezone, look & feel
- `start_widget.vbs` — silent launcher (no console window)
- `install_startup.ps1` / `uninstall_startup.ps1` — Windows startup hook

## Config

```json
{
  "billing_period_end_days": [14, "last"],
  "timezone": "America/New_York",
  "always_on_top": false,
  "opacity": 0.85
}
```

`billing_period_end_days` is a list of day-of-month values that mark the **end** of each period. Use the string `"last"` for the final day of the month. Days greater than the month length are clamped.

## Usage

Run once to test:

```
pythonw widget.py
```

Or double-click `start_widget.vbs`.

Install on startup (one time):

```
powershell -ExecutionPolicy Bypass -File install_startup.ps1
```

Uninstall:

```
powershell -ExecutionPolicy Bypass -File uninstall_startup.ps1
```

## Interaction

- **Left-click drag** — move it anywhere; position is saved to `.position`
- **Right-click** — reload config, toggle always-on-top, exit
- Updates automatically at local midnight (in the configured timezone)
