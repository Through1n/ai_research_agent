"""侧边栏：大模型接入（预设五家 / 自定义任意端点）、API Key、用户、导航菜单、演示数据。"""
import os
import json
from pathlib import Path

import streamlit as st

from config import DEMO_PAPERS, DESKTOP_MODE
from core.providers import list_providers, get_provider, custom_provider, CUSTOM_ID
from services import library
from ui.common import (memory_mb, ensure_state, jump_banner, friendly_error, empty_state, step_text)

MENU = [
    "🤖 0. Agent 总助手",
    "📁 1. 文献库管理",
    "💬 2. 跨文献问答与溯源",
    "📖 3. 版式阅读与智能翻译",
    "🔍 4. 论文自动检索与追踪",
    "🕸️ 5. 引用图谱与综述",
    "✍️ 6. 写作辅助与可视化",
    "🏗️ 系统架构设计",
    "💡 技术创新亮点",
]

_PROVIDER_FAIL_HINT = [
    "🩺 可能原因：API Key 有误或已过期 / 平台额度耗尽 / 代理拦截了出网请求。",
    "🛠️ 怎么办：确认 Key 正确后重试；无 Key 也可点下方「🚀 一键加载演示数据」继续演示离线功能。",
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


def render(ctx):
    ensure_state({"saved_api_key": "", "menu": MENU[0]})
    with st.sidebar:
        st.title("🔬 AI4S 科研助手")
        _ver = _desktop_version_caption()
        if _ver:
            st.caption(f"🕹️ {_ver}")

        # 桌面版首次启动提示：无 API Key 时引导在下方「接入方式」配置（不弹窗、不跳转）
        if DESKTOP_MODE and not os.getenv("ZHIPU_API_KEY") and not os.getenv("LLM_API_KEY") \
                and not st.session_state.get("saved_api_key"):
            with st.container(border=True):
                st.warning("📌 尚未配置 API Key：请在下方「接入方式」选择平台并填入 Key；无 Key 也可使用底部按钮一键加载离线演示数据。")

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
                    try:
                        if ctx.set_provider(pid, key):
                            st.success("✅ 模型已就绪")
                        elif not p.needs_key:
                            st.caption("本地 Ollama 需先运行 `ollama serve` 并拉取模型")
                        else:
                            st.caption("未填 Key：可使用内置离线演示数据")
                    except Exception as e:
                        friendly_error("切换模型平台", e, reasons=_PROVIDER_FAIL_HINT[0:1],
                                       actions=_PROVIDER_FAIL_HINT[1:2])
                    if p.hint:
                        st.caption("💡 " + p.hint)

        st.markdown("---")
        try:
            ctx.memory.user = st.text_input("当前用户 ID", value=ctx.memory.user)
            prof = ctx.memory.profile() or {}
            if prof:
                st.caption("📊 画像：" + "，".join(f"{k}×{v}" for k, v in prof.items()))
        except Exception as e:
            friendly_error("读取用户画像", e, reasons=["memory.json 损坏"],
                           actions=["删除 storage/memory.json 后重启"])

        st.markdown("---")
        # 跨页跳转提示条：消费 ui/page_qa.py 写入的 session_state["qa_jump_page"]
        try:
            jump_banner()
        except Exception:
            pass

        qp = st.query_params
        if "p" in qp:
            try:
                _raw = qp["p"]
                idx = int(_raw[0] if isinstance(_raw, (list, tuple)) else _raw)
                if 0 <= idx < len(MENU):
                    st.session_state["menu"] = MENU[idx]
            except (TypeError, ValueError, IndexError):
                pass

        def _sync_menu():
            try:
                st.query_params["p"] = MENU.index(st.session_state["menu"])
            except Exception:
                pass

        menu = st.radio("导航菜单", MENU, key="menu", on_change=_sync_menu)

        st.markdown("---")
        if st.button("🚀 一键加载演示数据（v1/v7/BERT）"):
            items = list(DEMO_PAPERS.items())
            done, fails = 0, []
            with st.spinner(step_text("📥 正在加载演示数据", done, len(items))):
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
        if st.button("🗑️ 清空文献库"):
            if not ctx.files:
                st.info("📭 文献库已经是空的，无需清空。")
            else:
                fails = []
                with st.spinner(step_text("🗑️ 正在清空文献库")):
                    for fn in list(ctx.files):
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
                st.rerun()
        st.caption(f"💾 内存 {memory_mb()}")
    return menu
