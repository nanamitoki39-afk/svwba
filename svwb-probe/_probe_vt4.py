# -*- coding: utf-8 -*-
import sys, time
import frida

JS = r"""
(function () {
  var pattern = "b0 57 0c 24 d6 01 00 00";
  var ranges = Process.enumerateRanges('rw-');
  var ri = 0, off = 0, found = [], scanning = false;
  var CHUNK = 32 * 1024 * 1024;

  function check(addr) {
    try {
      var deck = addr.add(32).readS32();
      var life = addr.add(36).readS32();
      var turn = addr.add(0x38).readS32();
      if (deck > 0 && deck <= 45 && life > 0 && life <= 30) {
        found.push({addr: addr.toString(), deck: deck, life: life, turn: turn});
      }
    } catch (e) {}
  }

  function nextRange() {
    if (ri >= ranges.length) { done(); return; }
    var r = ranges[ri++]; off = 0; nextChunk();
  }

  function nextChunk() {
    if (ri >= ranges.length) { done(); return; }
    var r = ranges[ri - 1];
    if (off >= r.size) { nextRange(); return; }
    var size = Math.min(CHUNK, r.size - off);
    var base = r.base.add(off);
    off += size;
    try {
      Memory.scan(base, size, pattern, {
        onMatch: function (address) { check(address); },
        onError: function () {},
        onComplete: function () { setTimeout(nextChunk, 0); }
      });
    } catch (e) { setTimeout(nextChunk, 0); }
  }

  function done() {
    send({ok: true, total: found.length, found: found.slice(0, 20)});
  }
  nextRange();
})();
"""


def main():
    sys.path.insert(0, r"D:/download/影之诗助手-v1.1.4/svwb-probe")
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("游戏 pid:", pid)
    if not pid:
        return 1
    session = frida.attach(pid)
    script = session.create_script(JS)
    def on_message(msg, data):
        if msg.get("type") != "send":
            return
        p = msg.get("payload") or {}
        if p.get("ok"):
            print("有 deck/life 的命中:", p.get("total"))
            for f in p.get("found") or []:
                print("  ", f)
    script.on("message", on_message)
    script.load()
    time.sleep(40)
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
