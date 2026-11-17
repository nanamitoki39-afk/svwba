import sys,unittest,subprocess,tempfile,threading
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=(Path(__file__).resolve().parents[1] / 'svwb-probe')
sys.path.insert(0,str(ROOT))

class Tests(unittest.TestCase):
 def test_no_numpy_for_normal_tracker_import(self):
  result=subprocess.run([sys.executable,'-c',"import sys; import deck_reader; assert 'numpy' not in sys.modules"],cwd=ROOT,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_legacy_scan_still_available_with_one_blas_thread(self):
  code="""import os
for n in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ.pop(n,None)
import deck_reader as d
class Memory:
 @staticmethod
 def regions(h):return []
assert d._scan_state(1,Memory)==[]
assert d._scan_deck(1,Memory,{10000000})==[]
assert d._scan_deck_runs(1,Memory,{10000000})==[]
assert os.environ['OPENBLAS_NUM_THREADS']=='1'
"""
  result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_reader_can_retry_when_thread_creation_fails(self):
  from reader_supervisor import ReaderSupervisor
  supervisor=ReaderSupervisor(ROOT,{},report=Mock())
  with patch('reader_supervisor.threading.Thread') as thread:
   thread.return_value.start.side_effect=RuntimeError('resource pressure')
   supervisor.ensure()
  self.assertTrue(supervisor.gate.acquire(False));supervisor.gate.release()
  class ImmediateThread:
   def __init__(self,target,**kw):self.target=target
   def start(self):self.target()
  with patch('reader_supervisor.threading.Thread',ImmediateThread),patch.object(supervisor,'check') as check:
   supervisor.ensure();check.assert_called_once()
 def test_builder_cannot_remove_own_install(self):
  import build_package as b
  with patch.object(b,'NAME',Path(b.PKG).name):
   with self.assertRaises(ValueError):b.build(str(Path(b.PKG).parent))
  self.assertTrue((ROOT/'app.py').exists())
 def test_builder_keeps_existing_output_and_required_modules(self):
  import build_package as b
  with tempfile.TemporaryDirectory() as temp:
   target=Path(temp)/b.NAME;target.mkdir();sentinel=target/'user.txt';sentinel.write_text('keep')
   with self.assertRaises(FileExistsError):b.build(temp)
   self.assertEqual(sentinel.read_text(),'keep')
  files=b.runtime_files()
  for name in ['card_subtitles.py','subtitle_bitmap.py','subtitle_overlay.py','voice_memory.py','stream_settings.py','battle_stats.py','launch.ps1','launch.vbs']:
   self.assertIn(name,files)
 def test_subtitle_close_survives_destroyed_tk_parent(self):
  import card_subtitles as s
  w=s.SubtitleWindow.__new__(s.SubtitleWindow)
  w.closed=False;w.worker=Mock();w.master=Mock();w.master.after_cancel.side_effect=s.tk.TclError('destroyed')
  w.place_id='place';w.timer='timer';w.poll_id='poll';w.overlay=Mock();w.root=Mock()
  w.close();w.close();w.worker.stop.assert_called_once();w.overlay.close.assert_called_once();w.root.destroy.assert_called_once()
if __name__ == "__main__":
 unittest.main()
