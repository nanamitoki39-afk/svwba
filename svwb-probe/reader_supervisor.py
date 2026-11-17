"""Reader-only recovery. Never terminates a game or an unverified process."""
from pathlib import Path
import ctypes
from ctypes import wintypes as W
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

SCRIPTS = {"frida_live.py", "international_reader.py"}


def creation_time(pid):
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
    k.OpenProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = (W.HANDLE,)
    k.GetProcessTimes.argtypes = (W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME))
    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        times = [W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k.CloseHandle(h)


def register_reader(game_pid):
    """Called only after the shared reader mutex has been acquired."""
    here = Path(__file__).resolve().parent
    folder = here / "logs"
    folder.mkdir(exist_ok=True)
    script = Path(sys.argv[0]).resolve()
    # Library entry from frida_live.py remains that entry even on international.
    if script.name not in SCRIPTS or script.parent != here:
        return
    data = dict(pid=os.getpid(), created=creation_time(os.getpid()),
                script=str(script), game_pid=int(game_pid), at=time.time())
    target = folder / "reader-owner.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data), encoding="utf8")
    os.replace(temp, target)
    (folder / "frida_live.lock").write_text(str(os.getpid()), encoding="ascii")


class AdoptedReader:
    def __init__(self, pid, created):
        self.pid, self.created = pid, created

    def poll(self):
        return None if creation_time(self.pid) == self.created else 0

    def wait(self, timeout):
        end = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= end:
                raise subprocess.TimeoutExpired("reader", timeout)
            time.sleep(.1)
        return 0

    def terminate(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
        k.OpenProcess.restype = W.HANDLE
        k.CloseHandle.argtypes = (W.HANDLE,)
        k.TerminateProcess.argtypes = (W.HANDLE, W.UINT)
        h = k.OpenProcess(0x1001, False, self.pid)
        if not h:
            return
        try:
            # Revalidate the identity before using the termination handle.
            if creation_time(self.pid) == self.created:
                if not k.TerminateProcess(h, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k.CloseHandle(h)


def adopt_reader(here):
    """Verify creation time AND exact Python entry path, including legacy PID files."""
    here = Path(here).resolve()
    try:
        data = json.loads((here / "logs/reader-owner.json").read_bytes())
        pid, expected = int(data["pid"]), data.get("created")
    except (OSError, ValueError, KeyError, TypeError):
        try:
            pid = int((here / "logs/frida_live.lock").read_text().strip())
            expected = None
        except (OSError, ValueError):
            return None
    created = creation_time(pid)
    if not created or (expected is not None and created != expected):
        return None
    command = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
               "Get-CimInstance Win32_Process -Filter 'ProcessId=%d' | "
               "Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress") % pid
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                capture_output=True, timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        raw = result.stdout.decode("utf-8-sig").strip()
        # PowerShell's native redirected stdout can use the system code page.
        try:
            line = json.loads(raw)
        except ValueError:
            line = json.loads(result.stdout.decode("mbcs").strip())
        if not isinstance(line, str):
            return None
        argc = ctypes.c_int()
        shell = ctypes.WinDLL("shell32")
        shell.CommandLineToArgvW.argtypes = (W.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        shell.CommandLineToArgvW.restype = ctypes.POINTER(W.LPWSTR)
        args = shell.CommandLineToArgvW(line, ctypes.byref(argc))
        try:
            values = [args[i] for i in range(argc.value)]
        finally:
            kernel = ctypes.WinDLL("kernel32")
            kernel.LocalFree.argtypes = (ctypes.c_void_p,)
            kernel.LocalFree(args)
        # Require the actual script argument, not a string in '-c' code.
        tail = values[1:]
        while tail and tail[0] in ("-u", "-B", "-I", "-E", "-s"):
            tail.pop(0)
        if not tail or tail[0].startswith("-"):
            return None
        entry = Path(tail[0]).resolve()
        if entry.parent != here or entry.name not in SCRIPTS:
            return None
        if creation_time(pid) != created:
            return None
        return AdoptedReader(pid, created)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


class ReaderSupervisor:
    def __init__(self, here, procs, report=print):
        self.here, self.procs, self.report = Path(here), procs, report
        self.gate = threading.Lock()
        self.started = {}
        self.game_pid = None
        self.last_message = None

    def message(self, text):
        if text != self.last_message:
            self.report(text)
            self.last_message = text

    def ensure(self):
        if not self.gate.acquire(False):
            return
        def work():
            try:
                self.check()
            except Exception as exc:
                self.message("记牌器：读取器检查失败：%s" % exc)
            finally:
                self.gate.release()
        try:
            threading.Thread(target=work, name="reader-supervisor", daemon=True).start()
        except (RuntimeError, OSError) as exc:
            # Thread creation can fail under resource pressure. Do not leave
            # the gate permanently held, otherwise every future check skips.
            self.gate.release()
            self.message("记牌器：检查线程启动失败，下次检查将重试：%s" % exc)

    def check(self):
        import singleton
        import shadow_warchess_reader as game
        logs = self.here / "logs"
        logs.mkdir(exist_ok=True)
        pid, image = game.find_game_pid(self.game_pid)
        cur = self.procs.get("frida")
        running = cur is not None and cur.poll() is None
        locked = singleton.frida_running()
        try:
            fresh = time.time() - (logs / "deck_live.json").stat().st_mtime < 8
        except OSError:
            fresh = False
        switching = bool(pid and self.game_pid and pid != self.game_pid)
        if running or locked:
            if fresh and not switching:
                return
            owner = cur if running else adopt_reader(self.here)
            # Snapshot silence during attach/script loading is not a deadlock.
            if owner is not None:
                seen = self.started.setdefault(owner.pid, time.monotonic())
                if not switching and time.monotonic() - seen < 90:
                    return
            stop = logs / "_frida.stop"
            stop.write_text("1", encoding="ascii")
            self.message("记牌器：读取器长时间未更新，正在停止并恢复")
            if owner is None:
                # Never kill an unknown PID or remove a stop request too early.
                self.message("记牌器：已有读取器占用锁，已请求停止；无法核实所属进程，暂不启动第二个读取器")
                return
            try:
                owner.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.message("记牌器：旧读取器未响应停止，正在结束已核实的读取器进程")
                owner.terminate()
                owner.wait(timeout=3)
            if singleton.frida_running():
                self.message("记牌器：旧读取器锁尚未释放，等待后再重试")
                return
            self.procs.pop("frida", None)
            stop.unlink(missing_ok=True)
        elif cur is not None:
            self.procs.pop("frida", None)
        if not pid:
            self.message("记牌器：未找到已加载 GameAssembly.dll 的影之诗 PC 客户端；MuMu 模拟器宿主不能直接读取")
            return
        international = Path(image).name.lower() in ("shadowversewb.exe", "shadowverseworldsbeyond.exe")
        if not international and importlib.util.find_spec("frida") is None:
            self.message("记牌器：缺少 Frida，请运行修复环境.bat")
            return
        # Recheck just before spawning, after the potentially slow ownership check.
        if singleton.frida_running():
            return
        (logs / "_frida.stop").unlink(missing_ok=True)
        script = "international_reader.py" if international else "frida_live.py"
        arguments = ["--pid", str(pid)] if international else [str(pid), "7200"]
        with (logs / "frida_live.log").open("a", encoding="utf8") as output:
            child = subprocess.Popen([sys.executable, "-u", str(self.here / script)] + arguments,
                                     stdout=output, stderr=subprocess.STDOUT, cwd=str(self.here),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.procs["frida"] = child
        self.started[child.pid], self.game_pid = time.monotonic(), pid
        self.message("记牌器：选择连接目标 %s（pid %s，%s）" %
                     ("Steam 国际服" if international else "国服高清 PC 客户端", pid, image))
