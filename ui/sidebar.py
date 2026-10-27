"""侧边栏：大模型接入（预设五家 / 自定义任意端点）、API Key、用户、导航菜单、演示数据。"""
import os
import json
from pathlib import Path

import streamlit as st

from config import BRAND, BRAND_TAGLINE, DEMO_PAPERS, DESKTOP_MODE, brand_logo
from core.providers import list_providers, get_provider, custom_provider, CUSTOM_ID
from services import library
from ui.common import (memory_mb, ensure_state, jump_banner, friendly_error, empty_state,
                       step_text, _inject_css_once, _COMPONENT_CSS, _CSS_FLAG_COMPONENT,
                       brand_logo_data_uri)

MENU = [
    "智能总助手",
    "文献库管理",
    "跨文献问答与溯源",
    "版式阅读与智能翻译",
    "论文检索与追踪",
    "引用图谱与综述",
    "写作辅助与可视化",
    "系统架构设计",
    "技术创新亮点",
]
# 导航分组：核心工作流 7 项平铺，展示型 2 项收进折叠区。
# 文案即标识（不再用「emoji + 序号」当前缀）：路由、测试、页面标题共用同一串，
# 少一层「显示名 vs 值」的翻译，也就少一处会漂的地方。
MENU_MAIN = MENU[:7]
MENU_ABOUT = MENU[7:]

_PROVIDER_FAIL_HINT = [
    "🩺 可能原因：API Key 有误或已过期 / 平台额度耗尽 / 代理拦截了出网请求。",
    "🛠️ 怎么办：确认 Key 正确后重试；无 Key 也可点下方「一键加载演示数据（6 篇）」继续演示离线功能。",
]
_DEMO_FAIL_HINT = [
    "🩺 可能原因：向量库写入失败 / 磁盘权限不足 / storage 目录被占用。",
    "🛠️ 怎么办：重试一次；仍失败请检查 storage 目录写权限。",
]

# ---------- 跨 rerun 存活的 flash 提示 ----------
# 背景：st.success()/st.info() 紧跟 st.rerun() 时，rerun 会立刻重跑脚本，
# 提示还没渲染就被清空，用户点完按钮零反馈。故把提示写进 session_state，
# rerun 后由 app.py 在主体区顶部调用 render_flash() 统一重放并清空。
# 本模块是导航/框架层（ui/common.py 的 jump_banner 也从这里 import MENU），
# 故 flash 组件放在此处，供 app.py 与各页面共用。
FLASH_KEY = "_flash"
_FLASH_LEVELS = ("success", "info", "warning", "error")


def set_flash(level: str, msg: str):
    """写入一条跨 rerun 存活的提示（在 st.rerun() 之前调用）。"""
    try:
        lv = str(level or "info").lower()
        if lv not in _FLASH_LEVELS:
            lv = "info"
        st.session_state[FLASH_KEY] = (lv, str(msg or ""))
    except Exception:
        pass


def render_flash():
    """在主体区顶部重放并清空 flash 提示；无提示时静默返回（不渲染任何占位）。"""
    try:
        item = st.session_state.get(FLASH_KEY)
    except Exception:
        return
    if not item:
        return
    try:
        level, msg = item
    except (TypeError, ValueError):
        try:
            st.session_state[FLASH_KEY] = None
        except Exception:
            pass
        return
    # 先清空再渲染：避免渲染阶段再次触发 rerun 时重复弹提示
    try:
        st.session_state[FLASH_KEY] = None
    except Exception:
        pass
    if not msg:
        return
    fn = {"success": st.success, "info": st.info,
          "warning": st.warning, "error": st.error}.get(level, st.info)
    try:
        fn(msg)
    except Exception:
        st.info(msg)


def _desktop_version_caption() -> str:
    """桌面模式下读 launcher_state.json 返回业务版本号字符串；失败返回空串。"""
    if not DESKTOP_MODE:
        return ""
    try:
        state_file = Path(os.environ.get("APPDATA", "")) / "AI4SResearchAgent" / "launcher_state.json"
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            v = data.get("current_version")
            if v:
                return f"桌面版 业务 v{v}"
    except Exception:
        pass
    return "桌面版"


def _library_stats(ctx):
    """侧边栏库统计行：篇数 + 片段数（1480 片段级大库也需要一眼可见的规模感知）。"""
    try:
        n_files = len(ctx.files)
    except Exception:
        n_files = 0
    try:
        n_chunks = len(getattr(ctx, "chunks", None) or [])
    except Exception:
        n_chunks = 0
    if n_files:
        return f"文献库 {n_files} 篇 · {n_chunks} 个片段"
    return "文献库为空（点上方「加载演示数据」开始）"


def _llm_ping(ctx):
    """最小请求测试 Key 是否真的可用；返回 (ok, 人话结果)。

    与页面真实调用同走 core/llm.py 的重试通道；失败时不抛异常，
    依据 ctx.llm.last_error 判定（伪响应的特征文案仅作二级判据）。
    """
    try:
        msg = ctx.llm.chat([{"role": "user", "content": "请只回复两个字：正常"}],
                           temperature=0.0)
        text = str(getattr(msg, "content", "") or "")
    except Exception as e:
        return False, f"调用本身出现异常：{str(e)[:120]}"
    if getattr(ctx.llm, "last_error", None):
        return False, str(ctx.llm.last_error)[:160]
    if "暂时不可用" in text or "LLM 返回结构异常" in text:
        return False, text[:160]
    return True, (text[:40] or "模型已返回")


def _llm_ready(ctx) -> bool:
    """Key/模型是否已就绪（决定是否自动展开设置折叠区）。"""
    try:
        return bool(getattr(getattr(ctx, "llm", None), "available", False))
    except Exception:
        return False


def _pending_key_ready() -> bool:
    """本回合前端刚提交、但 ``ctx.llm`` 尚未重建时的 Key 就绪信号。

    背景（Round 16 实测缺陷）：``render()`` 在**开头**渲染「📌 尚未配置 API Key」
    警告（按 ``ctx.llm.available`` 判断），而真正读 Key 输入框并调用
    ``ctx.set_provider`` 的是后面的 ``_render_model_settings()``（折叠区内）——
    同一回合内警告先渲染、Provider 后应用。于是用户刚把 Key 填进去的那一回合，
    侧边栏会**同时**挂两条自相矛盾的提示：

        📌 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key…
        ✅ Key 已填入，模型通道就绪

    实测（AppTest，无真实网络请求）：填入后当轮 sidebar warning=1 + success=1，
    下一回合 warning 才消失。演示现场看到这两条并排，读起来就是"项目有 bug"。

    修法：**不重排渲染顺序、不动 set_provider 的错误状态机**（那套 _prev 回补是
    Round 12 F2/F7 的成果，碰它风险远大于收益），只在警告条件里纳入本回合的
    待应用值。依据是 Streamlit 的既有语义：widget key 在脚本重跑**之前**就已
    被前端更新，所以 ``st.session_state["key_input"]`` 在本回合开头就能读到
    用户刚键入的内容。

    覆盖两个分支：预设平台的 ``key_input`` 与自定义端点的
    ``cu_base``/``cu_model``/``cu_key``/``cu_needs``。

    已知边界（不构成矛盾、可接受）：用户**清空** Key 的那一回合，``ctx.llm``
    还是上一回合的旧客户端（available=True），警告会晚一回合出现；但折叠区内
    当回合就会显示「未填 Key：可使用内置演示数据」 caption，用户有即时反馈，
    不会出现两条对立提示同时在场。
    """
    try:
        ss = st.session_state
        base = str(ss.get("cu_base", "") or "").strip()
        model = str(ss.get("cu_model", "") or "").strip()
        if base and model:                          # 自定义端点分支
            if ss.get("cu_needs", True):            # 需要 Key 的端点：Key 也要有
                return bool(str(ss.get("cu_key", "") or "").strip())
            return True                             # 免密端点：端点+模型齐了即就绪
        return bool(str(ss.get("key_input", "") or "").strip())   # 预设平台分支
    except Exception:
        return False


def _render_model_settings(ctx):
    """模型接入 / 连通性测试 / 账号设置（供「⚙️ 模型接入与账号」折叠区调用）。

    这一块有十余个控件（平台单选、自定义端点四个输入、连通性测试、用户 ID…），
    原先常驻侧边栏，让每一页都很挤。主流 Agent 的做法是把「设置」收进折叠区，
    主界面只留导航与主操作 —— 逻辑一字未改，只是换了摆放位置。
    """
    # ================= 大模型接入（OpenAI 兼容协议，任意模型热切换） =================
    try:
        provs = list_providers() or []
    except Exception as e:
        friendly_error("读取模型 Provider 列表", e,
                       reasons=["core/providers.py 配置读取失败"],
                       actions=["重启应用", "使用🟢离线演示功能"])
        provs = []
    mode = st.radio("接入方式", ["🏢 预设平台", "➕ 自定义端点（任意模型）"], horizontal=False)

    if mode.startswith("➕"):
        st.caption("填写任意 OpenAI 兼容端点即可接入（vLLM / LM Studio / 硅基流动 / 校内网关…）")
        c_base = st.text_input("Base URL", key="cu_base",
                               placeholder="如 http://10.0.0.8:8000/v1")
        c_model = st.text_input("对话模型名", key="cu_model", placeholder="如 qwen2.5-14b-instruct")
        c_vl = st.text_input("视觉模型名（可留空 = 无多模态）", key="cu_vl", placeholder="如 qwen2.5-vl-7b")
        c_needs = st.checkbox("该端点需要 API Key", key="cu_needs", value=True)
        spec = {"base_url": c_base, "model": c_model, "vl_model": c_vl, "needs_key": c_needs}
        key = st.text_input("API Key（免密端点可不填）", type="password",
                            value=st.session_state.get("saved_api_key", ""), key="cu_key").strip()
        st.session_state.saved_api_key = key
        if c_base.strip() and c_model.strip():
            try:
                ok = ctx.set_provider(CUSTOM_ID, key, custom_spec=spec)
                p = custom_provider(spec)
                st.caption(("🖼️ 多模态 OCR 可用：" + p.models_hint) if p.has_vision
                           else "📵 未填视觉模型：OCR 自动降级")
                if ok:
                    st.success(f"✅ 已接入 {c_model}")
                else:
                    st.caption("⚠️ 请填写端点与模型名后生效")
            except Exception as e:
                friendly_error("接入自定义端点", e, reasons=_PROVIDER_FAIL_HINT[0:1],
                               actions=_PROVIDER_FAIL_HINT[1:2])
        else:
            st.caption("⚠️ 请至少填写 Base URL 与对话模型名")
    else:
        if not provs:
            st.warning("⚠️ 未读取到任何预设平台，请检查 core/providers.py 配置。")
        else:
            labels = [p.label for p in provs]
            ids = [p.id for p in provs]
            _idx = ids.index(ctx.provider_id) if getattr(ctx, "provider_id", None) in ids else 0
            sel = st.selectbox(
                "选择平台", labels, index=max(0, _idx),
                help="全部走 OpenAI 兼容协议；DeepSeek/Ollama 无视觉模型，扫描件 OCR 自动降级"
            )
            pid = provs[labels.index(sel)].id if sel in labels else ids[0]
            try:
                p = get_provider(pid)
            except Exception as e:
                friendly_error("获取平台配置", e, reasons=_PROVIDER_FAIL_HINT[0:1],
                               actions=_PROVIDER_FAIL_HINT[1:2])
                p = None
            if p is not None:
                st.caption(("🖼️ 支持多模态 OCR：" + p.models_hint) if p.has_vision
                           else "📵 无多模态：扫描件 OCR 自动降级为离线解析")
                key = st.text_input(f"{p.label.split('（')[0]} API Key",
                                    type="password", value=st.session_state.get("saved_api_key", ""),
                                    key="key_input").strip()
                st.session_state.saved_api_key = key
                # ---- Round 12 F2：保住 last_error，别被"无谓重建"清零 ----
                # 现象：Key 来自 .env / 环境变量、侧边栏输入框为空时，set_provider 的
                # 重建条件 `manual_key != self.llm.api_key` 恒真 → 每轮重建 LLMClient
                # → last_error 被清零 → 主体区 LLM 失败时框架横幅永远不会出现。
                # 修法：**不改 set_provider 的重建语义**（切换平台 / 演示中途 Key 失效
                # 仍照常重建），只在"重建前后 Key 与平台都没变"这种无谓重建之后，
                # 把上一轮的 last_error 还回去。
                _prev = (getattr(ctx.llm, "api_key", None), getattr(ctx, "provider_id", None),
                         getattr(ctx.llm, "last_error", None),
                         getattr(ctx.llm, "last_error_kind", None),
                         # F7：序号也要一起回补。seq 是 UI 判断"这次失败是本轮新写的"
                         # 的依据，重建把它清零会让"每轮 seq 都变"→ 横幅在每一页都显示。
                         getattr(ctx.llm, "last_error_seq", 0))
                try:
                    if ctx.set_provider(pid, key):
                        # 注意：available 只代表"Key 已填入且客户端已建好"，
                        # 不代表 Key 一定有效——真实验证请点下方「🔌 测试连通性」。
                        # 同理：最近一次调用已经失败时**不能**再报「通道就绪」，
                        # 否则和本轮在消灭的假成功同族（degrade-api 实测观察）。
                        #
                        # 注意必须读 **_prev[2]（重建前的值）**：`.env` 供 Key 时
                        # set_provider 每轮都会重建 LLMClient，重建后 last_error 是空的，
                        # 而下面的回补块又在本次判断**之后**执行——读重建后的值恒为 None，
                        # 这条分支永远不会命中（degrade-api 01:19 实测：修了等于没修）。
                        # _prev[2] 正是回补块要还回去的那个值，用它判断与"回补后再判断"等价，
                        # 且不依赖执行顺序。
                        if _prev[2]:
                            st.caption("⚠️ Key 已填入，但最近一次大模型调用失败了"
                                       "（原因见主体区提示 / 点下方「🔌 测试连通性」复查）")
                        else:
                            st.success("✅ Key 已填入，模型通道就绪")
                    elif not p.needs_key:
                        st.caption("本地 Ollama 需先运行 `ollama serve` 并拉取模型")
                    else:
                        st.caption("未填 Key：可使用内置离线演示数据")
                except Exception as e:
                    friendly_error("切换模型平台", e, reasons=_PROVIDER_FAIL_HINT[0:1],
                                   actions=_PROVIDER_FAIL_HINT[1:2])
                # 仅当"平台与 Key 都没变"才回补：Key 真的变了（演示中途清空 Key）
                # 时不回补，避免把上一次的失败原因带到新的 Key 上。
                try:
                    _now = (getattr(ctx.llm, "api_key", None), getattr(ctx, "provider_id", None))
                    if _now == _prev[:2] and _prev[2] and not getattr(ctx.llm, "last_error", None):
                        ctx.llm.last_error = _prev[2]
                        ctx.llm.last_error_kind = _prev[3]
                        ctx.llm.last_error_seq = _prev[4]
                except Exception:
                    pass
                if p.hint:
                    st.caption("💡 " + p.hint)

    # ---------- Key 连通性测试：杜绝"假就绪"（填入≠有效） ----------
    if getattr(getattr(ctx, "llm", None), "available", False):
        _model = str(getattr(ctx.llm, "model", "") or "")
        st.caption(f"🎯 当前模型：`{_model}`" if _model else "🎯 当前模型：未指定")
        if st.button("🔌 测试连通性",
                     help="发送一条最小请求验证 Key 是否真的可用；失败时会自动重试，约需 10 秒"):
            with st.spinner(step_text("🔌 正在测试模型连通性")):
                ok, detail = _llm_ping(ctx)
            if ok:
                st.success(f"✅ 连通性正常，模型返回：{detail}")
            else:
                st.warning("⚠️ 连通性测试未通过：请检查 Key / 平台额度 / 网络。")
                st.caption(f"🩺 错误摘要：{detail}")
                st.caption("🛠️ 怎么办：确认 Key 无误后重试；无 Key 可点下方"
                           "「一键加载演示数据（6 篇）」继续离线演示。")

    st.markdown("---")
    try:
        ctx.memory.user = st.text_input("当前用户 ID", value=ctx.memory.user)
        prof = ctx.memory.profile() or {}
        if prof:
            st.caption("📊 画像：" + "，".join(f"{k}×{v}" for k, v in prof.items()))
    except Exception as e:
        friendly_error("读取用户画像", e, reasons=["memory.json 损坏"],
                       actions=["删除 storage/memory.json 后重启"])

def _e(s) -> str:
    """HTML 转义（品牌文案走 unsafe_allow_html 渲染，必须先转义）。"""
    try:
        from html import escape
        return escape(str(s if s is not None else ""))
    except Exception:                                    # pragma: no cover
        return ""


def _brand_block() -> None:
    """侧栏品牌区：徽标图 + MoreHand + 副标题（横排，取不到图就退回纯文字）。

    徽标图同时用于页面图标（app.py）与「品牌徽标」区块，三处同一张图
    （品牌资源/icon.png，生成脚本 品牌资源/make_logo.py）。
    """
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    _logo = brand_logo("icon.png")
    if not _logo:                                        # 图标缺失不该让侧栏塌掉
        st.title(BRAND)
        st.caption(BRAND_TAGLINE)
        return
    _c_logo, _c_text = st.columns([1, 3.4], gap="small")
    with _c_logo:
        st.image(_logo, width=46, output_format="PNG")
    with _c_text:
        st.markdown('<div class="ai-brand-name">%s</div>' % _e(BRAND),
                    unsafe_allow_html=True)
        st.markdown('<div class="ai-brand-tagline">%s</div>' % _e(BRAND_TAGLINE),
                    unsafe_allow_html=True)


def _brand_badge_block() -> None:
    """「品牌徽标」区块：**高度有界**的徽标卡 + 点击查看完整设计规范。

    需求（2026-10-25）两条硬约束在这里落实：
      ① 图片与文字一律不溢出框定范围 —— .ai-brand-badge 的 max-height 锁死高度，
         徽标图用 data URI 塞进同一个框（st.image 是顶层元素，会跑到框外），
         超出部分在框内上下滑动（overscroll-behavior:contain 不带动整页）；
      ② 提供「点击进入查看详情」的交互 —— 外层 st.expander 本身就是点击入口，
         点开才加载完整规范，不占首屏。
    """
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    with st.expander("品牌徽标", expanded=False):
        _img = brand_logo_data_uri("logo.png", 512) or brand_logo_data_uri("icon.png", 512)
        _img_html = (f'<img src="{_img}" alt="MoreHand 徽标" width="150" />'
                     if _img else "")
        st.markdown(
            '<div class="ai-brand-badge">'
            + _img_html
            + '<div class="ai-brand-name">%s</div>' % _e(BRAND)
            + '<div class="ai-brand-tagline">%s</div>' % _e(BRAND_TAGLINE)
            + '<div class="ai-brand-badge-cap">构成</div>'
            + '<div class="ai-muted">圆角方形（squircle）底 + 品牌蓝渐变；'
              '中央一只抽象的向上手掌，五根手指是圆头白线，指节处为浅色节点'
              '——手掌即 MoreHand 的 Hand，也是 AI agent 的神经网络（节点＝能力，'
              '连线＝编排）；右上角四角星 spark 表示智能生成。</div>'
            + '<div class="ai-brand-badge-cap">用色</div>'
            + '<div class="ai-muted">只取品牌强调色系 #2F7CC9 → #174E8E 与'
              '白 / 浅蓝白 #D6E6FA，不引入第三色相（与 ui/design.py 同源）。</div>'
            + '<div class="ai-brand-badge-cap">光学尺寸</div>'
            + '<div class="ai-muted">≥64px 细模（掌缘弧 + 关节节点 + spark）；'
              '32–63px 中模（去关节节点）；&lt;32px 剪影模（只留五根加粗圆头线）'
              '——16px 下任何内部细节都会糊成一团，宁可只要一只手。</div>'
            + '<div class="ai-brand-badge-cap">规格</div>'
            + '<div class="ai-muted">品牌资源/icon.ico 含 16/24/32/48/64/128/256 '
              '七档；logo.png 1024&sup2;、icon.png 256&sup2;；矢量源 品牌资源/logo.svg。</div>'
            + '</div>',
            unsafe_allow_html=True)
        st.caption("完整设计规范与实现方案见 资料/brand_logo_design.md")


def render(ctx):
    ensure_state({"saved_api_key": "", "menu": MENU[0]})
    with st.sidebar:
        _brand_block()
        _ver = _desktop_version_caption()
        if _ver:
            st.caption(f"🕹️ {_ver}")

        # 未配置 Key 提示：桌面/浏览器一致（按 llm 实际可用性判断，而非仅桌面模式）
        # 条件里的 _pending_key_ready() 见该函数文档：没有它，用户刚填入 Key 的
        # 那一回合会同时看到「尚未配置」警告与「Key 已填入」成功提示（自相矛盾）。
        if not (_llm_ready(ctx) or _pending_key_ready()):
            with st.container(border=True, key="card12"):
                st.warning("📌 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；"
                           "无 Key 也可使用底部按钮一键加载离线演示数据。")

        # 接入/账号设置收进折叠区：主流 Agent 的做法——设置不占主视觉，
        # 侧边栏只保留「导航 + 任务中心 + 主操作」。未配置 Key 时自动展开，
        # 配置就绪后默认收起。
        with st.expander("模型接入与账号", expanded=not _llm_ready(ctx)):
            _render_model_settings(ctx)

        st.markdown("---")
        # 跨页跳转提示条：消费 ui/page_qa.py 写入的 session_state["qa_jump_page"]
        try:
            jump_banner()
        except Exception:
            pass

        # URL 深链（?p=索引）只在会话首次进入时应用一次。
        # 旧实现每次 rerun 都无条件用它覆盖 session_state，而一旦选中过页面
        # ?p= 就常驻 URL —— 于是用户切到别的页后，下一次 rerun 又被拽回旧页
        # （表现为"点了导航没反应/自动跳回去"）。
        if "_nav_url_applied" not in st.session_state:
            st.session_state["_nav_url_applied"] = True
            qp = st.query_params
            if "p" in qp:
                try:
                    _raw = qp["p"]
                    idx = int(_raw[0] if isinstance(_raw, (list, tuple)) else _raw)
                    if 0 <= idx < len(MENU):
                        st.session_state["menu"] = MENU[idx]
                except (TypeError, ValueError, IndexError):
                    pass

        def _pick(item):
            """统一选中入口：写 session_state + 同步 URL 参数（替代旧 on_change）。"""
            st.session_state["menu"] = item
            try:
                st.query_params["p"] = MENU.index(item)
            except Exception:
                pass

        cur = st.session_state.get("menu")
        if cur not in MENU:                       # 兼容旧值 / 首次进入
            cur = MENU[0]
            st.session_state["menu"] = cur

        # ── 「用户真的点了」判定（导航分主区 / 折叠区两个 radio）─────────
        # 陷阱：st.radio 无论如何都会返回一个值（缺省取首项）。旧代码用
        # `about != cur` 判定，而 cur 属于主区时 about 恒为 MENU_ABOUT[0]，
        # 于是首次渲染就被判成"用户点了关于页"——应用一打开直接落在
        # 「🏗️ A. 系统架构设计」，首页和三步引导条全部失效。
        # 修法：给 radio 加 key，比较「本轮 widget 值」与「上一轮渲染值」，
        # 只有真正变化才算用户点击（首轮 prev 为 None，不触发）。
        # ⚠️ 快照必须存在 widget 自己的 key 之外：用户一点击，Streamlit 在脚本重跑
        # 之前就把 `_nav_main` 更新成了新值，读它做对比等于自己跟自己比，永远相等。
        # 故用独立的影子键记录"上一轮渲染值"，并在本轮末尾回写。
        # ── 导航：主区 radio + 折叠区按钮 ───────────────────────────────
        prev_main = st.session_state.get("_nav_main_shadow")
        nav_changed = st.session_state.get("_nav_ready", False)

        # 停在折叠区页面时，清掉主区 radio 的残留选中值：不清的话，用户点击那个被
        # 悄悄保留的项等于点了"已选中项"，widget 值无变化 → 检测不到点击 → 点不动。
        # 只在"值未变化"（说明不是用户刚点的）时清理，避免把新点击吃掉。
        raw_main = st.session_state.get("_nav_main")
        if cur in MENU_ABOUT and raw_main is not None and raw_main == prev_main:
            st.session_state["_nav_main"] = None

        # ── 分组标题（纯视觉，强化层次，不动导航逻辑）────────────────
        # 2026-10-24：粗体 + 表情的「功能导航」改成小标签（eyebrow）——11.5px / 7% 字距
        # / 三级灰，与下面的导航行形成两级层次，替代原先的粗体大字。
        st.markdown('<div class="ai-nav-eyebrow">功能导航</div>', unsafe_allow_html=True)
        main_val = st.radio("功能导航", MENU_MAIN,
                            index=MENU_MAIN.index(cur) if cur in MENU_MAIN else None,
                            key="_nav_main",
                            label_visibility="collapsed")
        # 仅非首轮判定点击：首轮没有历史值，任何返回值都不是用户动作。
        if nav_changed and main_val is not None and main_val != prev_main:
            _pick(main_val)
            cur = main_val

        # 折叠区用按钮：radio 无论如何都有选中态，两组 radio 共用同一个导航状态
        # 时必然互相打架（且 Streamlit 禁止在实例化后回填 widget key 来纠正）。
        # 按钮无状态，"点了"就是"点了"。
        with st.expander("关于", expanded=False):
            for item in MENU_ABOUT:
                if st.button(item, key="_nav_about_%s" % item,
                             use_container_width=True,
                             disabled=(item == cur)):
                    _pick(item)
                    cur = item
            # 品牌徽标：默认收起（不占首屏），点开才看——与「关于」同类，
            # 属展示型信息而非导航。
            _brand_badge_block()

        st.session_state["_nav_ready"] = True
        st.session_state["_nav_main_shadow"] = main_val
        menu = cur

        st.markdown("---")
        # 任务中心：后台任务的实时节点进度（问题五/六），任何页面都可见
        try:
            from services import tasks as _tasks
            from ui.common import render_task_panel

            def _consume_task(t):
                if t.get("kind") == "agent" and t.get("result") is not None:
                    runs = st.session_state.setdefault("agent_runs", [])
                    runs.append({"goal": str(t.get("goal") or t.get("name", "")),
                                 "trace": t["result"]})
                    return True
                return False

            # 录制演示素材时（MOREHAND_RECMODE=1）只显示运行中的任务：
            # 任务表是进程级共享的，历史卡会把前面段落跑完的任务堆在侧栏，
            # 成片里既杂乱、也提前剧透后几段的功能。
            render_task_panel(_tasks, on_finish=_consume_task,
                              show_recent=not os.environ.get("MOREHAND_RECMODE"))
        except Exception:
            pass

        st.markdown("---")
        # 演示数据是这里唯一的主动作；维护类操作只留一个次要按钮，不再用分组标题+说明
        # （侧栏一屏里能被读到的行数是有限的，标题本身也是噪音）。
        if st.button("一键加载演示数据（6 篇）",
                     use_container_width=True,
                     help="向文献库写入 6 篇内置示例论文（Transformer v1/v7、BERT、Adam、VAE、"
                          "BatchNorm），用于离线演示各项功能（不覆盖已有论文）。后三篇的正文里"
                          "带真实公式，「🧮 提取 LaTeX 公式」可直接抽出 α = 0.001、"
                          "θt ← θt−1 − α · m̂t/(√v̂t + ϵ) 这类原文表达式"):
            items = list(DEMO_PAPERS.items())
            done, fails = 0, []
            # 逐项进度：st.status 的 label 可随循环更新，用户能看到 1/3 → 2/3 → 3/3
            # （旧实现 spinner 文案创建时固定为 0/3，整个加载过程静止不动）
            try:
                status = st.status(step_text("📥 正在加载演示数据", 0, len(items)), expanded=True)
            except Exception:                            # pragma: no cover - 旧版本降级
                status = None
            if status is not None:
                with status:
                    for fn, info in items:
                        try:
                            if fn not in ctx.files:
                                library.add_text_corpus(ctx, fn, info.get("text", ""), info)
                            done += 1
                            st.caption(f"✅ {fn}")
                        except Exception as e:
                            fails.append(f"{fn}：{str(e)[:80]}")
                            st.caption(f"⚠️ {fn} 失败：{str(e)[:80]}")
                        status.update(label=step_text("📥 正在加载演示数据", done, len(items)))
                    status.update(label=f"📥 演示数据加载完成 {done}/{len(items)}",
                                  state="complete")
            else:                                        # pragma: no cover - 旧版本降级
                with st.spinner(step_text("📥 正在加载演示数据", 0, len(items))):
                    for fn, info in items:
                        try:
                            if fn not in ctx.files:
                                library.add_text_corpus(ctx, fn, info.get("text", ""), info)
                            done += 1
                        except Exception as e:
                            fails.append(f"{fn}：{str(e)[:80]}")
            if fails:
                # 失败提示同样写入 flash：后面紧跟 rerun，直接 st.warning 会被吞掉
                set_flash("warning", f"⚠️ 已加载 {done}/{len(items)} 篇，{len(fails)} 篇失败。"
                                     f"🩺 失败明细：" + "；".join(fails[:3]))
                st.caption("🛠️ 怎么办：重试一次；仍失败请检查 storage 目录写权限。")
            try:
                n = len(ctx.files)
            except Exception:
                n = done
            if done:
                # 提示写进 flash 再 rerun：否则 success 会被 rerun 吞掉，用户零反馈
                set_flash("success", f"✅ 演示数据已就绪，当前文献库 {n} 篇")
                st.rerun()
        # 「清空」为危险操作：先二次确认，避免误触（功能不变，仅加一步确认）。
        _clearing = bool(st.session_state.get("_confirm_clear", False))
        if st.button("清空文献库", use_container_width=True,
                     disabled=_clearing,
                     help="移除文献库全部论文与索引，操作不可撤销"):
            if not ctx.files:
                st.info("📭 文献库已经是空的，无需清空。")
            else:
                st.session_state["_confirm_clear"] = True
                st.rerun()
        if _clearing:
            st.warning("⚠️ 确认清空整个文献库？该操作不可撤销，将一并清除图谱/综述等缓存。")
            _cc1, _cc2 = st.columns(2)
            with _cc1:
                if st.button("✅ 确认清空", type="primary", use_container_width=True):
                    fails = []
                    targets = list(ctx.files)
                    try:
                        status = st.status(step_text("🗑️ 正在清空文献库", 0, len(targets)), expanded=False)
                    except Exception:                    # pragma: no cover - 旧版本降级
                        status = None
                    if status is not None:
                        with status:
                            for i, fn in enumerate(targets, 1):
                                try:
                                    library.remove_paper(ctx, fn)
                                except Exception as e:
                                    fails.append(f"{fn}：{str(e)[:80]}")
                                status.update(label=step_text("🗑️ 正在清空文献库", i, len(targets)))
                            status.update(label="🗑️ 清空完成", state="complete")
                    else:                                # pragma: no cover - 旧版本降级
                        with st.spinner(step_text("🗑️ 正在清空文献库")):
                            for fn in targets:
                                try:
                                    library.remove_paper(ctx, fn)
                                except Exception as e:
                                    fails.append(f"{fn}：{str(e)[:80]}")
                    for k in ("graph_pack", "review", "future", "struct", "meta_view",
                              "formula_view", "ref_view", "pending"):
                        st.session_state.pop(k, None)
                    if fails:
                        set_flash("warning", f"⚠️ 清空完成，但有 {len(fails)} 篇移除失败。"
                                             f"🩺 失败明细：" + "；".join(fails[:3]))
                    else:
                        set_flash("success", "✅ 已清空文献库")
                    st.session_state["_confirm_clear"] = False
                    st.rerun()
            with _cc2:
                if st.button("❌ 取消", use_container_width=True):
                    st.session_state["_confirm_clear"] = False
                    st.rerun()
        st.caption(f"{_library_stats(ctx)} · 内存 {memory_mb()}")
    return menu
