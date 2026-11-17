import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'svwb-probe'))
import tkinter as tk
from card_subtitles import SubtitleWindow,Caption,Catalog
class Tests(unittest.TestCase):
 def test_live_source_dimensions_and_position_do_not_change_between_lines_or_idle(self):
  root=tk.Tk();root.withdraw()
  with tempfile.TemporaryDirectory() as d,patch('subtitle_overlay.Overlay') as factory:
   overlay=factory.return_value;overlay.visible=False;overlay.rectangle.return_value=(0,0,640,100)
   w=SubtitleWindow(root,Catalog(Path(d)/'none.json',Path(d)/'events.json'),Path(d)/'settings.json',start_worker=False)
   try:
    w._stream_mode(True);calls=[]
    blank=overlay.show.call_args;calls.append((blank.args[0].size,blank.args[1:3]))
    self.assertIsNone(blank.args[0].getchannel('A').getbbox())
    for text in ['短句','这是一段长字幕，用来验证不同长度和换行不会改变 OBS 来源的尺寸。'*5,'又一条字幕']:
     w.show(Caption(text,'日本語字幕の確認です。'*3),text,3)
     call=overlay.show.call_args;image=call.args[0];calls.append((image.size,call.args[1:3]));self.assertEqual(image.getpixel((0,0))[3],0)
     bounds=image.getchannel('A').getbbox();self.assertLess(abs((bounds[0]+bounds[2])/2-image.width/2),24)
     w.hide();call=overlay.show.call_args;calls.append((call.args[0].size,call.args[1:3]));self.assertIsNone(call.args[0].getchannel('A').getbbox())
    self.assertTrue(all(c==calls[0] for c in calls),calls)
   finally:w.close();root.destroy()
if __name__ == "__main__":
 unittest.main()
