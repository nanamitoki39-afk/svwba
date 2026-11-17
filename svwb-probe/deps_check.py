#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查运行依赖是否可用（给 setup_env.ps1 用）。

用法：
    python deps_check.py --spec PIL numpy pygame
        只查 find_spec（快，不执行模块初始化）
    python deps_check.py PIL numpy pygame
        真正 import 一遍（默认；能发现 DLL 缺失等坏安装）

退出码：0 = 全部可用；1 = 有缺失（缺失模块名以空格分隔输出到 stdout）。
"""
import contextlib
import importlib
import importlib.util
import io
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_pair import SUPPORTED

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# 不传模块名时的默认集合，与 setup_env.ps1 的 $Deps 保持一致。
DEFAULT_MODULES = [
    "PIL", "numpy", "frida", "requests", "pygame", "OpenGL",
    "live2d", "edge_tts", "keyboard",
]


def check_local_reader():
    """在测试进程里确认本地 attach、脚本运行和卸载都成功。"""
    import frida
    import subprocess
    if frida.__version__ not in SUPPORTED:
        raise RuntimeError("unexpected Frida version")
    child = subprocess.Popen([sys._base_executable, "-I", "-S", "-c", "import time; time.sleep(15)"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        session = frida.get_local_device().attach(child.pid)
        try:
            script = session.create_script("rpc.exports = { pid() { return Process.id; } };")
            script.load()
            if script.exports_sync.pid() != child.pid:
                raise RuntimeError("Frida test PID mismatch")
            script.unload()
        finally:
            session.detach()
    finally:
        child.terminate()
        child.wait(timeout=5)


def local_reader_ok():
    import subprocess
    try:
        result = subprocess.run([sys.executable, "-I", __file__, "--frida-local"], timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv):
    spec_only = "--spec" in argv
    startup = "--startup" in argv
    strict = not spec_only
    names = [a for a in argv if not a.startswith("--")] or DEFAULT_MODULES
    missing = []
    for name in names:
        try:
            if spec_only and name == "frida" and not startup:
                if not local_reader_ok():
                    missing.append(name)
                continue
            if strict or name in ("live2d", "frida"):
                # live2d-py / pygame 导入时会往 stdout 打 banner，
                # 这里必须吞掉，否则 setup_env 会把 banner 当成模块名。
                with contextlib.redirect_stdout(io.StringIO()):
                    mod = importlib.import_module(name)
                # 0.4.x 的顶层 live2d 是空包（只有 live2d.v2/v3），
                # pet_view.py 需要 1.0.0 才有的 live2d.Model。
                if name == "frida":
                    if mod.__version__ not in SUPPORTED or (not startup and mod.get_local_device().type != "local"):
                        missing.append(name)
                if name == "live2d" and not hasattr(mod, "Model"):
                    missing.append(name)
            elif importlib.util.find_spec(name) is None:
                missing.append(name)
        except Exception:
            missing.append(name)
    if missing:
        sys.stdout.write("MISSING:" + " ".join(missing) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--frida-local"]:
        check_local_reader()
    elif sys.argv[1:] == ["--frida-test"]:
        sys.exit(0 if local_reader_ok() else 1)
    else:
        sys.exit(main(sys.argv[1:]))
