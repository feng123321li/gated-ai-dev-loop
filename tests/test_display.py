from __future__ import annotations

import unittest

from hdg.display import (
    format_display_date,
    format_display_minute,
    format_display_timestamp,
)


class DisplayFormattingTests(unittest.TestCase):
    def test_human_timestamps_default_to_utc_plus_eight(self) -> None:
        value = "2026-07-28T14:06:07.123Z"

        self.assertEqual(
            format_display_timestamp(value),
            "2026-07-28 22:06:07",
        )
        self.assertEqual(
            format_display_minute(value),
            "2026-07-28 22:06",
        )

    def test_human_date_uses_the_utc_plus_eight_calendar_day(self) -> None:
        self.assertEqual(
            format_display_date("2026-07-28T17:00:00Z"),
            "2026-07-29",
        )


if __name__ == "__main__":
    unittest.main()
