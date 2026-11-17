#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗 · 记牌器读取层（2026-10-21，纯内存版）

数据来源（Frida 实测 + dump.cs）：
  BattleStatePlayerMpo
    HandCards          +16   BattleHandCardMpo[]（CardId +0x14）
    DeckCount          +32   int32
    Life               +36   int32
    PlayedCardIds      +112  int64[]（低 32 位 = 卡 id）
    DestroyedCardIds   +120  int64[]
  完整卡组：内存里 max_length 30~45、元素都是卡表 id 的数组。

全部只读（ReadProcessMemory），不注入、不 hook、不碰 GC。
"""
import json
import os
import threading
import struct
import sys
import time


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NAMES_PATH = os.path.join(HERE, "gamedata", "sv_card_names.json")
META_PATH = os.path.join(HERE, "gamedata", "sv_card_meta.json")
# dsh 解包卡表作为备用来源；它只有部分卡，但能覆盖助手卡表缺失的条目。
DSH_CARDS_PATH = os.path.join(os.path.dirname(HERE),
                              "dsh-shadowverse-huanjing", "lib", "knowledge", "cards.json")
USER_DECK_PATH = os.path.join(HERE, "gamedata", "user_deck.json")
# 测试/排查时指到临时副本，避免和常驻读取抢同一个文件
LIVE_PATH = os.environ.get("SVWB_LIVE") or os.path.join(
    HERE, "logs", "deck_live.json")
_NAMES = [None]
_META = [None]
_META_KEY = [None]
_META_LOCK = threading.RLock()
_DSH_CARDS = [None]

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF

# il2cpp 运行时布局（64 位）
LIST_ITEMS_OFF = 0x10
LIST_SIZE_OFF = 0x18
ARRAY_DATA_OFF = 0x20

# 上次扫描到的地址（对局内有效，切局/地址失效时重扫）
_CACHE = {"state": 0, "deck": 0, "deck_ids": [], "at": 0.0}
_DECK_FILE_CACHE = {"key": None, "decks": [], "active": ""}
_DECK_FILE_LOCK = threading.RLock()


def _array_tools():
    # Normal JSON-based tracking never needs NumPy or its BLAS worker pools.
    # Legacy scans use vector operations, not parallel matrix multiplication.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    import numpy
    return numpy


def load_names():
    if _NAMES[0] is None:
        try:
            with open(NAMES_PATH, encoding="utf-8") as fh:
                _NAMES[0] = json.load(fh)
        except Exception:
            _NAMES[0] = {}
    return _NAMES[0]


def load_meta():
    """卡牌元数据按文件修改时间缓存，卡组不变时不重复读完整卡表。"""
    try:
        st = os.stat(META_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _META_LOCK:
        if _META[0] is not None and _META_KEY[0] == key:
            return _META[0]
        try:
            with open(META_PATH, encoding="utf-8") as fh:
                _META[0] = json.load(fh)
        except Exception:
            if _META[0] is None:
                _META[0] = {}
        _META_KEY[0] = key
        return _META[0]


def _deck_from_file():
    """手动导入的卡组：gamedata/user_deck.json 的 cards 数组（40 张，可重复）。

    截图/手填的兜底——内存扫描不到卡组时用它当基数。
    """
    try:
        with open(USER_DECK_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return [int(c) for c in (d.get("cards") or []) if c]
    except Exception:
        return []


def _deck_file_key():
    paths = (os.path.join(HERE, "gamedata", "user_decks.json"), USER_DECK_PATH)
    out = []
    for path in paths:
        try:
            st = os.stat(path)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _refresh_deck_file_cache():
    """卡组文件未变化时不重复读取和解析 JSON。"""
    key = _deck_file_key()
    with _DECK_FILE_LOCK:
        if key == _DECK_FILE_CACHE["key"]:
            return
        decks, active = [], ""
        p = os.path.join(HERE, "gamedata", "user_decks.json")
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            active = d.get("active") or ""
            for item in d.get("decks") or []:
                cards = [int(c) for c in (item.get("cards") or []) if c]
                if cards:
                    decks.append({"name": item.get("name") or "", "cards": cards})
        except Exception:
            pass
        if not decks:
            cards = _deck_from_file()
            if cards:
                decks = [{"name": "", "cards": cards}]
        _DECK_FILE_CACHE.update(key=key, decks=decks, active=active)


def _decks_from_file():
    """读取缓存的手动卡组；文件变化时自动刷新。"""
    _refresh_deck_file_cache()
    with _DECK_FILE_LOCK:
        return list(_DECK_FILE_CACHE["decks"])


def _active_name():
    """从卡组文件缓存读取当前卡组名。"""
    _refresh_deck_file_cache()
    with _DECK_FILE_LOCK:
        return _DECK_FILE_CACHE["active"]


def _load_dsh_cards():
    if _DSH_CARDS[0] is None:
        try:
            with open(DSH_CARDS_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _DSH_CARDS[0] = d.get("cards") or {}
        except Exception:
            _DSH_CARDS[0] = {}
    return _DSH_CARDS[0]


def _card_keys(cid):
    """生成同一张卡的可能 ID：原 ID、去掉异画尾号、dsh baseCardId。"""
    try:
        n = int(cid)
    except (TypeError, ValueError):
        return []
    out = [str(n)]
    # Shadowverse 的异画/形态 ID 常只在末位不同；仅作名称兜底，不改变计数。
    if n >= 10000000:
        out.extend([str(n - n % 10), str(n - n % 100)])
    return list(dict.fromkeys(out))


def card_meta(cid):
    """统一卡牌元数据查询，支持助手卡表和 dsh 解包卡表。"""
    meta = load_meta()
    for key in _card_keys(cid):
        v = meta.get(key)
        if isinstance(v, dict) and v.get("name"):
            return v
    dsh = _load_dsh_cards()
    for key in _card_keys(cid):
        v = dsh.get(key)
        if isinstance(v, dict) and v.get("name"):
            # 统一字段名，展示层和排序逻辑可以共用。
            return {"name": v.get("name"), "cost": v.get("cost"),
                    "rarity": v.get("rarity"), "atk": v.get("attack"),
                    "life": v.get("health"), "type": v.get("type"),
                    "class": v.get("class", v.get("classId"))}
    return {}


def card_name(cid):
    """卡 ID → 卡名；查不到时返回空字符串。"""
    v = card_meta(cid)
    return str(v.get("name") or "").strip()


def _name_of(cid):
    """卡 id → 卡名（用于把不同版本的同一张卡归并）。"""
    return card_name(cid)


# ---------------------------------------------------------------- 基础读取
def _u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def _i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def _valid(p):
    return MIN_PTR < p < MAX_PTR


def read_list(mem, list_obj, max_n=200):
    """读 il2cpp List<T> 元素指针（保留给自测/旧接口）。"""
    if not list_obj:
        return []
    try:
        size = _i32(mem.read(list_obj + LIST_SIZE_OFF, 4), 0)
        if not (0 <= size <= max_n):
            return []
        items = _u64(mem.read(list_obj + LIST_ITEMS_OFF, 8), 0)
        if not _valid(items):
            return []
        out = []
        for i in range(size):
            p = _u64(mem.read(items + ARRAY_DATA_OFF + i * 8, 8), 0)
            if p:
                out.append(p)
        return out
    except Exception:
        return []


def read_card_ids(mem, list_obj, param_off=None, card_id_off=None):
    if param_off is None or card_id_off is None:
        return []
    out = []
    for obj in read_list(mem, list_obj):
        try:
            param = _u64(mem.read(obj + param_off, 8), 0)
            if not _valid(param):
                continue
            cid = _i32(mem.read(param + card_id_off, 4), 0)
            if 10000000 <= cid <= 99999999:
                out.append(cid)
        except Exception:
            continue
    return out


def diff_counts(total_ids, remaining_ids):
    from collections import Counter
    t = Counter(total_ids)
    r = Counter(remaining_ids)
    drawn = {}
    for cid, n in t.items():
        d = n - r.get(cid, 0)
        if d > 0:
            drawn[cid] = d
    return drawn, dict(t)


def summarize(total_ids, remaining_ids, hand_ids=()):
    from collections import Counter
    t = Counter(total_ids)
    r = Counter(remaining_ids)
    h = Counter(hand_ids)
    rows = []
    for cid in sorted(t):
        rows.append((cid, t[cid], r.get(cid, 0), h.get(cid, 0)))
    return {"total": len(total_ids), "remaining": len(remaining_ids),
            "hand": len(hand_ids), "rows": rows}


# ---------------------------------------------------------------- 实时读取
def _arr_len(h, RW, arr):
    if not _valid(arr):
        return -1
    raw = RW.read(h, arr + 0x18, 4)
    return _i32(raw, 0) if raw else -1


def _arr_int64(h, RW, arr, k=60):
    n = _arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    return [_i32(raw, i * 8) for i in range(min(n, k, len(raw) // 8))]


def _read_state(h, RW, p):
    """读一个 BattleStatePlayerMpo 候选；无效返回 None。"""
    if not _valid(p):
        return None
    deck = _i32(RW.read(h, p + 32, 4) or b"", 0)
    life = _i32(RW.read(h, p + 36, 4) or b"", 0)
    if not (0 <= deck <= 45 and 1 <= life <= 20):
        return None
    hand_arr = _u64(RW.read(h, p + 16, 8) or b"", 0)
    played_arr = _u64(RW.read(h, p + 112, 8) or b"", 0)
    destroyed_arr = _u64(RW.read(h, p + 120, 8) or b"", 0)
    hand = []
    n = _arr_len(h, RW, hand_arr)
    if 0 < n <= 12:
        raw = RW.read(h, hand_arr + 0x20, n * 8) or b""
        for i in range(min(n, len(raw) // 8)):
            e = _u64(raw, i * 8)
            if not _valid(e):
                continue
            raw2 = RW.read(h, e + 0x10, 8) or b""
            if len(raw2) == 8:
                cid = _i32(raw2, 4)          # +0x14 = BattleHandCardMpo.CardId
                if cid:
                    hand.append(cid)
    return {"addr": p, "deckCount": deck, "life": life, "hand": hand,
            "played": [c for c in _arr_int64(h, RW, played_arr) if c],
            "destroyed": [c for c in _arr_int64(h, RW, destroyed_arr) if c]}


def _scan_state(h, RW, budget=64 << 30):
    np = _array_tools()
    total = 0
    cands = []
    for base, size in RW.regions(h):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x90:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 16:
                    deck = arr[4:-12] & 0xFFFFFFFF
                    life = arr[4:-12] >> 32
                    hand = arr[2:-14]
                    played = arr[14:-2]
                    mask = ((deck >= 1) & (deck <= 45)
                            & (life >= 1) & (life <= 20)
                            & (hand > MIN_PTR) & (hand < MAX_PTR)
                            & (played > MIN_PTR) & (played < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            cands.append(p)
            off += n
    return cands


def _scan_deck(h, RW, ids, budget=64 << 30):
    np = _array_tools()
    out = []
    total = 0
    for base, size in RW.regions(h):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    mask = (mlen >= 30) & (mlen <= 45)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        nl = int(mlen[i])
                        rawel = RW.read(h, p + 0x20, min(nl, 45) * 8)
                        if not rawel:
                            continue
                        ids2 = []
                        for k in range(min(nl * 2, len(rawel) // 4)):
                            v = _i32(rawel, k * 4)
                            if v in ids:
                                ids2.append(v)
                        if len(ids2) >= 30:
                            out.append((p, ids2))
            off += n
    return out


def _find_state(h, RW):
    meta = load_meta()
    ids = set(int(k) for k in meta) if meta else set()
    cands = _scan_state(h, RW)
    cands.sort(reverse=True)
    best = None
    for p in cands[:3000]:
        s = _read_state(h, RW, p)
        if not s:
            continue
        real = [c for c in s["hand"] + s["played"] + s["destroyed"]
                if not ids or c in ids]
        if not real:
            continue
        # 选“最像当前局”的：牌库少、有手牌/已打出
        if best is None or (s["deckCount"], len(s["hand"])) < (best["deckCount"],
                                                              len(best["hand"])):
            best = s
    return best


def _scan_deck_runs(h, RW, ids, min_run=30, max_gap=2):
    """暴力扫描：找内存里连续（允许小间隔）30+ 个合法卡表 id 的序列。"""
    np = _array_tools()
    try:
        ids_arr = np.array(sorted(int(k) for k in ids), dtype=np.int32)
    except Exception:
        return []
    best = []
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 128:
                a = np.frombuffer(raw, dtype=np.int32)
                mask = np.isin(a, ids_arr)
                if int(mask.sum()) >= min_run:
                    idx = np.nonzero(mask)[0]
                    runs = []
                    start = prev = int(idx[0])
                    for x in idx[1:]:
                        x = int(x)
                        if x - prev <= max_gap:
                            prev = x
                        else:
                            runs.append((start, prev))
                            start = prev = x
                    runs.append((start, prev))
                    for s, e in runs:
                        if e - s + 1 < min_run:
                            continue
                        vals = [int(v) for v in a[s:e + 1] if int(v) in ids]
                        if len(vals) > len(best):
                            best = vals
            off += n
    return best


def _find_deck(h, RW, state):
    meta = load_meta()
    ids = set(int(k) for k in meta) if meta else set()
    if not ids:
        return []
    known = set(state.get("played") or []) | set(state.get("hand") or [])
    known |= set(state.get("destroyed") or [])
    # ① 先进卡组界面/数组头方式
    decks = _scan_deck(h, RW, ids)
    best = None
    if decks:
        best = max(decks, key=lambda x: len(known & set(x[1])), default=None)
    # ② 暴力找连续 id 序列（卡组界面验证可用）
    runs = _scan_deck_runs(h, RW, ids)
    if runs and (best is None or len(known & set(runs)) > len(known & set(best[1]))):
        best = (0, runs)
    if not best:
        return []
    # 游戏外（没有手牌/已打出）时直接认“最长卡组序列”
    if not known:
        return best[1]
    # 只认“和当前手牌/已打出对得上”的卡组：对不上就宁可空，也不显示别的职业的牌
    hit = len(known & set(best[1]))
    need = max(2, len(known) // 2)
    if hit < need:
        return []
    return best[1]


_LAST_SNAP = {"data": None, "at": 0.0}
_LAST_SNAP_LOCK = threading.RLock()
SNAPSHOT_STALE_SEC = 6.0  # Three missed two-second heartbeats.


def snapshot(min_refresh=3.0):
    """读常驻读取写出的 logs/deck_live.json，返回记牌器快照。

    读取竞态（frida 正在原子替换文件）或短暂无更新时沿用上一次快照，
    避免记牌器在「对局列表」和「当前卡组」之间来回闪。
    """
    try:
        snap = _snapshot_impl(min_refresh)
    except Exception:
        snap = None
    now = time.monotonic()
    with _LAST_SNAP_LOCK:
        if snap is not None and snap.get("in_battle"):
            _LAST_SNAP["data"] = snap
            _LAST_SNAP["at"] = now - max(0.0, time.time() - snap["sample_at"] / 1000.0)
            return dict(snap)
        cached = _LAST_SNAP.get("data")
        age = now - _LAST_SNAP.get("at", 0.0)
        # 只有读取失败/文件替换竞态才沿用缓存；确认文件已过期则视为真实离局。
        transient = (snap is None or "等待常驻读取" in
                     str(snap.get("reason") or ""))
        if transient and cached is not None and age < SNAPSHOT_STALE_SEC:
            return dict(cached)
        _LAST_SNAP.update(data=None, at=0.0)
        if snap is None:
            return {"in_battle": False,
                    "reason": "等待常驻读取（logs/deck_live.json）"}
        return snap


def _snapshot_impl(min_refresh=3.0):
    """读常驻读取写出的 logs/deck_live.json，返回记牌器快照。"""
    import io
    path = LIVE_PATH
    try:
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
        s = json.loads(raw)
    except Exception:
        # 读到半写 JSON、文件被替换或瞬时 IO 错误时由 snapshot() 返回缓存。
        return None
    if s.get("idle") or s.get("ended"):
        return {"in_battle": False, "reason": "等待进入对局"}
    age = time.time() - float(s.get("at") or 0) / 1000.0
    if age >= SNAPSHOT_STALE_SEC:
        return {"in_battle": False, "reason": "对局已结束 / 实时读取未在跑"}
    ready = s.get("handReady", bool(s.get("mulliganDone") or (s.get("turn") or 0) > 0))
    hand = [c for c in s.get("hand") or [] if c] if ready else []
    played = [c for c in s.get("played") or [] if c]
    destroyed = [c for c in s.get("destroyed") or [] if c]
    deck_ids = _CACHE.get("deck_ids") or []
    from_file = False
    # 手动导入的卡组**优先**：内存扫描缓存可能是旧卡组/别的卡组，
    # 会导致牌库数字不减、显示错误的卡组构成（用户 2026-10-29）
    names_by_id = {}
    def name_of(cid):
        if cid not in names_by_id:
            names_by_id[cid] = _name_of(cid)
        return names_by_id[cid]

    decks_all = _decks_from_file()
    if decks_all:
        from_file = True
        seen0 = [c for c in (hand + played + destroyed) if c]
        active = _active_name()
        ordered = [x for x in decks_all if x.get("name") == active]
        ordered += [x for x in decks_all if x.get("name") != active]
        if ordered[0].get("name") == active or not seen0:
            deck_ids = ordered[0]["cards"]
        else:
            best = None
            for item in ordered:
                names = set(name_of(c) for c in item["cards"])
                hit = sum(1 for c in seen0
                          if name_of(c) and name_of(c) in names)
                if best is None or hit > best[0]:
                    best = (hit, item["cards"])
            # 手牌少时（1~3 张）也要能匹配上：门槛 max(1, len//4)
            need = max(1, len(seen0) // 4)
            if best and best[0] >= need:
                deck_ids = best[1]
    # 有导入卡组时不再做内存扫描（本来就扫不到，白卡 6GB）
    if not deck_ids and not from_file and not _CACHE.get("deck_scanning"):
        _CACHE["deck_scanning"] = True
        try:
            import shadow_warchess_reader as RW
            pid, _ = RW.find_game_pid()
            h = RW.open_proc(pid)
            state = {"addr": s.get("addr"), "deckCount": s.get("deck"),
                     "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"), "enemy": s.get("enemy"), "mulliganDone": bool(s.get("mulliganDone")), "retire": bool(s.get("retire")), "hand": hand,
                     "played": played, "destroyed": destroyed}

            def _bg():
                try:
                    ids = _find_deck(h, RW, state)
                    if ids:
                        _CACHE["deck_ids"] = ids
                finally:
                    _CACHE["deck_scanning"] = False

            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CACHE["deck_scanning"] = False
        deck_ids = []
    if not deck_ids:
        # 卡组没扫到：牌库列表留空，手牌由 deck_tracker 单独显示
        return {"in_battle": True, "handReady": ready, "addr": s.get("addr"), "sample_at": s["at"], "deckCount": s.get("deck"),
                "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"), "enemy": s.get("enemy"), "mulliganDone": bool(s.get("mulliganDone")), "retire": bool(s.get("retire")), "hand": hand, "played": played,
                "destroyed": destroyed, "total": 0,
                "remaining": s.get("deck") or 0, "rows": [],
                "overdraw": int(s.get("overdraw") or 0),
                 "opponentPublicHand": s.get("opponentPublicHand") or [],
                "opponentPublicHandReady": bool(s.get("opponentPublicHandReady")),
                "playedFromHand": s.get("playedFromHand") or {}, "returnCounts": s.get("returnCounts", {}), "returned": bool(s.get("returned") or s.get("returnCounts"))}
    from collections import Counter
    total = Counter(deck_ids)
    returns = Counter({int(cid): n for cid, n in s.get("returnCounts", {}).items()})
    available = total.copy()
    for cid, count in returns.items():
        match = cid
        if cid not in total and name_of(cid):
            match = next((k for k in total if name_of(k) == name_of(cid)), cid)
        available[match] += count

    def mapped(cards):
        remaining = available.copy()
        # 优先匹配卡 ID，同名异画只分配尚未扣除的份额。
        counts = Counter(cards)
        allocated = remaining & counts
        remaining.subtract(allocated)
        unmatched = Counter()
        for cid, qty in (counts - allocated).items():
            key = name_of(cid)
            if key:
                unmatched[key] += qty
        if unmatched:
            for cid in sorted(available):
                key = name_of(cid)
                qty = min(remaining[cid], unmatched[key])
                allocated[cid] += qty
                remaining[cid] -= qty
                unmatched[key] -= qty
        return allocated

    # 每种证据独立映射到导入卡组，再取最大值，避免同一张牌跨区重复扣除。
    in_hand = mapped(hand)
    observed = Counter({int(cid): int(n) for cid, n in
                        (s.get("observedDraws") or {}).items() if int(n) > 0})
    for raw_id, qty in (s.get("redrawFloor") or {}).items():
        cid = int(raw_id)
        if cid in total or (name_of(cid) and any(name_of(k) == name_of(cid) for k in total)):
            observed[cid] = max(observed[cid], int(qty))
    evidence = mapped(observed) | in_hand
    if not s.get("returned") and not returns:
        evidence |= mapped(hand + played) | mapped(hand + destroyed)
    remaining = available - evidence
    # 一张原始牌最多占一个位置。按卡名在各异画之间共享上限；
    # 新生成且不在原始牌组里的卡仍按实际洗入的数量统计。
    if returns:
        capacity = Counter()
        group = {cid: name_of(cid) or cid for cid in total}
        for cid, qty in total.items():
            capacity[group[cid]] += qty - in_hand[cid]
        for cid in sorted(total):
            key = group[cid]
            remaining[cid] = min(remaining[cid], max(0, capacity[key]))
            capacity[key] -= remaining[cid]
    if s.get("deck") == 0:
        remaining.clear()
    rows = [(cid, total[cid], remaining[cid], in_hand[cid]) for cid in sorted(set(total) | set(remaining))]
    return {"in_battle": True, "handReady": ready, "addr": s.get("addr"), "sample_at": s["at"], "deckCount": s.get("deck"),
            "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"), "enemy": s.get("enemy"), "mulliganDone": bool(s.get("mulliganDone")), "retire": bool(s.get("retire")), "hand": hand, "played": played,
            "destroyed": destroyed, "total": len(deck_ids),
            "remaining": s.get("deck") or 0, "rows": rows,
            "overdraw": int(s.get("overdraw") or 0),
             "opponentPublicHand": s.get("opponentPublicHand") or [],
                "opponentPublicHandReady": bool(s.get("opponentPublicHandReady")),
                "playedFromHand": s.get("playedFromHand") or {}, "returnCounts": s.get("returnCounts", {}), "returned": bool(s.get("returned") or s.get("returnCounts"))}


def warm_deck():
    """同步扫描卡组并写入缓存（窗口启动前调用，不在 Tk 线程里扫）。"""
    decks = _decks_from_file()
    if decks:
        active = _active_name()
        item = next((x for x in decks if x.get("name") == active), decks[0])
        _CACHE["deck_ids"] = item["cards"]
        return len(item["cards"])
    import io
    try:
        with io.open(LIVE_PATH, encoding="utf-8") as fh:
            raw = fh.read()
        s = json.loads(raw)
    except Exception:
        return 0
    ready = s.get("handReady", bool(s.get("mulliganDone") or (s.get("turn") or 0) > 0))
    hand = [c for c in s.get("hand") or [] if c] if ready else []
    played = [c for c in s.get("played") or [] if c]
    destroyed = [c for c in s.get("destroyed") or [] if c]
    try:
        import shadow_warchess_reader as RW
        pid, _ = RW.find_game_pid()
        h = RW.open_proc(pid)
        state = {"addr": s.get("addr"), "deckCount": s.get("deck"),
                 "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"), "enemy": s.get("enemy"), "mulliganDone": bool(s.get("mulliganDone")), "retire": bool(s.get("retire")), "hand": hand,
                 "played": played, "destroyed": destroyed}
        ids = _find_deck(h, RW, state)
        if ids:
            _CACHE["deck_ids"] = ids
        return len(ids)
    except Exception:
        return 0
