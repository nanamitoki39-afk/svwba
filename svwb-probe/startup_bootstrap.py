"""Fast local launch after a verified environment; no network or heavy imports."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
CACHE = HERE / '.startup-ready.json'
TTL = 24 * 3600
MODULES = ('PIL', 'numpy', 'frida', 'requests', 'pygame', 'OpenGL', 'live2d', 'edge_tts', 'keyboard')


def stamp(path):
    path = Path(path).resolve()
    st = path.stat()
    return [str(path), st.st_size, st.st_mtime_ns]


def runtime_key():
    # python.exe and pythonw.exe in the same installation share this key.
    return [str(HERE), sys.version, str(Path(sys.prefix).resolve()),
            os.environ.get('PYTHONPATH', ''), os.environ.get('PYTHONHOME', '')]


def record():
    files = [HERE / n for n in ('setup_env.ps1', 'deps_check.py', 'startup_bootstrap.py',
                                'python-path.txt', 'node-path.txt')]
    files += [Path(sys.executable).parent / 'python.exe', Path(sys.executable)]
    files.append(Path((HERE / 'node-path.txt').read_text(encoding='utf-8-sig').strip()))
    # Detect module removal/reinstall without initializing Frida, Live2D or NumPy.
    for name in MODULES:
        spec = importlib.util.find_spec(name)
        if spec is None:
            continue  # The installer reports optional missing components.
        if spec.origin and spec.origin not in ('built-in', 'frozen'):
            files.append(Path(spec.origin))
        for path in spec.submodule_search_locations or ():
            files.append(Path(path))
    files.extend(Path(p) for p in sys.path if p and Path(p).is_dir() and 'site-packages' in p)
    data = dict(schema=1, at=time.time(), key=runtime_key(), files=[stamp(p) for p in files])
    tmp = CACHE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    tmp.replace(CACHE)


def valid():
    try:
        data = json.loads(CACHE.read_text(encoding='utf8'))
        age = time.time() - data['at']
        return (data['schema'] == 1 and 0 <= age < TTL and data['key'] == runtime_key()
                and bool(data['files']) and all(stamp(p) == [p, size, modified]
                    for p, size, modified in data['files']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_again():
    # Validation changed: use the normal visible repair/error path once.
    ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.Popen([str(ps), '-NoLogo', '-NoProfile', '-NonInteractive',
                      '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                      '-File', str(HERE / 'launch.ps1')], cwd=HERE,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main(verified=False):
    started = time.perf_counter()
    if not verified and not valid():
        prepare_again()
        return 0
    logs = HERE / 'logs'
    logs.mkdir(exist_ok=True)
    previous_streams = sys.stdout, sys.stderr
    with (logs / 'console-app.log').open('a', encoding='utf8', buffering=1) as output:
        sys.stdout = sys.stderr = output
        try:
            if verified:
                try:
                    record()
                except Exception as exc:
                    print('启动缓存未写入，下次继续检查环境：', exc)
            sys.path.insert(0, str(HERE))
            os.chdir(HERE)
            # Retain migration checks; run in this interpreter rather than spawning another.
            from upgrade_helper import upgrade
            upgrade(HERE.parent)
            import app
            app._launch_started = started
            return app.main()
        except Exception:
            CACHE.unlink(missing_ok=True)
            traceback.print_exc()
            output.flush()
            ctypes.windll.user32.MessageBoxW(None,
                '助手未能启动，请查看：\n' + str(logs / 'console-app.log'), '影之诗助手', 0x10)
            return 1
        finally:
            sys.stdout, sys.stderr = previous_streams


if __name__ == '__main__':
    raise SystemExit(main('--verified' in sys.argv))
