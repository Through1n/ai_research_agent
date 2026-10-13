# Round 12 · 技术文档配套 9 张界面截图重拍报告

- 执行人：shot-retake
- 日期：2026-10-13
- 环境：conda env `ai_research`（Python 3.11 / streamlit 1.63.0），应用以 `streamlit run app.py --server.headless true --server.port 8599` 启动，`/_stcore/health` 200 后采集。
- 采集方式：playwright（Chromium headless，视口 1440×900，`device_scale_factor=2`，出图 2880px 宽）。全部通过**真实 UI 操作**完成（侧边栏导航点击 + 页面按钮点击 + 真实 LLM / arXiv 调用），未改任何业务代码，未动 `.env`。
- 采集脚本：`scripts/_reshoot_shots.py`（本任务独占文件，可一键复跑，用法见文末）。

## 一、9 张图逐张确认（均经 Read 读图自查，非空图、非串页）

| 文件 | 页面 | 尺寸(px) | 大小 | 自查确认的内容 |
|---|---|---|---|---|
| `docs/shots/p1.png` | 0. Agent 总助手 | 2880×7180 | 1810 KB | 示例任务「对比 Transformer 和 BERT 的架构承袭关系」真实后台执行完成（任务中心显示 ✅ 已完成·用时 1 分 20 秒）；运行卡片含 ✅ 已完成 · 📚 5 条来源；最终答案带【n】内联引用；「答案来源」5 张溯源卡（1810.04805v2.pdf / 1706.03762v7.pdf / UnderstandingDeepLearning 等，带页码徽章）；已展开「🧠 计划 / 路由 / 思考过程」（任务规划 Task Planning、多智能体 Routing、逐轮思考含工具调用与返回）；已展开「🔍 查看 Agent 执行轨迹」（多智能体路由 Routing→任务规划→调用工具 search_literature 及 JSON 参数/返回/耗时）。Function Calling 闭环 + Trace 可视化证据齐全。 |
| `docs/shots/p0.png` | 1. 文献库管理 | 2880×2120 | 427 KB | 「版本差异」页签：识别出同一论文 1706.03762 库中有 3 版（1706.03762v7.pdf、1706.03762v1.pdf、1706.03762v1_img.pdf），建议保留最新版 v7；「离线客观统计（可直接复核）」对比表：文档规模(页数) 15/15/15、切块数 37/35/42、正文字数 43,519/40,566/45,391、参考文献条目 57/51/51；下方「版本差异报告（1706.03762）（共 2987 字）」折叠区（真实 LLM 生成）。语义去重 + 版本管理（v1–v7）证据直接可见。 |
| `docs/shots/p2.png` | 2. 跨文献问答与溯源 | 2880×5510 | 934 KB | 真实提问「对比 Transformer 与 BERT 的架构（表格）」，答案为分维度对比表格（模型架构/自注意力机制/编码器结构/预训练任务/参数规模/应用领域），带【n】角标；「📚 答案来源」已展开：6 张来源卡片，每张含【n】角标、来源相似度、**原文页码定位（如"原文第 0-1 页"）与「📖 定位第 X 页」跳转按钮**；页码级溯源锚点证据齐全。另：本问触发了「幻觉防护校验未完全通过」黄色提示（角标与来源文本词汇重叠 15%）——这是防幻觉机制的真实如实展示，非缺陷画面。 |
| `docs/shots/p3.png` | 3. 版式阅读与智能翻译 | 2880×3430 | 668 KB | 「🌐 中英同步滚动」页签：对 1706.03762v7.pdf 第 1 页执行「📥 加载并逐段翻译（段落对齐）」，提示「逐段翻译：16 段调用（并发 ≤3，单次 ≤60s）峰值内存 819 MB / 耗时 19.3 秒」（真实 LLM 翻译）；中英对照双栏逐段对齐渲染，左英右中、无串栏，页面含「中英同步滚动（按比例 · 悬停/点击联联动）」悬浮提示（同步滚动机制标识）。 |
| `docs/shots/p6.png` | 4. 论文检索与追踪 | 2880×4716 | 698 KB | 「检索与下载」页签 arXiv 真实检索："graph neural network" → ✅ 检索到 5 篇论文，列表含真实标题/发表日期/arXiv 编号（2606.04964v1、1909.10061v3、2502.03549v1、1801.02421v2、2306.24733v1）、摘要折叠区与「⬇️ 下载并入库」按钮。 |
| `docs/shots/p4.png` | 5. 引用图谱与综述 | 2880×5206 | 820 KB | 「🕸️ 构建引用图谱」真实建图完成：vis-network 图渲染出 BERT→Attention Is All You Need 引用边，图例（基石论文/核心节点/边缘衍生）与节点标签**中文渲染正常，无方框**；图论指标表（PageRank/被引次数(入度)=1/中介中心性/特征向量）；🏆 基石论文《Attention Is All You Need》、🌿 边缘衍生《BERT…》；「✅ 领域综述已生成」提示与综述正文（一、研究脉络（时间线）…）已生成（真实 LLM）；「🗓️ 时间线与主题簇（networkx 确定性计算）」「🔗 引用关系（1 条）」可见。**语料规模坦诚说明**黄条为系统如实提示（2 个逻辑节点），保留。 |
| `docs/shots/p5.png` | 6. 写作辅助与可视化 | 2880×7974 | 1051 KB | 「📂 载入示例数据」后 CSV 自动出图：🤖 自动推荐图表（3 个，基于列类型推断）——params_M 柱状图、数值列相关性热力图、平行坐标图（plotly 真实渲染）；第一张图已生成真实 LLM **Figure Caption**（含具体数值引用）；页面下方另有无幻觉论文框架 / 架构流设计 / 学术翻译区块。 |
| `docs/shots/p7.png` | 系统架构（讲解页） | 2880×2174 | 447 KB | 四层纯 HTML/CSS 架构图：①交互层·Streamlit 多页应用 → ②Agent 内核（Guardrails/Task Planning/Multi-Agent Router/Memory + Function Calling 闭环 ReAct→工具执行→观察→Trace 可视化）→ ③工具/服务层 → ④存储层，与 `docs/technical_doc.md` 第 3 章同源（页面由 `ui/page_misc.py` 渲染同一份 `_ARCH_HTML`）。 |
| `docs/shots/p8.png` | 技术创新亮点（讲解页） | 2880×2438 | 568 KB | 12 项创新卡片完整可见（真正的 Agent 架构 / 多模型 Provider 抽象层 / 跨语言混合检索抗幻觉 / 内容语义去重 / 真实引用关系解析 / 基石边缘图论挖掘 / Future Work 前沿过滤 / 中英同步滚动阅读 / 无幻觉写作 / 动态追踪与个性化推荐 / 隔离代码执行 / 工程可复现），与第 7 章同源（`ui/page_misc.py` `_INNOV`）。 |

## 二、API Key 明文自查结论：✅ 全部通过

- 每张图截图前，脚本对 **主文档 + 全部 iframe 的可见文本（innerText）+ 全部 `<input>` 的 value 属性与 DOM value** 做扫描，与 `.env` 中 4 个真实 Key 逐串比对，**未发现任何明文**（脚本输出：每张图 `[leak-check …] OK（4 个 Key 无明文）`）。
- 结构性保障：侧边栏 Key 输入控件为 `type="password"`（圆点态）且当前 Key 走环境变量注入、输入框 value 为空；「⚙️ 模型接入与账号」折叠区默认收起，截图中不含任何 Key 相关控件展开态。
- 本报告、采集脚本与日志均未写入任何 Key 字符串（脚本只读 `.env` 做内存比对，从不打印）。

## 三、中文渲染检查（p4 图谱）：✅ 正常

- p4 走的是「零依赖 DOT→SVG」的 **vis-network 浏览器渲染链**（`services/graph.py: graph_html()` 内联 `lib/vis-9.1.2`，未走 matplotlib）：图例「基石论文 / 核心节点 / 边缘衍生」与悬浮提示中文全部正常，无方框乱码。节点文字为论文英文原标题（Attention Is All You Need / BERT…），属预期。
- 其余 8 张图为 Streamlit 原生组件渲染，中文均正常。

## 四、如实说明 / 需人工定夺的项

1. **p6 无"被引数据"列**：检索页（arXiv/PubMed）本身不提供被引次数（arXiv API 无该字段），本页证据为**真实检索结果列表**；"真实被引数据"由 **p4 图论指标表的「被引次数(入度)」列** 承担（BERT→Attention 真实引用解析得入度=1）。文档图注如需强调被引数据，建议指向 p4。
2. **p0 单屏无法同时覆盖三个卖点**：语义去重/版本管理（v1–v7）与"细粒度解析信息"分布在该页三个页签。已选「版本差异」页签（去重+版本管理证据最直接）作为 `p0.png`。**备选**「上传与解析」页签整页截图（含 元数据 JSON 真实抽取 + 🧩 全方位解析「标题树 22 项 · 表格 2 个 · 插图 3 张」+ 插图）已拍好，保存在 `C:/Users/Administrator/reshoot_backup/candidates/p0_up.png`（2880×15646，仓库外，避免污染 docs/）；如文档需要"细粒度解析"视角，用 `--only p0` 重跑后把该候选覆盖到 `p0.png` 即可。另有 `p0_lib.png`（文献库 5 篇条目表：篇名/版本 v7·v1·v2/年份/片段数）备选。
3. **p1 底部裁剪**：Agent 执行轨迹时间线会大量重复「任务规划（更新）」块（17+ 步，整页 14000+ px）。成图保留到首个工具调用完成卡为止（7180px），重复段已裁去；脚本复跑时会自动做同样裁剪。
4. **p2 的黄色"幻觉防护校验未完全通过"提示**：为防幻觉机制对该次回答的如实输出（词汇重叠率 15%），保留了该状态——它本身是卖点证据；如文档希望展示"校验通过"绿条，换个问法重拍 `--only p2` 即可。
5. **arXiv 限流（HTTP 429）**：采集期间 export.arxiv.org 对本机 IP 多次限流（可能与并行任务共用出口有关），脚本已带重试逻辑并最终拿到真实结果；如复跑遇到持续 429，脚本会回退拍 PubMed 真实结果并在日志注明。
6. 演示数据加载按钮已按流程点击（库内已有 5 篇真实论文，含 1480 片段，加载为幂等操作）。

## 五、采集脚本用法（一键复跑）

```bash
# 1) 启动应用（ai_research 环境）
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe -m streamlit run app.py \
    --server.headless true --server.port 8599 --server.address 127.0.0.1

# 2) 全量重拍（覆盖 docs/shots/p0-p8.png，约 20-30 分钟，含真实 LLM 调用）
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_reshoot_shots.py

#    只重拍部分页面（逗号分隔，可选 demo,p0,p1,p2,p3,p4,p5,p6,p7,p8）
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_reshoot_shots.py --only p4,p6

#    指定端口/输出目录
C:/Users/Administrator/anaconda3/envs/ai_research/python.exe scripts/_reshoot_shots.py \
    --base http://127.0.0.1:8599 --out docs/shots
```

要点：
- 脚本按真实用户路径操作（侧边栏导航点击，不用 URL 硬跳，保证 session_state 连续）；每张图截图前自动做 **Key 明文自查**，发现泄漏立即终止。
- 截图采用「临时拉高视口」方案而不是 `full_page=True`（Streamlit 滚动发生在内部容器，`full_page` 会从当前滚动位置截起导致页头丢失）。
- p0 会产出 3 张候选（上传与解析 / 版本差异 / 文献库），p3 产出 2 张候选（原生渲染 / 中英同步滚动），落在 `C:/Users/Administrator/reshoot_backup/candidates/`，正式文件以本报告第一节所述选择为准。
- 旧版 9 张图（2026-10-11 摄于改版前）已备份到 `C:/Users/Administrator/reshoot_backup/`。

## 六、文件清单（本次改动）

- 覆盖替换：`docs/shots/p0.png` … `docs/shots/p8.png`（9 张）
- 新增：`scripts/_reshoot_shots.py`（采集脚本）
- 未触碰：`docs/technical_doc.md`、`ui/**`、`core/**`、`services/**`、`.env`、Docker 三件套等一切他人文件；未执行 git commit。
