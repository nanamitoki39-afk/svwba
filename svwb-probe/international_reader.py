"""Steam 国际服我方手牌定位。只读，候选地址不能视为已确认的实时状态。"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import struct
import time

import readonly_reader as memory
import shadow_warchess_reader as processes

HERE = Path(__file__).resolve().parent
RESULT = HERE / "logs" / "international_candidates.json"


def resolve_cards(values):
    import deck_reader
    meta = deck_reader.load_meta()
    cards = []
    for value in values:
        if value.isdecimal() and 10000000 <= int(value) <= 99999999:
            cards.append(int(value))
            continue
        matches = {int(cid) for cid, entry in meta.items()
                   if str(cid).isdecimal() and entry.get("name") == value}
        if len(matches) != 1:
            raise ValueError("卡名无法唯一匹配，请使用卡牌 ID：" + value)
        cards.extend(matches)
    return cards


def chunks(handle, ranges, deadline, max_bytes, pulse=None,
           pulse_bytes=64 * 1024 * 1024):
    """按 4MB 分块读取。pulse 按字节节流，不再每块都查一次进程存活。"""
    total = 0
    since_pulse = 0
    for base, size in ranges:
        offset = 0
        while offset < size:
            if time.monotonic() >= deadline or total >= max_bytes:
                return
            length = min(4 * 1024 * 1024, size - offset, max_bytes - total)
            data = memory.read_mem(handle, base + offset, length)
            total += length
            if data:
                yield base + offset, data
                if pulse is not None:
                    since_pulse += length
                    if since_pulse >= pulse_bytes:
                        since_pulse = 0
                        pulse()
            # 保留足以匹配跨块的指针槽和列表头的重叠区。
            offset += length if length <= 128 or offset + length >= size else length - 128
            time.sleep(0)


def locate(handle, cards, seconds, max_mb):
    expected = Counter(cards)
    deadline = time.monotonic() + seconds
    ranges = processes.regions(handle, private_only=True)
    instances = {}
    needle = struct.pack("<i", cards[0])
    scanned = 0
    raw_hits = 0
    # 第一轮只收集布局合理、卡 ID 与已确认手牌相符的卡对象。
    for base, data in chunks(handle, ranges, deadline, max_mb * 1048576):
        scanned += len(data)
        position = data.find(needle)
        while position >= 0:
            raw_hits += 1
            address = base + position - 0x14
            if address > 0 and address % 8 == 0:
                raw = memory.read_mem(handle, address, 24)
                if raw:
                    klass, monitor, uid, cid = struct.unpack("<QQii", raw)
                    if klass and klass % 8 == 0 and uid > 0 and cid in expected:
                        instances[address] = cid
            position = data.find(needle, position + 1)
    if not instances:
        return [], {"phase": "card_objects", "objects": 0, "raw_hits": raw_hits,
                    "scanned_mb": round(scanned / 1048576, 1),
                    "available_mb": round(sum(size for _, size in ranges) / 1048576, 1)}
    # 优先在候选对象附近查找引用，避免大型映射耗尽扫描时限。
    ranges.sort(key=lambda region: min(
        0 if region[0] <= address < region[0] + region[1]
        else min(abs(region[0] - address), abs(region[0] + region[1] - address))
        for address in instances))
    # 第二轮寻找引用这些对象的列表，再完整复读每个列表，避免半更新。
    results = {}
    needles = [struct.pack("<Q", address) for address in instances]
    deadline = time.monotonic() + seconds
    for base, data in chunks(handle, ranges, deadline, max_mb * 1048576):
        for needle in needles:
            position = data.find(needle)
            while position >= 0:
                slot = base + position
                if slot % 8 == 0:
                    for index in range(len(cards)):
                        address = slot - 0x20 - index * 8
                        if address in results:
                            continue
                        count = memory.read_i32(handle, address + 0x18)
                        if count is not None and len(cards) <= count <= 64:
                            raw = memory.read_mem(handle, address + 0x20, len(cards) * 8)
                            if raw is None:
                                continue
                            hand = []
                            for (ptr,) in struct.iter_unpack("<Q", raw):
                                card = memory.read_mem(handle, ptr + 0x10, 8) if ptr else None
                                if card is None:
                                    break
                                hand.append(struct.unpack("<ii", card))
                            if (len(hand) == len(cards) and Counter(cid for _, cid in hand) == expected
                                    and len({uid for uid, _ in hand}) == len(hand)
                                    and raw == memory.read_mem(handle, address + 0x20, len(cards) * 8)):
                                results[address] = {"address": hex(address), "cards": hand,
                                                    "capacity": count, "kind": "array_candidate",
                                                    "confirmed": False}
                position = data.find(needle, position + 1)
        if len(results) >= 100:
            break
    return list(results.values()), {"phase": "hand_lists", "objects": len(instances),
                                    "scan_limit_mb": max_mb, "seconds_per_phase": seconds}


def watch(handle, candidates, duration):
    end = time.monotonic() + duration
    previous = {}
    while time.monotonic() < end:
        for item in candidates:
            address = int(item["address"], 16)
            hand = memory.read_hand(handle, address, instances=True)
            if hand != previous.get(address):
                print(json.dumps({"at": time.time(), "candidate": hex(address),
                                  "hand": hand, "confirmed": False}, ensure_ascii=False), flush=True)
                previous[address] = hand
        time.sleep(0.15)



def process_identity(handle):
    import ctypes
    from ctypes import wintypes as W
    times = [W.FILETIME() for _ in range(4)]
    call = memory.kernel32.GetProcessTimes
    call.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    call.restype = W.BOOL
    if not call(handle, *(ctypes.byref(t) for t in times)):
        raise OSError("无法校验进程启动时间")
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def class_name(handle, address):
    klass = memory.read_ptr(handle, address)
    ptr = memory.read_ptr(handle, klass + 16) if klass else None
    raw = memory.read_mem(handle, ptr, 96) if ptr else None
    return raw.split(bytes([0]), 1)[0].decode("ascii", errors="replace") if raw else ""



_FIELD_CACHE = {}


def view_lifecycle(handle, view):
    """Read named game lifecycle flags, not the age of the retained snapshot."""
    klass = memory.read_ptr(handle, view)
    if not klass:
        return None
    key = (handle, klass)
    fields = _FIELD_CACHE.get(key)
    if fields is None:
        table = memory.read_ptr(handle, klass + 128)
        fields = {}
        if not table:
            return None
        for index in range(512):
            raw = memory.read_mem(handle, table + index * 32, 32)
            if not raw:
                break
            name_ptr, _, owner, offset, _ = struct.unpack("<QQQii", raw)
            if owner != klass:
                break
            name = memory.read_mem(handle, name_ptr, 96) if name_ptr else None
            if name and 16 <= offset < 8192:
                fields[name.split(b"\0", 1)[0].decode("ascii", errors="replace")] = offset
        required = ("IsReceivedResponseBattleEnd", "_isRetired",
                    "_isFinishResultCutin", "<IsFinishedMulligan>k__BackingField")
        if any(name not in fields for name in required):
            return None
        _FIELD_CACHE[key] = fields
    values = {}
    for name in ("IsReceivedResponseBattleEnd", "_isRetired",
                 "_isFinishResultCutin", "<IsFinishedMulligan>k__BackingField"):
        raw = memory.read_mem(handle, view + fields[name], 1)
        if raw not in (b"\0", b"\1"):
            return None
        values[name] = bool(raw[0])
    if any(values[name] for name in ("IsReceivedResponseBattleEnd",
                                    "_isRetired", "_isFinishResultCutin")):
        return "ended"
    return "active" if values["<IsFinishedMulligan>k__BackingField"] else "mulligan"


def read_hand_view(handle, view):
    """读 BattleView._allyPlayer._handCards（UI 实时手牌视图）。

    BattleViewServerData 要等服务器同步才更新（出牌/加手牌会晚一回合），
    而 UI 手牌视图是实时的；读不到就回退到服务器快照，不影响原有路径。
    """
    import opponent_public as op
    try:
        if op.class_name(handle, view) != "BattleView":
            return None
        owner_slot = op.member(handle, view, "_allyPlayer")
        owner = memory.read_ptr(handle, owner_slot) if owner_slot else None
        if not owner or "BattlePlayerView" not in op.class_name(handle, owner):
            return None
        list_slot = op.member(handle, owner, "_handCards")
        collection = memory.read_ptr(handle, list_slot) if list_slot else None
        if not collection:
            return None
        head = memory.read_mem(handle, collection, 32)
        if not head:
            return None
        array, count = struct.unpack_from("<Qi", head, 16)
        if not 0 <= count <= 10:
            return None
        capacity = memory.read_i32(handle, array + 24) if array else None
        if capacity is None or not count <= capacity <= 64:
            return None
        slots = memory.read_mem(handle, array + 32, count * 8) if count else b""
        if slots is None:
            return None
        out = []
        for (card,) in struct.iter_unpack("<Q", slots):
            if not card:
                return None
            uid_slot = op.member(handle, card, "<UniqueId>k__BackingField")
            id_slot = op.member(handle, card, "<CardId>k__BackingField")
            uid = memory.read_i32(handle, uid_slot) if uid_slot else None
            cid = memory.read_i32(handle, id_slot) if id_slot else None
            if uid is None or cid is None or not 10000000 <= cid <= 99999999:
                return None
            out.append((uid, cid))
        if (head != memory.read_mem(handle, collection, 32)
                or (count and slots != memory.read_mem(handle, array + 32, count * 8))):
            return None
        uids = [uid for uid, _cid in out]
        if len(set(uids)) != len(uids):
            return None
        return out
    except (OSError, ValueError, TypeError, struct.error):
        return None


def read_lives(handle, profile):
    """读展示层里双方生命 (我方, 对方)；失败返回 None。"""
    try:
        view = int(profile["view"], 16)
        data = memory.read_ptr(handle, view + profile["data_offset"])
        if not data:
            return None
        array = memory.read_ptr(handle, data + 16)
        if not array:
            return None
        players = memory.read_mem(handle, array + 32, 16)
        if not players:
            return None
        ally, oppo = struct.unpack_from("<QQ", players)
        ally_life = memory.read_i32(handle, ally + 36) if ally else None
        oppo_life = memory.read_i32(handle, oppo + 36) if oppo else None
        if ally_life is None or oppo_life is None:
            return None
        return ally_life, oppo_life
    except (OSError, ValueError, TypeError, struct.error):
        return None


_RETIRE_FIELDS = ("_isRetire", "IsPlayerRetire", "IsRetire",
                  "<IsPlayerRetire>k__BackingField",
                  "_isRetired", "<IsRetired>k__BackingField")


def read_retire(handle, profile):
    """读双方玩家的投降标志 (我方, 对方)；读不到返回 None。"""
    import opponent_public as op
    try:
        view = int(profile["view"], 16)
        data = memory.read_ptr(handle, view + profile["data_offset"])
        if not data:
            return None
        array = memory.read_ptr(handle, data + 16)
        if not array:
            return None
        players = memory.read_mem(handle, array + 32, 16)
        if not players:
            return None
        ally, oppo = struct.unpack_from("<QQ", players)

        def flag(player):
            if not player:
                return None
            for name in _RETIRE_FIELDS:
                slot = op.member(handle, player, name)
                if not slot:
                    continue
                raw = memory.read_mem(handle, slot, 1)
                if raw in (b"\0", b"\1"):
                    return bool(raw[0])
            return None

        ally_ret, oppo_ret = flag(ally), flag(oppo)
        if ally_ret is None and oppo_ret is None:
            return None
        return bool(ally_ret), bool(oppo_ret)
    except (OSError, ValueError, TypeError, struct.error):
        return None


def read_result(handle, profile, fallback=None):
    """结算瞬间判胜负。True=我方胜，False=我方负，None=无法判断。"""
    lives = read_lives(handle, profile) or fallback
    if lives:
        ally_life, oppo_life = lives
        if ally_life <= 0:
            return False
        if oppo_life <= 0:
            return True
    retired = read_retire(handle, profile)
    if retired:
        ally_ret, oppo_ret = retired
        if ally_ret:
            return False
        if oppo_ret:
            return True
    if lives:
        # 两边生命都 >0 还没结算：投降 / 超时 / 主动退出，按我方投降记负。
        return False
    return None


FAIL = {"where": "", "at": 0.0}


def _fail(where):
    """记录最近一次 read_current 卡在哪一环，重定位失败时打进日志。"""
    FAIL["where"] = where
    FAIL["at"] = time.monotonic()
    return None


def read_current(handle, profile):
    view = int(profile["view"], 16)
    if class_name(handle, view) != "BattleView":
        return _fail("不是 BattleView")
    if view_lifecycle(handle, view) != "active":
        return _fail("展示层未处于战斗")
    data = memory.read_ptr(handle, view + profile["data_offset"])
    if not data or class_name(handle, data) != "BattleViewServerData":
        return _fail("ServerData 不可读")
    array = memory.read_ptr(handle, data + 16)
    if not array or memory.read_i32(handle, array + 24) != 2:
        return _fail("Players 数组不完整")
    players = memory.read_mem(handle, array + 32, 16)
    if not players:
        return _fail("Players 读取失败")
    # BattleViewServerData uses player-perspective order: Players[0] is ally,
    # Players[1] is opponent (also used by the game's ally extra-PP/SE logic).
    # Do not identify sides by which hand is readable: public opponent cards
    # can make both hands readable, and opponent hidden IDs must not be read.
    player = struct.unpack_from("<Q", players)[0]
    if class_name(handle, player) != "BattleStatePlayerMpo":
        return _fail("我方 BattleStatePlayerMpo 不可读")
    head = memory.read_mem(handle, player, 160)
    if not head:
        return _fail("玩家状态读取失败")
    hand, field = struct.unpack_from("<QQ", head, 16)
    turn = struct.unpack_from("<i", head, 0x38)[0]
    # Read the current displayed hand first. A transient/retained server hand
    # must not prevent a valid UI hand (including an empty hand) from publishing.
    view_hand = read_hand_view(handle, view)
    board = memory.read_board(handle, player, struct.unpack_from("<Q", head)[0],
                              hand, field, turn, include_hand=view_hand is None)
    if board is None:
        return _fail("手牌/场面读取失败")
    if view_hand is not None:
        board["hand"] = [cid for _uid, cid in view_hand]
        board["handUids"] = [uid for uid, _cid in view_hand]
    if len(set(board["handUids"])) != len(board["handUids"]):
        return _fail("手牌实例重复")
    log = struct.unpack_from("<Q", head, 112)[0]
    count = memory.read_i32(handle, log + 24) if log else None
    if count is None or not 0 <= count <= 200:
        return _fail("出牌记录不可读")
    raw = memory.read_mem(handle, log + 32, count * 8) if count else b""
    if raw is None:
        return _fail("出牌记录读取失败")
    played = [struct.unpack_from("<i", raw, i * 8)[0] for i in range(count)]
    if any(not 10000000 <= cid <= 99999999 for cid in played):
        return _fail("出牌记录含非法卡 id")
    if (head != memory.read_mem(handle, player, 160)
            or data != memory.read_ptr(handle, view + profile["data_offset"])
            or array != memory.read_ptr(handle, data + 16)
            or players != memory.read_mem(handle, array + 32, 16)
            or view_lifecycle(handle, view) != "active"):
        return _fail("读取期间状态已变化")
    import opponent_public
    public = opponent_public.read_snapshot(handle, view)
    return dict(board, addr=hex(player), turn=turn, maxpp=turn,
                pp=struct.unpack_from("<i", head, 0x34)[0], handReady=True,
                mulliganDone=bool(head[0x5C]), handSource="international_view",
                played=played, destroyed=[], playedFromHand=dict(Counter(played)),
                returnCounts={}, enemy=None, at=int(time.time() * 1000), **public)



def _range_distance(rng, address):
    base, size = rng
    if base <= address < base + size:
        return 0
    return min(abs(address - base), abs(address - (base + size)))


def discover_views(handle, pid, pulse=lambda: None, klasses=None, hint=None):
    """Find the display owner by class identity, never by a saved heap address.

    扫描顺序：hint（上一局的 BattleView 地址）附近的区域优先，远处的内存最后扫。
    pulse 由 chunks 按 64MB 节流，避免每读 4MB 都 OpenProcess 一次拖慢扫描。
    """
    def scan(ranges):
        for base, data in chunks(handle, ranges, time.monotonic() + 40,
                                 16 * 1024**3, pulse=pulse):
            yield base, data
    def matches(data, needle):
        offset = data.find(needle)
        while offset >= 0:
            yield offset
            offset = data.find(needle, offset + 1)
    private = processes.regions(handle, private_only=True)
    if not klasses:
        names = set()
        for base, data in scan(processes.regions(handle)):
            names.update(base + off for off in matches(data, b"BattleView\0"))
        klasses = set()
        needles = [struct.pack("<Q", addr) for addr in names]
        for base, data in scan(private):
            for needle in needles:
                for off in matches(data, needle):
                    addr = base + off - 16
                    if addr % 8 == 0:
                        # Validate the class' own name, not arbitrary strings.
                        name_ptr = memory.read_ptr(handle, addr + 16)
                        name = memory.read_mem(handle, name_ptr, 11) if name_ptr else None
                        namespace = memory.read_ptr(handle, addr + 24)
                        ns = memory.read_mem(handle, namespace, 13) if namespace else None
                        if (name == b"BattleView\0" and ns == b"Wizard2.View\0"
                                and memory.read_ptr(handle, addr + 64) == addr
                                and memory.read_ptr(handle, addr + 72) == addr):
                            klasses.add(addr)
        print("国际服：找到展示类 %d 个" % len(klasses), flush=True)
    candidates = {}
    ordered = sorted(private, key=lambda rng: _range_distance(rng, hint)) if hint else private
    for base, data in scan(ordered):
        for klass in klasses:
            needle = struct.pack("<Q", klass)
            for off in matches(data, needle):
                addr = base + off
                if addr % 8 or addr in candidates:
                    continue
                if class_name(handle, addr) == "BattleView":
                    candidates[addr] = dict(pid=pid, created=process_identity(handle),
                                            view=hex(addr), data_offset=912, side=1)
    return list(candidates.values()), klasses


def select_view(handle, candidates, hint=None):
    valid = []
    for profile in candidates:
        sample = read_current(handle, profile)
        if sample is not None:
            valid.append((profile, sample))
    if not valid:
        return None
    # More than one owner with different state is ambiguous; never pick an old
    # snapshot just because it has a larger turn/deck/card count.
    keys = {json.dumps({k: v for k, v in sample.items() if k not in ("at", "addr")},
                       sort_keys=True) for _, sample in valid}
    if len(keys) == 1:
        return valid[0][0]
    return None


def live_read(pid, profile=None):
    import singleton
    if not singleton.acquire("SvwbFrida", strict=True):
        print("已有读取器正在运行", flush=True)
        return 3
    from reader_supervisor import register_reader
    register_reader(pid)
    import frida_live as ledger
    handle = memory.open_process(pid)
    if not handle:
        raise OSError("无法打开国际服")
    try:
        birth = process_identity(handle)
        if profile and (profile.get("pid") != pid or profile.get("created") != birth):
            profile = None
        ledger.clear_stop()
        state, draw = {}, dict(deck=None, hand=0, over=0, ret=False, mull=False)
        last_key, written, valid = None, 0, time.monotonic()
        result_state = {"ended_for": None}
        try:
            import battle_stats
            deck_key = battle_stats.active_deck()
        except Exception:
            deck_key = None
        candidates, klasses = [], None
        next_scan, pulse_at = 0, 0
        def pulse():
            nonlocal pulse_at
            if ledger._stop_requested() or not ledger._pid_alive(pid):
                raise InterruptedError("国际服读取已停止")
            if time.monotonic() - pulse_at >= 2:
                ledger._write_snapshot(dict(idle=True, at=int(time.time()*1000)))
                pulse_at = time.monotonic()
        while not ledger._stop_requested() and ledger._pid_alive(pid):
            sample = read_current(handle, profile) if profile else None
            if profile and sample is None:
                phase = view_lifecycle(handle, int(profile["view"], 16))
                if phase == "ended":
                    if result_state["ended_for"] != int(profile["view"], 16):
                        result_state["ended_for"] = int(profile["view"], 16)
                        win = read_result(handle, profile, result_state.get("lives"))
                        if win is not None:
                            try:
                                import battle_stats
                                battle_stats.record(
                                    win,
                                    event_id="intl:%s:%d" % (
                                        profile["view"], int(time.time() // 30)),
                                    deck=deck_key)
                                print("国际服：对局结束，记%s" % ("胜" if win else "负"), flush=True)
                            except Exception as exc:
                                print("国际服：记录胜负失败 %s" % exc, flush=True)
                    if last_key is not None:
                        ledger._write_snapshot(dict(idle=True, at=int(time.time()*1000)))
                        last_key = None
                        state.clear()
                        draw = dict(deck=None, hand=0, over=0, ret=False, mull=False)
                elif phase == "mulligan":
                    if last_key is not None:
                        ledger._write_snapshot(dict(idle=True, at=int(time.time()*1000)))
                        last_key = None
                        state.clear()
                        draw = dict(deck=None, hand=0, over=0, ret=False, mull=False)
                elif phase == "active":
                    result_state["ended_for"] = None
            if sample is None and time.monotonic() - valid > 2:
                hint = int(profile["view"], 16) if profile else None
                profile = select_view(handle, candidates, hint)
                if profile is None and time.monotonic() >= next_scan:
                    reason = ("；上次卡在：" + FAIL["where"]) if FAIL["where"] else ""
                    print("国际服：正在自动定位当前展示层%s" % reason, flush=True)
                    t0 = time.monotonic()
                    candidates, klasses = discover_views(handle, pid, pulse, klasses, hint)
                    print("国际服：扫描 %.1fs，候选 %d 个" % (
                        time.monotonic() - t0, len(candidates)), flush=True)
                    profile = select_view(handle, candidates, hint)
                    next_scan = time.monotonic() + 20
                if profile:
                    target = HERE / "logs" / "international_session.json"
                    temp = target.with_suffix(".tmp")
                    temp.write_text(json.dumps(profile), encoding="utf8")
                    os.replace(temp, target)
                    sample = read_current(handle, profile)
                    print("国际服：已连接当前展示层 " + profile["view"], flush=True)
            if sample is None:
                if time.monotonic() - valid > 6:
                    ledger._write_snapshot(dict(idle=True, at=int(time.time()*1000)))
            else:
                valid = time.monotonic()
                result_state["lives"] = read_lives(handle, profile)
                ledger._track_deck_cards(state, sample)
                ledger._track_draw(draw, sample)
                key = {k: v for k, v in sample.items() if k != "at"}
                if key != last_key or time.monotonic() - written >= 2:
                    ledger._write_snapshot(sample)
                    print("国际服 hand=%s deck=%s pp=%s" %
                          (sample["hand"], sample["deck"], sample["pp"]), flush=True)
                    last_key, written = key, time.monotonic()
            time.sleep(0.05)
    finally:
        memory.kernel32.CloseHandle(handle)
        ledger.clear_stop()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--cards", nargs="+", help="完整我方手牌，卡 ID 或唯一中文卡名，重复牌重复输入")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--max-mb", type=int, default=1024)
    parser.add_argument("--watch", type=float, default=60)
    args = parser.parse_args()
    if args.seconds <= 0 or args.max_mb <= 0 or args.watch < 0:
        parser.error("扫描限制必须为正数，观察时间不能为负数")
    pid, image = (args.pid, processes.pid_image(args.pid)) if args.pid else processes.find_game_pid()
    if not pid or not image or os.path.basename(image).lower() not in (
            "shadowversewb.exe", "shadowverseworldsbeyond.exe"):
        parser.error("未找到 Steam 国际服，请先启动游戏")
    if not args.cards:
        profile_path = HERE / "logs" / "international_session.json"
        profile = None
        try:
            profile = json.loads(profile_path.read_bytes())
        except (OSError, ValueError):
            pass
        return live_read(pid, profile)
    import singleton
    if not singleton.acquire("SvwbFrida", strict=True):
        print("已有读取器正在运行，请等待其退出。", flush=True)
        return 3
    cards = resolve_cards(args.cards)
    if not 1 <= len(cards) <= 10:
        parser.error("请提供 1 至 10 张完整手牌")
    handle = memory.open_process(pid)
    if not handle:
        raise OSError("无法以只读权限打开国际服")
    try:
        print("开始只读定位，扫描期间请保持手牌不变。", flush=True)
        candidates, progress = locate(handle, cards, args.seconds, args.max_mb)
        RESULT.parent.mkdir(exist_ok=True)
        data = {"pid": pid, "image": image, "at": time.time(), "seed": cards,
                "confirmed": False, "progress": progress, "candidates": candidates}
        temp = RESULT.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        os.replace(temp, RESULT)
        print("候选列表 %d 个，结果 %s。候选尚待抽牌、出牌及洗回验证。" %
              (len(candidates), RESULT), flush=True)
        watch(handle, candidates, args.watch)
    finally:
        memory.kernel32.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
