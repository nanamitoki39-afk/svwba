#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗/影之幻境 · AI 陪玩（2026-10-27，插件内置模块）

设计：
  · 读 logs/deck_live.json（Frida 常驻写的实时状态）；
  · 检测事件：抽牌/出牌/牌库变化/生命变化；
  · 调 OpenAI 兼容接口（用户自己填 key/base/model，DeepSeek/方舟/本地 Ollama 都行）；
  · 生成 1~2 句短评，回调给 UI 气泡；
  · 语音：Edge TTS（云端、免费、0 显存）→ 临时 mp3 → MCI 播放 → 播完删除。

别人拿到插件：不填 key 就不调用，填了就能用；不联网就只出文字。
"""
import asyncio
import ctypes
import io
import json
import os
import re
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _user_dir():
    """配置放在用户目录，不随插件包分发（避免打包时泄漏 API Key）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "影之诗助手")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


CONFIG_PATH = os.path.join(_user_dir(), "companion.json")
LEGACY_CONFIG = os.path.join(HERE, "companion_config.json")
LIVE_PATH = os.path.join(HERE, "logs", "deck_live.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "api_key": "",
    "provider": "通义千问（DashScope）",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen3.8-flash",
    "tts_enabled": True,
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+8%",
    "max_per_min": 6,
    "style": "简短、有情绪、像一起打牌的朋友，不剧透对手隐藏信息",
    "api_history": [],
    "personality": "gentle",
}

# 四种性格：重点区分句式和情绪表达；建议内容仍必须服从当前局面。
PERSONALITIES = {
    "gentle": ("温柔体贴", "语气柔和耐心，先关心主人，再像并肩商量般给建议；失误时温和提醒一步，赢了具体夸操作，绝不挖苦。"),
    "lively": ("元气活力", "热情明快、爱起哄，短句有节奏，适度感叹；关键抽牌或漂亮交换时带气氛，建议积极直接，避免每句都喊。"),
    "tsundere": ("傲娇嘴硬", "先嘴硬后关心，偶尔说「才、才不是担心你」；提醒时装作嫌弃但讲清理由，赢了不情愿地承认打得不错，只傲娇不贬低主人。"),
    "toxic": ("毒舌（慎用）", "吐槽尖锐简短，只针对本回合具体操作、费用或交换；吐槽后必须给可执行建议，不人身攻击、不骂脏话、不脱离场面泛泛辱骂。"),
}

# 每种性格的具体话术示例：让同样的建议用不同口吻说出来
PERSONALITY_HINTS = {
    "gentle": "说话示例：「别急着上，先把这个5费随从解掉，不然下回合要挨8点哦。」",
    "lively": "说话示例：「冲！先解掉那个大怪，节奏就回来了！」",
    "tsundere": "说话示例：「哼，才不是担心你被秒……那个5费随从先处理掉啦。」",
    "toxic": "说话示例：「放着5攻不管，你是想下周再打？解了它。」",
}

_KNOW = {"data": None}


def _knowledge():
    """影之诗黑话/梗知识库（companion_knowledge.json）。"""
    if _KNOW["data"] is None:
        try:
            with io.open(os.path.join(HERE, "companion_knowledge.json"),
                         encoding="utf-8") as fh:
                _KNOW["data"] = json.load(fh)
        except Exception:
            _KNOW["data"] = {}
    return _KNOW["data"]


def _personality_id():
    try:
        p = (load_config().get("personality") or "gentle")
    except Exception:
        p = "gentle"
    return p if p in PERSONALITIES else "gentle"


def _personality_text():
    return PERSONALITIES[_personality_id()][1]


def remember_api(cfg):
    """把当前 API 配置记进历史（AI 设置里可直接选回用过的）。"""
    try:
        cfg = normalize_config(cfg or {})
        key = (cfg.get("api_key") or "").strip()
        if not key:
            return
        item = {"provider": cfg.get("provider") or "",
                "base_url": cfg.get("base_url") or "",
                "model": cfg.get("model") or "",
                "api_key": key}
        cur = load_config()
        hist = list(cur.get("api_history") or [])
        hist = [h for h in hist if not (
            h.get("provider") == item["provider"]
            and h.get("base_url") == item["base_url"]
            and h.get("model") == item["model"])]
        hist.insert(0, item)
        cur["api_history"] = hist[:10]
        save_config(cur)
    except Exception:
        pass

# 语音备选（Edge TTS 在线音色，只保留女声）
VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女，活泼）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女，温柔）"),
]

# 模型提供商预设（OpenAI 兼容接口）：选哪个就自动填 base_url + 可选模型列表
PROVIDERS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "火山方舟": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-evolving",
        "models": [
            "glm-5-3-flash-260828",
            "glm-5-2-260617",
            "doubao-seed-evolving",
            "doubao-seed-2-1-pro-260628",
            "doubao-seed-2-1-turbo-260628",
            "doubao-seedance-1-0-pro-250528",
        ],
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "o3-mini"],
    },
    "Kimi（月之暗面）": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "通义千问（DashScope）": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen3.8-flash"],
    },
    "智谱 GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4-plus", "glm-5-3-flash"],
    },
    "OpenRouter（含 Claude/GPT）": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-3.5-sonnet",
        "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini"],
    },
    "Ollama（本地）": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:3b",
        "models": ["qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b"],
    },
}


def resolve_provider(cfg, fallback="自定义"):
    """按 Base URL / 模型名推断真实提供商。

    旧配置可能没有 provider，或保存了过时的旧值（例如实际用 DeepSeek，
    界面却仍显示「通义千问」）。这里以显式填写的 Base URL 为准，其次看模型名。
    """
    cfg = cfg or {}
    cur = str(cfg.get("provider") or "").strip()
    base = normalize_base_url(cfg.get("base_url")).lower().rstrip("/")
    model = str(cfg.get("model") or "").strip().lower()
    if base:
        for name, preset in PROVIDERS.items():
            pb = str(preset.get("base_url") or "").strip().lower().rstrip("/")
            if pb and (base == pb or base.startswith(pb + "/")):
                return name
    if model:
        for name, preset in PROVIDERS.items():
            models = [str(m).strip().lower()
                      for m in (preset.get("models") or [])]
            if model in models:
                return name
    if cur in PROVIDERS:
        return cur
    return fallback


def normalize_config(cfg):
    """修正旧配置的 provider / model，避免「用着 DS 却显示千问」。"""
    out = dict(cfg or {})
    try:
        fixed = normalize_base_url(out.get("base_url"))
        if fixed:
            out["base_url"] = fixed
    except Exception:
        pass
    old_provider = str(out.get("provider") or "").strip()
    provider = resolve_provider(out)
    out["provider"] = provider
    preset = PROVIDERS.get(provider)
    if not preset:
        return out
    model = str(out.get("model") or "").strip()
    allowed = [str(m).strip().lower() for m in (preset.get("models") or [])]
    if not model:
        out["model"] = preset.get("model") or ""
        return out
    # 只有「提供商被修正」且模型明显属于另一个提供商时才替换；
    # 显式选了 OpenRouter/Ollama 等、又用别家模型名的配置保持不动。
    if (old_provider != provider and allowed
            and model.lower() not in allowed):
        known_elsewhere = any(
            model.lower() in [str(m).strip().lower()
                              for m in (p.get("models") or [])]
            for name, p in PROVIDERS.items() if name != provider)
        if known_elsewhere:
            out["model"] = preset.get("model") or model
    return out


def load_config():
    cfg = {}
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        # 旧版配置在插件目录里：读一次并迁移到用户目录
        try:
            with io.open(LEGACY_CONFIG, encoding="utf-8") as fh:
                cfg = json.load(fh)
            if cfg:
                save_config(cfg)
        except Exception:
            cfg = {}
    base = dict(DEFAULT_CONFIG)
    base.update(cfg or {})
    base = normalize_config(base)
    try:
        fixed_base = normalize_base_url(base.get("base_url"))
        if fixed_base:
            base["base_url"] = fixed_base
    except Exception:
        pass
    # 历史 API 里的旧 provider 也一起修正，否则下拉框仍会显示「千问」。
    hist = []
    for item in base.get("api_history") or []:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item["provider"] = resolve_provider(item)
        hist.append(item)
    base["api_history"] = hist
    return base


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_PATH)


def _card_name(cid):
    import deck_reader
    name = deck_reader.card_name(cid) or deck_reader.load_names().get(str(cid))
    return name if isinstance(name, str) and name.strip() else "这张牌"


def _named_reply(text, partial=False):
    if partial:
        # SSE 可能把一个卡号切成几段，等编号完整后再显示。
        text = re.sub(r"[#＃][0-9]{0,7}$", "", text)
    return re.sub(r"[#＃]([0-9]+)", lambda m: _card_name(m.group(1)), text)


def _strip_bubble_prefix(text):
    """去掉模型加上的「（气泡）」/「[气泡]」等前缀（模型会误当成输出格式）。"""
    t = (text or "").strip()
    for pref in ("（气泡）", "[气泡]", "(气泡)", "【气泡】", "气泡：", "气泡:"):
        while t.startswith(pref):
            t = t[len(pref):].lstrip("：: 　")
    return t


def _norm(cards):
    """手牌列表 → 名字列表（去重前保留重复）。"""
    return [_card_name(c) for c in (cards or []) if c]


def _guess_class():
    """从当前卡组推断我方职业（卡表 class 众数）。"""
    try:
        import deck_reader
        decks = deck_reader._decks_from_file()
        active = deck_reader._active_name()
        item = next((x for x in decks if x.get("name") == active),
                    decks[0] if decks else None)
        if not item:
            return ""
        meta = deck_reader.load_meta()
        from collections import Counter
        c = Counter()
        for cid in item["cards"]:
            v = meta.get(str(cid)) or {}
            cls = v.get("class")
            if cls:
                c[cls] += 1
        if not c:
            return ""
        return {0: "中立", 1: "精灵", 2: "皇家护卫", 3: "巫师",
                4: "龙族", 5: "梦魇", 6: "主教", 7: "复仇者"}.get(
            c.most_common(1)[0][0], "")
    except Exception:
        return ""


def explain_http_error(status, body):
    """将常见 API HTTP 错误转成可操作的中文原因。"""
    text = str(body or "")[:700]
    low = text.lower()
    if status in (401, 403):
        reason = "API Key 无效、已过期，或没有调用该模型的权限。请核对密钥和平台账号状态。"
    elif status == 400:
        reason = "请求被拒绝：模型名、参数或 Base URL 与接口不匹配。请核对提供商预设和模型 ID。"
    elif status == 404:
        reason = ("接口地址或模型/接入点名称不存在。请核对 Base URL"
                  "（通常要带 /v1、/api/v3 等路径）和模型 ID。")
    elif status == 429:
        reason = "请求频率或账户额度已达限制。请稍后重试并检查模型额度。"
    elif status >= 500:
        reason = "模型服务端暂时故障。请稍后重试，或查看服务商状态。"
    elif "model" in low and any(x in low for x in ("not found", "does not exist", "不存在")):
        reason = "模型 ID 不存在或当前账号未开通，请核对模型/接入点名称。"
    elif "余额" in text or "billing" in low or "quota" in low:
        reason = "账户余额或模型额度不足，请检查服务商控制台。"
    else:
        reason = "服务商返回 HTTP 错误，请核对 API 配置和账户状态。"
    detail = " ".join(text.split())
    return "HTTP %s：%s%s" % (status, reason,
                              ("\n服务端信息：" + detail[:280]) if detail else "")


def normalize_base_url(url):
    """把用户填的 Base URL 规范成接口根地址。

    - 没写 http(s):// 自动补 https://
    - 去掉末尾的 /chat/completions（用户常把完整接口粘进来）
    - 去掉多余斜杠
    """
    u = str(url or "").strip().strip('"').strip("'")
    if not u:
        return ""
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    u = u.rstrip("/")
    low = u.lower()
    for suffix in ("/chat/completions", "/completions"):
        if low.endswith(suffix):
            u = u[:-len(suffix)].rstrip("/")
            break
    # 常见服务只填域名时补齐接口前缀，避免 404 / 连接不上。
    try:
        from urllib.parse import urlparse
        host = (urlparse(u).hostname or "").lower()
        path = (urlparse(u).path or "").rstrip("/")
        if not path:
            if host in ("api.deepseek.com", "api.openai.com",
                        "api.moonshot.cn", "openrouter.ai"):
                u += "/v1" if host != "openrouter.ai" else "/api/v1"
            elif host.endswith("volces.com"):
                u += "/api/v3"
            elif host.endswith("dashscope.aliyuncs.com"):
                u += "/compatible-mode/v1"
            elif host == "open.bigmodel.cn":
                u += "/api/paas/v4"
            elif host in ("127.0.0.1", "localhost"):
                u += "/v1"
    except Exception:
        pass
    return u


def _system_proxies():
    """读取环境变量 + Windows 系统代理。

    Clash / v2rayN / 加速器通常只开“系统代理”，requests 默认不读 WinINET，
    结果就是明明浏览器能开网页，陪玩却一直连接超时。
    """
    try:
        import urllib.request
        raw = urllib.request.getproxies() or {}
    except Exception:
        return {}
    out = {}
    for key in ("http", "https"):
        val = str(raw.get(key) or "").strip()
        if val:
            if "://" not in val:
                val = "http://" + val
            out[key] = val
    if "https" not in out:
        allp = str(raw.get("all") or "").strip()
        if allp:
            if "://" not in allp:
                allp = "http://" + allp
            out["https"] = allp
            out.setdefault("http", allp)
    return out


def _force_ipv4():
    """部分网络 IPv6 不可达，requests 会先试 IPv6 卡到连接超时。"""
    try:
        import urllib3.util.connection as conn
        conn.HAS_IPV6 = False
        return True
    except Exception:
        return False


def _new_http_session(requests):
    """新建会话：带系统代理、keep-alive 和流式响应头。"""
    session = requests.Session()
    try:
        proxies = _system_proxies()
        if proxies:
            # 不打印代理地址：里面可能带账号密码
            session.proxies.update(proxies)
            print("AI：已启用系统代理", flush=True)
    except Exception:
        pass
    try:
        session.headers.update({
            "User-Agent": "svwb-companion/1.0",
            "Accept": "text/event-stream",
            "Connection": "keep-alive",
        })
    except Exception:
        pass
    return session


class Companion:
    """事件检测 + LLM 短评 + Edge TTS。"""

    def __init__(self, on_text=None, on_state=None):
        self.cfg = load_config()
        self.last_error = ""
        self.on_text = on_text or (lambda s: None)
        self.on_state = on_state or (lambda s: None)
        self._last = {}
        self._hand = []
        self._deck = None
        self._life = None
        self._last_call = 0.0
        self._last_turn = None
        self._context = None
        self._generation = 0
        self._valid_until = 0.0
        self._in_battle = False      # 是否在对局中（用于对局结束检测）
        self._end_done = False       # 本局结束语是否已说过
        self._last_life = None
        self._lock = threading.Lock()
        self._busy = False
        self._tmp_audio = []
        self._cfg_mtime = 0.0
        self._deck_context_key = None
        self._deck_context = {"name": "", "cards": (), "class": ""}
        self.timing = {}
        self._http_session = None
        self._closed = False

    def close(self):
        """Stop scene collection, invalidate queued replies and close HTTP."""
        self._closed = True
        self._generation += 1
        self._context = None
        self._hand = []
        self._last = {}
        session, self._http_session = self._http_session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    # ---------------- 状态读取 ----------------
    def _live(self):
        try:
            with io.open(LIVE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _diff(self, snap, commit=True):
        """返回事件列表。"""
        ev = []
        # 新对局：addr 变了（重开/换局）→ 重置基线，否则会把「上一局结束的牌库」
        # 和「新局开局牌库」的差值报成「有 N 张牌回到了牌库」。
        addr = snap.get("addr")
        if not hasattr(self, "_last_addr") or self._last_addr != addr:
            if commit:
                self._last_addr = addr
                self._deck = snap.get("deck")
                self._life = snap.get("life")
                self._hand = _norm(snap.get("hand"))
            return ev
        hand = _norm(snap.get("hand"))
        deck = snap.get("deck")
        life = snap.get("life")
        if self._deck is not None and deck is not None:
            if deck < self._deck:
                ev.append("抽了 %d 张牌" % (self._deck - deck))
            elif deck > self._deck:
                ev.append("有 %d 张牌回到了牌库" % (deck - self._deck))
        if self._life is not None and life is not None and life < self._life:
            ev.append("生命值掉了 %d 点（现在 %d）" % (self._life - life, life))
        old = list(self._hand)
        for name in hand:
            if name in old:
                old.remove(name)
            else:
                ev.append("抽到「%s」" % name)
        for name in old:
            ev.append("「%s」离开了手牌" % name)
        if commit:
            self._hand = hand
            self._deck = deck
            self._life = life
        return ev

    # ---------------- LLM ----------------
    def _deck_context_cached(self):
        """只在卡组文件或卡表变更时重新读取并分析当前卡组。"""
        try:
            import deck_reader
            decks_path = os.path.join(HERE, "gamedata", "user_decks.json")
            legacy_path = os.path.join(HERE, "gamedata", "user_deck.json")
            def _stamp(path):
                try:
                    st = os.stat(path)
                    return (st.st_mtime_ns, st.st_size)
                except OSError:
                    return None
            key = (_stamp(decks_path), _stamp(legacy_path),
                   _stamp(deck_reader.META_PATH))
            if key == self._deck_context_key:
                return self._deck_context
            decks = deck_reader._decks_from_file()
            active = deck_reader._active_name()
            item = next((x for x in decks if x.get("name") == active),
                        decks[0] if decks else None)
            meta = deck_reader.load_meta()
            from collections import Counter
            classes = Counter()
            cards = tuple(item.get("cards") or ()) if item else ()
            for cid in cards:
                card = meta.get(str(cid)) or {}
                cls = card.get("class")
                if cls:
                    classes[cls] += 1
            cls_id = classes.most_common(1)[0][0] if classes else None
            class_name = {0: "中立", 1: "精灵", 2: "皇家护卫", 3: "巫师",
                          4: "龙族", 5: "梦魇", 6: "主教", 7: "复仇者"}.get(
                              cls_id, "")
            self._deck_context = {"name": item.get("name", "") if item else "",
                                  "cards": cards, "class": class_name}
            self._deck_context_key = key
        except Exception:
            # 读取失败时保留最近一次有效快照，不在每个回合重复扫描。
            pass
        return self._deck_context

    def _prompt(self, snap, events, hand_names):
        """只用本回合相关数据组短 prompt；卡牌元数据由 deck_reader 缓存。"""
        import deck_reader
        deck_context = self._deck_context_cached()
        meta = deck_reader.load_meta()

        def card(cid):
            return meta.get(str(cid)) or {}

        def describe(unit):
            cid = unit.get("cardId")
            m = card(cid)
            name = m.get("name") or _card_name(cid)
            # 护符（type 2/3）没有面板数据，不能显示 0/0
            if int(m.get("type") or 0) in (2, 3):
                return "%s（护符）" % name
            atk, life = int(unit.get("atk") or 0), int(unit.get("life") or 0)
            return "%s %d/%d" % (name, atk, life)

        my_field = list(snap.get("field") or [])[:5]
        enemy = snap.get("enemy") or {}
        enemy_field = list(enemy.get("field") or [])[:5]
        my_txt = "、".join(describe(x) for x in my_field) or "空"
        enemy_txt = "、".join(describe(x) for x in enemy_field) or "空"
        turn = snap.get("turn")
        # 当前读取器的费用字段尚未对照游戏验证。
        pp = snap.get("pp") if snap.get("pp_verified") else None
        maxpp = snap.get("maxpp") if snap.get("pp_verified") else None

        # 检查完整手牌，避免漏掉靠后的解场牌。
        hand_ids = [c for c in (snap.get("hand") or []) if c]
        threats = [u for u in enemy_field
                   if int(u.get("atk") or 0) >= 3 or int(u.get("life") or 0) >= 4]
        # 解场牌判定：必须明确指向**敌方**随从，且不是「破坏自己」那种
        ENEMY_REMOVE = ("敌方随从", "对手的随从", "对手场上", "对对手的战场",
                        "破坏对手", "消灭对手", "对方随从", "对手方随从",
                        "使敌方", "给予敌方随从")
        SELF_REMOVE = ("自己的战场", "自己的随从", "该随从", "我方随从",
                       "自己的卡牌", "返回手牌")
        playable_removal = []
        key_cards = []
        for cid in hand_ids:
            m = card(cid)
            cost = int(m.get("cost") or 0)
            skill = " ".join((m.get("skill") or "").split())
            name = m.get("name") or _card_name(cid)
            if skill and pp is not None and cost <= pp:
                key_cards.append("%s(%s费)%s" % (name, cost, skill[:18]))
            if pp is not None and cost <= pp:
                is_removal = any(w in skill for w in ENEMY_REMOVE) and \
                    not any(w in skill for w in SELF_REMOVE)
                if is_removal:
                    playable_removal.append(name)

        # ---- 斩杀判断：场攻 + 手牌直伤 >= 对手生命 ----
        my_attack = sum(int(u.get("atk") or 0) for u in my_field)
        try:
            enemy_life = int(enemy.get("life") or 0)
        except (TypeError, ValueError):
            enemy_life = 0
        hand_damage = 0
        burst_cards = []
        dmg_re = re.compile(r"(?:造成|给予)(\d+)点伤害")
        # 伤害目标判定：只认打主战者的，打随从的不算斩杀
        FACE_WORDS = ("主战者", "对手生命", "敌方英雄", "对方生命",
                      "对对手造成", "对敌方造成")
        MINION_WORDS = ("随从", "战场上的", "1个", "一个", "随机")
        for cid in hand_ids:
            m2 = card(cid)
            skill2 = " ".join((m2.get("skill") or "").split())
            cost2 = int(m2.get("cost") or 0)
            if pp is not None and cost2 > pp:
                continue
            mm = dmg_re.search(skill2)
            if mm:
                # 看伤害出现位置前后 40 字符，判断打的是谁
                around = skill2[max(0, mm.start() - 40): mm.end() + 40]
                if any(w in around for w in MINION_WORDS):
                    continue       # 打随从，不算脸伤
                if not any(w in around for w in FACE_WORDS):
                    continue       # 没明确说打脸，保守不算
                n = int(mm.group(1))
                hand_damage += n
                burst_cards.append("%s(%d伤)" % (
                    m2.get("name") or _card_name(cid), n))
        lethal = enemy_life > 0 and hand_damage > 0 and \
            (my_attack + hand_damage) >= enemy_life

        # ---- 我方承伤风险 ----
        enemy_attack = sum(int(u.get("atk") or 0) for u in enemy_field)
        my_life = int(snap.get("life") or 20)

        advice = []
        if lethal:
            advice.append("能斩杀了：场攻%d + 手牌伤害%d（%s）≥ 对手生命%d" % (
                my_attack, hand_damage, "、".join(burst_cards[:2]), enemy_life))
        if enemy_attack >= my_life and my_life > 0:
            advice.insert(0, "这回合不解场会死：对手场攻%d ≥ 我方生命%d" % (
                enemy_attack, my_life))
        elif enemy_attack >= 6 and my_life <= 10:
            advice.insert(0, "血量危险（%d血/对手场攻%d），优先解场或架墙" % (
                my_life, enemy_attack))
        if threats and playable_removal:
            target = max(threats, key=lambda u: int(u.get("atk") or 0))
            t_name = _card_name(target.get("cardId"))
            t_atk = int(target.get("atk") or 0)
            t_life = int(target.get("life") or 0)
            advice.append("用【%s】处理【%s】（%d/%d），不然下回合要挨%d点" % (
                playable_removal[0], t_name, t_atk, t_life, t_atk))
        elif threats and my_field:
            advice.append("对手有%d攻高威胁随从，考虑交换，别让血线崩" % max(
                int(u.get("atk") or 0) for u in threats))
        elif threats:
            advice.append("对手有威胁随从，本回合优先守脸或找解场")
        # ---- 参考欧丝 AI 建议的 7 类：Act / Attack / Evolve / Fusion /
        #      ExtraPP / TurnEnd / Mulligan ----
        focus = []   # [(优先级, 类型, 内容)]

        # 1) Attack：斩杀
        if lethal:
            focus.append((100, "斩杀", "能斩杀了：场攻%d + 手牌伤害%d（%s）≥ 对手生命%d" % (
                my_attack, hand_damage, "、".join(burst_cards[:2]), enemy_life)))
        # 2) Attack：会被打死
        if enemy_attack >= my_life and my_life > 0:
            focus.append((98, "守脸", "这回合不解场会死：对手场攻%d ≥ 我方生命%d" % (
                enemy_attack, my_life)))
        # 3) Mulligan：换牌阶段
        if turn is not None and int(turn) == 0 and not snap.get("mulliganDone"):
            focus.append((95, "换牌", "换牌阶段：优先换掉高费卡，留低费起手"))
        # 4) Attack：血线危险
        elif enemy_attack >= 6 and my_life <= 10:
            focus.append((90, "守脸", "血量危险（%d血/对手场攻%d），优先解场或架墙" % (
                my_life, enemy_attack)))
        # 5) Act：解场
        if threats and playable_removal:
            target2 = max(threats, key=lambda u: int(u.get("atk") or 0))
            focus.append((85, "解场", "用【%s】处理【%s】（%d/%d），不然要挨%d点" % (
                playable_removal[0], _card_name(target2.get("cardId")),
                int(target2.get("atk") or 0), int(target2.get("life") or 0),
                int(target2.get("atk") or 0))))

        # 6) Evolve：手牌里有「进化时」收益的牌，且够费
        evolve_cards = []
        # 7) Fusion：手牌里有融合牌
        fusion_cards = []
        for cid in hand_ids:
            m3 = card(cid)
            sk = " ".join((m3.get("skill") or "").split())
            nm = m3.get("name") or _card_name(cid)
            cost3 = int(m3.get("cost") or 0)
            if pp is not None and cost3 > pp:
                continue
            if "进化" in sk and ("进化时" in sk or "进化后" in sk):
                evolve_cards.append(nm)
            if "融合" in sk:
                fusion_cards.append(nm)
        if evolve_cards:
            focus.append((55, "进化", "【%s】进化有额外效果，可以考虑进化" % evolve_cards[0]))
        if fusion_cards:
            focus.append((50, "融合", "【%s】可以融合，先融合再出更划算" % fusion_cards[0]))

        # 8) TurnEnd：费用没用完但没牌可出 / 已经没有可行动作
        if pp is not None and hand_ids:
            playable_any = False
            for cid in hand_ids:
                m4 = card(cid)
                if int(m4.get("cost") or 0) <= pp:
                    playable_any = True
                    break
            if not playable_any and not my_field:
                focus.append((40, "结束回合", "手上没够费的牌了，可以结束回合"))

        # ExtraPP：pp 字段尚未验证，暂不生成建议（等 pp_verified 之后再开）

        focus.sort(key=lambda x: -x[0])
        if focus:
            advice = ["[%s] %s" % (t, c) for _, t, c in focus[:2]]
        else:
            advice = []

        _pkey = self.cfg.get("personality") or "gentle"
        if _pkey not in PERSONALITIES:
            _pkey = "gentle"
        personality = PERSONALITIES[_pkey][1]
        hint = PERSONALITY_HINTS.get(_pkey, "")
        lines = [
            "鲸鲸口吻：" + personality,
            hint,
            "只依据场面和手牌说一句（40 字以内）；优先给可执行的操作建议"
            "（出哪张牌、打哪个目标、为什么），没有具体建议时才评论刚发生的事。",
            "我方%s 费用%s/%s 生命%s；场上：%s。" % (
                deck_context.get("class") or "", pp if pp is not None else "未知", maxpp if maxpp is not None else "未知", snap.get("life"), my_txt),
            "对手场上：%s；对手生命%s。" % (enemy_txt, enemy.get("life", "?")),
            "手牌：%s；牌库%s；回合%s。" % (
                "、".join(hand_names) if hand_names else "空",
                snap.get("deck"), turn),
        ]
        if advice:
            lines.append("场面判断：" + "；".join(advice[:1]))
        elif key_cards:
            lines.append("可用手牌：" + key_cards[0])
        if events:
            lines.append("刚发生：" + "；".join(events[:1]))
        lines.append("卡牌效果只按括号里的描述说，别自己发明用途"
                     "（描述写回血就是回血，不能说过牌/斩杀）；护符没有攻击/生命面板，"
                     "别给它编攻血；限 40 字，不编造事实；牌库数字是剩余张数，**不是洗回张数**；不给与当前局面无关的泛化建议。")
        return "\n".join(lines)

    def _call_llm(self, prompt, timeout=(6, 20), max_tokens=48,
                  on_chunk=None, stream=True):
        """调用 OpenAI 兼容接口。

        · 连接失败（DNS / IPv6 / 代理）时强制 IPv4 重试一次；
        · 陪玩用流式，测试 AI 用非流式，避免等完整 SSE；
        · 所有异常都写入 last_error，不再让后台线程静默死掉。
        """
        if getattr(self, "_closed", False):
            return None
        try:
            import requests
        except Exception as e:
            self.last_error = ("缺少 requests 库（%s）。"
                               "请双击「修复环境.bat」安装。" % e)
            if not getattr(self, "_dep_warned", False):
                self._dep_warned = True
                try:
                    self.on_state("缺少 requests，请双击「修复环境.bat」")
                except Exception:
                    pass
            return None
        exc = getattr(requests, "exceptions", None)

        cfg = self.cfg
        key = (cfg.get("api_key") or "").strip()
        if not key:
            self.last_error = "未填写 API Key。请在 AI 设置中填写有效密钥。"
            return None
        base = normalize_base_url(cfg.get("base_url"))
        if not base:
            base = normalize_base_url(
                (PROVIDERS.get(cfg.get("provider")) or {}).get("base_url"))
        if not base:
            self.last_error = ("未填写 Base URL。请在 AI 设置中选择提供商，"
                               "或填写完整的接口地址。")
            return None
        url = base + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        model = (cfg.get("model") or "").strip()
        if not model:
            model = ((PROVIDERS.get(cfg.get("provider")) or {}).get("model")
                     or "deepseek-chat")
        payload = {"model": model,
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": max_tokens, "temperature": 0.7,
                   "stream": bool(stream)}
        started = time.monotonic()
        connected = None
        first = None
        pieces = []
        try:
            session = self._http_session
            if session is None:
                session = _new_http_session(requests)
                self._http_session = session
        except Exception as e:
            self.last_error = "创建 HTTP 会话失败：%s" % str(e)[:180]
            return None

        def _post(timeout_value, stream_value=None):
            if getattr(self, "_closed", False):
                raise RuntimeError("AI 陪玩已关闭")
            if stream_value is None:
                stream_value = bool(stream)
            return session.post(url, json=payload, timeout=timeout_value,
                                headers=headers, stream=stream_value)

        r = None
        try:
            try:
                r = _post(timeout)
            except Exception as e:
                # 只有连接类错误才换 IPv4 重试；读超时重试只会越等越久。
                retry = (not getattr(self, "_closed", False) and exc is not None and isinstance(
                    e, (exc.ConnectTimeout, exc.ConnectionError)))
                if not retry:
                    raise
                _force_ipv4()
                try:
                    session.close()
                except Exception:
                    pass
                session = _new_http_session(requests)
                self._http_session = session
                r = _post(timeout)
            connected = time.monotonic()
            # 接口不返回 charset 时 requests 会按 ISO-8859-1 解 → 中文乱码，强制 UTF-8
            try:
                r.encoding = "utf-8"
            except Exception:
                pass
            if r.status_code != 200:
                self.last_error = explain_http_error(r.status_code, r.text)
                return None

            if not stream:
                data = {}
                try:
                    data = r.json() or {}
                except Exception:
                    data = {}
                choice = (data.get("choices") or [{}])[0]
                result = ((choice.get("message") or {}).get("content")
                          or (choice.get("delta") or {}).get("content") or "")
                result = str(result).strip()
                if first is None:
                    first = time.monotonic()
                if result and on_chunk:
                    on_chunk(_named_reply(result))
            else:
                for raw in r.iter_lines(chunk_size=1, decode_unicode=True):
                    if getattr(self, "_closed", False):
                        return None
                    if not raw:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    if not raw.startswith("data:"):
                        continue
                    chunk = raw[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        choice = (obj.get("choices") or [{}])[0]
                        delta = (choice.get("delta") or {}).get("content")
                        if delta is None:
                            delta = (choice.get("message") or {}).get("content")
                    except Exception:
                        continue
                    if not delta:
                        continue
                    if first is None:
                        first = time.monotonic()
                    pieces.append(str(delta))
                    if on_chunk:
                        on_chunk(_named_reply("".join(pieces), partial=True))
                result = "".join(pieces).strip()
                if not result:
                    # 兼容不返回 SSE 内容的 OpenAI 代理；用较短超时只补一次。
                    try:
                        r.close()
                    except Exception:
                        pass
                    payload["stream"] = False
                    headers["Accept"] = "application/json"
                    fallback_timeout = (timeout[0], min(12, timeout[1]))
                    r = _post(fallback_timeout, False)
                    connected = time.monotonic()
                    if r.status_code != 200:
                        self.last_error = explain_http_error(r.status_code, r.text)
                        return None
                    data = r.json() or {}
                    choice = (data.get("choices") or [{}])[0]
                    result = (choice.get("message", {}).get("content", "")
                              .strip())
                    if result and on_chunk:
                        on_chunk(_named_reply(result))
            ended = time.monotonic()
            self.timing = {"api_first_byte": connected - started if connected else None,
                           "first_token": first - started if first else None,
                           "total": ended - started}
            if not result:
                self.last_error = ("接口返回成功，但没有文本内容。"
                                   "请确认模型是文本聊天模型，且 Base URL 指向 chat/completions。")
                return None
            print("AI 耗时：接口首包 %.2fs / 模型首段 %s / 总计 %.2fs" % (
                self.timing["api_first_byte"] or 0,
                ("%.2fs" % self.timing["first_token"]
                 if self.timing["first_token"] is not None else "无首段"),
                self.timing["total"]), flush=True)
            self.last_error = ""
            return _strip_bubble_prefix(_named_reply(result)) or None
        except Exception as e:
            msg = ""
            try:
                if exc is not None:
                    if isinstance(e, exc.ConnectTimeout):
                        msg = ("连接超时：无法及时连接 API。请检查网络、DNS 或代理；"
                               "若使用 Clash/加速器，请开启系统代理或设置 HTTPS_PROXY。")
                    elif isinstance(e, exc.ReadTimeout):
                        msg = ("模型响应超时：API 已连接，但模型未及时返回。"
                               "可换 deepseek-chat 等更快的模型，或检查接入点状态。")
                    elif isinstance(e, exc.ConnectionError):
                        msg = ("网络连接失败：请检查网络、代理和 API 地址；"
                               "若使用 Clash/加速器，请开启系统代理或设置 HTTPS_PROXY。")
                    elif isinstance(e, exc.SSLError):
                        msg = "TLS 安全连接失败：检查系统时间、代理证书或网络拦截。"
                if not msg:
                    msg = "请求失败（%s）：%s" % (type(e).__name__, str(e)[:180])
            except Exception:
                msg = "请求失败：%s" % str(e)[:180]
            self.timing = {"api_first_byte": (connected - started) if connected else None,
                           "first_token": (first - started) if first else None,
                           "total": time.monotonic() - started}
            self.last_error = msg
            return None
        finally:
            try:
                if r is not None:
                    r.close()
            except Exception:
                pass

    # ---------------- Edge TTS ----------------
    def _template_reply(self, events, hand_names, snap):
        """本地模板回复：即时、不花 API（LLM 太慢时用）。"""
        import random
        pers = self.cfg.get("personality") or "gentle"
        ev = (events or [])[:2]
        low_life = (snap.get("life") or 99) <= 6
        enemy = snap.get("enemy") or {}
        enemy_field = len(enemy.get("field") or [])
        my_field = len(snap.get("field") or [])
        pool = []
        if any("抽到" in e for e in ev):
            pool += ["抽到新牌啦～", "这张摸得不错嘛", "手牌又厚了一点"]
        if any("掉了" in e or "受伤" in e for e in ev) or low_life:
            pool += ["小心点，血不多了", "稳住，别硬拼", "先保命要紧"]
        if enemy_field >= 3:
            pool += ["对面铺起来了，注意解场", "对面场面有点大"]
        if my_field >= 3:
            pool += ["我们场面不错嘛", "压着打，别松"]
        if hand_names:
            pool += ["手牌还行，找机会打", "别急着出，留着换"]
        if pers == "toxic":
            pool += ["就这？继续表演", "三流的决斗者用着四流的牌组"]
        elif pers == "tsundere":
            pool += ["哼，才没有在担心你", "别、别误会，我只是顺便看看"]
        else:
            pool += ["加油呀～", "打得不错嘛", "我在这看着呢"]
        if not pool:
            pool = ["嗯……", "继续打吧"]
        return random.choice(pool)

    def _pet_action(self, action):
        """让宠物播放一个动作（跨进程：写 logs/_pet_cmd.json，宠物轮询读取）。"""
        try:
            import json as _json
            p = os.path.join(HERE, "logs", "_pet_cmd.json")
            tmp = p + ".tmp"
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump({"action": action, "at": time.time()}, fh,
                           ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    def _speak(self, text):
        if not self.cfg.get("tts_enabled"):
            return
        text = _named_reply(text or "").strip()
        if not text:
            return
        voice = self.cfg.get("voice") or "zh-CN-XiaoxiaoNeural"
        rate = self.cfg.get("rate") or "+0%"
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="coo_")
        os.close(fd)
        try:
            import edge_tts

            async def _gen():
                c = edge_tts.Communicate(text, voice, rate=rate)
                await c.save(path)

            asyncio.run(_gen())
            if os.path.getsize(path) > 0:
                play_mp3_blocking(path)
        except Exception:
            pass
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    # ---------------- 主循环 ----------------
    def tick(self):
        if getattr(self, "_closed", False):
            return
        # 配置仅在文件变更时重读，避免每 2 秒重复解析磁盘文件。
        try:
            mt = os.path.getmtime(CONFIG_PATH)
            if mt != self._cfg_mtime:
                self.cfg = load_config()
                self._cfg_mtime = mt
        except Exception:
            pass
        enabled = self.cfg.get("enabled")
        snap = self._live() if enabled else None
        now = time.time()
        if enabled and not snap and now < self._valid_until:
            return
        active = bool(enabled and snap and not snap.get("idle")
                      and now < (snap.get("at") or 0) / 1000.0 + 30)
        context = (snap.get("addr"), snap.get("turn")) if active else None
        if context is None and self._context is not None:
            self._last_turn = None
            if hasattr(self, "_last_addr"):
                del self._last_addr
        if context != self._context:
            self._generation += 1
            self._context = context
        if active:
            self._valid_until = (snap["at"] / 1000.0) + 30
        if not active:
            if enabled and not self._busy:
                self._maybe_end_battle()
            return
        if self._busy:
            return
        # 换牌阶段不开口，等他确定手牌再说
        if not snap.get("handReady", bool(snap.get("mulliganDone") or (snap.get("turn") or 0) > 0)):
            return
        # 记录战况（结束判断用）：进入对局、记最后生命
        self._in_battle = True
        self._last_life = snap.get("life")
        if self._end_done:
            self._end_done = False
        # 每个回合最多说一次
        turn = snap.get("turn")
        if turn is not None and turn == self._last_turn and snap.get("addr") == getattr(self, "_last_addr", None):
            return
        with self._lock:
            events = self._diff(snap, commit=False)
        hand_names = _norm(snap.get("hand"))
        if not events and not hand_names:
            return
        now = time.time()
        # 播报降频：平时 6 秒内最多一次；普通出牌 18 秒内最多一次（用户 2026-10-29 要求多说）
        if now - self._last_call < 6.0:
            return
        important = any(("抽到" in e) or ("掉了" in e) or ("回到了牌库" in e)
                        for e in events)
        if not important and now - self._last_call < 18.0:
            return
        with self._lock:
            self._diff(snap)
        self._last_turn = turn
        self._last_call = now
        if not self.cfg.get("use_llm"):
            # 默认模板：即时开口（LLM 动辄 30~60 秒，对局结束才回来）
            try:
                line = self._template_reply(events, hand_names, snap)
                self.on_text(line)
                threading.Thread(target=self._speak, args=(line,), daemon=True).start()
            except Exception:
                pass
            return
        prompt = self._prompt(snap, events, hand_names)
        self._busy = True
        generation = self._generation

        def publish(text):
            if generation == self._generation:
                self.on_text(text)

        def _work():
            try:
                line = self._call_llm(
                    prompt, on_chunk=publish)
                if generation != self._generation:
                    return
                if line:
                    print("AI 说：%s" % line, flush=True)
                    try:
                        import random as _rnd
                        if _rnd.random() < 0.5:
                            self._pet_action(_rnd.choice(
                                ["喷水", "开盖", "自拍", "番茄酱"]))
                    except Exception:
                        pass
                    try:
                        with open(os.path.join(HERE, "logs",
                                               "companion.log"), "a",
                                  encoding="utf-8") as fh:
                            fh.write("%s %s\n"
                                     % (time.strftime("%H:%M:%S"), line))
                    except Exception:
                        pass
                    self.on_text(line)
                    threading.Thread(target=self._speak, args=(line,),
                                     daemon=True).start()
                else:
                    # 没 key / 请求失败：只写日志，不往宠物气泡上弹错误
                    # （用户 2026-10-29：戳模型时只会显示错误 → 错误不再覆盖气泡）
                    err = getattr(self, "last_error", "") or "未知原因"
                    print("AI 未接入：%s" % err, flush=True)
                    try:
                        with open(os.path.join(HERE, "logs",
                                               "companion-err.log"), "a",
                                  encoding="utf-8") as fh:
                            fh.write("%s AI 未接入：%s\n"
                                     % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                        err))
                    except Exception:
                        pass
            finally:
                self._busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _end_prompt(self, win):
        """对局结束的告别语：输了鼓励，赢了按性格夸/嘲讽。"""
        lines = [
            "你是「鲸鲸」，二头身鲸鱼女仆娘，只输出一句话（不要加任何前缀或括号，比如不要写「（气泡）」）。",
            "性格：%s" % _personality_text(),
        ]
        if win is None:
            lines.append("对局状态已结束，胜负未知，只说一句中性的陪伴语。")
        elif win:
            lines.append(
                "这局赢了。按性格说一句：温柔/元气就夸主人；傲娇可以嘲讽对手，"
                "用原句梗（「弱小的家伙没资格玩影之诗」「输给你的人都不玩影之诗了」）。")
        else:
            lines.append(
                "这局输了。鼓励主人一句、别嘲讽（「下一把稳的」「别灰心」这种）。")
        lines.append("只说 1 句、15~30 字，别自称 AI。")
        return "\n".join(lines)

    def _maybe_end_battle(self):
        """对局数据过期（10 秒无更新）→ 认为一局结束，按胜负说一句。"""
        if not getattr(self, "_in_battle", False):
            return
        if getattr(self, "_end_done", False):
            return
        self._in_battle = False
        self._end_done = True
        life = getattr(self, "_last_life", None)
        self._last_turn = None
        if hasattr(self, "_last_addr"):
            del self._last_addr
        win = False if life == 0 else None
        if not self.cfg.get("use_llm"):
            return
        prompt = self._end_prompt(win)
        self._busy = True
        generation = self._generation

        def publish(text):
            if generation == self._generation:
                self.on_text(text)

        def _work():
            try:
                line = self._call_llm(
                    prompt, on_chunk=publish)
                if generation != self._generation:
                    return
                if line:
                    self.on_text(line)
                    try:
                        threading.Thread(target=self._speak, args=(line,),
                                         daemon=True).start()
                    except Exception:
                        pass
            finally:
                self._busy = False

        threading.Thread(target=_work, daemon=True).start()

    def test_llm(self):
        """测试连接：短超时 + 短回复，别让用户等太久。"""
        return self._call_llm("只回复两个字：在线", timeout=(4, 10),
                              max_tokens=12, stream=False)

    def test_tts(self, text="陪玩语音测试，听得见吗？"):
        self._speak(text)


# ---------------- Windows 播放 ----------------
def play_mp3_blocking(path):
    """用 MCI 播放 mp3，阻塞到播完（零依赖）。"""
    try:
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "coo_play_%d" % (int(time.time() * 1000) % 100000)
        buf = ctypes.create_unicode_buffer(256)
        mci('open "%s" type mpegvideo alias %s' % (path, alias), buf, 256, 0)
        mci("play %s wait" % alias, buf, 256, 0)
        mci("close %s" % alias, buf, 256, 0)
    except Exception:
        pass


def main():
    """命令行自测：python companion.py test"""
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        c = Companion(on_text=lambda s: print("[AI]", s))
        print("cfg:", {k: ("***" if k == "api_key" and v else v)
                       for k, v in cfg.items()})
        if cfg.get("api_key"):
            print("llm:", c.test_llm())
        else:
            print("llm: 未填 api_key")
        c.test_tts()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "voices":
        for v, n in VOICES:
            print(v, n)
        return
    print("用法: companion.py test | voices")


if __name__ == "__main__":
    main()
