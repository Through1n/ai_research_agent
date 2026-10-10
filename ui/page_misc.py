"""系统架构设计与技术创新亮点页面。"""
import streamlit as st
from ui.common import embed_html


_ARCH = """flowchart TD
  U[用户 自然语言意图] --> UI[Streamlit 交互层<br/>PDF阅读/图谱/对话/可视化]
  UI --> AC[Agent 内核]
  subgraph CORE[Agent Core]
    RT[Guardrails 安全防护]
    PL[Task Planning 任务规划]
    RR[Multi-Agent Router 专家路由]
    MM[Memory 记忆: 短期/长期/画像]
    LOOP[Function Calling 闭环 ReAct]
    PL --> RR --> LOOP
    MM -.上下文.-> LOOP
    RT -.校验.-> LOOP
  end
  AC --> CORE
  LOOP --> T1[PDF解析/OCR]
  LOOP --> T2[混合检索 RAG]
  LOOP --> T3[arXiv/PubMed/Web]
  LOOP --> T4[引用图谱/图论]
  LOOP --> T5[写作/绘图/代码执行]
  T2 --> VDB[(Chroma 向量库)]
  T2 --> BM[(BM25 稀疏索引)]
  T4 --> NX[(NetworkX)]
  T3 --> NET[(互联网 学术API)]
"""


def render_arch(ctx):
    st.header("🏗️ 系统架构设计")
    embed_html(
        f'<div class="mermaid">{_ARCH}</div>'
        '<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";'
        'mermaid.initialize({startOnLoad:true});</script>', height=720, scrolling=True)
    st.markdown("""
**分层与数据流**
1. **交互层**：Streamlit 多页应用，承载 PDF 原生阅读、中英同步滚动、图谱可视化、对话与图表。
2. **Agent 内核**：Guardrails 注入/密钥校验 → Task Planning 动态拆解 → Multi-Agent Router 专家路由 →
   Function Calling 闭环执行，Memory 提供短期对话、长期笔记与用户画像。
3. **工具/服务层**：PDF 解析与 OCR、Dense+Sparse 混合 RAG、arXiv/PubMed/Web 集成、NetworkX 图谱、
   写作绘图与隔离 Python 执行。
4. **存储层**：Chroma 持久化向量库、BM25 索引、文献库元数据 JSON、原始 PDF 与图片。
""")


_INNOV = [
    ("真正的 Agent 架构", "任务规划 + 多专家路由 + Function Calling 闭环，而非按钮式脚本；全程 Trace 可视化。"),
    ("混合检索抗幻觉", "BM25(稀疏)+向量(稠密) RRF 融合，可选 LLM 重排；答案内联【n】角标并给原文页码锚点。"),
    ("内容语义去重", "基于论文级 embedding 相似度识别 arXiv v1/v7（非文件名），同号版本规则 + 版本差异表格。"),
    ("真实引用关系解析", "用标题 token 窗口匹配 + arXiv 号识别论文间真实引用，逻辑著作层建图。"),
    ("基石/边缘图论挖掘", "PageRank、入度、中介、特征向量中心性，自动定位基石论文与边缘衍生工作。"),
    ("Future Work 前沿过滤", "按时间线比对后续论文摘要，自动标注/排除已被解决的旧 idea。"),
    ("中英同步滚动阅读", "JS 按比例 + 段落对齐双机制联动，悬停联动高亮；双栏坐标级原生渲染。"),
    ("无幻觉写作", "框架的 References 只引用本地真实文献，不足处占位；生成 Mermaid/TikZ/Graphviz/Matplotlib。"),
    ("自主检索与复现推荐", "arXiv/PubMed/Web + 自动下载入库，优先推荐带 GitHub 开源代码的论文。"),
    ("隔离代码执行", "子进程 Python 环境执行数据处理脚本并回传结果，形成可复现闭环。"),
    ("工程可复现", "模块化包结构、requirements、.env 模板、Docker、一键启动 bat，密钥经环境变量隔离。"),
]


def render_innov(ctx):
    st.header("💡 技术创新亮点")
    for i, (t, d) in enumerate(_INNOV, 1):
        st.markdown(f"**{i}. {t}**\n\n{d}")
