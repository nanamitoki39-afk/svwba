"""Frida 配对检查。由修复环境入口使用启动器选中的 Python 调用。"""
import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "Frida配对说明.txt"
WHEELS = {
    "17.26.2": "60a77e5ff364cee68ee627fe1833bf872e28cb8bff966b62208ca7828fdaa34f",
    "17.5.1": "7efdeb7e95c8e025517e203c2d1121c9eb07c9a3f33a1312ed5960c757e2fe98",
}
SUPPORTED = tuple(WHEELS)


def write_report(lines):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", dir=ROOT,
                                     suffix=".tmp", delete=False) as f:
        f.write("\n".join(lines) + "\n")
    os.replace(f.name, REPORT)


def wheel_name(version):
    return f"frida-{version}-cp37-abi3-win_amd64.whl"


def installed_version():
    try:
        return metadata.version("frida")
    except metadata.PackageNotFoundError:
        return "未安装"


def usable():
    return subprocess.run([sys.executable, "-I", str(ROOT / "svwb-probe" / "deps_check.py"),
                           "--frida-test"], check=False).returncode == 0


def main():
    platform_tag = sysconfig.get_platform()
    current = installed_version()
    lines = ["影之诗助手 v1.1.4 · Frida 配对说明",
             f"Windows：{platform.platform()}",
             f"操作系统架构：{os.environ.get('PROCESSOR_ARCHITEW6432') or os.environ.get('PROCESSOR_ARCHITECTURE', '未知')}",
             f"助手使用的 Python：{sys.executable}",
             f"Python 版本：{platform.python_version()}，{struct.calcsize('P') * 8} 位，{platform_tag}",
             f"检测到的 Frida：{current}", ""]
    if (sys.implementation.name != "cpython" or sys.version_info < (3, 11)
            or platform_tag != "win-amd64" or sysconfig.get_config_var("Py_GIL_DISABLED")):
        write_report(lines + ["状态：当前解释器不适用。此助手面向 Windows x64 游戏客户端。",
                     "在支持 x64 的 Windows 上安装常规 CPython 3.12 Windows installer (64-bit)：",
                     "https://www.python.org/downloads/windows/",
                     "把 python.exe 完整路径写到 svwb-probe\\python-path.txt，再双击「修复环境.bat」。",
                     "如果当前目录有随包 python，它具有优先级。32 位 Windows 无法运行本项目的 x64 配置。"])
        return 5
    if current in SUPPORTED and usable():
        write_report(lines + [f"状态：已配对 Frida {current}。本地测试进程的连接和脚本执行均通过。",
                              "下一步：双击「启动.bat」。无需再次下载。"])
        return 0

    preferred = "17.5.1" if current == "17.26.2" else "17.26.2"
    detail = ""
    for version in (preferred, next(v for v in SUPPORTED if v != preferred)):
        wheel = ROOT / wheel_name(version)
        if not wheel.is_file():
            continue
        with wheel.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != WHEELS[version]:
            detail = f"安装包校验失败：{wheel.name}。请从下方官方页面重新下载。"
            break
        result = subprocess.run([sys.executable, "-m", "pip", "--isolated", "install",
                                 "--no-index", "--no-deps", "--force-reinstall", str(wheel)])
        if result.returncode == 0 and usable():
            write_report(lines + [f"状态：已配对 Frida {version}（官方文件校验、本地进程自检均通过）。",
                                  "下一步：双击「启动.bat」。"])
            return 0
        detail = f"已尝试 {wheel.name}；安装或本地进程自检失败，请查看修复窗口中的错误。"
        preferred = next(v for v in SUPPORTED if v != version)
        break
    cmd = f'"{sys.executable}" -m pip install --index-url https://pypi.org/simple "frida=={preferred}"'
    write_report(lines + ["状态：尚未配对。", detail, f"建议版本：Frida {preferred}（Windows x64）。",
                 "方式一：在命令提示符（cmd）中执行下面这一整行，然后再次双击「修复环境.bat」。", cmd,
                 "方式二：打开下面的官方文件页，下载指定文件，保持原文件名。",
                 f"https://pypi.org/project/frida/{preferred}/#files", wheel_name(preferred),
                 f"将 .whl 文件放到「修复环境.bat」旁边：{ROOT}",
                 "再次双击「修复环境.bat」，助手会校验文件、安装到上面列出的 Python，并测试配对。",
                 "主版本 17.26.2；备选 17.5.1。本地自检通过后，实际游戏读取仍取决于客户端版本和进程权限。"])
    return 5


if __name__ == "__main__":
    sys.exit(main())
