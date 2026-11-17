# -*- coding: utf-8 -*-
"""探针 2：从 Mpo 往上找 BattleManager 对象，再反查它的父指针。

Mpo +32 是 BattleManager（之前探测确认），BattleManager 可能是静态字段/单例，
反查它比反查 Mpo 更可能成功。
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
  var classGetStaticFieldData = null;

  try {
    classGetStaticFieldData = P("il2cpp_class_get_static_field_data", "pointer", ["pointer"]);
  } catch (e) {}

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null, bmKlass = null;
  for (var i = 0; i < n && (!klass || !bmKlass); i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    if (!klass) {
      var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
      if (!k.isNull()) klass = k;
    }
    if (!bmKlass) {
      var bk = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattleManagerBase"));
      if (!bk.isNull()) bmKlass = bk;
    }
  }
  if (!klass) { send({err: "no BattlePlayerBase"}); return; }
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
      // Mpo + 32 = BattleManager
      var bm = null;
      try { bm = mpo.add(32).readPointer(); } catch (e) {}
      var out = {ok: true, mpo: mpo.toString(), bm: bm ? bm.toString() : null};
      // 反查 BattleManager 的父指针
      if (bm && !bm.isNull()) {
        var needle = bm.readByteArray(8);
        var parents = [];
        var ranges = Process.enumerateRanges('rw-');
        for (var ri = 0; ri < ranges.length && parents.length < 30; ri++) {
          var r = ranges[ri];
          try {
            var hits = Memory.scanSync(r.base, r.size, needle);
            for (var j = 0; j < hits.length; j++) {
              parents.push(hits[j].address.toString());
              if (parents.length > 30) break;
            }
          } catch (e) {}
        }
        out.parents = parents;
        out.ranges = ranges.length;
      }
      // 静态字段数据
      if (classGetStaticFieldData && bmKlass) {
        try {
          var sd = classGetStaticFieldData(bmKlass);
          if (sd && !sd.isNull()) {
            out.staticFieldData = sd.toString();
            var vals = [];
            for (var o = 0; o < 64; o += 8) {
              try { vals.push(sd.add(o).readPointer().toString()); } catch (e) {}
            }
            out.staticVals = vals;
          }
        } catch (e) {}
      }
      out.moduleBase = gaBase.toString();
      send(out);
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
            print("MPO:", p.get("mpo"), "| BattleManager:", p.get("bm"))
            print("parents:", len(p.get("parents") or []))
            for pp in (p.get("parents") or [])[:10]:
                print("  ", pp)
            print("staticFieldData:", p.get("staticFieldData"))
            print("moduleBase:", p.get("moduleBase"))

    script.on("message", on_message)
    script.load()
    for _ in range(15):
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
        print("没抓到")
        return 1
    with open(r"C:/Users/33949/AppData/Local/Temp/chain_probe2.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("已保存 chain_probe2.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
