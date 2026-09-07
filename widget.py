import calendar
import ctypes
import json
import os
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from notion_client import (
    NotionError,
    active_elapsed_hours,
    fetch_active_timer_start_safe,
    fetch_total_hours_safe,
    get_local_timezone,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
POSITION_PATH = SCRIPT_DIR / ".position"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_end_days(end_days, year, month):
    last = calendar.monthrange(year, month)[1]
    resolved = []
    for d in end_days:
        if isinstance(d, str) and d.lower() == "last":
            resolved.append(last)
        else:
            resolved.append(min(int(d), last))
    return sorted(set(resolved))


def days_until_next_end(today, end_days):
    resolved = resolve_end_days(end_days, today.year, today.month)
    for end in resolved:
        if end >= today.day:
            return end - today.day

    next_month = today.month + 1
    next_year = today.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    next_resolved = resolve_end_days(end_days, next_year, next_month)
    target = date(next_year, next_month, next_resolved[0])
    return (target - today).days


def current_billing_period(today, end_days):
    """Return (start_date, end_date) for the billing period that contains `today`."""
    resolved_this = resolve_end_days(end_days, today.year, today.month)
    for end_day in resolved_this:
        if end_day >= today.day:
            end = date(today.year, today.month, end_day)
            earlier = [d for d in resolved_this if d < end_day]
            if earlier:
                prev_end = date(today.year, today.month, max(earlier))
            else:
                py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
                prev_end = date(py, pm, resolve_end_days(end_days, py, pm)[-1])
            return prev_end + timedelta(days=1), end
    raise RuntimeError("billing_period_end_days must include 'last' or the month's last day")


def format_days_left(days):
    if days == 1:
        return "1 day left"
    return f"{days} days left"


def format_hm(hours):
    """Format decimal hours as 'Hhrs & Mmin'."""
    if hours is None:
        return None
    sign = "-" if hours < 0 else ""
    total_minutes = int(round(abs(float(hours)) * 60))
    h, m = divmod(total_minutes, 60)
    return f"{sign}{h}hrs & {m}min"


def format_hm_prose(hours):
    """Alias for format_hm (kept for callers that used the prose variant)."""
    return format_hm(hours)


def format_hm_per_day(hours):
    formatted = format_hm(hours)
    if formatted is None:
        return None
    return f"{formatted} / day"


def format_target_hours(hours):
    """Format a target as whole hours only (no minutes), e.g. 60 -> '60hrs'."""
    if hours is None:
        return None
    sign = "-" if hours < 0 else ""
    return f"{sign}{int(round(abs(float(hours))))}hrs"


def hours_remaining(worked, target):
    if target is None:
        return None
    return float(target) - float(worked)


def hours_per_day(remaining, days_left, days_off=0):
    """Spread remaining hours over days_left minus optional days off."""
    if remaining is None:
        return None
    work_days = days_left - days_off
    # Pitfall: last day / taking more days off than remain → treat as one day
    if work_days <= 0:
        work_days = 1 if days_left >= 0 else 1
    return remaining / work_days


def hours_to_meet_day_goal(worked, today_hours, target, days_left, days_off=0):
    """Hours still needed today to hit the paced daily goal (optionally with days off)."""
    remaining = hours_remaining(worked, target)
    if remaining is None:
        return None
    if remaining <= 0:
        return 0.0
    daily_goal = hours_per_day(remaining, days_left, days_off=days_off)
    logged_today = float(today_hours or 0)
    return max(daily_goal - logged_today, 0.0)


def available_days_off(days_left):
    """Days-off tiers that fit in the remaining period (0 = base min goal)."""
    return [0] + [n for n in (1, 2, 3) if n <= days_left]


def current_goal_days_off(worked, today_hours, target, days_left):
    """Lowest unmet days-off tier; bumps up after today's min for that tier is hit."""
    remaining = hours_remaining(worked, target)
    if remaining is None or remaining <= 0:
        return None
    tiers = available_days_off(days_left)
    for off in tiers:
        to_meet = hours_to_meet_day_goal(
            worked, today_hours, target, days_left, days_off=off
        )
        if to_meet is not None and to_meet > 0:
            return off
    return tiers[-1]


def goal_tier_label(days_off):
    if not days_off:
        return "min goal"
    label = "day" if days_off == 1 else "days"
    return f"off {days_off} {label} goal"


def format_clock(dt):
    """Format a datetime as 6:06PM (no leading zero on the hour)."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{hour12}:{dt.minute:02d}{ampm}"


def finish_by_datetime(now, hours_needed):
    if now is None or hours_needed is None:
        return None
    minutes = int(round(float(hours_needed) * 60))
    if minutes <= 0:
        return now
    return now + timedelta(minutes=minutes)


def finish_by_label(now, hours_needed):
    """Clock time if work continues from now for hours_needed, e.g. (6:06PM)."""
    finish = finish_by_datetime(now, hours_needed)
    if finish is None:
        return ""
    return f"({format_clock(finish)})"


def halfway_by_label(now, hours_needed):
    """Clock time at the halfway point of hours_needed from now."""
    if now is None or hours_needed is None:
        return ""
    halfway = finish_by_datetime(now, float(hours_needed) / 2)
    if halfway is None:
        return ""
    return format_clock(halfway)


def halfway_clock_label(now, per_day_hours, worked_today_hours=0.0):
    """Clock time when today's logged hours reach half of the full per-day
    goal (not half of what's left). Empty once that halfway point has
    already passed, since already-worked hours count toward it."""
    if now is None or per_day_hours is None:
        return ""
    remaining_to_half = float(per_day_hours) / 2.0 - float(worked_today_hours or 0)
    if remaining_to_half <= 0:
        return ""
    dt = finish_by_datetime(now, remaining_to_half)
    if dt is None:
        return ""
    return format_clock(dt)


def widget_days_off(days_left):
    """Always pace the widget for one day off when the period allows it."""
    return 1 if days_left >= 1 else 0


def format_status_line(
    days_left,
    worked,
    target,
    today_hours=0.0,
    now=None,
    app_worked=None,
    app_target=None,
    app_today_hours=0.0,
):
    """Single-line summary: per-day pace for client (and app, if tracked), plus
    a combined finish time that accounts for both goals' remaining hours today."""
    days_part = format_days_left(days_left)
    if worked is None:
        return f"{days_part} | …"
    remaining = hours_remaining(worked, target)
    if remaining is None:
        return f"{days_part} | {format_hm(worked)}"

    app_tracked = app_target is not None
    app_remaining = hours_remaining(app_worked or 0, app_target) if app_tracked else None
    client_done = remaining <= 0
    app_done = app_tracked and app_remaining is not None and app_remaining <= 0

    if client_done and (not app_tracked or app_done):
        return f"{days_part} | done"

    off = widget_days_off(days_left)
    parts = [days_part]

    client_per_day = None
    client_to_meet = 0.0
    if client_done:
        parts.append("client done")
    else:
        client_per_day = hours_per_day(remaining, days_left, days_off=off)
        client_to_meet = hours_to_meet_day_goal(
            worked, today_hours, target, days_left, days_off=off
        )
        parts.append(f"client {format_hm_per_day(client_per_day)}")

    app_to_meet = 0.0
    if app_tracked:
        if app_done:
            parts.append("app done")
        else:
            app_per_day = hours_per_day(app_remaining, days_left, days_off=off)
            app_to_meet = hours_to_meet_day_goal(
                app_worked or 0, app_today_hours, app_target, days_left, days_off=off
            )
            parts.append(f"app {format_hm_per_day(app_per_day)}")

    combined_to_meet = client_to_meet + app_to_meet
    if combined_to_meet <= 0:
        parts.append("met today")
    else:
        parts.append(f"{format_hm(combined_to_meet)} left today")
        finish = finish_by_datetime(now, combined_to_meet)
        if finish is not None:
            # Halfway is specifically about the client goal, not the combined total
            halfway = "" if client_done else halfway_clock_label(now, client_per_day, today_hours)
            if halfway:
                parts.append(f"halfway {halfway}")
            parts.append(f"done {format_clock(finish)}")

    return " | ".join(parts)


def format_active_timer_note(active_elapsed):
    if not active_elapsed or active_elapsed <= 0:
        return ""
    return f" (−{format_hm(active_elapsed)} active timer)"


def effective_today_hours(stopped_today, active_start, now=None):
    """Stopped hours today plus live elapsed on the active Running timer."""
    active = active_elapsed_hours(active_start, now=now)
    return float(stopped_today or 0) + active, active


def format_pace_tooltip(
    worked,
    target,
    days_left,
    today_hours=0.0,
    now=None,
    active_elapsed=0.0,
    app_worked=None,
    app_target=None,
    app_today_hours=0.0,
    app_active_elapsed=0.0,
):
    """Multi-line breakdown: remaining/target/pace per goal, today's progress,
    and a combined 'need more today' line whose finish/halfway times include
    both client and app hours. Off-2/off-3 'what if I take more days off'
    rows stay client-only and only show while the client target is open."""
    remaining = hours_remaining(worked, target)
    if remaining is None:
        return f"{format_hm(worked)} logged (no target set)"

    app_tracked = app_target is not None
    app_remaining = hours_remaining(app_worked or 0, app_target) if app_tracked else None
    client_done = remaining <= 0
    app_done = app_tracked and app_remaining is not None and app_remaining <= 0
    off = widget_days_off(days_left)

    lines = []
    client_per_day = None
    if client_done:
        lines.append(f"Client: done (+{format_hm(-remaining)})")
    else:
        client_per_day = hours_per_day(remaining, days_left, days_off=off)
        lines.append(
            f"Client: {format_hm(remaining)} left of {format_target_hours(target)} "
            f"({format_hm_per_day(client_per_day)})"
        )
    app_per_day = None
    if app_tracked:
        if app_done:
            lines.append(f"App: done (+{format_hm(-app_remaining)})")
        else:
            app_per_day = hours_per_day(app_remaining, days_left, days_off=off)
            lines.append(
                f"App: {format_hm(app_remaining)} left of {format_target_hours(app_target)} "
                f"({format_hm_per_day(app_per_day)})"
            )

    if client_done and (not app_tracked or app_done):
        return "\n".join(lines)

    lines.append("")

    active_note = format_active_timer_note(active_elapsed)
    app_active_note = format_active_timer_note(app_active_elapsed)
    today_line = f"Today: {format_hm(today_hours or 0)} worked{active_note}"
    if app_tracked:
        today_line += f" | {format_hm(app_today_hours or 0)} app{app_active_note}"
    lines.append(today_line)

    client_to_meet = 0.0
    if not client_done:
        client_to_meet = hours_to_meet_day_goal(
            worked, today_hours, target, days_left, days_off=off
        )
    app_to_meet = 0.0
    if app_tracked and not app_done:
        app_to_meet = hours_to_meet_day_goal(
            app_worked or 0, app_today_hours, app_target, days_left, days_off=off
        )
    combined_to_meet = client_to_meet + app_to_meet

    if combined_to_meet <= 0:
        lines.append("Met today's goal")
    else:
        finish = finish_by_datetime(now, combined_to_meet)
        need_line = f"Need: {format_hm(combined_to_meet)} more"
        if finish is not None:
            need_line += f" \u2192 done {format_clock(finish)}"
            # Halfway is specifically about the client goal, not the combined total
            halfway = "" if client_done else halfway_clock_label(now, client_per_day, today_hours)
            if halfway:
                need_line += f" (halfway {halfway})"
        lines.append(need_line)

    if not client_done:
        for off_n in (2, 3):
            if off_n > days_left:
                continue
            per_day = hours_per_day(remaining, days_left, days_off=off_n)
            to_meet = hours_to_meet_day_goal(
                worked, today_hours, target, days_left, days_off=off_n
            )
            finish = finish_by_datetime(now, to_meet)
            label = "day" if off_n == 1 else "days"
            line = f"Off {off_n} {label}: client {format_hm_per_day(per_day)}"
            if finish is not None:
                line += f" \u2192 done {format_clock(finish)}"
            lines.append(line)

    return "\n".join(lines)


def format_earnings_line(worked, rate):
    if rate is None or rate <= 0:
        return None
    return f"${worked * rate:,.2f} earned"


def load_position():
    try:
        with open(POSITION_PATH, "r", encoding="utf-8") as f:
            x, y = f.read().strip().split(",")
            return int(x), int(y)
    except Exception:
        return None


def save_position(x, y):
    try:
        with open(POSITION_PATH, "w", encoding="utf-8") as f:
            f.write(f"{x},{y}")
    except Exception:
        pass


def get_work_area():
    """Return (left, top, right, bottom) of the primary monitor's work area
    (the screen minus the taskbar). Falls back to full screen on non-Windows."""
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        # SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


class HoverTip:
    """Simple multi-line tooltip shown while the pointer is over a widget."""

    def __init__(self, widget, delay_ms=400):
        self.widget = widget
        self.delay_ms = delay_ms
        self.text = ""
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text):
        self.text = text or ""
        if self._tip is not None:
            self._hide()

    def _schedule(self, _event=None):
        self._cancel()
        if not self.text:
            return
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip is not None or not self.text:
            return
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except Exception as e:
            print(f"warn: tooltip topmost failed: {e}", file=sys.stderr)
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#111111",
            foreground="#f0f0f0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            padx=6,
            pady=4,
        )
        label.pack()
        tw.update_idletasks()
        tip_w = tw.winfo_reqwidth()
        tip_h = tw.winfo_reqheight()
        pointer_x = self.widget.winfo_pointerx()
        pointer_y = self.widget.winfo_pointery()

        wa = get_work_area()
        if wa is not None:
            left, top, right, bottom = wa
        else:
            left, top = 0, 0
            right = self.widget.winfo_screenwidth()
            bottom = self.widget.winfo_screenheight()

        # Prefer above the cursor; flip below if there is not enough room
        x = pointer_x + 8
        y = pointer_y - tip_h - 10
        if y < top + 4:
            y = pointer_y + 16
        if y + tip_h > bottom - 4:
            y = max(top + 4, bottom - tip_h - 4)
        if x + tip_w > right - 4:
            x = right - tip_w - 4
        if x < left + 4:
            x = left + 4
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class Widget:
    def __init__(self, config):
        self.config = config
        # Prefer OS timezone (e.g. Pacific Standard Time → America/Los_Angeles)
        self.tz = get_local_timezone(
            fallback=config.get("timezone") or "America/Los_Angeles"
        )

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(config.get("always_on_top", False)))
        self.root.attributes("-alpha", float(config.get("opacity", 0.85)))
        bg = config.get("bg_color", "#1e1e1e")
        fg = config.get("fg_color", "#e6e6e6")
        font = (config.get("font_family", "Segoe UI"), int(config.get("font_size", 10)))
        self.root.configure(bg=bg)

        self.frame = tk.Frame(self.root, bg=bg, padx=8, pady=4)
        self.frame.pack()
        self.hours_label = tk.Label(self.frame, text="", font=font, fg=fg, bg=bg, anchor="w", justify="left")
        self.earn_label = tk.Label(self.frame, text="", font=font, fg=fg, bg=bg, anchor="w", justify="left")
        self.hours_label.pack(anchor="w")
        self.earn_label.pack(anchor="w")
        self.hours_tip = HoverTip(self.hours_label)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh hours", command=self.refresh_hours_async)
        self.menu.add_command(label="Reload config", command=self.reload)
        self.menu.add_command(label="Open config file", command=self.open_config)
        self.menu.add_command(label="Toggle always-on-top", command=self.toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.root.destroy)

        for w in (self.root, self.frame, self.hours_label, self.earn_label):
            w.bind("<ButtonPress-1>", self.start_drag)
            w.bind("<B1-Motion>", self.do_drag)
            w.bind("<ButtonRelease-1>", self.end_drag)
            w.bind("<Button-3>", self.show_menu)

        self._drag_offset = (0, 0)
        self._worked_hours = None
        self._app_worked_hours = None
        self._today_stopped_hours = 0.0
        self._app_today_stopped_hours = 0.0
        self._active_timer_start = None
        self._app_active_timer_start = None
        self._hours_error = None
        self._notion_lock = threading.Lock()

        self.position_window()
        self.update_text()
        self.refresh_hours_async()
        self.schedule_periodic_hours_refresh()
        self.schedule_active_timer_tick()

    def position_window(self):
        self.root.update_idletasks()
        saved = load_position()
        ww = self.root.winfo_reqwidth()
        wh = self.root.winfo_reqheight()
        if saved is not None:
            x, y = saved
        else:
            wa = get_work_area()
            if wa is not None:
                left, top, right, bottom = wa
                x = right - ww - 12
                y = bottom - wh - 6
            else:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                x = sw - ww - 20
                y = sh - wh - 60
        self.root.geometry(f"+{x}+{y}")

    def start_drag(self, event):
        self._drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def do_drag(self, event):
        ox, oy = self._drag_offset
        self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def end_drag(self, _event):
        save_position(self.root.winfo_x(), self.root.winfo_y())

    def show_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def toggle_topmost(self):
        self.root.attributes("-topmost", not bool(self.root.attributes("-topmost")))

    def open_config(self):
        try:
            os.startfile(str(CONFIG_PATH))
        except Exception as e:
            self.hours_label.configure(text=f"open failed: {e}")

    def reload(self):
        try:
            self.config = load_config()
            self.tz = get_local_timezone(
                fallback=self.config.get("timezone") or "America/Los_Angeles"
            )
            self.root.attributes("-alpha", float(self.config.get("opacity", 0.85)))
            bg = self.config.get("bg_color", "#1e1e1e")
            fg = self.config.get("fg_color", "#e6e6e6")
            font = (self.config.get("font_family", "Segoe UI"), int(self.config.get("font_size", 10)))
            self.root.configure(bg=bg)
            self.frame.configure(bg=bg)
            for lbl in (self.hours_label, self.earn_label):
                lbl.configure(font=font, fg=fg, bg=bg)
            self.update_text()
            self.refresh_hours_async()
        except Exception as e:
            self.hours_label.configure(text=f"config error: {e}")

    def render(self):
        now = datetime.now(self.tz)
        today = now.date()
        end_days = self.config["billing_period_end_days"]
        days = days_until_next_end(today, end_days)

        target = self.config.get("target_hours")
        app_target = self.config.get("target_app_hours")
        rate = self.config.get("hourly_rate")
        show_hours = bool(self.config.get("show_hours_line", True))
        show_earn = bool(self.config.get("show_earnings_line", True))

        if show_hours:
            if self._hours_error:
                self.hours_label.configure(
                    text=f"{format_days_left(days)} | {self._hours_error}"
                )
                self.hours_tip.set_text("")
            else:
                today_hours = 0.0
                active_elapsed = 0.0
                app_today_hours = 0.0
                app_active_elapsed = 0.0
                if self._worked_hours is not None:
                    today_hours, active_elapsed = effective_today_hours(
                        self._today_stopped_hours,
                        self._active_timer_start,
                        now=now,
                    )
                if self._app_worked_hours is not None:
                    app_today_hours, app_active_elapsed = effective_today_hours(
                        self._app_today_stopped_hours,
                        self._app_active_timer_start,
                        now=now,
                    )
                self.hours_label.configure(
                    text=format_status_line(
                        days,
                        self._worked_hours,
                        target,
                        today_hours=today_hours,
                        now=now,
                        app_worked=self._app_worked_hours,
                        app_target=app_target,
                        app_today_hours=app_today_hours,
                    )
                )
                if self._worked_hours is not None:
                    self.hours_tip.set_text(
                        format_pace_tooltip(
                            self._worked_hours,
                            target,
                            days,
                            today_hours=today_hours,
                            now=now,
                            active_elapsed=active_elapsed,
                            app_worked=self._app_worked_hours,
                            app_target=app_target,
                            app_today_hours=app_today_hours,
                            app_active_elapsed=app_active_elapsed,
                        )
                    )
                else:
                    self.hours_tip.set_text("")
            self.hours_label.pack(anchor="w")
        else:
            self.hours_tip.set_text("")
            self.hours_label.pack_forget()

        earn_text = None
        if show_earn and self._worked_hours is not None and not self._hours_error:
            earn_text = format_earnings_line(self._worked_hours, rate)
        if earn_text:
            self.earn_label.configure(text=earn_text)
            self.earn_label.pack(anchor="w")
        else:
            self.earn_label.pack_forget()

    def update_text(self):
        self.render()
        now = datetime.now(self.tz)
        today = now.date()
        tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), self.tz)
        ms_until_midnight = max(int((tomorrow - now).total_seconds() * 1000), 60_000)
        self.root.after(ms_until_midnight, self.update_text)

    def schedule_periodic_hours_refresh(self):
        refresh_min = float(self.config.get("notion_refresh_minutes", 15))
        if refresh_min > 0:
            self.root.after(int(refresh_min * 60_000), self._periodic_hours_tick)

    def _periodic_hours_tick(self):
        self.refresh_hours_async()
        self.schedule_periodic_hours_refresh()

    def schedule_active_timer_tick(self):
        # Recompute live Start→now elapsed / finish-by times without a Notion round-trip
        self.root.after(60_000, self._active_timer_tick)

    def _active_timer_tick(self):
        self.render()
        self.schedule_active_timer_tick()

    def _hours_query_range(self, today):
        start, period_end = current_billing_period(today, self.config["billing_period_end_days"])
        end = period_end
        if bool(self.config.get("notion_exclude_today", True)):
            end = min(end, today - timedelta(days=1))
        return start, end

    def refresh_hours_async(self):
        if not self.config.get("notion_database_id"):
            self._hours_error = "set notion_database_id in config"
            self.render()
            return
        if not self._notion_lock.acquire(blocking=False):
            return

        def worker():
            try:
                today = datetime.now(self.tz).date()
                start, end = self._hours_query_range(today)
                fetch_kwargs = dict(
                    end_property=self.config.get("notion_end_property", "End"),
                    agent_property=self.config.get("notion_agent_property") or None,
                    agent_user_id=self.config.get("notion_agent_user_id") or None,
                    exclude_client_names=self.config.get("notion_exclude_client_names") or [],
                    exclude_client_ids=self.config.get("notion_exclude_client_ids") or [],
                    local_tz=self.tz,
                )
                app_fetch_kwargs = dict(
                    end_property=fetch_kwargs["end_property"],
                    agent_property=fetch_kwargs["agent_property"],
                    agent_user_id=fetch_kwargs["agent_user_id"],
                    include_client_names=self.config.get("notion_app_client_names") or [],
                    include_client_ids=self.config.get("notion_app_client_ids") or [],
                    local_tz=self.tz,
                )
                database_id = self.config["notion_database_id"]
                hours_property = self.config.get("notion_hours_property", "Time (Hrs)")
                date_property = self.config.get("notion_date_property", "Start")
                try:
                    total, _rows = fetch_total_hours_safe(
                        database_id, hours_property, date_property, start, end, **fetch_kwargs
                    )
                    app_total, _app_rows = fetch_total_hours_safe(
                        database_id,
                        hours_property,
                        date_property,
                        start,
                        end,
                        **app_fetch_kwargs,
                    )
                    today_stopped = 0.0
                    app_today_stopped = 0.0
                    active_start = None
                    app_active_start = None
                    if bool(self.config.get("notion_exclude_today", True)):
                        today_stopped, _today_rows = fetch_total_hours_safe(
                            database_id,
                            hours_property,
                            date_property,
                            today,
                            today,
                            include_running=False,
                            **fetch_kwargs,
                        )
                        app_today_stopped, _app_today_rows = fetch_total_hours_safe(
                            database_id,
                            hours_property,
                            date_property,
                            today,
                            today,
                            include_running=False,
                            **app_fetch_kwargs,
                        )
                        active_start = fetch_active_timer_start_safe(
                            database_id,
                            date_property,
                            agent_property=fetch_kwargs.get("agent_property"),
                            agent_user_id=fetch_kwargs.get("agent_user_id"),
                            exclude_client_names=fetch_kwargs.get("exclude_client_names"),
                            exclude_client_ids=fetch_kwargs.get("exclude_client_ids"),
                        )
                        app_active_start = fetch_active_timer_start_safe(
                            database_id,
                            date_property,
                            agent_property=app_fetch_kwargs.get("agent_property"),
                            agent_user_id=app_fetch_kwargs.get("agent_user_id"),
                            include_client_names=app_fetch_kwargs.get("include_client_names"),
                            include_client_ids=app_fetch_kwargs.get("include_client_ids"),
                        )
                    worked, app_worked, err = total, app_total, None
                except NotionError as e:
                    worked, app_worked, today_stopped, app_today_stopped = None, None, 0.0, 0.0
                    active_start, app_active_start, err = None, None, str(e)

                def apply():
                    self._worked_hours = worked
                    self._app_worked_hours = app_worked
                    self._today_stopped_hours = today_stopped
                    self._app_today_stopped_hours = app_today_stopped
                    self._active_timer_start = active_start
                    self._app_active_timer_start = app_active_start
                    self._hours_error = err
                    self.render()
                self.root.after(0, apply)
            finally:
                self._notion_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def run(self):
        self.root.mainloop()


def main():
    try:
        config = load_config()
    except Exception as e:
        sys.stderr.write(f"Failed to load config: {e}\n")
        sys.exit(1)
    Widget(config).run()


if __name__ == "__main__":
    main()
