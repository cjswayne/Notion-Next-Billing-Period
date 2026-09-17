"""Offline tests for Notion hours extraction and pace math."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from notion_client import (
    _extract_number,
    _hours_from_start_end,
    _row_hours,
    _row_local_date,
    active_elapsed_hours,
    get_local_timezone,
    row_matches_excluded_client,
    row_matches_included_client,
)
from widget import (
    current_goal_days_off,
    effective_today_hours,
    finish_by_label,
    format_clock,
    format_hm,
    format_hm_per_day,
    format_hm_prose,
    format_pace_tooltip,
    format_status_line,
    format_target_hours,
    halfway_by_label,
    halfway_clock_label,
    hours_per_day,
    hours_remaining,
    hours_to_meet_day_goal,
    widget_days_off,
)


class TestHoursExtraction(unittest.TestCase):
    def test_formula_hours_preferred(self):
        props = {
            "Time (Hrs)": {"type": "formula", "formula": {"type": "number", "number": 1.5}},
            "Start": {
                "type": "date",
                "date": {"start": "2026-08-04T10:00:00.000Z", "end": None},
            },
            "End": {
                "type": "date",
                "date": {"start": "2026-08-04T12:00:00.000Z", "end": None},
            },
        }
        self.assertEqual(_row_hours(props, "Time (Hrs)"), 1.5)

    def test_duration_fallback_when_formula_zero(self):
        props = {
            "Time (Hrs)": {"type": "formula", "formula": {"type": "number", "number": 0}},
            "Start": {
                "type": "date",
                "date": {"start": "2026-08-04T15:29:00.000Z", "end": None},
            },
            "End": {
                "type": "date",
                "date": {"start": "2026-08-04T15:32:00.000Z", "end": None},
            },
        }
        self.assertAlmostEqual(_row_hours(props, "Time (Hrs)"), 0.05, places=4)

    def test_running_timer_uses_now(self):
        start = datetime.now(timezone.utc) - timedelta(minutes=30)
        hours = _hours_from_start_end(start, None)
        self.assertGreater(hours, 0.4)
        self.assertLess(hours, 0.6)

    def test_extract_number_rollup_array(self):
        prop = {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [
                    {"type": "number", "number": 1.0},
                    {"type": "number", "number": 2.5},
                ],
            },
        }
        self.assertEqual(_extract_number(prop), 3.5)


class TestLocalDateFilter(unittest.TestCase):
    def test_evening_pacific_stays_on_local_calendar_day(self):
        props = {
            "Start": {
                "type": "date",
                "date": {"start": "2026-08-03T20:16:00.000-07:00", "end": None},
            }
        }
        la = ZoneInfo("America/Los_Angeles")
        self.assertEqual(_row_local_date(props, "Start", la).isoformat(), "2026-08-03")

    def test_get_local_timezone_resolves_iana(self):
        tz = get_local_timezone()
        # On this Windows host we expect Pacific → America/Los_Angeles
        key = getattr(tz, "key", None)
        self.assertTrue(key or tz is not None)
        if key:
            self.assertEqual(key, "America/Los_Angeles")


class TestClientExclude(unittest.TestCase):
    def test_exclude_by_client_prop_name(self):
        props = {
            "Task Client Prop": {
                "type": "formula",
                "formula": {"type": "string", "string": "@JadePuma Apps"},
            }
        }
        self.assertTrue(
            row_matches_excluded_client(props, exclude_names=["JadePuma Apps"])
        )

    def test_keep_other_clients(self):
        props = {
            "Task Client Prop": {
                "type": "formula",
                "formula": {"type": "string", "string": "@The Open Road Printshop"},
            }
        }
        self.assertFalse(
            row_matches_excluded_client(props, exclude_names=["JadePuma Apps"])
        )

    def test_exclude_by_client_id(self):
        props = {
            "Task Client Prop": {
                "type": "formula",
                "formula": {"type": "string", "string": "@Something"},
            },
            "Task Client": {
                "type": "rollup",
                "rollup": {
                    "type": "array",
                    "array": [
                        {
                            "type": "relation",
                            "relation": [{"id": "59c6169f-08fb-4a17-901e-4c375bf18d7b"}],
                        }
                    ],
                },
            },
        }
        self.assertTrue(
            row_matches_excluded_client(
                props,
                exclude_ids=["59c6169f08fb4a17901e4c375bf18d7b"],
            )
        )


class TestClientInclude(unittest.TestCase):
    def test_include_by_client_prop_name(self):
        props = {
            "Task Client Prop": {
                "type": "formula",
                "formula": {"type": "string", "string": "@JadePuma Apps"},
            }
        }
        self.assertTrue(
            row_matches_included_client(props, include_names=["JadePuma Apps"])
        )

    def test_exclude_other_clients_from_include(self):
        props = {
            "Task Client Prop": {
                "type": "formula",
                "formula": {"type": "string", "string": "@The Open Road Printshop"},
            }
        }
        self.assertFalse(
            row_matches_included_client(props, include_names=["JadePuma Apps"])
        )


class TestPaceMath(unittest.TestCase):
    def test_hours_needed_per_day(self):
        # 54 target, 14 worked, 10 days left → 4.0 h/day
        remaining = hours_remaining(14, 54)
        self.assertEqual(remaining, 40)
        self.assertEqual(hours_per_day(remaining, 10, days_off=0), 4.0)
        self.assertAlmostEqual(hours_per_day(remaining, 10, days_off=1), 40 / 9)
        self.assertAlmostEqual(hours_per_day(remaining, 10, days_off=2), 5.0)
        self.assertAlmostEqual(hours_per_day(remaining, 10, days_off=3), 40 / 7)

    def test_format_hm(self):
        self.assertEqual(format_hm(4), "4hrs & 0min")
        self.assertEqual(format_hm(4.5), "4hrs & 30min")
        self.assertEqual(format_hm(0.25), "0hrs & 15min")
        self.assertEqual(format_hm(-1.25), "-1hrs & 15min")

    def test_format_hm_prose(self):
        self.assertEqual(format_hm_prose(4), "4hrs & 0min")
        self.assertEqual(format_hm_prose(4.5), "4hrs & 30min")

    def test_format_hm_per_day(self):
        self.assertEqual(format_hm_per_day(4.5), "4hrs & 30min / day")

    def test_format_target_hours(self):
        self.assertEqual(format_target_hours(60), "60hrs")
        self.assertEqual(format_target_hours(60.4), "60hrs")
        self.assertEqual(format_target_hours(20), "20hrs")

    def test_format_status_line(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(
            format_status_line(10, 14, 54, today_hours=0, now=now),
            "10 days left | client 4hrs & 27min / day | client 4hrs & 27min left today | "
            "halfway 5:43PM | done 7:57PM",
        )
        self.assertEqual(format_status_line(10, 60, 54, today_hours=0, now=now), "10 days left | done")
        self.assertEqual(format_status_line(10, None, 54), "10 days left | …")

    def test_format_status_line_shows_app_pace_and_combined_finish(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        line = format_status_line(
            10, 14, 54, today_hours=0, now=now, app_worked=5, app_target=20
        )
        self.assertIn("client 4hrs & 27min / day", line)
        self.assertIn("app 1hrs & 40min / day", line)
        # "Left today" is client-only, matching the client-only halfway
        self.assertIn("client 4hrs & 27min left today", line)
        self.assertNotIn("6hrs & 7min left today", line)
        # Client-only finish time, shown next to the combined (client+app) finish time
        self.assertIn("client off 7:57PM", line)
        self.assertIn("+app off 9:37PM", line)
        self.assertIn("halfway 5:43PM", line)

    def test_format_status_line_hides_app_when_no_app_target(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        # Even if app_worked is passed, no app_target means app is not tracked
        line = format_status_line(
            10, 14, 54, today_hours=0, now=now, app_worked=5, app_target=None
        )
        self.assertNotIn("app ", line)
        self.assertIn("client ", line)

    def test_format_status_line_client_done_app_not(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        line = format_status_line(
            10, 60, 54, today_hours=0, now=now, app_worked=5, app_target=20
        )
        self.assertIn("client done", line)
        self.assertIn("app 1hrs & 40min / day", line)
        self.assertIn("done 5:10PM", line)
        # No client pacing left to show once the client's period target is met
        self.assertNotIn("left today", line)
        # Halfway is a client-only concept; no client goal left means no halfway
        self.assertNotIn("halfway", line)

    def test_format_status_line_both_done(self):
        line = format_status_line(10, 60, 54, app_worked=25, app_target=20)
        self.assertEqual(line, "10 days left | done")

    def test_widget_days_off(self):
        self.assertEqual(widget_days_off(10), 1)
        self.assertEqual(widget_days_off(1), 1)
        self.assertEqual(widget_days_off(0), 0)
        self.assertEqual(widget_days_off(10, preferred=0), 0)
        self.assertEqual(widget_days_off(10, preferred=2), 2)
        self.assertEqual(widget_days_off(10, preferred=3), 3)
        self.assertEqual(widget_days_off(2, preferred=3), 2)
        self.assertEqual(widget_days_off(5, preferred=-1), 0)

    def test_format_status_line_respects_days_off(self):
        line0 = format_status_line(10, 14, 54, days_off=0)
        self.assertIn("client 4hrs & 0min / day", line0)
        line2 = format_status_line(10, 14, 54, days_off=2)
        self.assertIn("client 5hrs & 0min / day", line2)
        # Default remains off-1 pacing
        line_default = format_status_line(10, 14, 54)
        self.assertIn("client 4hrs & 27min / day", line_default)

    def test_format_status_line_off_one_with_progress(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        # 4h already logged today exceeds half of the ~4:27 daily goal (~2:13),
        # so the halfway point has already passed and should not be shown.
        self.assertEqual(
            format_status_line(10, 14, 54, today_hours=4.0, now=now),
            "10 days left | client 4hrs & 27min / day | client 0hrs & 27min left today | "
            "done 3:57PM",
        )

    def test_format_status_line_hides_halfway_exactly_at_the_halfway_point(self):
        now = datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        # target=8, worked=0, days_left=2 -> off=1 -> per_day = 8/(2-1) = 8.0h, half = 4.0h
        before = format_status_line(2, 0, 8, today_hours=3.99, now=now)
        self.assertIn("halfway", before)
        at_halfway = format_status_line(2, 0, 8, today_hours=4.0, now=now)
        self.assertNotIn("halfway", at_halfway)
        after = format_status_line(2, 0, 8, today_hours=4.01, now=now)
        self.assertNotIn("halfway", after)

    def test_halfway_clock_label_accounts_for_hours_already_worked(self):
        now = datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        # 6:12 full daily goal, 2:15 already worked → half (3:06) is 51min away
        self.assertEqual(halfway_clock_label(now, 6.2, 2.25), "12:51PM")
        # Already worked past the halfway mark → no halfway shown
        self.assertEqual(halfway_clock_label(now, 6.2, 4.0), "")

    def test_hours_to_meet_day_goal(self):
        # 4:00/day min, 1.5h already today → 2:30 left
        self.assertAlmostEqual(hours_to_meet_day_goal(14, 1.5, 54, 10), 2.5)
        self.assertEqual(hours_to_meet_day_goal(14, 5, 54, 10), 0.0)

    def test_format_clock_and_finish_by(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(format_clock(now), "3:30PM")
        self.assertEqual(finish_by_label(now, 2.5), "(6:00PM)")
        self.assertEqual(finish_by_label(now, 0), "(3:30PM)")
        self.assertEqual(halfway_by_label(now, 2.5), "4:45PM")

    def test_active_timer_reduces_time_to_meet(self):
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime(2026, 8, 4, 15, 30, tzinfo=tz)
        active_start = now - timedelta(minutes=30)
        today_hours, active = effective_today_hours(1.0, active_start, now=now)
        self.assertAlmostEqual(active, 0.5)
        self.assertAlmostEqual(today_hours, 1.5)
        self.assertAlmostEqual(active_elapsed_hours(active_start, now=now), 0.5)

    def test_current_goal_days_off(self):
        self.assertEqual(current_goal_days_off(14, 4.0, 54, 10), 1)
        self.assertEqual(current_goal_days_off(14, 1.0, 54, 10), 0)
        self.assertEqual(current_goal_days_off(14, 4.5, 54, 10), 2)

    def test_format_pace_tooltip_lists_days_off(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        tip = format_pace_tooltip(14, 54, 10, today_hours=1.5, now=now)
        self.assertIn("Client: 40hrs & 0min left of 54hrs (4hrs & 27min / day)", tip)
        self.assertIn("Today: 1hrs & 30min worked", tip)
        # Halfway accounts for the 1:30 already logged today, not half of the remaining 2:57
        self.assertIn("Need: 2hrs & 57min more \u2192 done 6:27PM (halfway 4:13PM)", tip)
        self.assertIn("Off No Days: client 4hrs & 0min / day \u2192 done 6:00PM", tip)
        self.assertIn("Off 1 day: client 4hrs & 27min / day \u2192 done 6:27PM", tip)
        self.assertIn("Off 2 days: client 5hrs & 0min / day \u2192 done 7:00PM", tip)
        self.assertIn("Off 3 days: client 5hrs & 43min / day \u2192 done 7:43PM", tip)

    def test_format_pace_tooltip_respects_days_off(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        tip1 = format_pace_tooltip(14, 54, 10, today_hours=1.5, now=now, days_off=1)
        self.assertIn("Need: 2hrs & 57min more", tip1)
        self.assertIn("(4hrs & 27min / day)", tip1)
        tip2 = format_pace_tooltip(14, 54, 10, today_hours=1.5, now=now, days_off=2)
        self.assertIn("Need: 3hrs & 30min more", tip2)
        self.assertIn("(5hrs & 0min / day)", tip2)

    def test_format_pace_tooltip_off_no_days_appears_above_off_tiers(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        tip = format_pace_tooltip(14, 54, 10, today_hours=1.5, now=now)
        lines = tip.split("\n")
        no_days_i = lines.index("Off No Days: client 4hrs & 0min / day \u2192 done 6:00PM")
        off1_i = lines.index("Off 1 day: client 4hrs & 27min / day \u2192 done 6:27PM")
        off2_i = next(i for i, l in enumerate(lines) if l.startswith("Off 2 days:"))
        self.assertLess(no_days_i, off1_i)
        self.assertLess(off1_i, off2_i)

    def test_format_pace_tooltip_client_done_app_not(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        tip = format_pace_tooltip(
            60, 54, 10, today_hours=0, now=now, app_worked=5, app_target=20
        )
        self.assertIn("Client: done (+6hrs & 0min)", tip)
        self.assertIn("App: 15hrs & 0min left of 20hrs (1hrs & 40min / day)", tip)
        self.assertIn("Need: 1hrs & 40min more \u2192 done 5:10PM", tip)
        # Halfway is a client-only concept; client target already hit
        self.assertNotIn("halfway", tip)
        self.assertNotIn("Off No Days", tip)
        self.assertNotIn("Off 2 days", tip)
        self.assertNotIn("Off 3 days", tip)

    def test_format_pace_tooltip_shows_client_and_app_remaining(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        tip = format_pace_tooltip(
            14, 54, 10, today_hours=1.5, now=now, app_worked=5, app_target=20
        )
        self.assertIn("Client: 40hrs & 0min left of 54hrs (4hrs & 27min / day)", tip)
        self.assertIn("App: 15hrs & 0min left of 20hrs (1hrs & 40min / day)", tip)
        self.assertIn("Today: 1hrs & 30min worked | 0hrs & 0min app", tip)
        # Need line shows the client-only finish next to the combined (client+app) finish
        self.assertIn(
            "Need: 4hrs & 37min more \u2192 client off 6:27PM, +app off 8:07PM (halfway 4:13PM)",
            tip,
        )

    def test_format_pace_tooltip_still_shows_need_line_when_client_pacing(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        tip = format_pace_tooltip(14, 54, 10, today_hours=4.0, now=now)
        self.assertIn("Need:", tip)
        self.assertIn("Off 1 day:", tip)
        self.assertIn("Off 2 days:", tip)
        self.assertIn("Off 3 days:", tip)
        # 4h logged already exceeds half of the ~4:27 daily goal
        self.assertNotIn("halfway", tip)

    def test_format_pace_tooltip_shows_active_timer_note(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        tip = format_pace_tooltip(
            14, 54, 10, today_hours=2.0, now=now, active_elapsed=0.5
        )
        self.assertIn(
            "Today: 2hrs & 0min worked (\u22120hrs & 30min active timer)", tip
        )
        self.assertIn("Need: 2hrs & 27min more \u2192 done 5:57PM (halfway 3:43PM)", tip)

    def test_off_day_lines_respect_days_left(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        last_day = format_pace_tooltip(14, 54, 0, today_hours=1.0, now=now)
        self.assertNotIn("Off 1 day:", last_day)
        self.assertNotIn("Off 2 days:", last_day)
        self.assertNotIn("Off 3 days:", last_day)

        one_left = format_pace_tooltip(14, 54, 1, today_hours=1.0, now=now)
        self.assertIn("Off 1 day:", one_left)
        self.assertNotIn("Off 2 days:", one_left)
        self.assertNotIn("Off 3 days:", one_left)

        two_left = format_pace_tooltip(14, 54, 2, today_hours=1.0, now=now)
        self.assertIn("Off 1 day:", two_left)
        self.assertIn("Off 2 days:", two_left)
        self.assertNotIn("Off 3 days:", two_left)


if __name__ == "__main__":
    unittest.main()
