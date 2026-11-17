import sys,json,tempfile,unittest,queue,threading
from pathlib import Path
from unittest.mock import Mock,patch
sys.stdout.reconfigure(encoding='utf8')
ROOT=(Path(__file__).resolve().parents[1] / 'svwb-probe');sys.path.insert(0,str(ROOT))
import tkinter as tk
import subtitle_source as source,card_subtitles as subtitles,deck_tracker as tracker,card_art
class Tests(unittest.TestCase):
 def test_subtitle_switch_persists_style_and_immediately_updates_window(self):
  with tempfile.TemporaryDirectory() as d,patch.object(source,'SETTINGS',Path(d)/'settings.json'),patch.object(source,'_find_subtitle_window') as find:
   source.SETTINGS.write_text(json.dumps(dict(zh_size=37,x=123,locked=True)))
   source.set_enabled(False);self.assertFalse(source.is_enabled());find.return_value.apply_options.assert_called_with({'enabled':False})
   self.assertEqual(json.loads(source.SETTINGS.read_text())['zh_size'],37)
   source.set_enabled(True);self.assertTrue(source.is_enabled());self.assertEqual(json.loads(source.SETTINGS.read_text())['x'],123)
 def test_disabled_caption_never_starts_a_reader_or_consumes_stale_voice(self):
  w=subtitles.SubtitleWindow.__new__(subtitles.SubtitleWindow);w.closed=False;w.master=Mock();w.options={'enabled':False};w.queue=queue.Queue();w.queue.put({'old':True});w.worker=None
  with patch('voice_memory.VoiceWorker') as reader:w._poll();reader.assert_not_called();self.assertTrue(w.queue.empty())
 def test_quick_reenable_waits_for_the_old_worker(self):
  w=subtitles.SubtitleWindow.__new__(subtitles.SubtitleWindow);w.closed=False;w.master=Mock();w.options={'enabled':True};w.queue=queue.Queue();w.queue.put({'old':True});w.worker=Mock();w.worker.cancel.is_set.return_value=True;w.worker.is_alive.return_value=True
  with patch('voice_memory.VoiceWorker') as reader:w._poll();reader.assert_not_called();self.assertTrue(w.queue.empty())
 def test_live_mode_keeps_alpha_zero_around_caption(self):
  root=tk.Tk();root.withdraw()
  with tempfile.TemporaryDirectory() as d,patch('subtitle_overlay.Overlay') as overlay:
   overlay.return_value.visible=False;overlay.return_value.rectangle.return_value=(10,10,200,80)
   w=subtitles.SubtitleWindow(root,Mock(),Path(d)/'settings.json',start_worker=False)
   try:
    w._stream_mode(True);w.show(subtitles.Caption('直播测试','テスト'),'test',2)
    bitmap=overlay.return_value.show.call_args.args[0]
    self.assertEqual(bitmap.getpixel((0,0))[3],0);self.assertEqual(w.body.cget('bg'),subtitles.TRANSPARENT)
    w.apply_options({'enabled':False});self.assertFalse(w.options['enabled']);self.assertIsNone(overlay.return_value.show.call_args.args[0].getchannel('A').getbbox())
   finally:w.close();root.destroy()
 def test_display_switches_hide_sections_but_retain_snapshot(self):
  cid=11001110
  snap=dict(in_battle=True,handReady=True,mulliganDone=True,hand=[11041110],deckCount=35,rows=[(11041110,3,2,1)],playedFromHand={str(cid):2},opponentPublicHandReady=True,opponentPublicHand=[{'uid':41,'cardId':cid}])
  with patch.object(tracker.DeckTracker,'poll'),patch('card_subtitles.SubtitleWindow'),patch.object(card_art.ArtManager,'ensure'):
   t=tracker.DeckTracker();t.root.withdraw()
   try:
    t.options.update(show_public=True,show_played=True);t.render(snap);t.root.update_idletasks()
    self.assertIn(('played',cid),t._qty_labels);self.assertEqual(t._public_win.state(),'normal')
    options=dict(t.options,show_public=False,show_played=False);t.apply_window_settings(options);t.root.update_idletasks()
    self.assertNotIn(('played',cid),t._qty_labels);self.assertEqual(t._public_win.state(),'withdrawn');self.assertEqual(t._last['playedFromHand'][str(cid)],2)
    t.apply_window_settings(dict(options,show_public=True));self.assertNotIn(('played',cid),t._qty_labels);self.assertEqual(t._public_win.state(),'normal')
    t.apply_window_settings(dict(options,show_played=True));self.assertIn(('played',cid),t._qty_labels);self.assertEqual(t._public_win.state(),'withdrawn')
   finally:t.close()
 def test_tracker_switches_reload_from_saved_file(self):
  with tempfile.TemporaryDirectory() as d,patch.object(tracker,'WINDOW_SETTINGS',str(Path(d)/'settings.json')):
   Path(tracker.WINDOW_SETTINGS).write_text(json.dumps(dict(show_public=False,show_played=True)))
   self.assertFalse(tracker.window_settings()['show_public']);self.assertTrue(tracker.window_settings()['show_played'])
if __name__ == "__main__":
 unittest.main()
