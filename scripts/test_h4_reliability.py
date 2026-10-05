import sys, unittest, tempfile
from pathlib import Path
from unittest.mock import patch
import pandas as pd
p=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(p/'scripts'))
from h4_data_quality import validate_h4_frame
import telegram_client as tg
import types
try:
 import requests
except ModuleNotFoundError:
 sys.modules['requests']=types.SimpleNamespace(HTTPError=type('HTTPError',(Exception,),{}))
import download_data_h4 as dl
import scan_obs_h4 as ob
import scan_divergences_h4 as div
class FixTests(unittest.TestCase):
 def test_fresh_stale_and_future(self):
  now=pd.Timestamp('2026-10-05T12:10:00Z')
  frame=pd.DataFrame([dict(ts='2026-10-05T04:00:00Z',confirm=1,open=1,high=2,low=1,close=2)])
  validate_h4_frame(frame,now)
  with self.assertRaises(ValueError): validate_h4_frame(frame,now+pd.Timedelta(hours=4))
  with self.assertRaises(ValueError): validate_h4_frame(frame,now-pd.Timedelta(hours=8))
 def test_failed_download_removes_old_file(self):
  with tempfile.TemporaryDirectory() as folder:
   f=Path(folder)/'TESTUSDT_4h.csv'; f.write_text('stale')
   with patch.object(dl,'RAW_DATA_DIR',Path(folder)),patch.object(dl,'PAIRS',['TESTUSDT']),patch.object(dl,'fetch_candles',side_effect=RuntimeError('failed')): dl.main()
   self.assertFalse(f.exists())
 def test_all_entries_fit_and_are_delivered(self):
  items=[str(i)+':'+'x'*600 for i in range(31)]
  with patch.object(tg,'send_message') as send:
   tg.send_digest(items,lambda batch:'\n'.join(batch))
   messages=[call.args[0] for call in send.call_args_list]
   self.assertEqual('\n'.join(messages).splitlines(),items)
   self.assertTrue(all(len(x.encode('utf-16-le'))//2<=4000 for x in messages))
 def test_formatters_do_not_truncate(self):
  zones=[dict(symbol=f'PAIR{i}',direction='bullish',bottom=1,top=2,score=4,ob_time='2026-10-05T00:00:00Z',bos_time='2026-10-05T04:00:00Z') for i in range(23)]
  signals=[dict(symbol=f'PAIR{i}',direction='bullish',first_price=1,price=2,first_rsi=20,rsi=30,confirmed_time='2026-10-05T04:00:00Z') for i in range(23)]
  for items,fmt in [(zones,ob.format_new_digest),(signals,div.format_digest)]:
   with patch.object(tg,'send_message') as send:
    tg.send_digest(items,fmt)
    combined='\n'.join(c.args[0] for c in send.call_args_list)
    for i in range(23): self.assertIn(f'PAIR{i} ',combined)
 def test_shared_lock(self):
  for name in ['scan_rsi_divergences_4h.yml','monitor_obs_h4.yml']:
   self.assertIn('group: h4-shared-state',(p/'.github/workflows'/name).read_text())
if __name__ == "__main__":
 unittest.main()

