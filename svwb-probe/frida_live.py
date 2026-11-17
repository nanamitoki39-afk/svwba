#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida 常驻：hook get_HandCardList，节流读 BattleStatePlayerMpo 状态并写 JSON。"""
import json
import os
import sys
import time

try:
    import frida
except ImportError:
    frida = None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "deck_live.json")
# 控制台要求停止时写这个文件；主循环看到就卸载 hook。
# 以前只能被 taskkill 强杀 → agent 留在游戏进程里，GC 直接崩。
STOP_FILE = os.path.join(HERE, "logs", "_frida.stop")
DEFAULT_WAIT = 7200

JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) throw new Error("GameAssembly.dll is not loaded");
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
  var nPtr = Memory.alloc(Process.pointerSize);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  // 本地结算画面直接接收玩家视角的 bool win。
  var battleEnded = false;
  function watchResults() {
    var paramCount = P("il2cpp_method_get_param_count", "uint", ["pointer"]);
    var getParam = P("il2cpp_method_get_param", "pointer", ["pointer","uint"]);
    var typeKind = P("il2cpp_type_get_type", "int", ["pointer"]);
    // Worlds Beyond 在各模式的工厂返回后已填好本玩家的 BattleResult。
    var fieldByName = P("il2cpp_class_get_field_from_name", "pointer", ["pointer","pointer"]);
    var fieldOffset = P("il2cpp_field_get_offset", "int", ["pointer"]);
    var ns = Memory.allocUtf8String("Wizard2.Domain");
    var baseName = Memory.allocUtf8String("BattleFinishData");
    var modes = ["Rank","RoomMatch","Practice","Starter","TwoPick","Story","Dungeon",
                 "Explore","GrandPrix","LobbyTournament","Festival","FestivalDecisive",
                 "OnlineQualifier","Tutorial","NtLd","Eudie"];
    for (var ai = 0; ai < n; ai++) {
      var img = asmImage(asms.add(ai * Process.pointerSize).readPointer());
      var base = classFromName(img, ns, baseName);
      if (base.isNull()) continue;
      var field = fieldByName(base, Memory.allocUtf8String("BattleResult"));
      if (field.isNull()) continue;
      var offset = fieldOffset(field);
      var attached = [], lastFactory = "", lastResult = "";
      modes.forEach(function(mode) {
        var k = classFromName(img, ns, Memory.allocUtf8String(mode + "BattleFinishDataFactory"));
        if (k.isNull()) return;
        var it = Memory.alloc(Process.pointerSize), m;
        while (!(m = classGetMethods(k, it)).isNull()) {
          if (methodGetName(m).readUtf8String() !== "CreateBattleFinishData" ||
              paramCount(m) !== 1) continue;
          Interceptor.attach(m.readPointer(), {
            onEnter: function(args) { this.factory = args[0].toString(); },
            onLeave: function(data) {
              if (data.isNull()) return;
              var value = data.add(offset).readS32();
              if (value !== 1 && value !== 2) return;
              var result = data.toString();
              if (lastFactory === this.factory && lastResult === result) return;
              lastFactory = this.factory;
              lastResult = result;
              battleEnded = true;
              send({confirmed_win: value === 1, screen: this.factory + "/" + result,
                    source: mode, at: Date.now()});
            }
          });
          attached.push(mode);
          break;
        }
      });
      if (attached.length) {
        send({result_sources: attached.join(",")});
        return;
      }
    }
    var empty = Memory.allocUtf8String("");
    var name = Memory.allocUtf8String("BattleResultUIController");
    var displayed = Object.create(null);
    for (var j = 0; j < n; j++) {
      var image = asmImage(asms.add(j * Process.pointerSize).readPointer());
      var k = classFromName(image, empty, name);
      if (k.isNull()) continue;
      var it = Memory.alloc(Process.pointerSize), m;
      var start = null, destroy = null;
      while (!(m = classGetMethods(k, it)).isNull()) {
        var method = methodGetName(m).readUtf8String();
        if (method === "StartUI" && paramCount(m) === 2 &&
            typeKind(getParam(m, 0)) === 2) start = m.readPointer();
        if (method === "OnDestroy" && paramCount(m) === 0) destroy = m.readPointer();
      }
      if (!start || !destroy) continue;
      Interceptor.attach(destroy, {onEnter: function(args) {
        delete displayed[args[0].toString()];
      }});
      Interceptor.attach(start, {onEnter: function(args) {
        var screen = args[0].toString(), win = args[1].toUInt32() & 255;
        if (win > 1 || displayed[screen]) return;
        displayed[screen] = true;
        battleEnded = true;
        send({confirmed_win: win === 1, screen: screen, at: Date.now()});
      }});
      return;
    }
    send({err: "BattleResultUIController.StartUI(bool, BattleCamera) unavailable"});
  }
  try { watchResults(); } catch (e) { send({err: "result listener: " + e}); }

  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var iter = Memory.alloc(Process.pointerSize);
  var fn = null, deckFn = null, m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "get_HandCardList") fn = m.readPointer();
    if (nm === "get_DeckCardList") deckFn = m.readPointer();
  }
  if (fn === null) { send({err: "no getter"}); return; }

  var stopped = false;
  var selfRef = null, enemyRef = null, lastEmit = 0, lastIdle = 0;
  var knownVtable = null;   // Mpo 的对象头（类 vtable 指针），hook 时记下
  var idleSince = 0, fallbackAddr = null, fallbackUntil = 0;
  function receiveFallback(msg) {
    fallbackAddr = msg.addr;
    fallbackUntil = Date.now() + 3000;
    queueSample();
    if (!stopped) recv("field_fallback", receiveFallback);
  }
  recv("field_fallback", receiveFallback);
  var lastBindAt = 0, lastHandAddr = null, lastHandCount = 0;
  var opening = false, allyCards = [], finishRoot = null, confirmedSide = null;

  function handKeys(array, count) {
    if (!array || array.isNull() || count < 1 || count > 10 ||
        array.add(0x18).readS32() < count) return [];
    var keys = [];
    for (var i = 0; i < count; i++) {
      var card = array.add(0x20 + i * Process.pointerSize).readPointer();
      if (card.isNull()) return [];
      var id = card.add(0x14).readS32();
      if (id < 10000000 || id > 99999999) return [];
      keys.push(card.add(0x10).readS32() + "/" + id);
    }
    return keys;
  }

  function selectFinishedPlayer() {
    if (!finishRoot || !opening) return;
    var players = finishRoot.add(0x10).readPointer();
    if (players.isNull() || players.add(0x18).readS32() !== 2) return;
    var sides = [players.add(0x20).readPointer(), players.add(0x28).readPointer()];
    var matches = [], visible = [], masked = [];
    sides.forEach(function(p, i) {
      if (!isPlayerState(p)) return;
      var hand = p.add(0x10).readPointer();
      if (hand.isNull()) return;
      var count = hand.add(0x18).readS32();
      var keys = handKeys(hand, count);
      if (keys.length) visible.push(i);
      if (!keys.length && count >= 1 && count <= 10) {
        var hidden = true;
        for (var j = 0; j < count; j++) {
          var item = hand.add(0x20 + j * Process.pointerSize).readPointer();
          if (item.isNull() || item.add(0x14).readS32() !== 0) hidden = false;
        }
        if (hidden) masked.push(i);
      }
      if (keys.length && allyCards.length &&
          allyCards.every(function(k) { return keys.indexOf(k) >= 0; })) matches.push(i);
    });
    if (!allyCards.length && visible.length === 1 && masked.length === 1) matches = visible;
    if (matches.length !== 1) return;
    selfRef = sides[matches[0]];
    enemyRef = sides[1 - matches[0]];
    confirmedSide = selfRef.add(0x98).readU8();
    opening = false;
    finishRoot = null;
    lastBindAt = Date.now();
    send({rebound: selfRef.toString()});
  }

  var lastStep = -1, sampleQueued = false, usingViewRoot = false;
  function queueSample() {
    if (sampleQueued || battleEnded || stopped) return;
    sampleQueued = true;
    setTimeout(function() { sampleQueued = false; sample(); }, 0);
  }
  function followStep(step) {
    if (battleEnded) return;
    var seq = step.add(0x28).readS32();
    if (step.add(0x10).readS32() !== 3 || seq <= lastStep) return;
    var root = step.add(0x18).readPointer();
    if (root.isNull()) return;
    if (followRoot(root, true)) lastStep = seq;
  }
  function followRoot(root, sequenced) {
    if (usingViewRoot && !sequenced) return false;
    if (!root || root.isNull() || battleEnded) return false;
    var arr = root.add(0x10).readPointer();
    if (arr.isNull() || arr.add(0x18).readS32() !== 2) return;
    if (confirmedSide === null || opening) {
      finishRoot = root;
      opening = true;
      selectFinishedPlayer();
      if (opening) return;
    }
    var a = arr.add(0x20).readPointer(), b = arr.add(0x28).readPointer();
    if (!isPlayerState(a) || !isPlayerState(b)) return;
    var sa = a.add(0x98).readU8(), sb = b.add(0x98).readU8();
    if (sa === sb || (sa !== confirmedSide && sb !== confirmedSide)) return;
    var candidate = sa === confirmedSide ? a : b;
    if (usingViewRoot && isPlayerState(selfRef)) {
      if (candidate.equals(selfRef)) return true;
      var oldTurn = selfRef.add(0x38).readS32(), turn = candidate.add(0x38).readS32();
      if (turn < oldTurn) return false;
      if (turn === oldTurn) {
        var old = handEvidence(selfRef), next = handEvidence(candidate);
        if (!old || !next || !handProgress(old, next)) return false;
      }
    }
    selfRef = candidate;
    enemyRef = sa === confirmedSide ? b : a;
    lastBindAt = 0;
    queueSample();
    return true;
  }

  function watchSteps() {
    var ns = Memory.allocUtf8String("Wizard2.ServerShared.MessagePackObjects.PubSub");
    var name = Memory.allocUtf8String("PublishStep");
    for (var j = 0; j < n; j++) {
      var img = asmImage(asms.add(j * Process.pointerSize).readPointer());
      var k = classFromName(img, ns, name);
      if (k.isNull()) continue;
      var it = Memory.alloc(Process.pointerSize), method;
      while (!(method = classGetMethods(k, it)).isNull()) {
        if (methodGetName(method).readUtf8String() !== "get_Root") continue;
        Interceptor.attach(method.readPointer(), {onEnter: function(args) {
          try { if (objClass(args[0]).equals(k)) followStep(args[0]); } catch (e) {}
        }});
        return;
      }
    }
  }
  try { watchSteps(); } catch (e) { send({err: "step: " + e}); }
  // 只跟随客户端展示层当前提交的局面，同一回合的不同动作也会更新。
  function watchDisplayedRoot() {
    var ns = Memory.allocUtf8String("Wizard2.Presentation.Battle");
    var name = Memory.allocUtf8String("BattleModel");
    for (var i = 0; i < n; i++) {
      var img = asmImage(asms.add(i * Process.pointerSize).readPointer());
      var k = classFromName(img, ns, name);
      if (k.isNull()) continue;
      var iter = Memory.alloc(Process.pointerSize), method;
      while (!(method = classGetMethods(k, iter)).isNull()) {
        if (methodGetName(method).readUtf8String() !== "ExecuteRootUpdated") continue;
        Interceptor.attach(method.readPointer(), {onEnter: function(args) {
          try {
            if (!opening && confirmedSide !== null && !battleEnded &&
                objClass(args[0]).equals(k)) followRoot(args[1]);
          } catch (e) {}
        }});
        return;
      }
    }
  }
  try { watchDisplayedRoot(); } catch (e) { send({err: "display: " + e}); }

  // ImmediateUpdate 提交的 Players[0] 同时驱动游戏自己的手牌数和牌库数。
  function acceptViewRoot(root) {
    if (stopped || battleEnded || opening || !root || root.isNull()) return;
    var arr = root.add(0x10).readPointer();
    if (arr.isNull() || arr.add(0x18).readS32() !== 2) return;
    var ally = arr.add(0x20).readPointer(), enemy = arr.add(0x28).readPointer();
    if (!isPlayerState(ally) || !isPlayerState(enemy)) return;
    var side = ally.add(0x98).readU8();
    if (side === enemy.add(0x98).readU8()) return;
    if (confirmedSide === null && !ally.add(0x5c).readU8()) return;
    confirmedSide = side;
    usingViewRoot = true;
    selfRef = ally;
    enemyRef = enemy;
    lastBindAt = 0;
    queueSample();
  }
  var handView = null, viewClass = null, viewOffsets = null;
  function shownHand() {
    if (!handView || !viewOffsets || opening || battleEnded) return null;
    try {
      if (!objClass(handView).equals(viewClass)) return null;
      var player = handView.add(viewOffsets.player).readPointer();
      if (player.isNull()) return null;
      var list = player.add(viewOffsets.list).readPointer();
      if (list.isNull()) return null;
      var version = list.add(0x1c).readS32(), count = list.add(0x18).readS32();
      var arr = list.add(0x10).readPointer();
      if (count < 0 || count > 10 || arr.isNull() || arr.add(0x18).readS32() < count) return null;
      var ids = [], uids = [];
      for (var i = 0; i < count; i++) {
        var card = arr.add(0x20 + i * Process.pointerSize).readPointer();
        if (card.isNull()) return null;
        var id = card.add(viewOffsets.card).readS32();
        var uid = card.add(viewOffsets.uid).readS32();
        if (id < 10000000 || id > 99999999 || uids.indexOf(uid) !== -1) return null;
        ids.push(id); uids.push(uid);
      }
      if (version !== list.add(0x1c).readS32() || count !== list.add(0x18).readS32() ||
          !list.equals(player.add(viewOffsets.list).readPointer())) return null;
      return {ids: ids, uids: uids};
    } catch (e) { return null; }
  }
  function watchViewUpdates() {
    var ns = Memory.allocUtf8String("Wizard2.View"), name = Memory.allocUtf8String("BattleView");
    var byName = P("il2cpp_class_get_field_from_name", "pointer", ["pointer","pointer"]);
    var offset = P("il2cpp_field_get_offset", "int", ["pointer"]);
    function member(klass, field) {
      if (klass.isNull()) return -1;
      var meta = byName(klass, Memory.allocUtf8String(field));
      return meta.isNull() ? -1 : offset(meta);
    }
    for (var i = 0; i < n; i++) {
      var img = asmImage(asms.add(i * Process.pointerSize).readPointer());
      var k = classFromName(img, ns, name);
      if (k.isNull()) continue;
      viewClass = k;
      var pv = classFromName(img, ns, Memory.allocUtf8String("BattlePlayerView"));
      var cv = classFromName(img, ns, Memory.allocUtf8String("HandCardView"));
      var fields = {player: member(k, "_allyPlayer"), list: member(pv, "_handCards"),
                    card: member(cv, "<CardId>k__BackingField"), uid: member(cv, "<UniqueId>k__BackingField")};
      if (Object.values(fields).every(function(value) { return value >= 0; })) viewOffsets = fields;
      var watched = [];
      var it = Memory.alloc(Process.pointerSize), m;
      while (!(m = classGetMethods(k, it)).isNull()) {
        let method = methodGetName(m).readUtf8String();
        if (["Update","UpdateView","ImmediateUpdate","AddHand","RemoveHand","GetHandCards"].indexOf(method) < 0) continue;
        Interceptor.attach(m.readPointer(), {onEnter: function(args) {
          if (stopped || battleEnded) return;
          // Shared native method bodies may also receive other classes.
          // Never replace a valid display owner with an unrelated object.
          try {
            if (args[0].isNull() || !objClass(args[0]).equals(viewClass)) return;
          } catch (e) { return; }
          handView = ptr(args[0].toString());
          if (method === "ImmediateUpdate") {
            try { acceptViewRoot(args[1]); } catch (e) { }
          }
        }, onLeave: function() {
          if (method === "AddHand" || method === "RemoveHand") queueSample();
        }});
        watched.push(method);
      }
      send({view_status: "fields=" + JSON.stringify(fields) + " methods=" + watched.join(",")});
      return;
    }
    send({err: "BattleView unavailable"});
  }
  try { watchViewUpdates(); } catch (e) { send({err: "view update: " + e}); }

  var returnCounts = {}, returnSteps = {};
  var playedFromHand = {}, playedSteps = {}, handDepartures = {};
  function recordHandPlay(args) {
    if (stopped || battleEnded || opening || !(args[3].toUInt32() & 1)) return;
    var card = args[2];
    if (card.isNull()) return;
    var id = card.add(0x14).readS32();
    if (id < 10000000 || id > 99999999) return;
    var key = args[1].toInt32() + "/" + card.add(0x10).readS32() + "/" + id;
    if (playedSteps[key]) return;
    playedSteps[key] = true;
    var before = args[4].toInt32();
    handDepartures[key] = before > 0 ? before : card.add(0x10).readS32();
    playedFromHand[id] = (playedFromHand[id] || 0) + 1;
    queueSample();
  }
  function recordReturn(args, bounce) {
    if (battleEnded || opening || confirmedSide === null) return;
    if (!bounce && !(args[3].toUInt32() & 1)) return;
    var key = (bounce ? "b" : "r") + args[1].toInt32();
    if (returnSteps[key]) return;
    var list = args[2];
    if (list.isNull()) return;
    var arr = list.add(0x10).readPointer(), count = list.add(0x18).readS32();
    if (arr.isNull() || count < 1 || count > 80 || arr.add(0x18).readS32() < count) return;
    var cards = [];
    for (var i = 0; i < count; i++) {
      var target = arr.add(0x20 + i * Process.pointerSize).readPointer();
      if (target.isNull()) return;
      if (bounce && !target.add(0x1c).readU8()) continue;
      var id = target.add(0x14).readS32();
      if (id >= 10000000 && id <= 99999999) cards.push(id);
    }
    returnSteps[key] = true;
    cards.forEach(function(id) { returnCounts[id] = (returnCounts[id] || 0) + 1; });
    if (cards.length) queueSample();
  }
  function watchMulligan() {
    var ns = Memory.allocUtf8String("Wizard2.ServerShared.MessagePackObjects");
    var handlers = {
      BattleResponsePlayOpen: function(args) {
        if (stopped || battleEnded || opening || !(args[2].toUInt32() & 1)) return;
        handDepartures["open/" + args[1].toInt32()] = args[3].toInt32();
        queueSample();
      },
      BattleResponsePutCardFromHand: recordHandPlay,
      BattleResponseCastSpellFromHand: recordHandPlay,
      BattleResponceReturnDeck: function(args) { recordReturn(args, false); },
      BattleResponseBounceIntoDeck: function(args) { recordReturn(args, true); },
      BattleResponseMulliganReady: function(args) {
        if (battleEnded || !opening) { returnCounts = {}; returnSteps = {}; playedFromHand = {}; playedSteps = {}; handDepartures = {}; send({deck_start: true}); }
        battleEnded = false;
        previousHands.clear(); previousTurn = -1;
        lastStep = -1; usingViewRoot = false; handView = null; opening = true; confirmedSide = null; allyCards = []; finishRoot = null;
        selfRef = null; enemyRef = null;
        send({idle: true, at: Date.now()});
      },
      BattleResponseMulligan: function(args) {
        if (!(args[3].toUInt32() & 1)) return;
        var list = args[4];
        if (list.isNull()) return;
        opening = true;
        allyCards = handKeys(list.add(0x10).readPointer(), list.add(0x18).readS32());
        selectFinishedPlayer();
      },
      BattleResponseMulliganFinish: function(args) {
        opening = true;
        finishRoot = args[2];
        selectFinishedPlayer();
      }
    };
    for (var j = 0; j < n; j++) {
      var img = asmImage(asms.add(j * Process.pointerSize).readPointer());
      Object.keys(handlers).forEach(function(name) {
        var k = classFromName(img, ns, Memory.allocUtf8String(name));
        if (k.isNull()) return;
        var it = Memory.alloc(Process.pointerSize), m;
        while (!(m = classGetMethods(k, it)).isNull()) {
          if (methodGetName(m).readUtf8String() !== ".ctor") continue;
          var handler = handlers[name];
          Interceptor.attach(m.readPointer(), {onEnter: function(args) {
            try { if (objClass(args[0]).equals(k)) handler(args); } catch (e) {}
          }});
          break;
        }
      });
    }
  }
  try { watchMulligan(); } catch (e) { send({err: "mulligan: " + e}); }
  // Python 侧外部扫描找到新 Mpo 后，通过这里换绑（不注入、不卡）
  function receiveBinding(msg) {
    try {
      if (msg && msg.addr && !isPlayerState(selfRef)) {
        var p = ptr(msg.addr);
        if (isPlayerState(p)) {
          selfRef = p;
          enemyRef = null;
          lastBindAt = Date.now();
          send({rebound: msg.addr});
        }
      }
    } finally {
      if (!stopped) recv("set_self", receiveBinding);
    }
  }
  recv("set_self", receiveBinding);
  var lastScan = 0;         // 上次主动扫描时间

  function rememberVtable(p) {
    try {
      var vt = p.readPointer();
      if (!vt.isNull()) knownVtable = vt;
    } catch (e) {}
  }

  var scanning = false;

  function vtHex(vt) {
    // NativePointer → 小端十六进制字节串（Memory.scan 的 pattern 格式）
    var hex = vt.toString().replace("0x", "");
    while (hex.length < 16) hex = "0" + hex;
    var bytes = [];
    for (var i = hex.length - 2; i >= 0; i -= 2) bytes.push(hex.substr(i, 2));
    return bytes.join(" ");
  }

  function tryAdopt(addr) {
    try {
      var obj = addr;
      var deck = obj.add(32).readS32();
      var life = obj.add(36).readS32();
      if (deck < 0 || deck > 45 || life < 0 || life > 30) return false;
      if (!isPlayerState(obj)) return false;
      if (isPlayerState(selfRef) && selfRef.toString() === obj.toString()) return false;
      selfRef = obj;
      enemyRef = null;
      send({found: obj.toString(), via: "scan"});
      return true;
    } catch (e) {}
    return false;
  }

  // 兜底：分块 + 异步扫（每块 32MB，Memory.scan 异步回调，不阻塞游戏）
  function scanForMpo() {
    if (knownVtable === null || scanning) return false;
    scanning = true;
    var pattern = vtHex(knownVtable);
    var ranges = Process.enumerateRanges('rw-');
    var CHUNK = 32 * 1024 * 1024;
    var ri = 0, off = 0, done = false;

    function finish() {
      scanning = false;
      done = true;
    }

    function nextRange() {
      if (done) return;
      if (ri >= ranges.length) { finish(); return; }
      var r = ranges[ri++];
      off = 0;
      nextChunk();
    }

    function nextChunk() {
      if (done) return;
      if (off >= r.size) { nextRange(); return; }
      var size = Math.min(CHUNK, r.size - off);
      var base = r.base.add(off);
      off += size;
      try {
        Memory.scan(base, size, pattern, {
          onMatch: function (address) {
            if (done) return "stop";
            if (tryAdopt(address)) {
              finish();
              return "stop";
            }
          },
          onError: function () {},
          onComplete: function () {
            if (!done) setTimeout(nextChunk, 0);
          }
        });
      } catch (e) {
        if (!done) setTimeout(nextChunk, 0);
      }
    }

    nextRange();
    return true;
  }
  var others = {};
  function readArr(listPtr, kind, handUids) {
    // 手牌和场面保存对象指针；游戏战斗日志保存内联 (CardId, StyleId)。
    //   List +0x18 = count；元素从 +0x20 起，每个 8 字节指针；
    //   不同类型元素的卡牌 ID 偏移分别处理。
    if (!listPtr || listPtr.isNull()) return kind === "hand" ? null : [];
    var count = listPtr.add(0x18).readS32();
    if (count < 0 || count > 80) return kind === "hand" ? null : [];
    var out = [];
    for (var i = 0; i < count; i++) {
      try {
        if (kind === "cardLog") {
          var cid = listPtr.add(0x20 + i * 8).readS32();
          if (cid >= 10000000 && cid <= 99999999) out.push(cid);
          continue;
        }
        var item = listPtr.add(0x20 + i * 8).readPointer();
        if (!item || item.isNull()) continue;
        if (kind === "field") {
          var fieldId = item.add(0x14).readS32();
          if (fieldId < 10000000 || fieldId > 99999999) continue;
          out.push({cardId: fieldId,
                    atk: item.add(0x1C).readS32(),
                    life: item.add(0x20).readS32()});
        } else if (kind === "hand") {
          // BattleHandCardMpo: UniqueId at +0x10, CardId at +0x14.
          out.push(item.add(0x14).readS32());
          if (handUids) handUids.push(item.add(0x10).readS32());
        } else {
          out.push(item.add(0x10).readS32());
        }
      } catch (e) {}
    }
    if (kind === "hand" && (out.length !== count || listPtr.add(0x18).readS32() !== count)) return null;
    return out;
  }
  function isPlayerState(p) {
    try { return !p.isNull() && className(objClass(p)).readUtf8String() === "BattleStatePlayerMpo"; }
    catch (e) { return false; }
  }
  function sample() {
    if (stopped) return;
    var now = Date.now();
    if (battleEnded) {
      if (now - lastIdle >= 2000) {
        lastIdle = now;
        send({idle: true, ended: true, at: now});
      }
      return;
    }
    if (!isPlayerState(selfRef)) {
      // 兜底：idle 超过 10 秒，主动扫内存找 Mpo（不依赖 hook 触发）
      if (false && now - lastScan >= 4000) {   // 暂时关闭全内存扫描（会卡游戏）
        lastScan = now;
        try { scanForMpo(); } catch (e) {}
      }
      if (now - lastIdle >= 2000) {
        lastIdle = now;
        send({idle: true, at: now});
      }
      // idle 持续 15 秒且已知 vtable：请求外部扫描（不注入、不卡）
      if (!idleSince) { idleSince = now; }
      else if (now - idleSince > 15000 && knownVtable) {
        idleSince = 0;
        send({need_scan: true,
              vtable: knownVtable ? knownVtable.toString() : null});
      }
      return;
    }
    idleSince = 0;
    rememberVtable(selfRef);
    try {
      var me = selfRef;
      var enemy = isPlayerState(enemyRef) ? enemyRef : null;
      var handPtr = me.add(16).readPointer();
      var useExternal = externalBoard && !(fallbackAddr === me.toString() && now < fallbackUntil);
      var ready = !opening && (me.add(0x38).readS32() > 0 || confirmedSide !== null);
      var displayed = ready ? shownHand() : null;
      var externalHand = !displayed && useExternal && ready && confirmedSide !== null &&
                         me.add(0x98).readU8() === confirmedSide;
      var uids = [];
      var info = {
        addr: me.toString(), vt: knownVtable.toString(), handPtr: handPtr.toString(),
        deck: me.add(32).readS32(),
        life: me.add(36).readS32(),
        mulliganDone: me.add(0x5C).readS8() != 0,
        turn: me.add(0x38).readS32(),
        pp: me.add(0x34).readS32(),
        maxpp: me.add(0x38).readS32(),
        returnCounts: returnCounts, playedFromHand: playedFromHand,
        hand: displayed ? displayed.ids : (ready && !externalHand ? readArr(handPtr, "hand", uids) : []),
        // Public opponent cards do not depend on a stable ally-hand animation.
        publicView: ready && handView && objClass(handView).equals(viewClass) ? handView.toString() : null,
        handSource: displayed ? "visible" : "snapshot",
        handUids: displayed ? displayed.uids : uids, handDepartures: handDepartures,
        externalHand: externalHand,
        fieldPtr: me.add(24).readPointer().toString(),
        field: useExternal ? null : readArr(me.add(24).readPointer(), "field"),
        played: readArr(me.add(112).readPointer(), "cardLog"),
        destroyed: readArr(me.add(120).readPointer(), "cardLog"),
        enemy: enemy ? {addr: enemy.toString(), life: enemy.add(36).readS32(),
          deck: enemy.add(32).readS32(), field: readArr(enemy.add(24).readPointer(), "field")} : null,
        at: now
      };
      if (ready && usingViewRoot) {
        var counts = Object.assign({}, playedFromHand), logCounts = {};
        info.played.forEach(function(id) { logCounts[id] = (logCounts[id] || 0) + 1; });
        Object.keys(logCounts).forEach(function(id) {
          counts[id] = Math.max(counts[id] || 0, logCounts[id]);
        });
        info.playedFromHand = counts;
      }
      info.handReady = ready;
      if (!info.handReady) info.hand = [];
      if (info.hand === null || !handPtr.equals(me.add(16).readPointer())) return;
      if (typeof info.deck !== "number" || typeof info.life !== "number"
          || typeof info.turn !== "number") return;
      if (info.deck < 0 || info.deck > 45 || info.life < 0 || info.life > 30
          || info.turn < 0 || info.turn > 60 || info.hand.length > 10) return;
      // 换绑后 600ms 内不采样（对象可能还没初始化，读出来是空/垃圾）
      if (now - lastBindAt < 600) return;
      // 正常思考期间数值可以长期不变，不以静止时长判定对象失效。
      if (!displayed && info.hand.length === 0 && lastHandAddr === info.addr && lastHandCount > 0) {
        lastHandCount = 0;
        return;  // 下一次完整采样仍为空时才发布
      }
      // 数据合理性：卡 id 必须是 8 位以上（10000000+），否则说明对象已释放/读错
      for (var hi = 0; hi < info.hand.length; hi++) {
        var hv = info.hand[hi];
        if (!(hv >= 10000000 && hv <= 99999999)) {
          return;
        }
      }
      lastHandAddr = info.addr;
      lastHandCount = info.hand.length;
      send(info);
    } catch (e) { send({err: String(e)}); }
  }
  if (deckFn !== null) {
    Interceptor.attach(deckFn, {onEnter: function(args) {
      if (stopped) return;
      try {
        var p = args[0];
        if (!isPlayerState(p)) return;
        // selfRef 只由 get_HandCardList 确定（那只在我方调用）；
        // 这里只修 enemy，避免把敌方当成我方（会导致胜负判反）
        if (isPlayerState(selfRef) && p.toString() !== selfRef.toString()
            && !isPlayerState(enemyRef)) {
          enemyRef = p;
        }
        rememberVtable(selfRef);
      } catch (e) {}
    }});
  }
  function handProgress(old, next) {
    if (next.log.length < old.log.length ||
        !old.log.every(function(id, i) { return id === next.log[i]; })) return false;
    if (next.log.length > old.log.length) return true;
    return old.uids.length !== next.uids.length || old.uids.some(function(uid, i) {
      return uid !== next.uids[i] || old.cards[i] !== next.cards[i];
    });
  }
  function handEvidence(player) {
    var uids = [];
    var cards = readArr(player.add(16).readPointer(), "hand", uids);
    if (!cards || cards.length > 10 || new Set(uids).size !== cards.length ||
        cards.some(function(id) { return id < 10000000 || id > 99999999; })) return null;
    return {uids: uids, cards: cards,
            log: readArr(player.add(112).readPointer(), "cardLog")};
  }
  var previousHands = new Set(), previousTurn = -1;
  Interceptor.attach(fn, {onEnter: function(args) {
    if (stopped) return;
    try {
      var p = args[0];
      if (!isPlayerState(p)) return;
      // get_HandCardList 会被多个对象调用（我方/敌方/回放），不能每次都换绑，
      // 否则数据会在多个对局对象间跳。只在「当前没有有效对象」时绑定；
      // 对象失效由数据冻结自愈（外部扫描）负责换绑。
      if (battleEnded) return;
      if (confirmedSide !== null && p.add(0x98).readU8() !== confirmedSide) return;
      var bound = isPlayerState(selfRef);
      if (bound && selfRef.equals(p)) { queueSample(); return; }
      var turn = p.add(0x38).readS32();
      var oldTurn = bound ? selfRef.add(0x38).readS32() : -1;
      if (previousTurn !== oldTurn) {
        previousHands.clear();
        previousTurn = oldTurn;
      }
      var newer = confirmedSide !== null && bound && turn > oldTurn;
      var action = false;
      if (confirmedSide !== null && bound && !opening && turn === oldTurn) {
        var old = handEvidence(selfRef), next = handEvidence(p);
        action = old && next && handProgress(old, next) &&
                 !previousHands.has(p.toString() + JSON.stringify(next));
      }
      if (!bound || newer || action) {
        if (action) previousHands.add(selfRef.toString() + JSON.stringify(old));
        selfRef = p;
        enemyRef = null;
        lastBindAt = bound ? 0 : Date.now();
        if (bound) queueSample();
        send({rebound: p.toString()});
      }
    } catch (e) {}
  }});
  var timer = setInterval(sample, 75);
  send({hooked: true});
  rpc.exports = {stop: function() {
    stopped = true;
    try { clearInterval(timer); } catch (e) {}
    try { Interceptor.detachAll(); } catch (e) {}
    selfRef = null; enemyRef = null;
    return true;
  }};
})();
"""


def _parse_args(argv):
    """argv[1] = 目标 PID（缺省自动找游戏进程），argv[2] = 最长运行秒数。"""
    pid = 0
    wait = DEFAULT_WAIT
    if len(argv) > 1:
        try:
            pid = int(argv[1])
        except (TypeError, ValueError):
            pid = 0
    if len(argv) > 2:
        try:
            wait = int(argv[2])
        except (TypeError, ValueError):
            wait = DEFAULT_WAIT
    return pid, wait


def _reconcile_hand(state, sample):
    """出牌消息和手牌数组可分帧到达；仅修正 UI 使用的手牌。"""
    from collections import Counter
    uids = sample.pop("handUids", [])
    departures = sample.pop("handDepartures", {})
    if not sample.get("handReady") or sample.get("handSource") == "visible":
        state.clear()
        return
    cards = sample["hand"]
    if len(cards) != len(uids) or len(set(uids)) != len(uids):
        return
    raw = dict(zip(uids, cards))
    previous = state.get("raw", {})
    events = state.setdefault("events", set())
    masked = state.setdefault("masked", set())
    masked.intersection_update(raw)
    for key, uid in departures.items():
        if key not in events and uid in raw:
            masked.add(uid)
    events.update(departures)
    # 只有累计出牌日志而没有实例事件时，以张数暂扣；候选仅限原手里
    # 的同名牌。真实快照到来后用消失的实例抵消暂扣，不会扣到新抽的牌。
    counts = Counter({int(cid): n for cid, n in sample.get("playedFromHand", {}).items()})
    pending = state.setdefault("pending", {})
    old_masked = state.get("last_masked", set())
    removed = Counter(c for u, c in previous.items()
                      if u not in old_masked and (raw.get(u) != c or u in masked))
    for cid, (candidates, debt) in list(pending.items()):
        still = {uid for uid in candidates if raw.get(uid) == cid and uid not in masked}
        settled = min(debt, len(candidates - still))
        removed[cid] = max(0, removed[cid] - settled)
        debt -= settled
        if debt and still:
            pending[cid] = (still, min(debt, len(still)))
        else:
            del pending[cid]
    # 原始列表也可能比累计日志早一帧变化。短期保留离手额度，免得
    # 日志追上时再减掉第二张同名牌。
    at = sample.get("at", 0)
    credits = [(c, n, expiry) for c, n, expiry in state.get("credits", []) if expiry >= at]
    credits.extend((c, n, at + 1500) for c, n in removed.items() if n)
    for cid, n in (counts - state.get("counts", counts)).items():
        for i, (c, amount, expiry) in enumerate(credits):
            if c == cid and n:
                used = min(amount, n)
                n -= used
                credits[i] = (c, amount - used, expiry)
        still = {u for u, c in previous.items() if c == cid and raw.get(u) == cid and u not in masked}
        if n and still:
            prior, debt = pending.get(cid, (set(), 0))
            pending[cid] = (prior | still, min(len(prior | still), debt + n))
    state["credits"] = [credit for credit in credits if credit[1]]
    state["last_masked"] = masked.copy()
    hidden = masked.copy()
    for candidates, debt in pending.values():
        hidden.update(sorted(candidates)[:debt])
    visible = {uid: cid for uid, cid in raw.items() if uid not in hidden}
    sample["hand"] = list(visible.values())
    state.update(raw=raw, counts=counts)


def _track_deck_cards(state, sample):
    """在写快照前累计已识别的抽牌，使 UI 跳过中间帧也能得到整局记录。"""
    from collections import Counter
    if not sample.get("handReady"):
        state.clear()
        return
    hand = Counter(sample.get("hand") or [])
    deck = sample["deck"]
    returned = Counter({int(cid): n for cid, n in sample.get("returnCounts", {}).items()})
    explicit_delta = returned - state.get("explicit_returns", Counter())
    # 牌库增长和手牌减少吻合时补记；相同卡的事件晚到时抵消这笔暂记。
    inferred = state.setdefault("inferred_returns", Counter())
    inferred -= explicit_delta
    if "deck" in state:
        growth = deck - state["deck"] - sum(explicit_delta.values())
        played = Counter({int(cid): n for cid, n in (sample.get("playedFromHand") or {}).items()})
        spent = played - state.get("last_plays", Counter())
        missing = state["hand"] - hand - spent - explicit_delta
        if growth > 0 and sum(missing.values()) == growth:
            inferred.update(missing)
    state["last_plays"] = Counter({int(cid): n for cid, n in (sample.get("playedFromHand") or {}).items()})
    state["explicit_returns"] = returned.copy()
    returned += inferred
    sample["returnCounts"] = dict(returned)
    new_returns = returned - state.get("returns", Counter())
    if "deck" not in state:
        state["drawn"] = hand.copy()
    else:
        gained = hand - state["hand"]
        delta = state["deck"] - deck + sum(new_returns.values())
        now = sample["at"]
        if delta > 0:
            state["credit"] = delta + (state.get("credit", 0) if now <= state.get("until", 0) else 0)
            state["until"] = now + 1000
        if gained and now <= state.get("until", 0):
            credit = state.get("credit", 0)
            count = sum(gained.values())
            if count <= credit:
                state["drawn"].update(gained)
            state["credit"] = max(0, credit - count)
        state["drawn"] |= hand
    # 洗回同一张再上手，可能只观察到手牌变化，错过牌库先增后减的中间帧。
    # 为原始牌组中的卡记录可证明的最低累计离库次数；不改生成牌的账。
    floor = state.setdefault("redraw_floor", Counter())
    previous_hand = state.get("hand", Counter())
    for cid, times in returned.items():
        floor[cid] = max(floor[cid], times)
        if hand[cid] > previous_hand[cid] or "hand" not in state:
            floor[cid] = max(floor[cid], times + hand[cid])
    sample["redrawFloor"] = dict(floor)
    state.update(hand=hand, deck=deck, returns=returned)
    sample["observedDraws"] = dict(state["drawn"])


def _track_draw(st, p):
    """更新爆牌/洗回统计（就地对 st 修改，并写回 p 的 overdraw/returned）。

    - 爆牌：手牌满(8)时抽牌，牌库减少但手牌没增加 → 差额计入 over。
    - 洗回：牌库数量变多 → ret=True。
    - 换牌阶段(mulliganDone=False)重置本局统计。
    """
    try:
        deck = int(p.get("deck") or 0)
        hand_n = len(p.get("hand") or [])
        st["addr"] = p.get("addr")
        mull = bool(p.get("mulliganDone") or (p.get("turn") or 0) > 0)
        if not mull:
            st.update(deck=deck, hand=hand_n, mull=False, over=0, ret=False)
        else:
            if st["mull"] and st["deck"] is not None:
                dd = st["deck"] - deck
                dh = hand_n - st["hand"]
                if dd > 0 and st["hand"] >= 8 and dh < dd:
                    st["over"] += dd - dh
                    print("爆牌 +%d（手牌 %d）" % (dd - dh, hand_n), flush=True)
                if deck > st["deck"]:
                    st["ret"] = True
            st.update(deck=deck, hand=hand_n, mull=True)
        p["overdraw"] = st["over"]
        p["returned"] = st["ret"]
        # 爆掉的卡 id 列表（由牌库列表 diff 提供；当前探测未确定偏移，先给空）
        p["overdraw_cards"] = st.get("over_cards") or []
    except Exception:
        pass


def _stop_requested():
    """控制台是否请求停止（写 logs/_frida.stop）。"""
    try:
        return os.path.exists(STOP_FILE)
    except Exception:
        return False


def _pid_alive(pid):
    """游戏进程还活着吗（Windows）。"""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    except Exception:
        return True




def _external_scan_and_rebind(script, pid, vtable_hex, state):
    """数据冻结时的自愈：用外部 ReadProcessMemory 扫描找当前 Mpo，再通知 agent 换绑。

    全程不注入、不扫游戏进程内内存（frida 扫会卡死游戏）。
    """
    if state.get("busy"):
        return
    now = time.time()
    if now - state.get("last", 0) < 20:
        return
    state["busy"] = True
    state["last"] = now

    def worker():
        h = None
        try:
            import readonly_reader as RO
            vt = int(vtable_hex, 16) if vtable_hex else 0
            if not vt:
                print("冻结自愈：没有 vtable，跳过", flush=True)
                return
            h = RO.open_process(pid)
            if not h:
                return
            hits = RO.scan_vtable_ctypes(h, vt, want=30)
            if not hits:
                print("冻结自愈：外部扫描没找到 Mpo", flush=True)
                return
            # 选「数据在变化」的对象（当前对局才会变；历史对局是冻结的）。
            # 两次采样对比，变的那个才是活的；都冻结再退回 turn/hand 打分。
            import time as _t
            # 只有唯一活动候选才尝试恢复；手牌多或生命高不能区分玩家。
            def signature(hit):
                a = hit["addr"]
                return tuple(RO.read_i32(h, a + off) for off in
                             (RO.OFF_DECK, RO.OFF_LIFE, RO.OFF_TURN))
            samples = [(hit, signature(hit)) for hit in hits]
            _t.sleep(0.4)
            changed = []
            for hit, before in samples:
                after = signature(hit)
                if None not in before and None not in after and before != after:
                    changed.append(hit)
            best = changed[0] if len(changed) == 1 else None
            if best:
                addr_hex = hex(best["addr"])
                print("冻结自愈：找到新 Mpo %s" % addr_hex,
                      flush=True)
                script.post({"type": "set_self", "addr": addr_hex})
        except Exception as e:
            print("冻结自愈失败:", e, flush=True)
        finally:
            if h:
                RO.kernel32.CloseHandle(h)
            state["busy"] = False

    import threading
    threading.Thread(target=worker, daemon=True).start()



LOCK_FILE = os.path.join(HERE, "logs", "frida_live.lock")


def _write_lock():
    try:
        os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
        with open(LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass


def _clear_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as fh:
            owner = int(fh.read().strip() or 0)
        if owner == os.getpid():
            os.remove(LOCK_FILE)
    except Exception:
        pass

def clear_stop():
    """清掉停止标记（启动时先清，退出时再清）。"""
    try:
        os.remove(STOP_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _write_snapshot(data):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    for delay in (0, 0.005, 0.012, 0.024):
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, OUT)
            return
        except PermissionError:
            if delay == 0.024:
                raise


def _attach_with_timeout(pid, timeout=15.0):
    import threading
    cancel = frida.Cancellable()
    deadline = time.monotonic() + timeout
    finished = threading.Event()

    def watch():
        while not finished.wait(0.1):
            if _stop_requested() or time.monotonic() >= deadline:
                cancel.cancel()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        with cancel:
            return frida.attach(pid)
    except frida.OperationCancelledError as exc:
        if _stop_requested():
            raise RuntimeError("连接已停止") from exc
        raise RuntimeError("Frida 连接超过 %.0f 秒，尚未读取游戏数据" % timeout) from exc
    finally:
        finished.set()
        watcher.join(timeout=0.3)


def main():
    pid, wait = _parse_args(sys.argv)
    if pid <= 0:
        # 不传 PID 时自动找游戏进程（以前默认写死 27692，换局就连错对象）
        try:
            import shadow_warchess_reader as RW
            pid, _ = RW.find_game_pid()
        except Exception:
            pid = 0
    if not pid:
        print("没找到游戏进程，实时读取未启动", flush=True)
        return 1

    import shadow_warchess_reader as RW
    image = RW.pid_image(pid) or ""
    if os.path.basename(image).lower() in ("shadowversewb.exe", "shadowverseworldsbeyond.exe"):
        import international_reader
        return international_reader.live_read(pid)
    if not RW._is_game_image(image) or not RW.has_game_module(pid):
        print("目标不是已加载 GameAssembly.dll 的影之诗 PC 客户端，未连接", flush=True)
        return 1
    if frida is None:
        print("缺少 Frida，请运行修复环境.bat", flush=True)
        return 1
    # 单实例：已有 frida_live 在跑就直接退出（避免两个进程抢写 deck_live）
    import singleton
    if not singleton.acquire("SvwbFrida", strict=True):
        print("已有实时读取在跑，跳过本次启动", flush=True)
        return 0
    from reader_supervisor import register_reader
    register_reader(pid)
    _write_lock()
    import atexit
    atexit.register(_clear_lock)

    clear_stop()
    print("attach", pid, "wait", wait, flush=True)
    session = None
    script = None
    import readonly_reader as ro
    board_handle = ro.open_process(pid)
    try:
        session = _attach_with_timeout(pid)
        print("attach complete; creating reader", flush=True)
        script = session.create_script("var externalBoard = %s;\n" % json.dumps(bool(board_handle)) + JS)
        latest = {}
        write_state = {"key": None, "heartbeat": 0.0, "line": None}
        freeze_state = {"busy": False, "last": 0.0}

        observed_cards = {}
        hand_state = {}
        callback_enabled = {"value": True}
        script_errors = []
        # 爆牌 / 洗回统计状态
        st = {"deck": None, "hand": 0, "mull": False,
              "over": 0, "ret": False, "over_cards": []}

        import battle_stats
        deck_at_start = None
        deck_captured = False

        def on_message(m, d):
            nonlocal deck_at_start, deck_captured
            if not callback_enabled["value"]:
                return
            if m.get("type") == "error":
                detail = m.get("stack") or m.get("description") or str(m)
                script_errors.append(detail)
                print("读取脚本错误：" + detail, flush=True)
                return
            if m.get("type") != "send":
                return
            p = m.get("payload") or {}
            if p.get("deck_start"):
                st.clear()
                st.update(deck=None, hand=0, over=0, ret=False, mull=False)
                observed_cards.clear()
                hand_state.clear()
                deck_at_start = battle_stats.active_deck()
                deck_captured = True
                return
            if p.get("result_sources"):
                print("result sources:", p["result_sources"], flush=True)
                return
            if isinstance(p.get("confirmed_win"), bool):
                event_id = "%s/%s/%s" % (pid, p["screen"], p["at"])
                try:
                    st_result = battle_stats.record(p["confirmed_win"], event_id=event_id, deck=deck_at_start)
                    deck_at_start = None
                    print("result recorded:", p.get("source", "StartUI"), st_result["wins"], st_result["losses"], flush=True)
                except Exception as exc:
                    print("写入结算结果失败：", exc, flush=True)
                p = {"idle": True, "ended": True, "at": p["at"]}
            if p.get("view_status"):
                print("hand view:", p["view_status"], flush=True)
                return
            if p.get("err") or p.get("hooked"):
                print("MSG:", m)
                return
            if p.get("need_scan"):
                print("长时间无数据，外部扫描找对象…", flush=True)
                _external_scan_and_rebind(script, pid, p.get("vtable"), freeze_state)
                return
            if p.get("rebound"):
                print("已换绑 Mpo:", p["rebound"], flush=True)
            if p.get("idle"):
                observed_cards.clear()
                hand_state.clear()
                st.update(deck=None, over=0, ret=False, mull=False)
                # 空闲心跳证明 agent 正常，避免 watchdog 误判读取器失联。
                idle_key = ("idle", bool(p.get("ended")))
                if write_state["key"] != idle_key or time.monotonic() - write_state["heartbeat"] >= 2.0:
                    try:
                        _write_snapshot({"idle": True, "ended": idle_key[1], "at": p.get("at")})
                        write_state["heartbeat"] = time.monotonic()
                        write_state["key"] = idle_key
                    except Exception as e:
                        print("write err", e, flush=True)
                return
            # 拒绝空数据：换绑瞬间读到空对象，别让它覆盖掉有效快照
            if p.get("deck") is None or p.get("life") is None:
                return
            vt = p.pop("vt", None)
            hand_ptr = p.pop("handPtr", None)
            field_ptr = p.pop("fieldPtr", None)
            external_hand = p.pop("externalHand", False)
            if p["field"] is None:
                board = ro.read_board(board_handle, int(p["addr"], 16), int(vt, 16),
                                      int(hand_ptr, 16), int(field_ptr, 16), p["turn"],
                                      include_hand=external_hand)
                if board is None or board["deck"] != p["deck"] or board["life"] != p["life"]:
                    # 不拼接不同时刻的数据。下一帧从同一个绑定对象取完整快照。
                    script.post({"type": "field_fallback", "addr": p["addr"]})
                    return
                p.update(board)
            import opponent_public
            public_view = p.pop("publicView", None)
            p.update(opponent_public.read_snapshot(board_handle, int(public_view, 16))
                     if public_view else
                     {"opponentPublicHand": [], "opponentPublicHandReady": False})
            # 爆牌 / 洗回统计
            if not deck_captured:
                deck_at_start = battle_stats.active_deck()
                deck_captured = True
            p["sampleHandUids"] = list(p.get("handUids") or [])
            _reconcile_hand(hand_state, p)
            _track_deck_cards(observed_cards, p)
            _track_draw(st, p)
            latest.clear()
            latest.update(p)
            # 内容无变化时只每 2 秒刷新心跳；有变化立即原子写快照。
            state_for_key = dict(p)
            state_for_key.pop("at", None)
            state_key = json.dumps(state_for_key, sort_keys=True, ensure_ascii=False)
            now_mono = time.monotonic()
            if state_key != write_state["key"] or now_mono - write_state["heartbeat"] >= 2.0:
                try:
                    _write_snapshot(latest)
                    write_state["key"] = state_key
                    write_state["heartbeat"] = now_mono
                except Exception as e:
                    print("write err", e, flush=True)
                    return
            hand_key = (p.get("handSource"), tuple(p["hand"]))
            if write_state.get("hand_key") != hand_key:
                print("hand at=%s source=%s ids=%s" % (p["at"], *hand_key), flush=True)
                write_state["hand_key"] = hand_key
            line = "Deck=%s Life=%s Hand=%s Played=%s" % (
                p.get("deck"), p.get("life"), len(p.get("hand") or []),
                len(p.get("played") or []))
            if line != write_state["line"]:
                print("state: " + line, flush=True)
                write_state["line"] = line

        script.on("message", on_message)
        print("loading reader", flush=True)
        script.load()
        if script_errors:
            raise RuntimeError(script_errors[0])
        print("reader loaded", flush=True)
        t0 = time.time()
        while time.time() - t0 < wait:
            if script_errors:
                raise RuntimeError(script_errors[0])
            if _stop_requested():
                # 直接退出：script.unload() 在 agent 卡住时会挂死，
                # 导致进程占着单实例锁、不再写文件（数据冻结 + 新进程起不来）。
                print("收到停止请求，退出", flush=True)
                try:
                    os._exit(0)
                except Exception:
                    pass
                break
            # 控制台退出后自己收尸，别留孤儿 Frida 进程
            try:
                import parent_guard
                if parent_guard.orphaned():
                    print("控制台已退出，停止实时读取", flush=True)
                    break
            except Exception:
                pass
            try:
                if not _pid_alive(pid):
                    print("游戏进程已退出，停止实时读取", flush=True)
                    break
            except Exception:
                pass
            time.sleep(1)
    except Exception as e:
        print("实时读取失败：%s" % e, flush=True)
        return 1
    finally:
        # 先屏蔽消息回调；目标进程已退出时不再向它发 unload/detach 请求。
        try:
            if "callback_enabled" in locals():
                callback_enabled["value"] = False
        except Exception:
            pass
        alive = _pid_alive(pid)
        if alive:
            import threading
            def cleanup_session():
                for operation in (lambda: script.exports_sync.stop() if script else None,
                                  lambda: script.unload() if script else None,
                                  lambda: session.detach() if session else None):
                    try:
                        operation()
                    except Exception:
                        pass
            cleanup_thread = threading.Thread(target=cleanup_session, daemon=True)
            cleanup_thread.start()
            cleanup_thread.join(timeout=2)
            if cleanup_thread.is_alive():
                print("读取器清理超时，结束读取器进程以释放单实例锁", flush=True)
                os._exit(1)
        else:
            print("目标游戏已退出，跳过 Frida 卸载请求", flush=True)
        if board_handle:
            ro.kernel32.CloseHandle(board_handle)
        clear_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
