"""Minimal Notion API client for fetching summed hours from Time Tracker.

Stdlib-only (urllib). Reads NOTION_SECRET from a .env file next to this script.

Time Tracker schema (JadePuma): hours live in formula "Time (Hrs)", dated by "Start".
If the formula is empty, duration is derived from Start/End (or Start→now for running timers).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
CONFIG_PATH = SCRIPT_DIR / "config.json"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Windows TimeZoneKeyName / tzname → IANA (keeps PST/PDT transitions correct)
_WINDOWS_TZ_TO_IANA = {
    "Pacific Standard Time": "America/Los_Angeles",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Central Standard Time": "America/Chicago",
    "Eastern Standard Time": "America/New_York",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "UTC": "UTC",
    "Greenwich Standard Time": "UTC",
}


class NotionError(Exception):
    pass


def _windows_timezone_key():
    """Read the active Windows time zone key, or None off Windows / on failure."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as key:
            name, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
            return (name or "").strip() or None
    except OSError as e:
        print(f"warn: could not read Windows timezone key: {e}", file=sys.stderr)
        return None


def get_local_timezone(fallback="America/Los_Angeles"):
    """Resolve the host timezone dynamically (IANA ZoneInfo when possible)."""
    win_key = _windows_timezone_key()
    if win_key and win_key in _WINDOWS_TZ_TO_IANA:
        return ZoneInfo(_WINDOWS_TZ_TO_IANA[win_key])

    # time.tzname[0] is often the Windows standard name on this platform
    if time.tzname:
        std_name = time.tzname[0]
        if std_name in _WINDOWS_TZ_TO_IANA:
            return ZoneInfo(_WINDOWS_TZ_TO_IANA[std_name])

    local = datetime.now().astimezone().tzinfo
    if getattr(local, "key", None):
        return local
    if local is not None and not win_key:
        # Non-Windows: prefer ZoneInfo from key if present; else keep system tzinfo
        return local

    try:
        return ZoneInfo(fallback)
    except Exception as e:
        print(f"warn: invalid fallback timezone {fallback!r}: {e}", file=sys.stderr)
        return timezone.utc


def load_env():
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method, path, token, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{NOTION_API}/{path}", data=data, headers=_headers(token), method=method
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _extract_number(prop):
    """Pull a number out of a Notion property value, handling number/formula/rollup."""
    if not prop:
        return 0.0
    t = prop.get("type")
    if t == "number":
        return float(prop.get("number") or 0)
    if t == "formula":
        f = prop.get("formula") or {}
        if f.get("type") == "number":
            return float(f.get("number") or 0)
    if t == "rollup":
        r = prop.get("rollup") or {}
        if r.get("type") == "number":
            return float(r.get("number") or 0)
        if r.get("type") == "array":
            return sum(_extract_number(item) for item in r.get("array", []))
    return 0.0


def _parse_notion_dt(value):
    """Parse Notion date/datetime strings into timezone-aware UTC datetimes."""
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime(int(value[0:4]), int(value[5:7]), int(value[8:10]), tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as e:
        print(f"warn: could not parse Notion datetime {value!r}: {e}", file=sys.stderr)
        return None


def _extract_date_start(prop):
    if not prop or prop.get("type") != "date":
        return None
    date_obj = prop.get("date") or {}
    return _parse_notion_dt(date_obj.get("start"))


def _hours_from_start_end(start_dt, end_dt):
    if start_dt is None:
        return 0.0
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    seconds = (end_dt - start_dt).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / 3600.0


def _row_hours(props, hours_property, date_property="Start", end_property="End"):
    """Prefer formula/number hours; fall back to date_property→end_property duration."""
    formula_hours = _extract_number(props.get(hours_property))
    if formula_hours > 0:
        return formula_hours
    start_dt = _extract_date_start(props.get(date_property))
    end_dt = _extract_date_start(props.get(end_property)) if end_property else None
    return _hours_from_start_end(start_dt, end_dt)


def _formula_string(prop):
    if not prop or prop.get("type") != "formula":
        return ""
    f = prop.get("formula") or {}
    if f.get("type") == "string":
        return f.get("string") or ""
    return ""


def _rollup_relation_ids(prop):
    ids = []
    if not prop or prop.get("type") != "rollup":
        return ids
    rollup = prop.get("rollup") or {}
    if rollup.get("type") != "array":
        return ids
    for item in rollup.get("array") or []:
        if item.get("type") == "relation":
            for rel in item.get("relation") or []:
                rid = rel.get("id")
                if rid:
                    ids.append(rid)
    return ids


def _normalize_id(value):
    return (value or "").replace("-", "").lower()


def _client_text_and_ids(props, client_prop="Task Client Prop", client_rollup_prop="Task Client"):
    client_text = _formula_string(props.get(client_prop)).lower()
    relation_ids = [_normalize_id(rid) for rid in _rollup_relation_ids(props.get(client_rollup_prop))]
    return client_text, relation_ids


def row_matches_excluded_client(props, exclude_names=None, exclude_ids=None, client_prop="Task Client Prop", client_rollup_prop="Task Client"):
    """True when the row's client should be dropped from billable hours."""
    exclude_names = exclude_names or []
    exclude_ids = {_normalize_id(i) for i in (exclude_ids or []) if i}
    if not exclude_names and not exclude_ids:
        return False
    client_text, relation_ids = _client_text_and_ids(props, client_prop, client_rollup_prop)
    for name in exclude_names:
        if name and name.lower() in client_text:
            return True
    for rid in relation_ids:
        if rid in exclude_ids:
            return True
    return False


def row_matches_included_client(props, include_names=None, include_ids=None, client_prop="Task Client Prop", client_rollup_prop="Task Client"):
    """True when the row's client is in the include list (include-only mode)."""
    include_names = include_names or []
    include_ids = {_normalize_id(i) for i in (include_ids or []) if i}
    if not include_names and not include_ids:
        return True
    client_text, relation_ids = _client_text_and_ids(props, client_prop, client_rollup_prop)
    for name in include_names:
        if name and name.lower() in client_text:
            return True
    for rid in relation_ids:
        if rid in include_ids:
            return True
    return False


def _build_filter(date_property, start, end, agent_property=None, agent_user_id=None):
    clauses = [
        {"property": date_property, "date": {"on_or_after": start.isoformat()}},
        {"property": date_property, "date": {"on_or_before": end.isoformat()}},
    ]
    if agent_property and agent_user_id:
        clauses.append({
            "property": agent_property,
            "people": {"contains": agent_user_id},
        })
    return {"and": clauses}


def _row_local_date(props, date_property, local_tz):
    """Calendar date of the row's start in local_tz (Notion date filters are UTC-biased)."""
    start_dt = _extract_date_start(props.get(date_property))
    if start_dt is None:
        return None
    return start_dt.astimezone(local_tz).date()


def _status_name(props):
    status = props.get("Status") or {}
    if status.get("type") != "select":
        return ""
    select = status.get("select") or {}
    return select.get("name") or ""


def active_elapsed_hours(active_start, now=None):
    """Hours elapsed on a running timer from Start until now."""
    if active_start is None:
        return 0.0
    end = now or datetime.now(timezone.utc)
    return _hours_from_start_end(active_start, end)


def fetch_total_hours(
    database_id,
    hours_property,
    date_property,
    start,
    end,
    token,
    end_property="End",
    agent_property=None,
    agent_user_id=None,
    exclude_client_names=None,
    exclude_client_ids=None,
    include_client_names=None,
    include_client_ids=None,
    local_tz=None,
    include_running=True,
):
    """Sum hours for rows whose local start date falls in [start, end]."""
    if end < start:
        return 0.0, 0

    tz = local_tz or timezone.utc
    # Widen API window so UTC-edge rows are not dropped before local-date filtering
    api_start = start - timedelta(days=1)
    api_end = end + timedelta(days=1)

    body = {
        "filter": _build_filter(date_property, api_start, api_end, agent_property, agent_user_id),
        "page_size": 100,
    }
    total = 0.0
    rows = 0
    while True:
        data = _request("POST", f"databases/{database_id}/query", token, body)
        for page in data.get("results", []):
            props = page.get("properties", {}) or {}
            local_day = _row_local_date(props, date_property, tz)
            if local_day is None or local_day < start or local_day > end:
                continue
            if include_client_names or include_client_ids:
                if not row_matches_included_client(
                    props,
                    include_names=include_client_names,
                    include_ids=include_client_ids,
                ):
                    continue
            elif row_matches_excluded_client(
                props,
                exclude_names=exclude_client_names,
                exclude_ids=exclude_client_ids,
            ):
                continue
            if not include_running and _status_name(props) == "Running":
                continue
            total += _row_hours(
                props,
                hours_property,
                date_property=date_property,
                end_property=end_property,
            )
            rows += 1
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return total, rows


def fetch_active_timer_start(
    database_id,
    date_property,
    token,
    agent_property=None,
    agent_user_id=None,
    exclude_client_names=None,
    exclude_client_ids=None,
    include_client_names=None,
    include_client_ids=None,
):
    """Return Start datetime of the newest Running timer (or None)."""
    clauses = [{"property": "Status", "select": {"equals": "Running"}}]
    if agent_property and agent_user_id:
        clauses.append({
            "property": agent_property,
            "people": {"contains": agent_user_id},
        })
    body = {
        "filter": {"and": clauses} if len(clauses) > 1 else clauses[0],
        "page_size": 100,
    }
    best_start = None
    while True:
        data = _request("POST", f"databases/{database_id}/query", token, body)
        for page in data.get("results", []):
            props = page.get("properties", {}) or {}
            if include_client_names or include_client_ids:
                if not row_matches_included_client(
                    props,
                    include_names=include_client_names,
                    include_ids=include_client_ids,
                ):
                    continue
            elif row_matches_excluded_client(
                props,
                exclude_names=exclude_client_names,
                exclude_ids=exclude_client_ids,
            ):
                continue
            start_dt = _extract_date_start(props.get(date_property))
            if start_dt is None:
                continue
            if best_start is None or start_dt > best_start:
                best_start = start_dt
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return best_start


def get_database_schema(database_id, token):
    return _request("GET", f"databases/{database_id}", token)


def _friendly_http_error(code, message, database_id):
    if code == 404:
        return (
            f"HTTP 404: Time Tracker DB not shared with this integration "
            f"(id={database_id}). In Notion open Time Tracker, use the ... menu, "
            f"Connections, and add the integration that owns NOTION_SECRET "
            f"(currently \"Client OS\"). API said: {message}"
        )
    if code == 400 and "Date" in (message or ""):
        return (
            f"HTTP 400: {message}. Restart the widget so it reloads config "
            f"(date property should be \"Start\", not \"Date\")."
        )
    return f"HTTP {code}: {message}"


def _with_notion_errors(database_id, fn):
    try:
        return fn()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("message", body)
        except Exception as parse_err:
            print(f"error: failed to parse Notion error body: {parse_err}", file=sys.stderr)
            msg = body
        raise NotionError(_friendly_http_error(e.code, msg, database_id)) from e
    except urllib.error.URLError as e:
        raise NotionError(f"network error: {e.reason}") from e


def _require_token_and_db(database_id):
    env = load_env()
    token = env.get("NOTION_SECRET")
    if not token:
        raise NotionError("NOTION_SECRET not set in .env")
    if not database_id:
        raise NotionError("notion_database_id not set in config.json")
    return token


def fetch_total_hours_safe(
    database_id,
    hours_property,
    date_property,
    start,
    end,
    end_property="End",
    agent_property=None,
    agent_user_id=None,
    exclude_client_names=None,
    exclude_client_ids=None,
    include_client_names=None,
    include_client_ids=None,
    local_tz=None,
    include_running=True,
):
    """Wrapper that loads the token, calls the API, and converts errors to NotionError."""
    token = _require_token_and_db(database_id)
    return _with_notion_errors(
        database_id,
        lambda: fetch_total_hours(
            database_id,
            hours_property,
            date_property,
            start,
            end,
            token,
            end_property=end_property,
            agent_property=agent_property,
            agent_user_id=agent_user_id,
            exclude_client_names=exclude_client_names,
            exclude_client_ids=exclude_client_ids,
            include_client_names=include_client_names,
            include_client_ids=include_client_ids,
            local_tz=local_tz,
            include_running=include_running,
        ),
    )


def fetch_active_timer_start_safe(
    database_id,
    date_property,
    agent_property=None,
    agent_user_id=None,
    exclude_client_names=None,
    exclude_client_ids=None,
    include_client_names=None,
    include_client_ids=None,
):
    """Safe wrapper for fetch_active_timer_start."""
    token = _require_token_and_db(database_id)
    return _with_notion_errors(
        database_id,
        lambda: fetch_active_timer_start(
            database_id,
            date_property,
            token,
            agent_property=agent_property,
            agent_user_id=agent_user_id,
            exclude_client_names=exclude_client_names,
            exclude_client_ids=exclude_client_ids,
            include_client_names=include_client_names,
            include_client_ids=include_client_ids,
        ),
    )


def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch worked hours from Notion Time Tracker")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved DB/properties/date range without calling Notion",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Call Notion and print summed hours for the given (or config) date range",
    )
    parser.add_argument("--start", help="Inclusive start date YYYY-MM-DD (with --test)")
    parser.add_argument("--end", help="Inclusive end date YYYY-MM-DD (with --test)")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.test:
        parser.error("Specify --dry-run and/or --test")

    config = _load_config()
    local_tz = get_local_timezone(fallback=config.get("timezone") or "America/Los_Angeles")
    if args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    else:
        today = datetime.now(local_tz).date()
        start = today.replace(day=1)
        end = today

    database_id = config.get("notion_database_id")
    hours_property = config.get("notion_hours_property", "Time (Hrs)")
    date_property = config.get("notion_date_property", "Start")
    end_property = config.get("notion_end_property", "End")
    agent_property = config.get("notion_agent_property") or None
    agent_user_id = config.get("notion_agent_user_id") or None
    exclude_names = config.get("notion_exclude_client_names") or []
    exclude_ids = config.get("notion_exclude_client_ids") or []

    print(f"database_id={database_id}")
    print(f"hours_property={hours_property!r} date_property={date_property!r} end_property={end_property!r}")
    print(f"agent_property={agent_property!r} agent_user_id={agent_user_id!r}")
    print(f"exclude_client_names={exclude_names!r}")
    print(f"exclude_client_ids={exclude_ids!r}")
    print(f"local_tz={local_tz}")
    print(f"range={start.isoformat()} .. {end.isoformat()}")

    if args.dry_run and not args.test:
        print("dry-run: skipping Notion API call")
        return 0

    total, rows = fetch_total_hours_safe(
        database_id,
        hours_property,
        date_property,
        start,
        end,
        end_property=end_property,
        agent_property=agent_property,
        agent_user_id=agent_user_id,
        exclude_client_names=exclude_names,
        exclude_client_ids=exclude_ids,
        local_tz=local_tz,
    )
    print(f"rows={rows} total_hours={total:.2f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NotionError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
