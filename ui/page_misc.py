"""系统架构设计与技术创新亮点页面。"""
import pandas as pd
import streamlit as st

from ui.common import embed_html, page_header, pretty_table, long_block, friendly_error

# 讲稿式的完整分层说明（较长，统一折叠，避免首屏过长）
_ARCH_DETAIL = """
**分层与数据流**
1. **交互层**：Streamlit 多页应用，承载 PDF 原生阅读、中英同步滚动、图谱可视化、对话与图表。
2. **Agent 内核**：Guardrails 注入/密钥校验 → Task Planning 动态拆解 → Multi-Agent Router 专家路由 →
   Function Calling 闭环执行，Memory 提供短期对话、长期笔记与用户画像。
3. **工具/服务层**：PDF 解析与 OCR、Dense+Sparse 混合 RAG、arXiv/PubMed/Web 集成、NetworkX 图谱、
   写作绘图与隔离 Python 执行；大模型经 Provider 抽象层多家热切换。
4. **存储层**：Chroma 持久化向量库、BM25 索引、文献库元数据 JSON、原始 PDF 与图片。
"""

# 纯 HTML/CSS 分层架构图：不依赖任何外部 CDN，评审离线环境同样可渲染
_ARCH_HTML = """
<style>
.arch{font-family:'Segoe UI','Microsoft YaHei','PingFang SC','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif;padding:14px;background:#f7f9fc;}
.layer{border-radius:10px;padding:10px 14px;margin:0 auto 6px;max-width:880px;border:1.5px solid;}
.lname{font-weight:700;font-size:14px;margin-bottom:6px;}
.chips{display:flex;flex-wrap:wrap;gap:6px;}
.chip{background:#fff;border:1px solid #c9d6e8;border-radius:6px;padding:4px 10px;font-size:12px;color:#1d2733;}
.arr{max-width:880px;margin:0 auto;text-align:center;color:#5c7ba3;font-size:14px;line-height:1.4;}
.loop{border:1.5px dashed #1f5fae;border-radius:8px;padding:5px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.fn{background:#fff;border:1px solid #b4d0ee;border-radius:18px;padding:3px 12px;font-size:12px;}
.fa{color:#1f5fae;font-weight:700;}
</style>
<div class="arch">
 <div class="layer" style="border-color:#9db8d9;background:#eaf1fa;">
  <div class="lname" style="color:#173a63;">① 交互层 · Streamlit 多页应用</div>
  <div class="chips"><span class="chip">Agent 总助手</span><span class="chip">文献库管理</span><span class="chip">跨文献问答与溯源</span><span class="chip">版式阅读·同步滚动翻译</span><span class="chip">检索·追踪·推荐</span><span class="chip">引用图谱与综述</span><span class="chip">写作辅助与可视化</span></div>
 </div>
 <div class="arr">▼ 意图驱动 &nbsp;▲ 流式结果</div>
 <div class="layer" style="border-color:#1f5fae;background:#f2f8fe;">
  <div class="lname" style="color:#1f5fae;">② Agent 内核（自研编排）</div>
  <div class="chips" style="margin-bottom:6px;">
   <span class="chip">Guardrails 安全防护 / 密钥隔离 / 引用校验</span>
   <span class="chip">Task Planning 动态任务拆解</span>
   <span class="chip">Multi-Agent Router 专家路由</span>
   <span class="chip">Memory 短期对话 / 长期笔记 / 用户画像</span>
  </div>
  <div class="loop"><span class="fn">Function Calling 闭环 ReAct</span><span class="fa">→</span><span class="fn">工具执行</span><span class="fa">→</span><span class="fn">观察</span><span class="fa">→</span><span class="fn">Trace 可视化</span></div>
 </div>
 <div class="arr">▼ 工具调用</div>
 <div class="layer" style="border-color:#8fb98f;background:#f0f7f0;">
  <div class="lname" style="color:#2f5d2f;">③ 工具 / 服务层</div>
  <div class="chips"><span class="chip">PDF 解析 + 多模态 OCR</span><span class="chip">Dense+Sparse 混合检索 RAG</span><span class="chip">arXiv / PubMed / Web</span><span class="chip">引用图谱 + 图论(NetworkX)</span><span class="chip">写作 / 绘图 / 沙箱代码执行</span><span class="chip">多模型 Provider（智谱·DeepSeek·通义·OpenAI·Ollama）</span></div>
 </div>
 <div class="arr">▼ 读写</div>
 <div class="layer" style="border-color:#c9b28a;background:#faf5ec;">
  <div class="lname" style="color:#7a5c1e;">④ 存储层</div>
  <div class="chips"><span class="chip">Chroma 向量库</span><span class="chip">BM25Plus 稀疏索引</span><span class="chip">文献库 / 记忆 / 点赞 JSON</span><span class="chip">原始 PDF 与提取图片</span></div>
 </div>
</div>
"""


def render_arch(ctx):
    page_header("🏗️ 系统架构设计",
                "四层架构：交互层 → Agent 内核 → 工具/服务层 → 存储层，数据流与 Function Calling 闭环。",
                icon="🏗️")
    try:
        embed_html(_ARCH_HTML, height=620, scrolling=False)
    except Exception as e:
        friendly_error("架构图渲染", e,
                       reasons=["内嵌 HTML 组件不可用"],
                       actions=["刷新页面重试", "下方文字版分层说明不受影响"])
    # 分层结论与闭环流程已由上方架构图 + 页头副标题表达，此处不再重复色块，只保留完整版折叠
    with st.expander("📐 分层与数据流（完整版）", expanded=False):
        st.markdown(_ARCH_DETAIL)


# (标题, 卡片一句话, 完整说明)：首屏只展示前两项，完整说明收进折叠表格
_INNOV = [
    ("真正的 Agent 架构",
     "任务规划 + 专家路由 + Function Calling 闭环，全程 Trace 可视化。",
     "任务规划 + 多专家路由 + Function Calling 闭环，而非按钮式脚本；全程 Trace 可视化。"),
    ("多模型 Provider 抽象层",
     "智谱/DeepSeek/通义/OpenAI/Ollama 侧边栏热切换，支持内网自定义网关。",
     "智谱/DeepSeek/通义千问/OpenAI/本地 Ollama 侧边栏热切换（OpenAI 兼容协议）；无视觉能力的 Provider 自动降级 OCR 链路，LLM_PROVIDERS_EXTRA 支持内网自定义网关。"),
    ("跨语言混合检索抗幻觉",
     "中英查询扩展 + Dense/BM25Plus 融合，答案内联【n】角标与原文页码。",
     "查询扩展（LLM + 中英术语表双通道）弥合中文提问与英文文献的词表鸿沟；Dense+BM25Plus RRF 融合，可选 LLM 重排；答案内联【n】角标并给原文页码锚点。"),
    ("内容语义去重",
     "按内容指纹识别 arXiv 同文多版本（v1/v7），并给出版本差异表。",
     "论文级 embedding + 分段内容指纹交叉匹配识别 arXiv v1/v7（不依赖文件名），同号版本规则 + 版本差异表格。"),
    ("真实引用关系解析",
     "References 锚点 + 标题倒排索引，兼容 IEEE/Nature 排版。",
     "References 标题锚点定位 + 标题 token 倒排索引候选 + 语义兜底，识别论文间真实引用，兼容 IEEE/Nature 排版。"),
    ("基石/边缘图论挖掘",
     "PageRank 与多中心性定位基石论文，20+ 篇秒级建图。",
     "PageRank、入度、中介、特征向量中心性，自动定位基石论文与边缘衍生工作；20+ 篇规模秒级建图。"),
    ("Future Work 前沿过滤",
     "按时间线比对后续论文，排除已被解决的旧 idea。",
     "按时间线比对后续论文摘要，自动标注/排除已被解决的旧 idea。"),
    ("中英同步滚动阅读",
     "按比例 + 段落对齐双机制联动，悬停高亮。",
     "JS 按比例 + 段落对齐双机制联动，悬停联动高亮；双栏坐标级原生渲染。"),
    ("无幻觉写作",
     "References 只引本地真实文献，可出 Mermaid/TikZ/Graphviz/Matplotlib。",
     "框架的 References 只引用本地真实文献，不足处占位；生成 Mermaid/TikZ/Graphviz/Matplotlib。"),
    ("动态追踪与个性化推荐",
     "arXiv 订阅增量推送 + 画像加权推荐，附推荐理由。",
     "arXiv 订阅 + last-seen 检查点增量推送；点赞/踩 + 用户画像 + 开源代码加权排序，附推荐理由。"),
    ("隔离代码执行",
     "AST 白名单护栏 + 子进程沙箱跑数据处理脚本。",
     "AST 白名单护栏（危险 import/系统调用静态拦截）+ 子进程沙箱执行数据处理脚本，形成可复现闭环。"),
    ("工程可复现",
     "模块化 + 版本锁定 + Docker 一键启动，密钥只走环境变量。",
     "模块化包结构、requirements 版本锁定、.env 模板、Docker、一键启动 bat，密钥经环境变量隔离、绝不进前端。"),
]


def render_innov(ctx):
    page_header("💡 技术创新亮点",
                "12 项工程与算法创新：Agent 闭环、多模型 Provider、混合检索抗幻觉、图论挖掘等。",
                icon="💡")
    # 卡片化：两列网格（行优先，编号从左到右读），每卡「标题 + 一句话」
    for _i in range(0, len(_INNOV), 2):
        for _col, _idx in zip(st.columns(2), (_i, _i + 1)):
            if _idx >= len(_INNOV):
                continue
            _t, _s, _ = _INNOV[_idx]
            with _col:
                with st.container(border=True):
                    st.markdown(f"**{_idx + 1}. {_t}**\n\n{_s}")
    # 完整说明不丢，只从首屏收进折叠区
    with st.expander(f"📋 完整说明（{len(_INNOV)} 项）", expanded=False):
        rows = [{"#": i, "创新点": t, "说明": d} for i, (t, _s, d) in enumerate(_INNOV, 1)]
        pretty_table(pd.DataFrame(rows))
