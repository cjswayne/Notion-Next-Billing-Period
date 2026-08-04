"""Offline tests for Notion hours extraction and pace math."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from notion_client import (
    _extract_number,
    _hours_from_start_end,
    _row_hours,
    _row_local_date,
    row_matches_excluded_client,
)
from widget import (
    finish_by_label,
    format_clock,
    format_hm,
    format_pace_tooltip,
    format_status_line,
    hours_per_day,
    hours_remaining,
    hours_to_meet_day_goal,
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
        # 2026-08-03 20:16 PDT is still Aug 3 in America/New_York
        props = {
            "Start": {
                "type": "date",
                "date": {"start": "2026-08-03T20:16:00.000-07:00", "end": None},
            }
        }
        ny = ZoneInfo("America/New_York")
        self.assertEqual(_row_local_date(props, "Start", ny).isoformat(), "2026-08-03")


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
        self.assertEqual(format_hm(4), "4:00")
        self.assertEqual(format_hm(4.5), "4:30")
        self.assertEqual(format_hm(0.25), "0:15")
        self.assertEqual(format_hm(-1.25), "-1:15")

    def test_format_status_line(self):
        self.assertEqual(format_status_line(10, 14, 54), "10 days left | 4:00/day")
        self.assertEqual(format_status_line(1, 14, 54), "1 day left | 40:00/day")
        self.assertEqual(format_status_line(10, 60, 54), "10 days left | done")
        self.assertEqual(format_status_line(10, None, 54), "10 days left | …")

    def test_hours_to_meet_day_goal(self):
        # 4:00/day min, 1.5h already today → 2:30 left
        self.assertAlmostEqual(hours_to_meet_day_goal(14, 1.5, 54, 10), 2.5)
        self.assertEqual(hours_to_meet_day_goal(14, 5, 54, 10), 0.0)

    def test_format_clock_and_finish_by(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(format_clock(now), "3:30PM")
        self.assertEqual(finish_by_label(now, 2.5), "(6:00PM)")
        self.assertEqual(finish_by_label(now, 0), "(3:30PM)")

    def test_format_pace_tooltip_lists_days_off(self):
        now = datetime(2026, 8, 4, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        tip = format_pace_tooltip(14, 54, 10, today_hours=1.5, now=now)
        self.assertIn("40:00 left to 54:00", tip)
        self.assertIn("2:30 to meet min goal (6:00PM) | 1:30 worked today", tip)
        # 40/9 ≈ 4:27/day → 2:57 left after 1:30 today → 6:27PM
        self.assertIn("off 1 day: 4:27/day | 2:57 to meet (6:27PM)", tip)
        self.assertIn("off 2 days: 5:00/day | 3:30 to meet (7:00PM)", tip)
        self.assertIn("off 3 days: 5:43/day | 4:13 to meet (7:43PM)", tip)


if __name__ == "__main__":
    unittest.main()
