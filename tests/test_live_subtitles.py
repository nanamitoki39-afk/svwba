import sys,tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'svwb-probe'))
import tkinter as tk
import subtitle_source as source,card_subtitles as subtitles
from subtitle_overlay import Overlay
class Tests(unittest.TestCase):
 def test_idle_capture_layer_is_visible_and_reuses_hwnd(self):
  layer=Overlay(lambda:None,lambda *a:None)
  try:
   hwnd=layer.hwnd;layer.set_capture_mode(True)
   original=layer.show;images=[]
   def show(image,*args,**kw):images.append(image.copy());return original(image,*args,**kw)
   with patch.object(layer,'show',side_effect=show):layer.hide();layer.hide()
   self.assertEqual(layer.hwnd,hwnd);self.assertTrue(layer.u.IsWindowVisible(hwnd));self.assertFalse(layer.visible);self.assertTrue(layer.locked)
   self.assertTrue(all(i.getchannel('A').getbbox() is None for i in images))
   layer.set_capture_mode(False);layer.hide();self.assertFalse(layer.u.IsWindowVisible(hwnd))
  finally:layer.close()
 def test_live_switch_enables_saved_off_caption_but_manual_off_still_works(self):
  root=tk.Tk();root.withdraw()
  with tempfile.TemporaryDirectory() as d:
   settings=Path(d)/'settings.json';settings.write_text(json.dumps(dict(enabled=False)))
   catalog=subtitles.Catalog(Path(d)/'missing.json',Path(d)/'events.json')
   window=subtitles.SubtitleWindow(root,catalog,settings,start_worker=False)
   try:
    window._stream_mode(True);self.assertTrue(window.options['enabled']);self.assertTrue(json.loads(settings.read_text(encoding='utf8'))['enabled']);self.assertTrue(window.overlay.u.IsWindowVisible(window.overlay.hwnd))
    window.apply_options({'enabled':False});self.assertFalse(window.options['enabled']);self.assertFalse(window.overlay.visible);self.assertTrue(window.overlay.u.IsWindowVisible(window.overlay.hwnd))
    window._stream_mode(False);self.assertFalse(window.overlay.u.IsWindowVisible(window.overlay.hwnd))
   finally:window.close();root.destroy()
 def test_global_live_watch_works_without_tracker_and_reuses_window(self):
  with patch('stream_settings.watch') as watch,patch.object(source,'set_enabled') as enable,patch.object(source,'ensure_window') as ensure:
   root=Mock();source.watch_live(root);change=watch.call_args.args[1];change(False);ensure.assert_not_called();change(True);enable.assert_called_once_with(True);ensure.assert_called_once_with(root);ensure.return_value._stream_mode.assert_called_once_with(True)
 def test_shared_window_is_not_duplicated(self):
  existing=Mock();existing.closed=False
  with patch.object(source,'_WINDOW',existing),patch('card_subtitles.SubtitleWindow') as create:
   self.assertIs(source.ensure_window(Mock()),existing);create.assert_not_called()
if __name__ == "__main__":
 unittest.main()
