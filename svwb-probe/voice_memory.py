"""Observe the game's actual voice playback pool using read-only memory."""
from pathlib import Path
import json
import os
import math
import queue
import struct
import threading
import time

import readonly_reader as memory
import shadow_warchess_reader as processes
from opponent_public import class_name, member


def text(handle, address, maximum=512):
    if not address:
        return ""
    count = memory.read_i32(handle, address + 16)
    if count is None or not 0 <= count <= maximum:
        return None
    raw = memory.read_mem(handle, address + 20, count * 2) if count else b""
    return raw.decode("utf-16-le", errors="strict") if raw is not None else None


def value(handle, obj, name, kind="i"):
    address = member(handle, obj, name)
    if address is None:
        return None
    size = struct.calcsize("<" + kind)
    raw = memory.read_mem(handle, address, size)
    return struct.unpack("<" + kind, raw)[0] if raw else None


def pointer(handle, obj, name):
    return value(handle, obj, name, "Q")


def static_instance(handle, klass):
    table = memory.read_ptr(handle, klass + 128)
    storage = memory.read_ptr(handle, klass + 184)
    if not table or not storage:
        return None
    for index in range(256):
        raw = memory.read_mem(handle, table + index * 32, 32)
        if not raw:
            return None
        name, _, owner, offset, _ = struct.unpack("<QQQii", raw)
        if owner != klass:
            break
        name_raw = memory.read_mem(handle, name, 64) if name else None
        if name_raw and name_raw.split(b"\0", 1)[0] == b"instance" and 0 <= offset <= 1024:
            instance = memory.read_ptr(handle, storage + offset)
            if instance and memory.read_ptr(handle, instance) == klass:
                return instance
    return None


CACHE = Path(__file__).resolve().parent / "logs/voice-manager.json"
STATUS = Path(__file__).resolve().parent / "logs/voice-status.json"

def report_status(state, **details):
    try:
        STATUS.parent.mkdir(exist_ok=True)
        tmp = STATUS.with_name(STATUS.name + ".%d.%d.tmp" % (os.getpid(), threading.get_ident()))
        tmp.write_text(json.dumps(dict(state=state, at=time.time(), **details), ensure_ascii=False), encoding="utf8")
        tmp.replace(STATUS)
    except OSError:
        pass

def cached_manager(handle, pid):
    from international_reader import process_identity
    try:
        saved = json.loads(CACHE.read_bytes())
        if saved["pid"] != pid or saved["created"] != process_identity(handle):
            return None
        manager = int(saved["manager"], 16)
        return manager if read_voice_pool(handle, manager) is not None else None
    except (OSError, ValueError, TypeError, KeyError):
        return None

def save_manager(handle, pid, manager):
    from international_reader import process_identity
    if read_voice_pool(handle, manager) is None:
        return
    try:
        CACHE.parent.mkdir(exist_ok=True)
        tmp = CACHE.with_name(CACHE.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(dict(pid=pid, created=process_identity(handle), manager=hex(manager))), encoding="utf8")
        tmp.replace(CACHE)
    except OSError:
        pass

def locate_audio_manager(handle, cancel):
    """Find the Cute.Wwise class and its own static instance, never attach Frida."""
    from international_reader import chunks
    private = processes.regions(handle, private_only=True)
    private_bases = {base for base, _ in private}
    mapped = [region for region in processes.regions(handle) if region[0] not in private_bases]
    names = set()
    def scan(ranges):
        for base, data in chunks(handle, ranges, time.monotonic()+35, 16*1024**3):
            if cancel.is_set():
                raise InterruptedError()
            yield base, data
            cancel.wait(.002)
    for ranges in (mapped, private):
        for base, data in scan(ranges):
            offset = data.find(b"AudioManager\0")
            while offset >= 0:
                names.add(base+offset)
                offset = data.find(b"AudioManager\0", offset+1)
        if names:
            break
    needles = [struct.pack("<Q", address) for address in names]
    # Metadata usually lives near the active card-state class: prioritize it.
    try:
        import json
        current = json.loads((Path(__file__).resolve().parent/"logs/deck_live.json").read_bytes())
        candidate = int(current.get("addr", "0"), 16)
        target = memory.read_ptr(handle, candidate) or 0
        if target:
            private.sort(key=lambda region:abs(region[0]-target))
    except (OSError, ValueError, TypeError):
        pass
    for base, data in scan(private):
        for needle in needles:
            offset = data.find(needle)
            while offset >= 0:
                klass = base+offset-16
                if klass%8 == 0 and memory.read_ptr(handle,klass+64)==klass and memory.read_ptr(handle,klass+72)==klass:
                    namespace = memory.read_ptr(handle,klass+24)
                    raw = memory.read_mem(handle,namespace,32) if namespace else None
                    if raw and raw.split(b"\0",1)[0]==b"Cute.Wwise":
                        instance=static_instance(handle,klass)
                        if instance:
                            return instance
                offset=data.find(needle,offset+1)
    return None


def read_voice_pool(handle, manager):
    if class_name(handle, manager) != "AudioManager":
        return None
    pools = pointer(handle, manager, "pools")
    if not pools or memory.read_i32(handle,pools+24) != 4:
        return None
    # SoundGroup.Voice == 2. Ignore music, effects and voice chat.
    pool = memory.read_ptr(handle,pools+32+2*8)
    collection = pointer(handle,pool,"<entityList>k__BackingField") if pool else None
    head = memory.read_mem(handle,collection,32) if collection else None
    if not head:
        return None
    array,count=struct.unpack_from("<Qi",head,16)
    capacity=memory.read_i32(handle,array+24) if array else None
    if not array or capacity is None or not 0<=count<=64 or not count<=capacity<=128:
        return None
    slots=memory.read_mem(handle,array+32,count*8) if count else b""
    if slots is None:
        return None
    records=[]
    for (entity,) in struct.iter_unpack("<Q",slots):
        if not entity:
            continue
        group=value(handle,entity,"<SoundGroup>k__BackingField")
        if group != 2:
            return None
        playing=value(handle,entity,"<AkPlayingId>k__BackingField","I")
        sound_handle=value(handle,entity,"<SoundHandle>k__BackingField")
        status=value(handle,entity,"<PlayStatus>k__BackingField")
        marker_ptr=pointer(handle,entity,"<SoundFileName>k__BackingField")
        marker=text(handle,marker_ptr)
        events=pointer(handle,entity,"events")
        event_raw=memory.read_mem(handle,events+32,4) if events and memory.read_i32(handle,events+24)==1 else None
        duration=value(handle,entity,"<Duration>k__BackingField","f")
        started=value(handle,entity,"<AtPlayStartTime>k__BackingField","f")
        paused=value(handle,entity,"<IsPaused>k__BackingField","B")
        if (None in (playing,sound_handle,status,marker,duration,started,paused)
                or event_raw is None or not math.isfinite(duration) or not math.isfinite(started)):
            return None
        if (playing!=value(handle,entity,"<AkPlayingId>k__BackingField","I")
                or marker_ptr!=pointer(handle,entity,"<SoundFileName>k__BackingField")):
            return None
        records.append(dict(entity=entity,playing_id=playing,handle=sound_handle,status=status,
                            file=marker,marker_ptr=marker_ptr or 0,event_id=struct.unpack("<I",event_raw)[0],
                            duration=duration,started=started,paused=bool(paused)))
    if (head!=memory.read_mem(handle,collection,32)
            or (count and slots!=memory.read_mem(handle,array+32,count*8))):
        return None
    return records


class PlaybackTracker:
    def __init__(self):
        self.previous={}
        self.pending={}
        self.shown=set()
        self.active=None
        self.seeded=False

    def update(self, pid, records, catalog):
        if records is None:
            return []
        output=[];active_tokens=set()
        for row in sorted(records,key=lambda record:record["started"]):
            entity=row["entity"]
            token=(pid,row["playing_id"],row["handle"])
            previous=self.previous.get(entity)
            changed=previous is not None and row["marker_ptr"] and row["marker_ptr"]!=previous["marker_ptr"]
            if previous is not None and (row["playing_id"],row["handle"])!=(previous["playing_id"],previous["handle"]):
                self.pending.pop(entity,None)
            if changed or (self.seeded and previous is None and row["marker_ptr"]):
                self.pending[entity]=(token,row["marker_ptr"])
            self.previous[entity]=dict(row)
            # Posted means Wwise accepted a nonzero playing ID. Some banks do
            # not send AK_Duration, so the state stays Posted while audible.
            actual=(row["status"] in (4,5) and row["playing_id"]>0 and row["handle"]>=0 and not row["paused"])
            if not actual:
                continue
            active_tokens.add(token)
            if not self.seeded or token in self.shown or row["event_id"] not in catalog.allowed:
                continue
            fresh=self.pending.get(entity)==(token,row["marker_ptr"])
            # The marker pointer changes on a new callback, even for identical
            # repeated lines. A cached marker from a pooled entity is rejected.
            if not fresh and row["event_id"] not in catalog.by_event:
                continue
            self.shown.add(token)
            voice=dict(row,actual=True,token=token,duration=max(.3,min(row["duration"] if row["status"]==5 and row["duration"]>0 else 5,40)))
            output.append(voice)
            self.active=token
        if self.active and self.active not in active_tokens:
            output.append(None);self.active=None
        self.seeded=True
        if len(self.shown)>4096:
            self.shown=set(active_tokens)
        return output


class VoiceWorker(threading.Thread):
    def __init__(self, output, catalog):
        super().__init__(daemon=True,name="card-voice-readonly")
        self.output,self.catalog=output,catalog
        self.cancel=threading.Event()

    def stop(self):
        self.cancel.set()

    def put(self,item):
        try:
            self.output.put_nowait(item)
        except queue.Full:
            try:self.output.get_nowait()
            except queue.Empty:pass
            self.output.put_nowait(item)

    def run(self):
        while not self.cancel.is_set():
            handle=None
            try:
                if not self.catalog.ready:
                    self.cancel.wait(1);continue
                pid,_=processes.find_game_pid()
                if not pid:
                    report_status("waiting_game")
                    self.cancel.wait(1);continue
                handle=memory.open_process(pid)
                if not handle:
                    self.cancel.wait(2);continue
                report_status("locating", pid=pid)
                manager=cached_manager(handle,pid) or locate_audio_manager(handle,self.cancel)
                if not manager:
                    report_status("manager_not_found", pid=pid)
                    self.cancel.wait(2);continue
                save_manager(handle,pid,manager)
                report_status("ready", pid=pid, manager=hex(manager))
                tracker=PlaybackTracker()
                failures=0
                while not self.cancel.is_set() and self.catalog.ready:
                    records=read_voice_pool(handle,manager)
                    failures=failures+1 if records is None else 0
                    if failures>40:
                        break
                    for item in tracker.update(pid,records,self.catalog):
                        if item:
                            report_status("playing", pid=pid, event_id=item["event_id"],
                                          matched=self.catalog.match(item) is not None)
                        self.put(item)
                    self.cancel.wait(.05)
            except InterruptedError:
                break
            except Exception as exc:
                # Keep the worker alive and expose failures instead of silently
                # terminating the only subtitle observer.
                report_status("error", error=repr(exc))
                self.cancel.wait(1)
            finally:
                self.put(None)
                if handle:memory.kernel32.CloseHandle(handle)
