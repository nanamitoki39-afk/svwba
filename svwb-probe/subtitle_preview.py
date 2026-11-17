"""Local-only preview: real caption renderer and real imported dialogue, no game reads."""
from pathlib import Path
import json,sys,tkinter as tk
from card_subtitles import Catalog,SubtitleWindow,wwise_id

HERE=Path(__file__).resolve().parent
CHOICES=[('爱意天使·艾克夏丝 · 普通入场','Play_dx_11001110_1'),
 ('爱意天使·艾克夏丝 · 遇到理想国之神','Play_dx_11001110_9_11004110'),
 ('信仰天使·迪纳梅斯 · 遇到理想国之神','Play_dx_11002110_9_11004110'),
 ('希求天使·丘丽奥丝 · 遇到理想国之神','Play_dx_11003110_9_11004110')]

def main(check=False):
 import ui_dpi
 ui_dpi.enable()
 root=tk.Tk();root.title('字幕预览 · 仅演示，不读取游戏');root.configure(bg='#171c25')
 root.geometry('590x185+80+100');root.resizable(False,False)
 catalog=Catalog();window=SubtitleWindow(root,catalog,HERE/'logs/subtitle-preview.json',start_worker=False)
 window.options.update(x=None,y=None)
 index=0;timer=None;playing=False
 label=tk.Label(root,bg='#171c25',fg='#efe9dc',font=('Microsoft YaHei UI',12))
 label.pack(pady=(16,7))
 tk.Label(root,text='中文在上、日文较小；字幕默认在屏幕高度 75% 居中，可拖动。',bg='#171c25',fg='#9aa7a0',font=('Microsoft YaHei UI',10)).pack()
 controls=tk.Frame(root,bg='#171c25');controls.pack(pady=14)
 def show(step=0):
  nonlocal index
  index=(index+step)%len(CHOICES);name,event=CHOICES[index]
  caption=catalog.match(dict(event_id=wwise_id(event),actual=True,playing_id=index+1,file=''))
  if caption is None:raise ValueError('Imported preview dialogue is missing: '+event)
  label.configure(text=name);window.show(caption,('preview',index),40)
  return caption
 def tick():
  nonlocal timer
  if playing:show(1);timer=root.after(5000,tick)
 def toggle():
  nonlocal playing,timer
  playing=not playing;auto.configure(text='暂停演示' if playing else '自动演示')
  if timer:root.after_cancel(timer);timer=None
  if playing:timer=root.after(5000,tick)
 def close():
  if timer:root.after_cancel(timer)
  window.close();root.destroy()
 for text,command in [('上一条',lambda:show(-1)),('下一条',lambda:show(1))]:
  tk.Button(controls,text=text,command=command,width=10).pack(side='left',padx=5)
 auto=tk.Button(controls,text='自动演示',command=toggle,width=10);auto.pack(side='left',padx=5)
 tk.Button(controls,text='关闭预览',command=close,width=10).pack(side='left',padx=5)
 root.protocol('WM_DELETE_WINDOW',close);root.bind('<Escape>',lambda e:close());window.root.bind('<Escape>',lambda e:close())
 if check:root.withdraw()
 caption=show(1);root.update()
 if check:
  assert window.worker is None
  assert caption.zh=='那颗心脏就由我收下'
  for _ in CHOICES:show(1)
  close();print('Real subtitle preview verified: all four dialogues, no game worker')
 else:toggle();root.mainloop()

if __name__=='__main__':main('--check' in sys.argv)
