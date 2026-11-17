# -*- coding: utf-8 -*-
"""探针 3：不依赖 hook，直接用 vtable 扫描找 Mpo。

vtable 从上次探测拿到（0x1d6240c57b0），如果进程重启会变，脚本会报 0。
"""
import json
import sys

import frida

VT = 0x1d6240c57b0

JS = r"""
(function () {
  var vt = ptr("%s");
  var ga = Process.findModuleByName("GameAssembly.dll");
  var needle = vt.readByteArray(8);
  if (!needle) { send({err: "no needle"}); return; }
  var found = [];
  var ranges = Process.enumerateRanges('rw-');
  for (var ri = 0; ri < ranges.length; ri++) {
    var r = ranges[ri];
    try {
      var hits = Memory.scanSync(r.base, r.size, needle);
      for (var j = 0; j < hits.length && found.length < 20; j++) {
        var addr = hits[j].address;
        var deck = 0, life = 0;
        try {
          deck = addr.add(32).readS32();
          life = addr.add(36).readS32();
        } catch (e) { continue; }
        if (deck < 0 || deck > 45 || life < 0 || life > 30) continue;
        found.push({addr: addr.toString(), deck: deck, life: life});
      }
    } catch (e) {}
    if (found.length >= 20) break;
  }
  send({ok: true, total: found.length, found: found, ranges: ranges.length});
})();
""" % (hex(VT),)


def main():
    sys.path.insert(0, r"D:/download/影之诗助手-v1.1.4/svwb-probe")
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("游戏 pid:", pid)
    if not pid:
        return 1
    session = frida.attach(pid)
    script = session.create_script(JS)
    result = {}

    def on_message(msg, data):
        if msg.get("type") != "send":
            return
        p = msg.get("payload") or {}
        if p.get("err"):
            print("err:", p["err"])
            return
        if p.get("ok"):
            result.update(p)
            print("命中:", p.get("total"), "| ranges:", p.get("ranges"))
            for f in p.get("found") or []:
                print("  ", f)

    script.on("message", on_message)
    script.load()
    import time
    time.sleep(20)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
