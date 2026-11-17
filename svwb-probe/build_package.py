#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包脚本 —— 生成 v1.1.4 发行包。

用法：
    python build_package.py [输出目录]
    默认输出到 D:\\download

产物：
    <输出目录>\\影之幻境助手-v1.1.4\\        解压目录
    <输出目录>\\影之幻境助手-v1.1.4.zip      压缩包

**不包含**：训练图片、训练数字样本、调试脚本、研究脚本。
"""
import os
import ast
import tempfile
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
GAME = os.path.join(PKG, "dsh-shadowverse-huanjing")

VERSION = "v1.1.4"
NAME = f"影之诗助手-{VERSION}"
EN_NAME = f"svwba-{VERSION}"      # 英文名（导出/上传用）

# svwb-probe 里运行必需的脚本
PROBE_FILES = [
    "一键出狱.py",             # 一键出狱（搜索并删除影之诗）
    "app.py",                      # 控制台（启动 + 两套独立设置）
    "panel_ui.py",                 # 战绩面板
    "leader_panel.py",             # 主战者效果面板
    "live_mode.py",                # 直播信息窗（可选）
    "live_capture.py",             # 区域截图（可选）
    "watch_mem.py",                # 开战判定主循环
    "read_world.py",               # 读双方玩家与随从
    "shadow_warchess_reader.py",   # 进程/内存底层
    "grab.py",                     # 抓屏
    "phase.py",                    # 阶段判别
    "sim_from_memory.py",          # 内存 → 模拟器
    "history.py",                  # 战绩记录
    "game_win.py",                 # 游戏窗口识别 + Z 序（从属窗口）
    "single_instance.py",          # 单实例守卫（避免重复面板）
    "read_rank.py",                # 读名次
    "cleanup.py",                  # 截图自动清理
    "setup_env.ps1",               # 首次启动：环境检查 / 自动安装（Python/Node/依赖）
    "deps_check.py",               # 依赖检查（setup_env 调用：一次性检查缺哪个包）
    "ui_icon.py",                  # 窗口/托盘图标（assets/icon.png|ico）
    "ui_dpi.py",                   # DPI 感知（高清渲染）
    "collect_diag.py",             # 一键收集诊断信息（给测试者反馈 bug 用）
    "international_reader.py",     # Steam 国际服读取器（BattleView 展示层定位）
    "readonly_reader.py",          # 只读内存底层（国际服 / 记牌器共用）
    "reader_supervisor.py",        # 读取器监管（国服 frida / 国际服自动切换）
    "opponent_public.py",          # 对手公开信息（国际服快照扩展）
    "frida_pair.py",               # Frida 配对说明（setup_env 调用）
    "meta_client.py",              # 阵容统计：服务器连接门控（未连接不收集/不显示）
    "meta_page.py",                # 阵容统计页
    "deck_reader.py",              # 影之诗：记牌器读取层（只读内存）
    "deck_tracker.py",             # 影之诗：记牌器窗口
    "deckcode.py",                 # 卡组码解码（导入卡组）
    "frida_live.py",               # 实时读取（frida 常驻）
    "companion.py",                # AI 陪玩（LLM/TTS）
    "companion_knowledge.json",    # AI 陪玩的影之诗黑话/梗知识库
    "ai_settings.py",              # AI 设置窗口
    "upgrade_helper.py",           # 启动时检测旧版本：迁移数据 + 清理旧目录
    "版本.txt",                    # 版本号（升级检测用）
    "pet_view.py",                 # 鲸鱼娘宠物窗口（Live2D）
    "run_companion.py",            # 宠物进程入口
    "parent_guard.py",             # 父进程守卫（控制台退出宠物也退）
    "singleton.py",                # 单实例锁
    "sim_plays.py",                # 模拟出牌（判定器依赖）
]

LIB_FILES = ["index.js", "battle.mjs", "knowledge.mjs", "mechanics.mjs",
             "rng.mjs", "rules.mjs", "simulate.mjs", "skills.mjs",
             "heroskills.mjs", "winrate.mjs", "recognizer.mjs", "validate.mjs"]

KNOWLEDGE_FILES = ["cards.json", "heroes.json", "keywords.json",
                   "meta.json", "patches.json", "professions.json"]

TOOL_FILES = ["overlay.py", "overlay_feed.py", "pet_detect.py", "damage_dist.py"]

# tools/extracted/ 下运行时需要的脚本（模拟 worker 是 damage_dist 的依赖）
EXTRACTED_FILES = ["sim_worker.mjs"]

# 文档：使用说明 = .txt（用户要求双击就能看）；更新记录 = .md（用户要求拆出来）。
# README、直播采集说明、主战者效果面板说明 都已经并进使用说明（2026-10-18）。
# 资源文件（图标）：原样拷到 svwb-probe/assets/
ASSET_FILES = ["icon.png", "icon.ico"]

DOC_FILES = {"_使用说明_v010.txt": "使用说明.txt",
             "_收集诊断信息_v010.bat": "收集诊断信息.bat",
             "_修复环境_v010.bat": "修复环境.bat",
             "_启动_v010.bat": "启动.bat"}


def runtime_files():
    """Include local runtime imports, including imports inside UI callbacks."""
    files = set(PROBE_FILES) | {"launch.ps1", "launch.vbs", "startup_bootstrap.py", "subtitle_bitmap.py", "subtitle_overlay.py"}
    pending = [name for name in files if name.endswith(".py")]
    while pending:
        name = pending.pop()
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as source:
            tree = ast.parse(source.read(), filename=path)
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in names:
                dependency = module.split(".")[0] + ".py"
                if dependency not in files and os.path.isfile(os.path.join(HERE, dependency)):
                    files.add(dependency)
                    pending.append(dependency)
    return sorted(files)


def build(outdir):
    target = os.path.join(outdir, NAME)
    if os.path.realpath(target) == os.path.realpath(PKG):
        raise ValueError("不能把当前安装目录作为打包输出目录，请选择其他空目录")
    if os.path.exists(target):
        raise FileExistsError("输出目录已存在，为保护配置不覆盖：" + target)
    dirs = [
        os.path.join(target, "svwb-probe", "assets"),
        os.path.join(target, "svwb-probe", "logs"),
        os.path.join(target, "svwb-probe", "hero-portraits"),
        os.path.join(target, "dsh-shadowverse-huanjing", "lib", "knowledge"),
        os.path.join(target, "dsh-shadowverse-huanjing", "lib", "screenshot",
                     "pet-template"),
        os.path.join(target, "dsh-shadowverse-huanjing", "tools"),
        os.path.join(target, "dsh-shadowverse-huanjing", "tools", "extracted"),
        os.path.join(target, "dsh-shadowverse-huanjing", "shots", "live"),
    ]
    for p in dirs:
        os.makedirs(p, exist_ok=True)

    missing = []

    def copy(src, dst):
        if not os.path.isfile(src):
            missing.append(src)
            return
        shutil.copy2(src, dst)

    for f in runtime_files():
        copy(os.path.join(HERE, f), os.path.join(target, "svwb-probe", f))
    for f in ASSET_FILES:                      # 图标资源
        copy(os.path.join(HERE, "assets", f),
             os.path.join(target, "svwb-probe", "assets", f))
    # 表情包（assets/emotes/*.png）
    emo = os.path.join(HERE, "assets", "emotes")
    if os.path.isdir(emo):
        dst_emo = os.path.join(target, "svwb-probe", "assets", "emotes")
        os.makedirs(dst_emo, exist_ok=True)
        for fn in os.listdir(emo):
            copy(os.path.join(emo, fn), os.path.join(dst_emo, fn))
    # 宠物模型 / 表情 / 卡表（大资源，不在白名单里，必须整目录复制）
    for _d in ("_l2d", "gamedata"):
        _src = os.path.join(HERE, _d)
        _dst = os.path.join(target, "svwb-probe", _d)
        if os.path.isdir(_src):
            shutil.copytree(_src, _dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("card-art", "__pycache__", "imported_fonts.json"))

    for f in LIB_FILES:
        copy(os.path.join(GAME, "lib", f),
             os.path.join(target, "dsh-shadowverse-huanjing", "lib", f))
    for f in KNOWLEDGE_FILES:
        copy(os.path.join(GAME, "lib", "knowledge", f),
             os.path.join(target, "dsh-shadowverse-huanjing", "lib",
                          "knowledge", f))
    copy(os.path.join(GAME, "lib", "screenshot", "pet-template", "pet-live.png"),
         os.path.join(target, "dsh-shadowverse-huanjing", "lib", "screenshot",
                      "pet-template", "pet-live.png"))
    for f in TOOL_FILES:
        copy(os.path.join(GAME, "tools", f),
             os.path.join(target, "dsh-shadowverse-huanjing", "tools", f))
    for f in EXTRACTED_FILES:
        copy(os.path.join(GAME, "tools", "extracted", f),
             os.path.join(target, "dsh-shadowverse-huanjing", "tools", "extracted", f))
    for src, dst in DOC_FILES.items():
        current = os.path.join(PKG, dst)
        copy(current if os.path.isfile(current) else os.path.join(HERE, src), os.path.join(target, dst))
    copy(os.path.join(PKG, "一键出狱.exe"), os.path.join(target, "一键出狱.exe"))
    bundled_node = os.path.join(PKG, "node")
    if os.path.isdir(bundled_node):
        shutil.copytree(bundled_node, os.path.join(target, "node"))

    # 运行时会写文件的目录占位
    for d in ("svwb-probe/logs", "svwb-probe/hero-portraits",
              "dsh-shadowverse-huanjing/shots/live"):
        open(os.path.join(target, d, ".keep"), "w").close()

    # 打包必须是**原始设置**：清掉用户数据（API/卡组/战绩/宠物配置/日志）
    for rel in ("HANDOFF.md", "更新记录.md",
                "svwb-probe/gamedata/user_decks.json",
                "svwb-probe/gamedata/user_deck.json",
                "svwb-probe/pet_config.json",
                "svwb-probe/companion.json",
                "svwb-probe/logs/battle_result.json"):
        p = os.path.join(target, rel)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    logs_dir = os.path.join(target, "svwb-probe", "logs")
    if os.path.isdir(logs_dir):
        for fn in os.listdir(logs_dir):
            if fn == ".keep":
                continue
            fp = os.path.join(logs_dir, fn)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass

    for name in ("python-path.txt", "node-path.txt", ".env-ready.json", ".startup-ready.json"):
        hint = os.path.join(target, "svwb-probe", name)
        if os.path.isfile(hint):
            os.remove(hint)
    return target, missing


def write_python_path(target):
    """
    不再把**构建机**的 Python 路径塞进包里。

    以前这里是 `fh.write(sys.executable)`：在开发机上没问题，但打进包里就是
    一条 `C:\\Users\\<开发机>\\.dsh\\...\\python.exe` —— 换台机器根本不存在，
    只是噪音（启动.bat 会先 `if exist` 再试，所以不会崩，但也没用）。

    现在改由 `svwb-probe\\setup_env.ps1` 在**第一次启动**时检测并写本机路径。
    这里只负责删掉可能残留的旧文件，保证发出去的包是干净的。
    """
    p = os.path.join(target, "svwb-probe", "python-path.txt")
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass
    return None


def make_zip(target, outdir):
    zpath = os.path.join(outdir, NAME + ".zip")
    fd, temporary = tempfile.mkstemp(prefix=".assistant-package-", suffix=".zip", dir=outdir)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for dirpath, _dirs, files in os.walk(target):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    arc = os.path.join(NAME, os.path.relpath(fp, target))
                    z.write(fp, arc)
        with zipfile.ZipFile(temporary) as check:
            if check.testzip() is not None:
                raise OSError("压缩包校验失败")
        os.replace(temporary, zpath)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return zpath


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else r"D:\download"
    os.makedirs(outdir, exist_ok=True)
    target, missing = build(outdir)
    write_python_path(target)
    zpath = make_zip(target, outdir)

    n_files = sum(len(f) for _d, _s, f in os.walk(target))
    total = sum(os.path.getsize(os.path.join(dp, fn))
                for dp, _d, fs in os.walk(target) for fn in fs)
    imgs = [fn for dp, _d, fs in os.walk(target) for fn in fs
            if fn.lower().endswith((".png", ".jpg", ".webp"))]

    print(f"输出目录: {target}")
    print(f"压缩包  : {zpath}")
    print(f"文件数  : {n_files}")
    print(f"解压后  : {total/1048576:.2f} MB")
    print(f"压缩包  : {os.path.getsize(zpath)/1024:.0f} KB")
    print(f"图片数  : {len(imgs)}  {'（运行时模板）' if len(imgs) <= 1 else ''}")
    if missing:
        print("\n[警告] 以下文件缺失，未打进包：")
        for m in missing:
            print("  -", m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
