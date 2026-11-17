import importlib.util,json,tempfile,time,unittest,sys
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=(Path(__file__).resolve().parents[1] / 'svwb-probe')
spec=importlib.util.spec_from_file_location('boot',ROOT/'startup_bootstrap.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.cache=self.root/'cache.json';self.binary=self.root/'node.exe';self.binary.write_bytes(b'node')
  self.p=patch.object(b,'HERE',self.root);self.p.start();self.addCleanup(self.p.stop)
  self.c=patch.object(b,'CACHE',self.cache);self.c.start();self.addCleanup(self.c.stop)
  self.data=dict(schema=1,at=time.time(),key=b.runtime_key(),files=[b.stamp(self.binary)]);self.save()
 def save(self):self.cache.write_text(json.dumps(self.data),encoding='utf8')
 def test_valid_without_importing_heavy_modules(self):
  with patch.object(b.importlib.util,'find_spec',side_effect=AssertionError('no imports on warm boot')):self.assertTrue(b.valid())
 def test_missing_or_replaced_dependency_invalidates(self):
  self.binary.write_bytes(b'changed');self.assertFalse(b.valid());self.binary.unlink();self.assertFalse(b.valid())
 def test_expiry_and_clock_rollback(self):
  for at in (time.time()-b.TTL-1,time.time()+300):self.data['at']=at;self.save();self.assertFalse(b.valid())
 def test_moved_installation_or_interpreter_invalidates(self):
  self.data['key'][0]='other directory';self.save();self.assertFalse(b.valid())
 def test_bad_cache_repairs(self):
  self.cache.write_text('{bad');self.assertFalse(b.valid())
 def test_invalid_cache_falls_back_once(self):
  with patch.object(b,'valid',return_value=False),patch.object(b,'prepare_again') as repair:self.assertEqual(b.main(),0);repair.assert_called_once()
 def test_normal_and_duplicate_exit_keep_output_streams_usable(self):
  original=sys.stdout,sys.stderr
  fake_app=Mock();fake_app.main.return_value=0
  with patch.object(b.os,'chdir'),patch.object(b,'valid',return_value=True),patch.dict(sys.modules,{'app':fake_app,'upgrade_helper':Mock()}):self.assertEqual(b.main(),0)
  self.assertEqual((sys.stdout,sys.stderr),original)
if __name__ == "__main__":
 unittest.main()
