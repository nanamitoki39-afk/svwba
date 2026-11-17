# -*- coding: utf-8 -*-
"""一次性探测：找当前对局的 Mpo，并反查指针链到静态基址。

运行前提：游戏在对局中（有手牌）。
用法：python _probe_chain.py
输出：Mpo 地址 + vtable + 向上一层的指针（用于建立链）
"""
import json
import sys
import time

import frida

JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) { send({err: "no GameAssembly"}); return; }
  var gaBase = ga.base;
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var domainGet = P("il2cpp_domain_get", "pointer", []);
  var threadAttach = P("il2cpp_thread_attach", "pointer", ["pointer"]);
  var getAssemblies = P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var asmImage = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var classFromName = P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var classGetMethods = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var methodGetName = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var it = Memory.alloc(Process.pointerSize);
  var m, fn = null;
  while (!(m = classGetMethods(klass, it)).isNull()) {
    var nm = "";
    try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (fn === null) { send({err: "no getter"}); return; }
  var got = false;
  Interceptor.attach(fn, {onEnter: function (args) {
    if (got) return;
    try {
      var p = args[0];
      var cls = "";
      try { cls = className(objClass(p)).readUtf8String(); } catch (e) {}
      if (cls !== "BattleStatePlayerMpo") return;
      got = true;
      var mpo = p;
      var vt = mpo.readPointer();
      // 反查：扫内存找「值等于 mpo 地址」的 8 字节指针（指向它的父对象/字段）
      var needle = mpo.readByteArray(8);
      var parents = [];
      var ranges = Process.enumerateRanges('rw-');
      for (var ri = 0; ri < ranges.length; ri++) {
        var r = ranges[ri];
        try {
          var hits = Memory.scanSync(r.base, r.size, needle);
          for (var j = 0; j < hits.length; j++) {
            parents.push({at: hits[j].address.toString(), range: r.base.toString()});
            if (parents.length > 20) break;
          }
        } catch (e) {}
        if (parents.length > 20) break;
      }
      send({
        ok: true,
        mpo: mpo.toString(),
        vtable: vt.toString(),
        moduleBase: gaBase.toString(),
        parents: parents,
        ranges: ranges.length
      });
    } catch (e) {}
  }});
})();
"""


def main():
    sys.path.insert(0, r"D:/download/影之诗助手-v1.1.4/svwb-probe")
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("游戏 pid:", pid)
    if not pid:
        print("游戏没在跑")
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
            print("MPO:", p["mpo"], "| vtable:", p["vtable"])
            print("moduleBase:", p["moduleBase"], "| rw 区段:", p["ranges"])
            print("指向它的指针（parents）:", len(p["parents"]))
            for pp in p["parents"][:10]:
                print("  ", pp)

    script.on("message", on_message)
    script.load()
    # 等 hook 触发（需要游戏里有手牌操作）
    for i in range(15):
        if result:
            break
        time.sleep(1)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    if not result:
        print("15 秒内没抓到（对局中没有手牌操作？）")
        return 1
    with open(r"C:/Users/33949/AppData/Local/Temp/chain_probe.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("已保存 chain_probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
