"""Regression checks for the Candidate -> Forming -> Confirmed H4 lifecycle."""

import unittest

import pandas as pd

from ob_detector import find_all_obs, find_forming_obs


def sample_frame(include_bos: bool) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-09-01T00:00:00Z")
    for index in range(20):
        rows.append((start + pd.Timedelta(hours=4 * index), 100.0, 101.0, 99.0, 100.2))
    rows.extend([
        (start + pd.Timedelta(hours=80), 100.0, 110.0, 99.5, 100.5),  # fractal high
        (start + pd.Timedelta(hours=84), 100.0, 101.0, 99.0, 100.0),
        (start + pd.Timedelta(hours=88), 100.0, 101.0, 98.0, 99.0),   # base candle
        (start + pd.Timedelta(hours=92), 99.0, 106.0, 102.0, 105.0),  # displacement
    ])
    if include_bos:
        rows.append((start + pd.Timedelta(hours=96), 105.0, 112.0, 106.0, 111.0))
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])


class EarlyOrderBlockTest(unittest.TestCase):
    def test_forming_is_visible_before_bos(self) -> None:
        blocks = find_forming_obs(sample_frame(False), "TESTUSDT", timeframe="4H")
        matches = [block for block in blocks if block.direction == "bullish" and block.bottom == 98.0]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].bos_level, 110.0)

    def test_confirmed_detector_is_unchanged(self) -> None:
        self.assertFalse(find_all_obs(sample_frame(False), "TESTUSDT", timeframe="4H"))
        blocks = find_all_obs(sample_frame(True), "TESTUSDT", timeframe="4H")
        self.assertTrue(any(block.direction == "bullish" and block.bottom == 98.0 for block in blocks))


if __name__ == "__main__":
    unittest.main()
