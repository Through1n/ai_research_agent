"""共享 UI 组件：页面头部、美化表格、空状态、错误边界、执行轨迹时间线、溯源卡片。

UI 一致性规范（话术 3）——本模块是全部页面的公共层，提供：
  1. page_header()          统一页面顶部（白底卡片 + 左侧强调色竖条）
  2. pretty_table()         统一表格渲染（柔底表头 + 1px 描边 + 斑马纹）
  3. ensure_state()         session_state 集中初始化，避免 KeyError
  4. empty_state()          空数据友好提示 + 引导操作
  5. friendly_error() / net_error() / llm_guard()   API 调用失败的统一降级话术
  6. long_block()           长文本自动折叠
  7. safe_render() / safe_call()  统一异常边界（增强版，保持旧调用签名）
  8. jump_banner()          跨页跳转提示条（消费 page_qa 写入的 qa_jump_page）

对外历史函数（render_styled_table / source_card / render_trace /
memory_mb / require_library / resolve_pdf / embed_html）全部保留，签名不变，
以免破坏其他页面（page_qa / page_reading / page_writing）的既有调用。
（死代码 html_table 已于 2026-10-24 删除：全仓 0 调用者，连带 _C_BORDER_CELL2 /
_C_BG_ZEBRA2 两个只服务它的常量。）

==============================================================================
全站视觉重做（接令牌）—— 本模块不再持有任何写死的视觉字面量
==============================================================================
改动要点：
  1) 所有颜色 / 圆角 / 间距 / 字号 / 行高改为引用 ui/design.py 的 CSS 变量
     （var(--...)）。这些 fragment 运行在主文档里，能正常继承页面级变量。
  2) 每个公共渲染入口（page_header / pretty_table / source_card / empty_state_card /
     metric_strip / embed_html / long_block …）先调用 design.ensure()；它是会话级
     幂等的（内部有旗标），多处调用无害——页面渲染顺序不固定，不能假定上游已注入。
  3) 装饰性阴影（原 _C_SHADOW）全部删除，层级改用 1px solid var(--c-border) 表达；
     圆角一律收敛到 --r(8px)、间距一律取 8px 倍数、字号取 --fs-* 字阶。
  4) 旧常量名 _C_* **保留**（外部可能 import），但**值已改成 var 引用**；
     新代码请直接用 var(--...) 或下面的共用构件类，别再写死字面量。

共用构件类清单（一次性注入 :: _COMPONENT_CSS，后续页面迁移直接调用）
-------------------------------------------------------------------
  .ai-card            标准白底卡片：圆角 8 / 1px 描边 / 内边距 --s-5 / 长词可控换行
  .ai-card-tight      同系列，内边距收紧到 --s-3（列表项、小幅卡片）
  .ai-section         分区容器：上间距 --s-8 / 下间距 --s-4
  .ai-section-title   分区标题：--fs-title / 650 字重 / -0.01em 字距（不用彩色竖条）
  .ai-section-sub     分区副文案：--fs-caption / --c-text-2
  .ai-kpi             KPI 指标卡容器（白底 + 1px 描边 + 圆角 8）
  .ai-kpi-val         指标数值：--fs-display / 强调色 / tabular-nums 等宽数字
  .ai-kpi-label       指标名（或口径小字）：--fs-micro / --c-text-2
  .ai-chip            行内标签：圆角 8 / --c-subtle 底 / --fs-micro / --s-1 --s-3 内距
  .ai-num             引导动作的序号标记（1. / 2.，用于空状态卡片）
  .ai-empty           空状态卡片容器（白底 + 1px 描边 + 圆角 8）
  .ai-empty-title     空状态标题：--fs-title / 加粗 / --c-text
  .ai-empty-hint      空状态引导行：--fs-caption / --c-text-2
  .ai-muted           次级文字（--c-text-2）
  .ai-snippet         等宽代码块：可横向滚动，绝不撑破栅格
  .ai-think           思考块：左侧 3px 细线表达层级（不用阴影）
  .ai-table-wrap      表格包裹层：overflow-x:auto，防宽表撑破布局
  .ai-divider         1px 分隔线
  .ai-brand-name      侧栏品牌名：--fs-title / --c-text（配 logo 图横排）
  .ai-brand-tagline   侧栏品牌副标题：--fs-caption / --c-text-3
  .ai-brand-badge     品牌徽标卡：**高度有界**（max-height + overflow-y:auto），
                      图片与文字一律不溢出框定范围，超出在框内上下滑动查看
  .ai-brand-badge-cap 徽标卡内的图注/规格行：--fs-micro / --c-text-3
  .ptab / .ptab-wrap  统一表格（见 _PRETTY_CSS：柔底表头 + 斑马纹 + 悬停态）
"""

import json
import os
import socket
import time
import traceback
from functools import lru_cache
from html import escape as _escape

import pandas as pd
import streamlit as st

from config import brand_logo
from ui import design as _design

# 长文本折叠阈值：超过该字数或 15 行一律折叠（UI 规范 5）
LONG_TEXT_LIMIT = 300
LONG_LINE_LIMIT = 15

# 跨页跳转 session_state key（由 ui/page_qa.py 写入，本模块只读）
QA_JUMP_KEY = "qa_jump_page"
# 📖 版式阅读页在 ui/sidebar.py MENU / app.py _PAGES 中的索引
READING_MENU_IDX = 3

# ============================ 视觉令牌（设计系统对齐） ============================
# ⚠️ 全站视觉重做后本模块**不再持有字面量**：下面每个常量都改写成 ui/design.py 的
#    CSS 变量引用。常量名保留是为了兼容历史 import（同名-semantic 映射关系见下表），
#    **新代码请直接用 var(--...) 或 _COMPONENT_CSS 里的共用类，别再写死字面量**。
# 颜色映射遵循「中性色为主 + 唯一强调色 #1f5fae」：
#   · 原渐变链 _C_PRIMARY/#3b6fb6、_C_PRIMARY_DK/#2f5a96、_C_SOURCE/#27518a 全部并入
#     唯一强调色 --c-accent(#1f5fae) / --c-accent-hover(#1a4f92)，原强调蓝被删除；
#   · 原浅紫 _C_BG_PURPLE/#f6f1fd 是全站唯一的第二种彩色（紫），已删除，并与浅蓝
#     _C_BG_BLUE 一起并入中性柔和面 --c-subtle(#f4f4f5)；
#   · 原 _C_INK/#173a63、_C_INK_TBL/#17325a、_C_SUB/#4a5a70、_C_CELL/#26313f 这些深浅
#     不一的蓝灰，统一并入中性文字三档 --c-text / --c-text-2 / --c-text-3；
#   · 其余边框 / 底色 / 斑马纹并入 --c-border、--c-border-strong、--c-surface、--c-bg。
# 圆角：原三档 10 / 12 / 6px 全部收敛到 --r(8px)（_C_RADIUS / _C_RADIUS_BADGE /
#   _C_RADIUS_SM 三档同时消失）。
# 阴影：_C_SHADOW 原为装饰性轻投影，已删除（改为 none）；层级一律靠 1px 边框表达。
# 间距：原 padding `12px 16px` → --s-3 --s-4；原 margin `2px 0 14px` → 0 0 --s-2，
#   全部落在 8px 网格（S1=4 / S3=12 是设计系统允许的半档）。
_C_PRIMARY      = "var(--c-accent)"          # 唯一强调色（原 #3b6fb6 被删除，并入主色）
_C_PRIMARY_DK   = "var(--c-accent-hover)"    # 强调色按压态（原 #2f5a96）
_C_INK          = "var(--c-text)"            # 标题（原 #173a63 并入中性正文色）
_C_INK_TBL      = "var(--c-text)"            # 表头文字（原 #17325a 并入中性正文色）
_C_SUB          = "var(--c-text-2)"          # 副文案（原 #4a5a70）
_C_CELL         = "var(--c-text)"            # 单元格正文（原 #26313f）
_C_BORDER       = "var(--c-border)"          # 卡片外边框（原 #d9e3f2 浅蓝，改中性）
_C_BORDER_TBL   = "var(--c-border)"          # 表格外框（原 #dde5f0）
_C_BORDER_HEAD  = "var(--c-border-strong)"   # 表头底框（原 #bfd2ee）
_C_BORDER_CELL  = "var(--c-border)"          # 表格单元格分隔线（原 #e9eef5）
_C_BG_BLUE      = "var(--c-subtle)"          # 原渐变浅蓝起点（渐变已删，改中性柔和面）
_C_BG_PURPLE    = "var(--c-subtle)"          # 原渐变浅紫终点（紫色被删，并入 --c-subtle）
_C_BG_SOFT      = "var(--c-surface)"         # 卡片底色（原 #f7faff）
_C_BG_ZEBRA     = "var(--c-bg)"              # 斑马纹（原 #f7fafd）
_C_BG_HOVER     = "var(--c-accent-soft)"     # 悬停底色（唯一保留的色彩强调：8% 主色）
_C_SOURCE       = "var(--c-text)"            # 溯源标题（原 #27518a 蓝色，改中性正文色）
_C_THOUGHT_BG   = "var(--c-subtle)"          # 思考块底色（原 #f4f6fa）
_C_THOUGHT_BD   = "var(--c-border-strong)"   # 思考块竖条（原 #9aa7bd）
_C_RADIUS       = "var(--r)"                 # 卡片 / 表格圆角（原 10px → 统一 8px）
_C_RADIUS_BADGE = "var(--r)"                 # 徽章 / 标签圆角（原 pill 12px → 统一 8px）
_C_RADIUS_SM    = "var(--r)"                 # 小控件圆角（原 6px → 统一 8px）
_C_SHADOW       = "none"                     # 装饰性阴影已删除；层级一律用 1px 边框
_C_PAD          = "var(--s-3) var(--s-4)"    # 统一卡片内边距（原 12px 16px → 8px 网格）
_C_MARGIN       = "0 0 var(--s-2)"           # 统一卡片外边距（原 2px 0 14px → 8px 网格）


# ==================== 令牌接入 / 一次性 CSS 注入（会话幂等） ====================
# 为什么需要它：page_pretty_table 的 _PRETTY_CSS 以前是**每渲染一次表格就整段重灌**
# 一次 <style>，一张页面有 3 张表就灌 3 遍。这里统一走会话级旗标，只注入一次。
_CSS_FLAG_COMPONENT = "_ai_common_component_css"   # _COMPONENT_CSS 已注入标记
_CSS_FLAG_TABLE     = "_ai_common_table_css"       # _PRETTY_CSS 已注入标记


def _ensure_tokens() -> None:
    """确保 ui/design.py 的令牌层已注入本次会话（幂等，design.apply 自带旗标）。

    为什么每个渲染入口都要调：页面之间渲染顺序不固定（用户可能直达任意一页），
    不能假定上游页面/框架已经注入过令牌。
    """
    try:
        # bare 模式（纯函数单测、离线脚本）没有脚本运行上下文，此时不触碰 st.*：
        # 既无必要，也会刷 "missing ScriptRunContext" 警告污染测试输出。
        # suppress_warning=True：取不到上下文是预期情况，不需要日志（streamlit 1.63 签名）。
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx(suppress_warning=True) is None:
            return
    except Exception:
        pass
    try:
        _design.ensure()
    except Exception:
        pass


def _inject_css_once(css_block: str, flag: str) -> bool:
    """把一段**自带 <style> 包裹**的 CSS 注入本页 —— **每次运行一次**（本轮幂等）。

    ⚠️ 原实现是"会话级幂等"（旗标置 True 后永久跳过），这是真实事故（2026-10-24）：
    Streamlit 每次 rerun 重建整个元素树，上一轮的 <style> 不会保留；旗标导致第二轮起
    不再注入 → 第一次点击后全站构件类（.ai-card / .ai-section / 表格）样式全部消失。
    现按 app.py 维护的 `_run_seq` 判定：同一轮只注入一次，跨轮必注入；无序号时每调用
    都注入（宁可多一份 style，绝不丢样式）。
    """
    _ensure_tokens()
    try:
        if st.session_state.get(flag) == st.session_state.get("_run_seq") \
                and st.session_state.get("_run_seq") is not None:
            return False
        st.markdown(css_block, unsafe_allow_html=True)
        seq = st.session_state.get("_run_seq")
        st.session_state[flag] = seq if seq is not None else True
        return True
    except Exception:
        return False


# ==================== 全站共用构件类（供后续各 page_*.py 迁移） ====================
# 全部引用 var(--...)，因此在任何页面任何位置渲染都自动跟随令牌；只需 _COMPONENT_CSS
# 注入一次（会话幂等，见 _inject_css_once）。
_COMPONENT_CSS = """<style>
/* AI4S 共用构件类（ui/common.py :: _COMPONENT_CSS）—— 新页面优先复用，勿再内联写死样式 */
.ai-page-head{                  /* 页面顶端：标题 + 一句话说明 + 发丝线（不再用卡片包框） */
  padding:0 0 var(--s-4);margin:0 0 var(--s-5);min-width:0;
  border-bottom:1px solid var(--c-border);
}
.ai-page-title{                 /* 页面主标题：全站唯一 display 级锚点，负字距收紧 */
  font-size:var(--fs-display);line-height:var(--lh-display);font-weight:650;
  letter-spacing:-0.01em;color:var(--c-text);overflow-wrap:anywhere;
}
.ai-page-sub{                   /* 页面副文案：说明这一屏干什么，三级层级的最弱一级 */
  margin-top:var(--s-1);font-size:var(--fs-caption);line-height:var(--lh-caption);
  color:var(--c-text-2);overflow-wrap:anywhere;
}
.ai-card{                       /* 标准白底卡片：列表项、说明块、溯源卡的通用容器 */
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-5);margin:0 0 var(--s-3);min-width:0;overflow-wrap:anywhere;
}
.ai-card-tight{                 /* 紧凑卡片：密集列表（如溯源多条、目录项） */
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-3);margin:0 0 var(--s-2);min-width:0;overflow-wrap:anywhere;
}
.ai-section{                    /* 分区容器：区块之间的垂直节奏由它统一给 */
  margin-top:var(--s-8);margin-bottom:var(--s-4);min-width:0;
}
.ai-section-title{              /* 分区标题：靠字号/字重/留白拉层级，不用彩色竖条（去装饰） */
  font-size:var(--fs-title);line-height:var(--lh-title);font-weight:650;
  letter-spacing:-0.01em;color:var(--c-text);overflow-wrap:anywhere;
}
.ai-section-sub{                /* 分区副文案：紧随标题的一句话口径说明 */
  font-size:var(--fs-caption);line-height:var(--lh-caption);color:var(--c-text-2);
  margin-top:var(--s-1);overflow-wrap:anywhere;
}
.ai-kpi{                        /* KPI 指标卡容器：白底 + 1px 描边 + 圆角 8 */
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-4);margin:0 0 var(--s-2);min-width:0;overflow:hidden;
}
.ai-kpi-val{                    /* 指标数值：大号等宽数字，保证多列小数点对齐 */
  margin-top:var(--s-1);font-size:var(--fs-display);line-height:var(--lh-display);
  font-weight:700;color:var(--c-accent);overflow-wrap:anywhere;
}
.ai-kpi-label{                  /* 指标名 / 口径小字：最弱一级文字 */
  font-size:var(--fs-micro);line-height:var(--lh-micro);color:var(--c-text-2);
  overflow-wrap:anywhere;
}
.ai-chip{                       /* 行内标签：标签云、徽章、状态位 */
  display:inline-block;max-width:100%;border-radius:var(--r);background:var(--c-subtle);
  color:var(--c-text-2);font-size:var(--fs-micro);line-height:var(--lh-micro);
  padding:var(--s-1) var(--s-3);overflow-wrap:anywhere;
}
.ai-num{                        /* 引导动作序号（1. / 2.）：比正文深一级，作为视觉锚点 */
  color:var(--c-accent);font-weight:700;
}
.ai-empty{                      /* 空状态卡片容器：整块内容区为空时的首屏教学位 */
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-4);margin:0 0 var(--s-2);min-width:0;overflow-wrap:anywhere;
}
.ai-empty-title{                /* 空状态标题 */
  font-size:var(--fs-title);line-height:var(--lh-title);font-weight:700;color:var(--c-text);
}
.ai-empty-hint{                 /* 空状态引导行（会出现什么 / 下一步点哪里 / 口径边界） */
  font-size:var(--fs-caption);line-height:var(--lh-caption);color:var(--c-text-2);
}
.ai-muted{                      /* 次级文字：解释性、补充说明性内容 */
  color:var(--c-text-2);
}
/* ---- 品牌区 / 品牌徽标卡（2026-10-25 加）---------------------------------
   .ai-brand-badge 是**唯一**允许出现滚动条的构件：产品需求明确要求
   「所有展示内容限定在明确的最大框定范围内，不得溢出；超出则框内上下滑动」。
   高度上限走 --brand-badge-max-h（默认 320px），窄侧栏可在调用处覆盖。 */
.ai-brand-name{
  font-size:var(--fs-title);line-height:var(--lh-title);font-weight:650;
  color:var(--c-text);letter-spacing:-0.01em;margin:0;
}
.ai-brand-tagline{
  font-size:var(--fs-caption);line-height:var(--lh-caption);color:var(--c-text-3);margin:0;
}
.ai-brand-badge{
  max-height:var(--brand-badge-max-h,320px);
  overflow-y:auto;overflow-x:hidden;
  background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-4);margin:var(--s-2) 0;min-width:0;
  -webkit-overflow-scrolling:touch;                 /* iOS/WebView 惯性滚动 */
  overscroll-behavior:contain;                      /* 滚到头不带动整页 */
}
.ai-brand-badge img{max-width:100%;height:auto;display:block;margin:0 auto var(--s-2);}
.ai-brand-badge-cap{
  font-size:var(--fs-micro);line-height:var(--lh-micro);color:var(--c-text-3);
  margin:var(--s-1) 0 0;
}
.ai-snippet{                    /* 等宽代码块：原文片段、工具参数，横向溢出时滚动，不撑破栅格 */
  background:var(--c-subtle);border:1px solid var(--c-border);border-radius:var(--r);
  padding:var(--s-2) var(--s-3);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:var(--fs-caption);line-height:var(--lh-caption);color:var(--c-text);
  max-width:100%;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;
}
.ai-think{                      /* 思考块：左侧细线表达"这是过程不是结论"，取代原灰底 + 阴影 */
  background:var(--c-subtle);border-left:3px solid var(--c-border-strong);border-radius:var(--r);
  padding:var(--s-2) var(--s-3);color:var(--c-text-2);
  font-size:var(--fs-caption);line-height:var(--lh-caption);
  white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;
}
.ai-table-wrap{                 /* 宽表兜底：任何表格都要套一层，防窄屏横向溢出把整页顶宽 */
  max-width:100%;overflow-x:auto;
}
.ai-divider{                    /* 轻量 1px 分隔线 */
  border-top:1px solid var(--c-border);margin:var(--s-4) 0;
}
</style>"""


# ============================ 基础工具 ============================
def _esc(x) -> str:
    """统一转义，防止用户输入/PDF 文本里的 HTML 破坏页面结构或造成注入。"""
    return _escape(str(x if x is not None else ""), quote=True)


def step_text(action: str, cur=None, total=None) -> str:
    """生成带进度的 spinner 文案：`正在检索 3/8 …`（UI 规范 6）。"""
    if total and cur is not None:
        return f"{action} {int(cur)}/{int(total)} …"
    return f"{action} …"


def at(items, idx, default=None):
    """安全下标读取：越界或非法索引返回 default，杜绝 IndexError（演示加固）。"""
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return default
    try:
        if 0 <= i < len(items):
            return items[i]
    except TypeError:
        return default
    return default


# ============================ UI 规范 1：页面顶部 ============================
def _head_text(title, icon) -> str:
    """拼「图标 + 标题」，并在标题自带同一图标时**不重复叠加**。

    事故（2026-10-24 视觉快照实测）：6 个页面同时传 `page_header("🤖 xxx", icon="🤖")`，
    旧实现无条件 `f"{icon} {title}"` → 页头实渲成「🤖 🤖 智能总助手（Agent）」，
    是重启后第一眼就能看到的错位。这里按字符比对去重（忽略 emoji 变体选择符 U+FE0F，
    否则 "🕸" 与 "🕸️" 会被判成不同字符而漏判），既修当前调用点，也防后续新增页面复发。
    """
    t = str(title or "").strip()
    ic = str(icon or "").strip()
    if not ic:
        return t
    flat = lambda s: s.replace("\ufe0f", "")      # noqa: E731 — 单行归一化
    if flat(t).startswith(flat(ic)):
        return t
    return f"{ic} {t}".strip()


def page_header(title: str, subtitle: str = "", icon: str = ""):
    """统一页面顶部：标题 + 一句话说明 + 发丝线。各 page.render 第一行调用。

    视觉（2026-10-24 三次修订，用户第二次反馈「还是没变」）：取消「卡片包框 + 左侧
    3px 强调色竖条」——那是最像"老后台模板"的元素；改为**无框页头**：28px 标题、
    12px 三级灰副文案、底部 1px 发丝线分隔主区，强调色只留给交互控件与选中态。
    字号/间距全部走令牌（--fs-display / --fs-caption / --s-4 / --s-5）。

    向后兼容：旧调用 `page_header("文献库管理", "说明")` 仍可用（positional）。
    """
    head = _head_text(title, icon)
    sub = str(subtitle or "").strip()
    _ensure_tokens()
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    st.markdown(
        '<div class="ai-page-head">'
        '<div class="ai-page-title">%s</div>'
        '%s</div>' % (
            _esc(head),
            "" if not sub else '<div class="ai-page-sub">%s</div>' % _esc(sub),
        ),
        unsafe_allow_html=True,
    )


# ============================ UI 规范 1.5：统一分区标题 ============================
def section_header(title: str, desc: str = "", icon: str = ""):
    """统一分区标题（page_header 的次级形态）：18px 标题 + 副文案两级层次。

    收编原 page_library._section 与 page_graph._sec_head 两份各自为政的内联实现
    （曾各带 #3b6fb6 第二强调蓝、蓝色渐变底、6/10px 圆角、20px/10px 脱轨间距，
    且 page_graph 每次调用整段重灌 CSS）。样式全部走 _COMPONENT_CSS 的
    .ai-section* 类，自动跟随令牌；垂直节奏由 .ai-section 统一给。
    """
    head = _head_text(title, icon)
    sub = str(desc or "").strip()
    _ensure_tokens()
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    st.markdown(
        '<div class="ai-section">'
        '<div class="ai-section-title">%s</div>'
        '%s</div>' % (
            _esc(head),
            "" if not sub else '<div class="ai-section-sub">%s</div>' % _esc(sub),
        ),
        unsafe_allow_html=True,
    )


# ============================ UI 规范 4：统一表格 ============================
# 表格样式：全部引用 var(--...)，故**不需要再做 % 格式化**（原实现是 % 模板，含有
# 必须转义的 `100%%`，一旦漏写就 ValueError）。这里直接是普通字符串，`width:100%`
# 不会再炸 —— 注意：保持普通字符串的前提是绝不重新启用 % 格式化。
# 注入方式由「每次 pretty_table 都全量重灌整段」改为**会话级一次性**（见 _inject_css_once）。
# 渐变表头去掉：改中性柔和底 --c-subtle + 加深 1px 底框表层级，颜色噪音更少。
_PRETTY_CSS = """<style>
.ptab-wrap{overflow-x:auto;max-width:100%;margin:0 0 var(--s-2);
border:1px solid var(--c-border);border-radius:var(--r);}
table.ptab{border-collapse:collapse;width:100%;font-family:var(--font-sans);
font-size:var(--fs-caption);line-height:var(--lh-caption);}
.ptab th{background:var(--c-subtle);color:var(--c-text);font-weight:600;
padding:var(--s-2) var(--s-3);text-align:left;
border-bottom:1px solid var(--c-border-strong);white-space:nowrap;}
.ptab td{padding:var(--s-2) var(--s-3);border-bottom:1px solid var(--c-border);
color:var(--c-text);vertical-align:top;line-height:var(--lh-body);overflow-wrap:anywhere;}
.ptab tr:nth-child(even) td{background:var(--c-bg);}
.ptab tr:hover td{background:var(--c-accent-soft);}
</style>"""


def _to_frame(df_or_rows, columns=None) -> pd.DataFrame:
    """把 DataFrame / dict / list[dict] / list[list|tuple] 归一化成 DataFrame。"""
    if df_or_rows is None:
        return pd.DataFrame()
    if isinstance(df_or_rows, pd.DataFrame):
        df = df_or_rows.copy()
    else:
        try:
            df = pd.DataFrame(df_or_rows)
        except Exception:
            return pd.DataFrame()
    if columns:
        try:
            missing = [c for c in columns if c in df.columns]
            df = df[missing] if missing else df
        except Exception:
            pass
    return df


def pretty_table(df_or_rows, columns=None, title: str = "", max_rows: int = 200):
    """全项目统一表格：柔底表头 + 正文色文字 + 1px 细边框 + 斑马纹。

    支持 DataFrame / list[dict] / list[list]。空表自动给出友好提示而非渲染空白。
    """
    # 令牌 + 表格 CSS 都在这里一次性注入，避免每个表格重复灌同一段 <style>
    _ensure_tokens()
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    _inject_css_once(_PRETTY_CSS, _CSS_FLAG_TABLE)
    if title:
        st.markdown(f"##### {title}")
    try:
        df = _to_frame(df_or_rows, columns)
    except Exception as e:
        st.warning(f"⚠️ 表格数据解析失败，已跳过渲染：{str(e)[:120]}")
        return
    if df is None or df.empty or len(df.columns) == 0:
        st.info("📭 暂无数据可展示。")
        return
    if len(df) > max_rows:
        st.caption(f"共 {len(df)} 行，仅展示前 {max_rows} 行。")
        df = df.head(max_rows)
    try:
        body = df.to_html(index=False, classes="ptab", border=0, escape=True)
        # .ptab-wrap 复用共用类语义（宽表防撑破），表格 CSS 已一次性注入，此处不再拼 CSS
        st.markdown(f'<div class="ptab-wrap">{body}</div>', unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"⚠️ 表格渲染失败，已降级为原生表格：{str(e)[:120]}")
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================ UI 规范 3：统一输出分级 ============================
def empty_state(text: str, hints=(), icon: str = "📭"):
    """空状态统一话术：说明 + 引导操作列表（演示加固：绝不渲染空白）。"""
    st.info(f"{icon} {text}")
    hints = [h for h in (hints or ()) if h]
    if hints:
        st.caption("👉 " + "；".join(f"**{h}**" if not h.startswith(("👉", "·")) else h for h in hints))


# ==================== 异常统一包装（话术：统一所有页面的异常输出） ====================
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "errors.log")


class _MsgError(RuntimeError):
    """服务层直接给出的「人话原因」的异常包装。

    历史调用点会把**字符串**（如 ``prepared.get("error")`` = ``"打开 PDF 失败：…"``）
    当异常对象传给 friendly_error / show_error；本模块原先对它取 ``.__traceback__``
    会抛 AttributeError，冒泡到调用方的外层 except 后**又渲染一个一模一样的红框**
    （Round 12 场景 7/8 实测：2 个红框 + 真因被吞）。这里统一把非异常入参包装成
    异常对象，``str(exc)`` 与原文本完全一致，全站调用点一次性免疫。
    """


def _as_exception(exc):
    """把任意入参归一化成 BaseException 实例（已是异常则原样返回）。"""
    if isinstance(exc, BaseException):
        return exc
    if exc is None:
        return _MsgError("未提供具体错误原因")
    try:
        return _MsgError(str(exc))
    except Exception:                                   # pragma: no cover
        return _MsgError("无法解析的错误原因")


def log_exception(what, exc, extra=""):
    """把异常完整写入 logs/errors.log —— 包装只改展示方式，绝不吞掉异常。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        exc = _as_exception(exc)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s | %s: %s\n%s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), what, type(exc).__name__, exc,
                extra or "", tb[-4000:]))
    except Exception:
        pass
    return None


# 网络类异常的判定依据（Round 12 场景 S6：PubMed 的 DNS 失败
# ``URLError(socket.gaierror(11001))`` 文案是 ``<urlopen error [Errno 11001]
# getaddrinfo failed>``，既不含 "connection" 也不含 "timeout"，以前会落到
# "发生内部错误" 兜底 —— 把网络故障说成内部错误，评委问"哪里错了"答不上来）。
# 按「模块 + 类名」精确命中，避免把 requests 的 HTTPError（5xx/404）也误判成网络故障
_NET_EXC_CLASS = {
    ("requests.exceptions", "ConnectionError"),
    ("requests.exceptions", "ConnectTimeout"),
    ("requests.exceptions", "ProxyError"),
    ("requests.exceptions", "SSLError"),
    ("urllib.error", "URLError"),          # 注意：HTTPError 是 URLError 的子类，按精确类名排除
    ("socket", "gaierror"),
    ("socket", "herror"),
}
# 文案兜底：DNS 失败的 errno 在各平台写法不一，靠关键字覆盖
_NET_MSG_MARKS = ("getaddrinfo", "gaierror", "urlopen error",
                  "name or service not known", "temporary failure in name resolution",
                  "nodename nor servname", "no route to host", "network is unreachable",
                  "errno 11001", "errno 11004", "errno -2", "errno -3",
                  "failed to establish a new connection", "max retries exceeded",
                  "connection refused", "connection reset", "connection aborted",
                  "proxy error", "proxyerror", "certificate verify failed",
                  "name resolution")


def _is_network_exc(exc):
    """是否是「网络不可达 / DNS 失败 / 被代理拦截」这一类异常。"""
    try:
        if (type(exc).__module__, type(exc).__name__) in _NET_EXC_CLASS:
            return True
    except Exception:
        pass
    if isinstance(exc, (ConnectionError, socket.gaierror, socket.herror)):
        return True
    return any(k in str(exc).lower() for k in _NET_MSG_MARKS)


def friendly_hint(exc):
    """异常 → 一句用户能看懂的话（不暴露内部实现细节）。"""
    exc = _as_exception(exc)
    # 服务层已经给了人话原因（str 入参）：原样透出，别吞成"发生内部错误"
    if type(exc) is _MsgError:
        return str(exc)[:200] or "发生内部错误，请重试或换一种操作方式"
    name = type(exc).__name__.lower()
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, TimeoutError) or "timeout" in low or "timed out" in low:
        return "网络请求超时，请检查网络后重试"
    if _is_network_exc(exc):
        return "网络连接失败，请检查网络后重试（外网不可达或被代理拦截）"
    if "401" in msg or "403" in msg or "unauthor" in low or "invalid api key" in low:
        return "API Key 无效或已过期，请在左侧重新填写后重试"
    if "429" in msg or "rate limit" in low or "quota" in low:
        return "请求过于频繁或额度不足，请稍后再试"
    if isinstance(exc, ConnectionError) or "connection" in low:
        return "网络连接失败，请检查网络后重试"
    if "json" in low or "expecting value" in low:
        return "返回内容格式异常，请重试一次"
    if isinstance(exc, FileNotFoundError) or "no such file" in low:
        return "所需文件不存在或已被移动"
    if isinstance(exc, PermissionError):
        return "没有访问该文件的权限"
    if isinstance(exc, KeyError):
        return "数据结构中缺少必要字段，请刷新页面后重试"
    if isinstance(exc, ValueError):
        return "输入内容格式不符合要求，请检查后重试"
    if "error" in name:
        return "发生内部错误，请重试或换一种操作方式"
    return "发生内部错误，请重试或换一种操作方式"


def show_error(what, exc, hint="", reasons=(), actions=(), log=True):
    """统一的异常展示：⚠️ 操作失败 前缀 + 人话说明 + 技术细节折叠 + 落盘日志。

    what   —— 用户视角的操作名，如「生成回答」「打开 PDF」
    exc    —— 捕获到的异常对象（历史调用点会传 str，入口统一归一化，见 _as_exception）
    hint   —— 可选，覆盖自动推断的人话说明
    """
    exc = _as_exception(exc)
    if log:
        log_exception(what, exc)
    st.error("⚠️ 操作失败：%s。%s" % (what, hint or friendly_hint(exc)))
    if reasons:
        st.caption("🩺 可能原因：" + "；".join(str(r) for r in reasons if r))
    if actions:
        st.caption("🛠️ 怎么办：" + "；".join(str(a) for a in actions if a))
    with st.expander("🔍 查看技术细节"):
        st.code("%s: %s" % (type(exc).__name__, exc))
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if tb.strip():
            st.code(tb[-3000:])
    return None


def friendly_error(what: str, exc, reasons=(), actions=()):
    """失败统一话术（旧签名保留）：转为 show_error，保证全站前缀/折叠/日志一致。"""
    return show_error(what, exc, reasons=reasons, actions=actions)


_NET_REASONS = ["演示现场外网不可达或被代理拦截", "arXiv / PubMed 接口限流或临时不可用"]
_NET_ACTIONS = ["重试一次", "点击左侧「一键加载演示数据（6 篇）」使用离线数据继续演示",
                "检查网络/代理后再试"]


def net_error(what: str, exc, fallback_hint: str = ""):
    """网络类 API 失败的统一降级提示（页面 4 检索页专用话术）。"""
    actions = list(_NET_ACTIONS)
    if fallback_hint:
        actions.insert(1, fallback_hint)
    friendly_error(what, exc, reasons=_NET_REASONS, actions=actions)


def llm_guard(ctx, action: str = "该操作") -> bool:
    """大模型可用性守卫：未配置 Key 时给中文引导，而不是静默无反应。"""
    try:
        if getattr(ctx, "llm", None) is not None and ctx.llm.available:
            return True
    except Exception:
        pass
    st.warning(f"⚠️ {action}需要大模型，但未检测到可用 API Key。")
    st.caption("🛠️ 怎么办：在左侧「接入方式」选择平台并填写 Key；"
               "无 Key 时仍可使用本地演示数据与离线功能（解析/检索/图谱检索式演示）。")
    return False


# LLM 失败分类话术：命中 core/llm.py 落盘的 last_error 后按类型给人话指引
_LLM_ERR_KINDS = (
    ("key", ("401", "invalid_api_key", "令牌", "验证不正确", "api key", "apikey", "unauthorized"),
     "API Key 无效或已过期",
     ["Key 填写有误 / 已过期 / 被平台重置", "平台账号额度已用完"],
     ["在左侧「接入方式」重新填写正确的 Key，再点「🔌 测试连通性」验证",
      "切换到其他预设平台或本地 Ollama 继续演示"]),
    ("rate", ("429", "rate limit", "too many requests", "限流", "频率"),
     "触发平台限流（429）",
     ["请求过于频繁，平台暂时限流", "当前 Key 的并发/额度已打满"],
     ["等待 30 秒到 1 分钟后重试", "减少连续操作，或切换其他平台"]),
    ("timeout", ("timeout", "timed out", "超时"),
     "大模型调用超时",
     ["平台响应慢或网络抖动（已自动重试 3 次）", "生成内容过长导致超时"],
     ["重试一次；仍超时可换更短的问题", "检查网络/代理后重试"]),
    ("network", ("connection", "connect", "network", "网络", "ssl", "proxy"),
     "无法连接大模型服务",
     ["本机网络不可达或代理拦截了出网请求", "自定义端点 Base URL 填写有误"],
     ["检查网络/代理设置后重试", "自定义端点请核对 Base URL 与服务是否已启动"]),
)


def classify_llm_error(err) -> tuple:
    """把原始错误文本分类为 (人话摘要, 可能原因 list, 怎么办 list)；未命中给通用兜底。"""
    s = str(err or "")
    low = s.lower()
    for _kind, keys, what, reasons, actions in _LLM_ERR_KINDS:
        if any(k in low for k in keys):
            return what, list(reasons), list(actions)
    return ("大模型调用失败", ["平台返回了未分类的错误"], ["重试一次"])


def llm_failure_banner(ctx, action: str = "大模型调用"):
    """把 ctx.llm.last_error 渲染成人话警告（框架层统一消费，惠及所有页面）。

    背景：core/llm.py 调用失败（重试后仍失败，或 401/400 等不必重试的错误）
    不抛异常，只把原因写进 llm.last_error 并返回伪响应——用户看到的是
    「【LLM 暂时不可用…】」混在答案里，既不知道该怎么办，也不知道日志在哪。
    本函数在主体区顶部按错误类型给出「人话 + 可能原因 + 怎么办 + 日志位置」。
    last_error 在下一次调用成功时自动清空，横幅随之消失，无需手动关闭。
    """
    try:
        err = getattr(getattr(ctx, "llm", None), "last_error", None)
    except Exception:
        err = None
    if not err:
        return
    what, reasons, actions = classify_llm_error(err)
    try:
        from core.llm import FAIL_LOG_PATH_HINT
    except Exception:
        FAIL_LOG_PATH_HINT = "storage/llm_failures.log"
    st.warning(f"⚠️ {action}失败：{what}（点击页面内其他功能仍可继续使用）")
    st.caption("🩺 可能原因：" + "；".join(reasons))
    # 「离线演示仍可用」必须写进去：现场遇到 429（尤其 Retry-After 很长）时，
    # 只有"重试/等待"这类建议会让评委以为系统卡死了——实际本地解析 / 检索 /
    # 图谱检索式演示不受影响，可以直接继续往下讲。
    st.caption("🛠️ 怎么办：" + "；".join(actions)
               + "；**离线演示仍可用**（本地解析 / 检索 / 图谱演示不受影响，可继续演示）"
               + f"；完整错误已记录到 {FAIL_LOG_PATH_HINT}")
    with st.expander("🔧 错误摘要（供排查，已自动脱敏）", expanded=False):
        st.code(str(err)[:300])


def llm_degraded_banner():
    """离线降级结果的统一标识条（橙色 warning，绝不用成功态）。

    Round 12 场景 S1/S4 实测：主体区在 LLM 失败时渲染的是 ``✅ 分析完成`` +
    离线占位文案 —— **假成功 + 假答案**，比红字报错更危险（评委看到"分析完成"
    就会当真）。凡是走了离线占位响应的结果，都必须先渲染这条可见的降级标识。

    话术**沿用 core/llm.py:19 / DEGRADED_HINT 已定的那句**，不另造词：
    ``⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成``。

    维护注意：``friendly_hint`` 里用 ``type(exc) is _MsgError`` 精确判断，
    若将来给 _MsgError 加子类，那处要换成 isinstance，否则服务层的人话原因
    又会被吞成"发生内部错误"（= 本轮场景 7/8 的症状复发）。
    """
    st.warning("⚠️ 离线占位响应：LLM 不可用，以下内容不是模型生成，请勿作为答案引用。")
    st.caption("🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试；"
               "无 Key 时可点「一键加载演示数据（6 篇）」用本地离线链路继续演示。")


def is_llm_degraded(content) -> bool:
    """判断一段大模型产出是否为离线占位（结构化标记优先，纯文本标记兜底）。"""
    try:
        from core.llm import is_degraded, is_degraded_text
    except Exception:                                   # pragma: no cover
        return False
    try:
        if is_degraded(content):
            return True
    except Exception:
        pass
    try:
        return bool(is_degraded_text(getattr(content, "content", content)))
    except Exception:
        return False


# 「离线确定性生成」与「离线占位响应」是**两类不同的降级**，不能混用同一句话：
#   · 占位响应（core/llm.py 的 _fake_response）——什么都没算出来，纯兜底文案；
#   · 确定性生成（services/graph.py 的 _deterministic_review / explore_future）
#     ——本地图结构算出了**真实内容**，只是撰写者不是大模型。
# 后者若套用 llm_degraded_banner 的「离线占位响应」措辞，等于把真实能力说成空壳，
# 是另一种失分（评委看到"占位"就不会看内容了）。故单列一套判定与话术。
# 判定标记直接取 services/graph.py 里已经写死的那两句，不另造标记。
_OFFLINE_GEN_MARKS = (
    "LLM 不可用，以下综述由 networkx 图谱结构确定性生成",       # graph.py:842
    "LLM 不可用，以下直接使用引用图可达性的确定性判定结果",     # graph.py:1050
)


def is_llm_offline_generated(content) -> bool:
    """判断产出是否为「LLM 不可用 → 本地确定性生成」的非模型产出。"""
    text = "" if content is None else str(getattr(content, "content", content) or "")
    return any(m in text for m in _OFFLINE_GEN_MARKS)


def llm_offline_generated_banner(what: str = "内容"):
    """确定性生成结果的降级标识条：内容真实，但**不是**大模型输出。"""
    st.warning(f"⚠️ 大模型不可用：以下{what}由本地确定性算法生成，"
               f"**不是**大模型输出（内容取自本地真实数据，未编造）。")
    st.caption("🛠️ 怎么办：在左侧「接入方式」检查 API Key / 平台额度 / 网络后重试，"
               "即可改由大模型撰写。")


def show_llm_result(content, success_msg="✅ 完成", title="查看完整内容",
                    action="该操作", limit=LONG_TEXT_LIMIT):
    """渲染一次大模型产出：降级内容走**可见的降级态**，绝不使用成功态文案。

    返回 True 表示拿到了真实模型输出（调用方可继续后续成功态逻辑）；
    返回 False 表示本次是离线降级，不应报"完成 / 成功"。
    """
    text = "" if content is None else str(getattr(content, "content", content) or "")
    if is_llm_degraded(content) or "【LLM 暂时不可用" in text:
        st.warning(f"⚠️ {action}未完成：大模型暂时不可用，本次**没有**生成任何真实结果。")
        llm_degraded_banner()
        long_block(text, title=title, limit=limit)
        return False
    st.success(success_msg)
    long_block(text, title=title, limit=limit)
    return True


# ============================ UI 规范 5：长文本折叠 ============================
def long_block(text, title: str = "查看完整内容", limit: int = LONG_TEXT_LIMIT,
               expander_label: str = "📄"):
    """超过 ~300 字或 15 行的内容自动折叠；短内容直接展示。"""
    _ensure_tokens()
    t = "" if text is None else str(text)
    if not t.strip():
        st.info("📭 暂无内容。")
        return
    if len(t) <= limit and t.count("\n") < LONG_LINE_LIMIT:
        st.markdown(t)
        return
    st.markdown(t[:limit].rstrip() + " …")
    with st.expander(f"{expander_label} {title}（共 {len(t)} 字）", expanded=False):
        st.markdown(t)


# ============================ state / 异常边界 ============================
def ensure_state(defaults: dict):
    """集中初始化 session_state key：先初始化再读，杜绝 KeyError（演示加固）。

    value 为 callable 时调用它取默认值；list/dict/set 自动 copy，避免多会话共享可变对象。
    """
    ss = st.session_state
    for k, v in (defaults or {}).items():
        if k in ss:
            continue
        try:
            val = v() if callable(v) else v
            ss[k] = val.copy() if isinstance(val, (dict, list, set)) else val
        except Exception:
            ss[k] = None


def safe_call(what, fn, *args, default=None, reasons=(), actions=(), **kwargs):
    """函数级异常边界：返回 (ok, value)；失败时渲染友好错误并返回 (False, default)。"""
    try:
        return True, fn(*args, **kwargs)
    except Exception as e:
        friendly_error(what, e, reasons=reasons, actions=actions)
        return False, default


def safe_render(fn, ctx, what="该功能"):
    """页面级错误边界（增强版）：捕获异常并优雅降级，避免整页白屏崩溃。

    保持 ui/common.py 的旧调用签名 `safe_render(fn, ctx, what)`，app.py 无需改动。
    """
    try:
        fn(ctx)
    except Exception as e:
        show_error("渲染「%s」页面" % what, e,
                   reasons=["该项依赖的外部服务未就绪 / 数据尚未加载 / 网络受限"],
                   actions=["点击左侧「一键加载演示数据（6 篇）」后重试",
                            "或切换到其他页面继续演示，其余功能不受影响"])


def require_library(ctx):
    """空库守卫：文献库为空时给出引导并返回 False（页面统一调用，保持旧签名）。"""
    try:
        ok = bool(getattr(ctx, "files", None))
    except Exception:
        ok = False
    if not ok:
        # 空库是全站最高频的空状态（问答/图谱/写作页都会走到这里）：
        # 从「一行提示」升级为教学式卡片——讲清这里会出现什么、下一步点哪里。
        empty_state_card(
            "文献库为空，暂无内容可展示",
            desc="装入文献后，这里会展示基于本地文献库的问答、图谱与写作结果。",
            actions=["点击左侧「一键加载演示数据（6 篇）」，秒级装入 6 篇经典论文",
                     "或到「文献库管理」上传 PDF（自动解析 + 语义查重 + 入库）"],
            icon="📭",
        )
        return False
    return True


# ============================ 任务组 C：跨页跳转提示条 ============================
def _jump_target_label(source: str) -> str:
    """把文件名裁剪成可读标题（去掉 .pdf 后缀）。"""
    s = str(source or "")
    return s[:-4] if s.lower().endswith(".pdf") else s


def jump_banner(show_goto: bool = True):
    """消费 ui/page_qa.py 写入的 qa_jump_page，在侧边栏显示「→ 跳转到《X》第 N 页」提示条。

    只读该 key，不修改 page_qa.py；跳转动作通过 session_state["menu"] + query_params 完成。
    """
    payload = st.session_state.get(QA_JUMP_KEY)
    if not isinstance(payload, dict) or not payload.get("source"):
        return
    try:
        page = int(payload.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    para = payload.get("para_idx")
    label = _jump_target_label(payload.get("source"))
    tail = f"（第 {para} 段）" if para else ""
    with st.container(border=True, key="card01"):
        st.info(f"🎯 问答溯源待跳转：《{label}》第 {page} 页{tail}")
        if show_goto:
            c1, c2 = st.columns([1, 1])
            if c1.button("🗺️ 前往该页", key="jump_go"):
                try:
                    from ui.sidebar import MENU
                    target = at(MENU, READING_MENU_IDX, MENU[0] if MENU else "智能总助手")
                    st.session_state["menu"] = target
                    st.query_params["p"] = str(READING_MENU_IDX)
                    st.query_params["jump_page"] = str(page)
                    st.query_params["paper"] = str(payload.get("source"))
                    st.rerun()
                except Exception as e:
                    st.caption(f"跳转失败，请手动切换左侧导航：{str(e)[:100]}")
            if c2.button("✅ 已查看", key="jump_done"):
                st.session_state[QA_JUMP_KEY] = None
                try:
                    st.rerun()
                except Exception:
                    pass


# ============================ 历史组件（签名保持不变） ============================
def render_styled_table(df, title: str = ""):
    """轻量美化表格：title 可选，统一走 pretty_table 的中性柔底样式。"""
    pretty_table(df, title=title)


def embed_html(html: str, height: "int | str" = 600, scrolling: bool = True):
    """HTML 嵌入兼容层：优先 components.html，失败降级 st.iframe，再失败给友好提示。

    html      —— 传入的 HTML / srcdoc 字符串。内部会先用 design.inject_into_head()
                 塞一份 iframe 基础令牌：**CSS 变量穿透不进 iframe**（srcdoc 是独立文档，
                 取父窗口 :root 变量返回空，只会走 fallback，实测依据见 ui/design.py
                 文件头 §二）。inject_into_head 找不到 </head> 时原样返回，且不删除任何
                 原有内容 —— 外界依赖 </head> 做 replace 的脚本不受影响。
    height    —— int：像素高度兜底值 600；str：可传 "content"，走 Streamlit 1.63 的
                 LayoutConfig 官方 content-height 自动测量（scripts/_smoke_ui.py 第 8 页
                 架构图与 ui/page_misc.py:168 已在真实 AppTest 上下文实跑 0 异常）。
                 两者都是合法取值，**不是魔法数字**，改需求时请改调用方而非这里。
    scrolling —— 是否允许 iframe 内滚动。
    """
    _ensure_tokens()
    try:
        html = _design.inject_into_head(html)
    except Exception:
        pass                       # 令牌注入失败不得阻断渲染：原样用传入的 html
    import streamlit as _st
    try:
        import streamlit.components.v1 as _c
        _c.html(html, height=height, scrolling=scrolling)
    except Exception:
        try:
            _st.iframe(html, height=height)
        except Exception as e:
            st.warning(f"⚠️ 可视化组件渲染失败：{str(e)[:120]}")
            st.caption("🛠️ 怎么办：刷新页面重试；其余功能不受影响。")


def source_card(source, page, text, n=None, snippet_len=520):
    """溯源卡片：页码徽章可点击跳转阅读页（?p=3&jump_page=N&paper=文件名）。"""
    tag = f"【{n}】 " if n else ""
    # 复用全站统一的 _esc 转义（原为内联 .replace，存在重复转义逻辑），避免注入
    t = _esc(str(text or "")[:snippet_len])
    # 阅读页在 MENU 中的索引为 3（📖 版式阅读与智能翻译）
    safe_src = str(source).replace('"', "%22")
    # DOM 内 source 保持完整（AppTest 按完整文件名断言）；显示过长由 .ai-ellipsis 截断
    # 强调色底上的锚点必须显式给白字：design 层有全局 a{color:var(--c-accent)}，
    # 不覆盖的话会变成「蓝底蓝字」看不见（原实现就是这么写的，此处保留并注明原因）
    badge = (f'<a href="?p={READING_MENU_IDX}&jump_page={page}&paper={safe_src}" target="_self" '
             f'style="color:var(--c-on-accent);text-decoration:none;display:inline-block;">第 {page} 页</a>')
    # 视觉：左侧 3px 强调色竖条取代原来的 4px + 阴影；整卡走 .ai-card / 共用类 + var
    _ensure_tokens()
    return f"""<div class="ai-card" style="border-left:3px solid var(--c-accent);">
<div style="display:flex;flex-wrap:wrap;gap:var(--s-2);justify-content:space-between;
align-items:baseline;margin-bottom:var(--s-2);min-width:0;">
<strong class="ai-ellipsis" style="flex:1 1 auto;min-width:0;font-size:var(--fs-body);
line-height:var(--lh-body);color:var(--c-text);font-weight:600;">{tag}📄 {_esc(source)}</strong>
<span class="ai-chip" style="background:var(--c-accent);color:var(--c-on-accent);flex:0 0 auto;">{badge}</span></div>
<div class="ai-snippet">{t}…</div></div>"""


_ICON = {
    "plan": "🗂️", "route": "🧭", "thought": "💭", "tool": "🔧",
    "observation": "📥", "final": "✅", "guard": "🛡️", "error": "⚠️",
}


def render_trace(trace, in_expander=False):
    """把 Agent Trace 渲染为带样式的时间线。"""
    try:
        dicts = trace.to_dicts()
    except Exception as e:
        st.warning(f"⚠️ 执行轨迹解析失败：{str(e)[:120]}")
        return
    body = st.expander("🧭 Agent 执行轨迹（规划 / 路由 / 工具 / 思考）",
                       expanded=not in_expander) if in_expander else st.container(border=True, key="card02")
    with body:
        for ev in dicts:
            try:
                kind = ev.get("kind", "")
                icon = _ICON.get(kind, "•")
                if kind == "plan":
                    st.markdown(f"**{icon} {ev.get('title','')}**")
                    for i, s in enumerate((ev.get("data") or {}).get("steps", []), 1):
                        # 原 margin-left:18px 不是 8px 网格 → --s-4；#444 → 次级文字令牌
                        st.markdown("<div class='ai-muted' style='margin-left:var(--s-4);"
                                    "font-size:var(--fs-caption);line-height:var(--lh-caption);'>"
                                    f"{i}. {_esc(s)}</div>",
                                    unsafe_allow_html=True)
                elif kind == "route":
                    st.markdown(f"{icon} **{ev.get('title','')}** → `{(ev.get('data') or {}).get('expert','')}`  "
                                "<span class='ai-muted' style='font-size:var(--fs-micro);'>"
                                f"{_esc(ev.get('content',''))}</span>",
                                unsafe_allow_html=True)
                elif kind == "thought":
                    # 思考块：改共用类 .ai-think（左侧 3px 细线），删掉原来的灰底 + 自定义圆角
                    st.markdown('<div class="ai-think">'
                                f"{icon} {_esc(ev.get('content',''))}</div>",
                                unsafe_allow_html=True)
                elif kind == "tool":
                    d = ev.get("data") or {}
                    st.markdown(f"{icon} **调用工具 `{d.get('name')}`**")
                    args = d.get("args")
                    if args:
                        try:
                            st.code(json.dumps(args, ensure_ascii=False, indent=2)
                                    if not isinstance(args, str) else args, language="json")
                        except Exception:
                            st.code(str(args)[:800])
                    if ev.get("content"):
                        st.caption(f"返回：{str(ev['content'])[:240]}")
                elif kind == "guard":
                    st.warning(f"{icon} {ev.get('content','')}")
                elif kind == "error":
                    st.error(f"{icon} {ev.get('content','')}")
            except Exception as e:
                st.caption(f"（轨迹条目渲染跳过：{str(e)[:100]}）")
        finals = [e for e in dicts if e.get("kind") == "final"]
        if finals:
            content = finals[-1].get("content", "") or ""
            # 离线占位不能顶着「✅ 最终答案」出现（Round 12 S1/S4：假成功比报错更危险）
            if is_llm_degraded(content) or "【LLM 暂时不可用" in str(content):
                st.markdown("##### ⚠️ 最终答案（离线降级）")
                llm_degraded_banner()
                st.warning(str(content))
            else:
                st.markdown("##### ✅ 最终答案")
                st.success(content)


def memory_mb():
    try:
        import psutil
        import os
        return f"{psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024:.0f} MB"
    except Exception:
        return "—"


def resolve_pdf(filename):
    """按 上传目录 → 官方测试 pdf 目录 顺序解析论文原始文件；找不到返回 None。"""
    import os
    try:
        from config import UPLOAD_DIR, PDF_DIR
    except Exception:
        return None
    try:
        for base in (UPLOAD_DIR, PDF_DIR):
            p = str(base / filename)
            if os.path.exists(p):
                return p
    except Exception:
        return None
    return None


# ============================ 教学式空状态 / KPI 指标条（UI/UX 优化） ============================
# 空白页是用户流失的第一现场：整块内容区为空时如果只渲染空白，用户的第一反应是
# 「页面坏了？」而不是「我该点哪」。以下两个组件分别解决「空状态」与「信息层级」：
#   · empty_state_card()  教学式空状态卡片（说明会出现什么 + 下一步动作 + 口径边界）
#   · metric_strip()      KPI 指标条（3-5 个 hero 指标先行，明细表格后置）
# 视觉层一律引用 ui/design.py 的令牌：颜色 / 圆角 / 间距 / 字号都走 var(--...) 或
# 本模块 _COMPONENT_CSS 的共用类，装饰性阴影已删除，不引入第二套视觉语言。
@lru_cache(maxsize=4)
def brand_logo_data_uri(name: str = "icon.png", box: int = 512) -> str:
    """把品牌徽标转成 data URI，供**有界框内部**的 <img> 使用。

    为什么必须 data URI 而不是 st.image：产品需求要求「图片与文字一起限定在
    明确的最大框定范围内」，而 st.image 是 Streamlit 的顶层元素，塞不进我们自己
    的 .ai-brand-badge 容器里——那样图片就跑到框子外面，框只圈住了文字
    （scripts/_verify_brand_badge.py 第 5 步正是断言这一点）。
    大图先缩到 box 见方再编码，避免把几 MB 的 base64 塞进 HTML。
    """
    path = brand_logo(name)
    if not path:
        return ""
    try:
        import base64
        import io

        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGBA")
            if max(im.size) > box:
                im.thumbnail((box, box), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:                                    # pragma: no cover
        return ""


def _empty_state_card_html(title: str, desc: str = "", actions=(), icon: str = "🧭",
                           footer: str = "") -> str:
    """教学式空状态卡片的 HTML（纯函数，便于单测；st.* 包装见 empty_state_card）。

    借鉴 Notion / Linear「空状态即教学」：不道歉、不卖萌，讲清三件事——
    ① 这里会出现什么（desc）；② 下一步点哪里（带序号的动作）；
    ③ 必须如实说明的口径边界（footer，如演示语料规模、非真实引用）。
    与 empty_state() 的分工：后者是行内轻提示（一行信息 + 一句引导），
    本卡片用于「整块内容区为空」的首屏，视觉与 page_header 同源。

    改造：原来整卡手写背景/边框/圆角/内边距/阴影 + 15px/13px/12px 字号，
    现全部改为共用类 + var(--...)。两个被单测精确断言的字面量在外部使用者眼里
    敏感，**逐字保留**：① 序号标记 `<span>N.</span>`；② footer 的虚线分隔
    `border-top:1px dashed`（只在有 footer 时出现，无 footer 时整段不生成）。
    """
    _ensure_tokens()
    acts = [str(a) for a in (actions or ()) if str(a).strip()]
    rows = "".join(
        '<div class="ai-empty-hint" style="margin-top:var(--s-1);line-height:1.7;">'
        '<span class="ai-num">%d.</span> %s</div>'
        % (_i, _esc(_a)) for _i, _a in enumerate(acts, 1))
    _desc_html = ("" if not str(desc or "").strip() else
                  '<div class="ai-empty-hint" style="margin-top:var(--s-1);line-height:1.75;">%s</div>'
                  % _esc(desc))
    # footer 的虚线分隔线必须"有 footer 才出现"，故不进 _COMPONENT_CSS，留在行内
    _foot_html = ("" if not str(footer or "").strip() else
                  '<div style="margin-top:var(--s-2);padding-top:var(--s-2);'
                  'border-top:1px dashed var(--c-border);'
                  'font-size:var(--fs-micro);line-height:var(--lh-micro);'
                  'color:var(--c-text-2);">%s</div>'
                  % _esc(footer))
    return (
        '<div class="ai-empty" style="border-left:3px solid var(--c-accent);">'
        '<div class="ai-empty-title">%s %s</div>'
        '%s%s%s</div>'
        % (_esc(icon), _esc(title), _desc_html, rows, _foot_html))


def empty_state_card(title: str, desc: str = "", actions=(), icon: str = "🧭",
                     footer: str = ""):
    """整块内容区为空时的首屏教学卡片（empty_state 行内轻提示版保留不变）。

    典型场景：没有执行记录 / 尚未构建图谱 / 文献库为空 / 没有问答历史。
    """
    _ensure_tokens()
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    st.markdown(_empty_state_card_html(title, desc=desc, actions=actions,
                                       icon=icon, footer=footer),
                unsafe_allow_html=True)


def _metric_card_html(label: str, value: str, note: str = "") -> str:
    """单个 KPI 卡片的 HTML（纯函数，便于单测）。

    改造：卡片容器/数值/标签全部改走共用类 .ai-kpi / .ai-kpi-val / .ai-kpi-label。
    数值必须保持等宽数字（多列对齐），`font-variant-numeric:tabular-nums` 这句被
    tests/test_ui_components.py:76-82 **按字符串精确断言**，故仍显式写在内联 style 里，
    不随类上移——这是为了测试契约，不是重复实现（类里也保留了同一条）。
    """
    _ensure_tokens()
    _note_html = ("" if not str(note or "").strip() else
                  '<div class="ai-kpi-label" style="margin-top:var(--s-1);">%s</div>'
                  % _esc(note))
    return (
        '<div class="ai-kpi">'
        '<div class="ai-kpi-label">%s</div>'
        '<div class="ai-kpi-val" style="font-variant-numeric:tabular-nums;">%s</div>'
        '%s</div>'
        % (_esc(label), _esc(value), _note_html))


def _normalize_metrics(items):
    """把 (label, value[, note]) 序列归一化为三元组列表（纯函数，便于单测）。

    非法条目（长度不足 / 不可下标）直接跳过，绝不抛异常——演示现场
    指标条坏掉不能带崩整页。
    """
    out = []
    for it in items or ():
        try:
            label, value = it[0], it[1]
            note = it[2] if len(it) > 2 else ""
        except (TypeError, IndexError):
            continue
        out.append((str(label), str(value), str(note or "")))
    return out


def metric_strip(items, max_cols: int = 4):
    """KPI 指标条：hero 指标先行、明细表格后置（借鉴 Stripe / Vercel 仪表盘）。

    items: [(指标名, 数值[, 口径小字]), ...]；超过 max_cols 个时自动分行，
    避免窄屏下卡片被压成竖条。空列表时什么都不渲染。
    """
    norm = _normalize_metrics(items)
    if not norm:
        return
    _ensure_tokens()
    _inject_css_once(_COMPONENT_CSS, _CSS_FLAG_COMPONENT)
    for _start in range(0, len(norm), max_cols):
        chunk = norm[_start:_start + max_cols]
        cols = st.columns(len(chunk))
        for col, (label, value, note) in zip(cols, chunk):
            with col:
                st.markdown(_metric_card_html(label, value, note),
                            unsafe_allow_html=True)


# ============================ 任务中心（问题五/六） ============================
# 本 session 已尝试 / 已成功交付的任务 id（只放 session_state，不碰后台模块）
_SEEN_TASKS_KEY = "_task_panel_seen"
_MINE_TASKS_KEY = "_task_panel_mine"


def _task_id_set(key):
    try:
        v = st.session_state.get(key)
        if not isinstance(v, set):
            v = set()
            st.session_state[key] = v
        return v
    except Exception:
        return set()


def _render_task_result(t):
    """把后台任务记录里留存的原始结果渲染出来（刷新后页面内容不可恢复时的兜底）。"""
    res = t.get("result")
    if res is None:
        return False
    try:
        if isinstance(res, str):
            if not res.strip():
                return False
            st.markdown(res[:4000])
        elif isinstance(res, (dict, list)):
            import json as _json
            st.code(_json.dumps(res, ensure_ascii=False, default=str)[:4000], language="json")
        else:
            st.code(str(res)[:4000])
    except Exception:
        return False
    return True


def render_task_panel(tasks_mod, on_finish=None, expanded_max=3):
    """后台任务面板：运行中的任务显示当前节点与已耗时，完成后交 on_finish 消费。

    tasks_mod: services.tasks 模块（由调用方传入，避免本模块顶层依赖）。
    on_finish: 可选回调 fn(task_dict)，任务完成后第一次渲染时在主线程内调用
               （可安全写 session_state）；调用后该任务被标记 consumed 不再重复。
    使用 st.fragment 每 2 秒自动刷新；fragment 内不调用 st.rerun，避免整页抖动。
    """
    try:
        decorator = st.fragment(run_every="2s")
    except Exception:                        # 旧版本无 fragment：退化为静态渲染
        decorator = lambda f: f

    @decorator
    def _panel():
        snaps = tasks_mod.snapshot()
        active = [t for t in snaps if t["state"] in ("pending", "running")]
        recent = [t for t in snaps if t["state"] in ("done", "error")][:expanded_max]
        if not active and not recent:
            return
        if active:
            st.caption("⏳ **运行中的任务**（可切换其他页面，任务在后台继续）")
            for t in active:
                used = tasks_mod.elapsed_str((time.time() - t["started"])
                                             if t["started"] else 0)
                if t["state"] == "pending":
                    line = f"🕐 **{t['name']}** — 排队等待中（并发上限 2）"
                else:
                    line = f"⚙️ **{t['name']}** — {t['stage']}（已进行 {used}）"
                if t.get("pct") is not None:
                    st.progress(t["pct"], text=line)
                else:
                    st.markdown(line)
        for t in recent:
            used = tasks_mod.elapsed_str((t["ended"] or 0) - (t["started"] or 0))
            icon = "✅" if t["state"] == "done" else "❌"
            label = f"{icon} {t['name']} — {t['stage']}" + (f"（用时 {used}）" if used else "")
            tid = t["id"]
            fresh = not t.get("consumed")
            # 板块 1-6（非 agent）任务：完成/失败都触发一次整页重绘，让提交页的
            # bg_consume 当场交付结果——否则用户提交后没有其他交互，页面主体会
            # 一直停在旧状态，只有侧边栏进度在跳。每任务仅一次（tid 先进 seen），
            # RerunException 继承 BaseException，不会被 render_task_panel 外层的
            # except Exception 吞掉（2026-10-22 实证 MRO）。
            if t.get("kind") != "agent" and fresh and tid not in _task_id_set(_SEEN_TASKS_KEY):
                _task_id_set(_SEEN_TASKS_KEY).add(tid)
                try:
                    st.rerun(scope="app")
                except Exception:
                    pass
            if t["state"] == "error":
                with st.expander(label):
                    st.caption("失败原因：" + str(t.get("error", ""))[:200])
                continue
            mine = _task_id_set(_MINE_TASKS_KEY)
            if on_finish is not None and fresh and tid not in _task_id_set(_SEEN_TASKS_KEY):
                # 每个 session 只尝试交付一次：回调返回 False（结果为空 / 写入失败）
                # 时**不**标记 consumed，避免"标记了但页面里其实没有"。
                _task_id_set(_SEEN_TASKS_KEY).add(tid)
                try:
                    ok = bool(on_finish(t))
                except Exception:
                    ok = False
                if ok:
                    mine.add(tid)
                    tasks_mod.mark_consumed(tid)
                    # 结果已写入 session_state；fragment 内触发整页重绘，
                    # 让页面主体（历史记录 / 下载区）立即显示新结果。
                    try:
                        st.rerun(scope="app")
                    except Exception:
                        pass
            with st.expander(label, expanded=fresh):
                if tid in mine:
                    st.caption("结果已转入页面内容（历史记录 / 下载区）。")
                elif t.get("kind") != "agent":
                    # 非 agent 任务的结果由提交页按 kind 认领（见下方 bg_consume），
                    # 不经 on_finish/mine 通道。不套用"上一个会话"兜底话术，
                    # 也不倾倒原始结果——那可能是含全文/raw 字节的大 dict。
                    st.caption("结果由对应功能页拾取；若该页未见更新，切换到提交任务的那个页即可。")
                else:
                    # Round 12 场景 12 实测：刷新后新 session 的 agent_runs=0，
                    # 历史记录区还是新手引导，旧文案「结果已转入页面内容」指向一个
                    # 不存在的地方。改成诚实说法，并把后台任务记录里留存的结果直接渲染出来。
                    # 前缀统一用 ⚠️：这是"降级 / 不可恢复"语义，用 ℹ️ 会被当成普通信息
                    st.caption("⚠️ 该结果属于上一个会话：浏览器刷新后页面内容不可恢复，"
                               "以下是后台任务留存的原始结果（可复制保存）。")
                    if not _render_task_result(t):
                        st.caption("⚠️ 该任务没有留存可展示的结果载荷。")

    try:
        _panel()
    except Exception:
        pass


# ==================== 后台任务提交 / 消费（板块 1-6 长任务后台化） ====================
# 背景（用户 2026-10-22 要求）："0 到 6 的板块中的任务任何一项都是可以同时运行的，
# 而不只是 0 板块"。services/tasks.py 的线程池（MAX_PARALLEL=2 + 排队 + 进度回调）
# 本就支持并发，缺的只是各功能页的接入。公共件三件：
#   bg_submit()   点击处提交长任务，tid 登记进 session_state 待办队列
#   bg_consume()  页面渲染顶部按 kind 认领交付（一次性 + 触发重绘让 flash 生效）
#   bg_running()  提交按钮防重复闸门（只数本页 kind，不拦其他板块的并发）
#
# 硬约束（services/tasks.py 模块 docstring）：后台线程内禁止任何 st.* 调用——
# 页面传入的 fn 只允许写 progress 回调与返回值。
#
# 两个设计坑（都踩过/规避过）：
# 1) 交付必须按 kind 路由：待办队列是全会话共享的，若每页都无脑消费，
#    A 页提交的任务可能在 B 页被吞掉（结果丢失）。bg_consume 只认领
#    handlers map 里有的 kind，其余留给所属页。
# 2) 交付后必须重绘：flash 由 app.py 在页面渲染前重放，本页刚写入的提示
#    不重绘就要等下一次用户交互才可见。RerunException 继承 BaseException
#    （2026-10-22 实证 MRO），不会被 except Exception 误吞。
_BG_TASK_IDS_KEY = "_bg_task_ids"


def bg_submit(name, fn, kind="generic", goal=""):
    """提交后台任务并登记 tid 到待办队列；返回 tid。"""
    from services import tasks as _tasks
    tid = _tasks.submit(name, fn, kind=kind, goal=goal)
    try:
        st.session_state.setdefault(_BG_TASK_IDS_KEY, []).append(tid)
    except Exception:
        pass
    return tid


def bg_running(kinds=None):
    """是否有未完成后台任务；``kinds`` 限定只数某些 kind（页面防重复提交用）。

    不传 kinds = 数全部。跨板块并发是特性，只有"本页同一类任务已在跑"
    才禁用按钮——避免同一任务被重复提交把并发队列打爆。
    """
    from services import tasks as _tasks
    try:
        ids = st.session_state.get(_BG_TASK_IDS_KEY) or []
    except Exception:
        return False
    for tid in ids:
        try:
            t = _tasks.get(tid)
        except Exception:
            t = None
        if t is None or t["state"] not in ("pending", "running"):
            continue
        if kinds is None or t.get("kind") in kinds:
            return True
    return False


def bg_consume(handlers):
    """页面渲染顶部调用：按 kind 把已完成的后台任务交付给本页处理器。

    handlers: ``{kind: fn(kind, task_dict)}``。只认领 map 里有的 kind，
    其余任务留在队列等所属页来取（用户在 A 页提交、切到 B 页，任务完成时
    B 页不得吞掉它）。每个任务一次性交付（无论成败），交付后触发一次整页
    重绘让 flash 生效。
    fn 内部自行区分 done / error 两态（``task["state"]`` / ``task["result"]`` /
    ``task["error"]``）；同类多任务同一轮完成时 flash 只保留最后一条
    （单槽位），重要结论请同时写入页面状态而非只依赖 flash。
    """
    from services import tasks as _tasks
    try:
        ids = st.session_state.setdefault(_BG_TASK_IDS_KEY, [])
    except Exception:
        return
    delivered = False
    for tid in list(ids):
        try:
            t = _tasks.get(tid)
        except Exception:
            t = None
        if t is None or t["state"] in ("pending", "running"):
            continue
        fn = handlers.get(t.get("kind"))
        if fn is None:
            continue                          # 不是本页认领的 kind，留给所属页
        try:
            ids.remove(tid)
        except ValueError:
            continue
        delivered = True
        try:
            fn(t.get("kind"), t)
        except Exception as e:                # RerunException 走 BaseException，不会被这里捕获
            try:
                from ui.sidebar import set_flash
                set_flash("error", "⚠️ 后台任务结果处理失败：%s" % str(e)[:120])
            except Exception:
                pass
    if delivered:
        try:
            st.rerun()
        except Exception:
            pass
