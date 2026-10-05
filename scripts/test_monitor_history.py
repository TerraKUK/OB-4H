import unittest
from unittest.mock import patch, Mock
from datetime import datetime, timezone
import pandas as pd
import monitor_obs_h4 as monitor
import scan_obs_h4 as scan

class HistoryMonitorTests(unittest.TestCase):
    def zone(self):
        return dict(symbol="TESTUSDT", direction="bullish", bottom=100., top=110., atr=1., status="armed", bos_time="2026-10-05T00:00:00Z", notifications={})

    def test_brief_touch_after_price_has_left(self):
        z=self.zone()
        now=datetime(2026,10,5,5,0,tzinfo=timezone.utc)
        candles=[dict(ts=int(now.timestamp()*1000)-60000, low=105.,high=120.)]
        with patch.object(monitor,"send_message") as send:
            monitor.apply_observation(z,candles,120.,now)
        self.assertEqual(z["status"],"touched")
        self.assertEqual(send.call_count,1)
        self.assertNotIn("tradingview",send.call_args.args[0])
        with patch.object(monitor,"send_message") as send:
            monitor.apply_observation(z,candles,120.,now)
            send.assert_not_called()

    def test_temporary_breach_is_not_hidden_by_recovery(self):
        z=self.zone()
        with patch.object(monitor,"send_message"):
            monitor.apply_observation(z,[dict(ts=1791176400000,low=99.,high=120.)],120.,datetime.now(timezone.utc))
        self.assertEqual(z["status"],"invalidated")

    def test_bearish_touch_and_breach(self):
        z=self.zone();z["direction"]="bearish"
        with patch.object(monitor,"send_message"):
            monitor.apply_observation(z,[dict(ts=1791176400000,low=95.,high=105.)],95.,datetime.now(timezone.utc))
        self.assertEqual(z["status"],"touched")
        with patch.object(monitor,"send_message"):
            monitor.apply_observation(z,[dict(ts=1791176460000,low=95.,high=111.)],95.,datetime.now(timezone.utc))
        self.assertEqual(z["status"],"invalidated")

    def test_current_price_and_cursor_without_event(self):
        z=self.zone();now=datetime(2026,10,5,5,7,35,tzinfo=timezone.utc)
        with patch.object(monitor,"send_message"):
            monitor.apply_observation(z,[],120.,now)
        self.assertEqual(z["last_checked_at"],"2026-10-05T05:07:00+00:00")
        self.assertEqual(z["status"],"armed")
        self.assertEqual(monitor.monitoring_start(z),int(now.replace(second=0).timestamp()*1000))

    def test_history_gap_rejected_and_pagination(self):
        def response(rows):
            r=Mock();r.json.return_value={"code":"0","data":rows};return r
        def row(t):return [str(t),"105","120","105","115","1","1","1","1"]
        with patch.object(monitor.requests,"get",create=True,return_value=response([row(120000),row(0)])):
            with self.assertRaises(ValueError):monitor.fetch_history("TESTUSDT",0,180000)
        with patch.object(monitor.requests,"get",create=True,side_effect=[response([row(180000),row(120000)]),response([row(60000),row(0)])]) as get:
            self.assertEqual(len(monitor.fetch_history("TESTUSDT",0,180000)),4)
            self.assertEqual(get.call_args.kwargs["params"]["after"],"120000")

    def test_failed_history_keeps_cursor(self):
        z=self.zone();z["last_checked_at"]="2026-10-05T04:07:00Z"
        with patch.object(monitor,"load_state",return_value={"zones":{"test":z}}),patch.object(monitor,"fetch_history",side_effect=ValueError("gap")),patch.object(monitor,"save_state") as save:
            monitor.main()
            save.assert_not_called()
        self.assertEqual(z["last_checked_at"],"2026-10-05T04:07:00Z")

    def test_cannot_confirm_before_touch(self):
        z=self.zone();z.update(status="touched",touched_at="2026-10-05T05:00:00Z")
        frame=pd.DataFrame([dict(ts="2026-10-05T00:00:00Z",close=120.)])
        with patch.object(scan,"send_message") as send:
            self.assertFalse(scan.update_confirmations({"zones":{"test":z}},{"TESTUSDT":frame}))
            send.assert_not_called()

    def test_workflow_and_messages(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        workflow=(root/".github/workflows/scan_rsi_divergences_4h.yml").read_text()
        self.assertNotIn("run: python scripts/scan_divergences_h4.py",workflow)
        self.assertIn('7,37 * * * *',(root/".github/workflows/monitor_obs_h4.yml").read_text())
        z=self.zone();z.update(score=4,ob_time=z["bos_time"],displacement_atr=1.,bos_level=120.)
        for text in [scan.format_new_digest([z]),scan.format_forming_message(z),scan.format_confirmation_message(z,120.)]:
            self.assertNotIn("tradingview",text)

if __name__=="__main__":unittest.main()
