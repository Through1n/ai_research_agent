# Round 3 修复报告 · 修复工程师 R4（交付物：技术文档 + README + Docker 安全）

- 修复时间：2026-10-11
- 运行环境：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（Python 3.11.16 / streamlit 1.63.0）
- 独占改动文件（未越界）：
  - `README.md`（重写）
  - `docs/technical_doc.md`（重写）、`docs/技术文档.html`（重新生成）、`docs/技术文档.pdf`（重新生成）
  - `.dockerignore`（**新建**）
  - `docs/shots/p0.png ~ p8.png`（重拍覆盖，未删除任何已有文件；`test0.png` 未引用、未动）
- 未改动：任何 `.py` 文件、`config.py`、`requirements.txt`、`Dockerfile`、`docker-compose.yml`、`docs/review/*`、`docs/audit_report_20261010.md`、`docs/演示视频脚本.md`、`docs/desktop_guide.md`
- 临时脚本（项目外）：`C:/tmp/r4/`（`md2html.py` / `html2pdf.py` / `cmp_storage.py` / PDF 预览页）
- storage 污染检查：截图前后 `library.json / memory.json / user_profile.json` 逐字段比对**完全一致**（备份在 `C:/tmp/r4/backup/`）

---

## 0. 结论速览

| 任务 | 状态 | 一句话结论 |
|---|---|---|
| README 重构（话术8） | ✅ | 主截图裂图已修（`docs/shots/p0.png`）、3 行启动命令、10 行×5 列竞品矩阵、演示链接占位、团队占位、**5 大类赛题对照表（全部行号逐条 grep/read 核对）** |
| 技术文档 8 章（话术7） | ✅ | `technical_doc.md` 重写为唯一事实源；第 1 章 **1597 汉字**（要求 ≥1000）；全文 **10472 汉字**；架构图 Mermaid + 等价文本分层图双版本；9 张真实截图；全部过时数据已更新 |
| PDF 生成 | ✅ | **25 页 / 9 张嵌入图片 / 2.31MB**，工具链：纯标准库 md→HTML 转换脚本 + **playwright chromium 打印 A4**（零 pip install） |
| `.dockerignore` + Docker 安全 | ✅ | 新建 `.dockerignore`（`.env` 首条排除），**堵住"真实 API Key 打进镜像层"的安全事故**；Dockerfile/compose 其余检查结论见 §4 |
| 截图更新 | ✅ | playwright + chromium 可用，`scripts/resnap.py` 实跑成功，**9/9 张已重拍**（1560×950，2026-10-11 17:19），服务已关闭、storage 未污染 |
| 代码与文档不一致发现 | ✅ | 7 项记录，见 §6（只记录未改代码） |

---

## 1. README.md 重构（任务 1）

### 1.1 板块清单（评委 3 分钟读完的顺序）

1. **顶部演示截图**：`![主界面 · 文献库管理](docs/shots/p0.png)` —— 已 `ls` 确认存在（文件名与页面映射按 `round3_fixR2.md §4` 实况表，`p0.png`=文献库页）。
2. **在线演示占位**：`<!-- TODO: 替换为真实演示链接 -->` + 占位行 + 提交前替换说明。
3. **一键运行 3 行命令**：`git clone` / `pip install -r requirements.txt`（附 conda 激活等价写法）/ `streamlit run app.py`；另附 **Docker 方式**（`cp .env.example .env` + `docker compose up -d`）与 Windows 双击 `启动科研助手.vbs`（内部即 `conda activate ai_research` + `streamlit run app.py`，与 `启动科研助手.bat` 一致）。
4. **功能矩阵**：10 个能力维度 × 4 列（ChatGPT 类通用对话 / 通用 RAG / Zotero+插件 / 本系统），维度选取有区分度（双栏解析、页码溯源、拒答+校验+修复环、真实被引、图谱挖掘、定时追踪、无幻觉 References、对照阅读、541 页长文档、离线演示/私有化）。
5. **Agent 内核简介**（一段话，替代旧版大段堆砌）。
6. **系统架构**：6 层文本图（ui→core→services→tools→storage）。
7. **赛题要求对照表**：5 大类共 33 条，每条精确到 `文件:行号` 或函数名（见 1.2）。
8. **关键实测数字**（10 行表）+ **6 条复跑命令**。
9. **技术栈 / 配置 / 测试数据 / 目录结构 / 演示与文档**（精简，删除旧版过时描述）。
10. **团队信息占位**：队伍/组别/队员与分工（按 6 个模块角色给出占位结构）/指导教师。
11. **AI 协同声明与局限**（指向技术文档第 8 章）。

### 1.2 赛题对照表行号真实性核对方法

所有行号均于 2026-10-11 用 `grep -n "^def "` / `cat -n` 在当前工作区逐条核对（非照抄旧文档）。抽查示例（均与文档一致）：

| 引用 | 核对结果 |
|---|---|
| `services/parsing.py:123 _detect_two_columns`、横跨否决 `:190-195` | ✅ `cat -n` 确认（横跨否决代码位于 190-195 行） |
| `services/rag.py:411` 拒答闸门、`:418-429` 修复环 | ✅ `cat -n` 确认（`if top < RELEVANCE_FLOOR` 在 411 行） |
| `core/guardrails.py:83 check_citation_support / :127 validate_citations / :152 validate_grouped_citations / :181 ACADEMIC_SYSTEM` | ✅ |
| `services/library.py:206 _validate_prepared / :253 prepare_pdf / :430 commit` | ✅ |
| `services/graph.py:299 detect_frontier / :644 generate_review / :833 explore_future_structured` | ✅ |
| `services/writing.py:837 validate_reference_whitelist / :956 generate_outline_verified / :1298 infer_chart_plan / :1405 chart_facts` | ✅ |
| `core/router.py:4-23 EXPERTS`、`tools/definitions.py:355 build_registry`（21 处 `r.register` 在 :357-430） | ✅ |
| `ui/page_qa.py:51 anchor_card`、`ui/page_reading.py:454 _consume_qa_jump`、`app.py:84-94 _PAGES` | ✅ |

### 1.3 相对旧版 README 修正的过时内容

| 旧版问题 | 新版处理 |
|---|---|
| `README.md:5` 主截图指向不存在的 `docs/screenshots/main.png`（裂图） | 改为真实存在的 `docs/shots/p0.png`，并重拍为最新 UI |
| "1523 分块 / 26.3s / 292MB"（不可复现旧口径） | 全部改为实测 **1301 分块 / 24.56s / 峰值 283MB / 0 失败页**（round2_judgeD 实测） |
| 赛题表引用已不存在的 `_sorted_blocks_by_column`（旧函数名/旧行号） | 全部重写为当前函数名与行号 |
| "16 个 Function Schema 工具" | 改为 **21 个**（`tools/definitions.py:355 build_registry` 实数） |
| 技术文档页数宣称 "21 页"（实为 19 页） | 新版不再在 README 写具体页数（当前 PDF 实测 25 页，见 §3） |
| 功能矩阵只有 3 列竞品 | 扩为 4 列（新增 Zotero+插件）且维度重选 |
| 无团队分工结构、无赛题 5 大类完整对照 | 已补齐 |

---

## 2. 技术文档补全（任务 2）

基准文件 `docs/technical_doc.md` 全文重写（v3.0），HTML 与 PDF 均由它单一事实源生成，**消除旧版"md 与 pdf 两套内容分叉"问题**。

### 2.1 8 章节完成情况

| 章 | 要求 | 完成情况 | 字数（汉字）/ 图 |
|---|---|---|---|
| 1 项目概述 | ≥1000 字 | 背景/目标用户/赛题定位/**与现有工具本质差异**/状态总结/**1.6 成果速览表** | **1597** / 无图 |
| 2 需求分析 | 痛点逐条映射 | 2.1 痛点→需求→答案映射表（8 条）/ 2.2 五大类功能需求逐条 / **2.3 三个用户故事端到端走查** / 2.4 非功能需求 | ~1600 |
| 3 系统架构设计 | 必须含架构图 | **Mermaid flowchart**（UI→core→services→tools→storage 分层，HTML 版渲染为带色框说明）+ **等价 ASCII 分层图**（PDF 内直接可读）+ 调用主线 + ctx 上下文对象表 | ~900 / 2 张架构图 |
| 4 AI 技术方案 | 抗幻觉重点 | 4.1 Provider 抽象+统一重试实测 / 4.2 RAG 管线（含**检索参数表**与"为什么混合检索"论证）/ 4.3 Prompt 工程（5 类 prompt）/ **4.4 抗幻觉四道防线（拒答闸门、引用一致性校验、修复环、References 白名单，各含实测证据与行号）** / 4.5 依赖降级矩阵（8 类依赖的降级行为表） | ~2600 |
| 5 核心功能实现 | 每模块配截图 | **模块总览表（9 张截图↔页面↔赛题大类↔实测）** + 6 个小节，每节引用 `docs/shots/` 真实存在的图（p0/p1/p2/p3/p4/p5/p6/p7/p8 全部用到） | ~2400 / 9 图 |
| 6 测试与部署 | 含 Docker | 6.1 三层测试+实测数据表（9 行）/ 6.2 本地启动 / **6.3 Docker（Dockerfile/`.dockerignore`/compose 逐项说明 + 安全事故修复声明）** / 6.4 配置表 / **6.5 已知问题清单（10 条，诚实公开）** / 6.6 三轮评审修复闭环 | ~1800 |
| 7 创新点说明 | ≥3-4 条实质创新 | **6 条**（四道防线抗幻觉、版面感知先于检索、真实被引数据纪律、图谱驱动综述、演示鲁棒性工程、诚实降级设计）+ **与通用 RAG 正交对比表（6 维）** | ~1400 |
| 8 局限性与未来规划 | 诚实写 | 8.1 **10 条局限表**（含影响面）/ 8.2 **结构化 Roadmap（短期/中期/长期 × 目标 × 关键动作）** | ~1100 |
| 附录 A/B/C | — | 复跑命令与预期输出 / 评审材料索引 / 文档版本与数据来源（含 1523→1301 更正声明） | ~500 |

全文合计 **10472 汉字**；PDF 25 页（目标 25-30 ✅）。

### 2.2 过时数据更新对照

| 项目 | 旧文档口径 | 新文档口径（实测来源） |
|---|---|---|
| 541 页长文档 | 1523 分块 / 26.3s / 292MB | **1301 分块 / 24.56s / 峰值 283MB / 0 失败页**（round2_judgeD §3.2） |
| 工具数 | 16 个 | **21 个**（grep `r.register` 实数） |
| 引用含金量 | "纯规则打分、未联网"（局限章节） | OpenAlex→S2→Crossref 三级降级真实被引链路 + `low_confidence` 显式标注（round3_fixR3） |
| 双栏 | "朴素中线二分"描述 | 4 条必要条件（横跨否决等），1111 页单栏语料误判 0%（round3_fixR1） |
| 沙箱 | "25s 超时"描述未提缺陷 | 补记 `[-3000]` 索引必崩已修复 + `_tail_text` 安全截尾（round3_fixR1） |
| 演示稳定性 | 无量化 | 7/7 条演示路径 0 未捕获异常（round3_fixR2） |
| 截图 | 9 张摄于 10-10 14:03（UI 大改前） | 9 张重摄于 **10-11 17:19**（最新 UI） |
| 附录 | "PDF 未生成"注释 | 已生成（见 §3），注释移除 |

---

## 3. PDF 生成（任务 2b）

**成功。** 本机无 weasyprint / wkhtmltopdf / pandoc / md2pdf（已逐个 import 探测，均为 MISS；系统 PATH 亦无），但检测到 **playwright + chromium 已安装**（`chromium launch OK 153.0.8010.12`），故采用：

1. `C:/tmp/r4/md2html.py` —— 纯标准库 Markdown 子集解析器（表格/代码围栏/引用/列表/图片/标题），套用原 `技术文档.html` 的比赛风格 CSS（封面 + 目录 + A4 打印样式），生成 `docs/技术文档.html`（46.6KB）；
2. `C:/tmp/r4/html2pdf.py` —— playwright chromium `page.pdf(format="A4", print_background=True)` 生成 `docs/技术文档.pdf`。

**产物**：25 页 / 9 张嵌入截图 / 2.31MB（pymupdf 校验页数与图片数）。

**在其它环境复现的确切命令**（若无 playwright）：

```bash
# 方式一（本机同款，零安装）：
python -c "from playwright.sync_api import sync_playwright; \
p=sync_playwright().start(); b=p.chromium.launch(); pg=b.new_page(); \
pg.goto('file:///C:/Users/Administrator/ai_research_agent/docs/技术文档.html'); \
pg.pdf(path='docs/技术文档.pdf', format='A4', print_background=True); b.close(); p.stop()"

# 方式二（有 pandoc 的环境）：
pandoc docs/technical_doc.md -o docs/技术文档.pdf --pdf-engine=wkhtmltopdf -V CJKmainfont="Microsoft YaHei"

# 方式三（有浏览器即可）：用 Chrome 打开 docs/技术文档.html → 打印 → 另存为 PDF（A4，背景图形勾选）
```

---

## 4. `.dockerignore` 与 Docker 安全检查（任务 3）

### 4.1 新建 `.dockerignore`（内容摘要）

按任务要求至少排除的 10 项全部覆盖，另加运行无关项：

```
.env / .env.*（保留 .env.example）/ *.key / *.pem     ← 安全第一
.git/ / .github/ / .gitignore
storage/ / logs/ / *.log                              ← 运行时数据不入镜像
dist/ / build/ / dist-release/ / *.spec / installer.iss / desktop/ / build_desktop.bat
__pycache__/ / *.pyc / *.py[cod] / *.egg-info / .pytest_cache / .mypy_cache / .ruff_cache
node_modules/
.streamlit/                                            ← 任务要求排除；Docker CMD 已显式指定端口/地址
docs/ / tests/ / version_history/ / LICENSE            ← 不参与运行
.DS_Store / Thumbs.db / .idea/ / .vscode/
```

排除项安全性核对：运行链路（`app.py`/`core`/`services`/`tools`/`ui`/`config.py`/`lib`/`demo_data`/`pdf`/`requirements.txt`）全部保留；`grep -rn "from desktop|import desktop"` 确认运行代码只 import `ui.desktop_chrome`（未排除），`desktop/` 包仅桌面壳使用。

### 4.2 Dockerfile / docker-compose 安全检查结论（只建议，未改 Dockerfile）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | **`.env` 打进镜像层（安全事故）** | ✅ **已根治**：`.dockerignore` 首段排除 `.env`；`COPY . .` 不再把它烧进镜像层。密钥现在只经 `docker-compose.yml` 的 `env_file: .env` **运行时注入环境变量**（这是正确做法） |
| 2 | docker-compose 挂载 `./storage:/app/storage` | ✅ 合理（容器重建不丢文献库）；`.dockerignore` 排除 `storage/` 后容器首启自动重建该目录，不冲突 |
| 3 | 启动命令 | ✅ `--server.port=8501 --server.address=0.0.0.0` 显式覆盖，排除 `.streamlit/` 后仅损失主题配色（不影响功能），已在技术文档 6.3 说明 |
| 4 | 建议（未动手）：容器以 root 运行 | 建议加 `USER` 非 root 用户；`.streamlit/` 排除导致主题丢失如在意可改为白名单式 COPY |
| 5 | 建议（未动手）：无 healthcheck | 建议加 `HEALTHCHECK`（`curl -f http://localhost:8501/_stcore/health`） |
| 6 | 建议（未动手）：`requirements.txt` 缺 matplotlib/graphviz/pywebview | 代码侧已有降级不崩，但镜像内降级层级多两级；建议以注释行补充可选依赖 |
| 7 | `pip install --upgrade pip` 未锁版本 | 轻微可复现性问题，建议锁定 pip 版本 |

---

## 5. 截图更新（任务 4）

**成功，9/9 张已重拍。** 步骤与证据：

1. 可用性检查：`import playwright` OK；`p.chromium.launch()` OK（chromium 153.0.8010.12）。
2. 先备份 `storage/library.json / memory.json / user_profile.json` 到 `C:/tmp/r4/backup/`。
3. 实跑项目自带脚本 `scripts/resnap.py`（起临时 streamlit 于 127.0.0.1:8599 → 点"一键加载演示数据"→ 逐页截图）。
4. 输出：`✅ p1/p0/p2/p3/p4/p5/p6/p7/p8.png` 全部成功，PIL 校验均 1560×950；文件时间 2026-10-11 17:19-17:20。目视抽查：p0（文献库新版式）、p2（问答页示例问题/多篇对比控件）、p4（真实引用图谱 2 节点 1 边 + 基石星标）均为最新 UI。
5. **服务已关闭**：`netstat` 无 8599 LISTENING、无 python/streamlit 进程残留（resnap.py 的 `finally: proc.terminate()` 生效）。
6. storage 前后逐字段比对一致（papers=3 / chunks=4 / likes={} / memory / profile 均未变）。

> 提醒：`test0.png`（9KB 早期测试图）按要求未引用、未删除。

---

## 6. 写文档过程中发现的代码与文档不一致（只记录，未改代码）

| # | 不一致点 | 位置 | 说明 |
|---|---|---|---|
| 1 | **`docs/演示视频脚本.md:18` 仍写"1523 分块 26 秒入库"** | `docs/演示视频脚本.md` | 与实测 1301 块/24.56s 不符。该文件不在我的可改清单，**建议下一位负责人同步刷新**（同文件 :24 的"AST 白名单沙箱"表述现已属实——R1 修好了执行工具本身） |
| 2 | **`.gitignore:20` 忽略 `docs/shots/`** | `.gitignore` | 截图目录不进版本库：本地交付可见，但推 GitHub 后 README 顶部图片与其余 9 图会**全部裂图**。建议：`.gitignore` 中加 `!docs/shots/` 例外（或 `git add -f docs/shots/*.png`）。**这是 README 图片能否在远端呈现的关键** |
| 3 | `core/llm.py __init__` 不校验 API Key 合法性 | `core/llm.py:133` 附近 | 含中文的占位 Key（本机 `.env` 现状）在拼 HTTP 头时才抛 UnicodeEncodeError，白耗 3 轮重试（9s）；建议 `isascii()` 前置校验（已写入技术文档 6.5/8.2） |
| 4 | `json_chat` 解析失败抛 ValueError，违反 `core/llm.py` 文件头"全部失败不抛异常"承诺 | `core/llm.py` | 调用点（planner/router/page_library）均有外层 try/except 兜底，实际不崩；文档中已如实注明 |
| 5 | `t_run_python` 临时目录 `ai4s_code_*` 无 finally 清理 | `tools/definitions.py:299` 附近 | 每次调用泄漏一个 KB 级目录；同项目内 `fig_*`/`wcopilot_tikz_*` 均有 `finally: rmtree`，标准不一（已写入技术文档 6.5） |
| 6 | `commit()` 文件名未清洗 | `services/library.py:430` 附近 | `filename="a/b/c.pdf"` 时建路径 FileNotFoundError（兼路径穿越面）；建议 `os.path.basename()` + 非法字符替换 |
| 7 | 旧文档宣称"18 项单元测试"仍属实，但 README 旧版"图谱规模压测 n=20 7.9s / n=40 25.0s"**本轮无人复跑**（三轮评审报告均未含该数字的新实测） | `scripts/bench_graph.py` | 新 README/技术文档保留该数字但标注"脚本可复跑"；如需严格背书建议赛前实跑一次 |

另有 2 项环境观察（非不一致）：项目根存在空目录 `config/`（评委D 已提过，易误导模块解析）；`启动科研助手.bat` 注释行在 UTF-8 终端下显示乱码（GBK 编码注释，功能不受影响）。

---

## 7. 自验与约束核对

| 约束 | 状态 |
|---|---|
| 不 pip install 大件 | ✅ 未安装任何包；PDF 转换用已有 playwright（转换脚本与 resnap 同款工具链） |
| 起的服务用完关闭 | ✅ resnap 结束后 netstat/tasklist 双确认无残留 |
| 文档引用路径真实存在 | ✅ README/技术文档引用的 `docs/shots/p0-p8.png`、`scripts/*.py`、`pdf/*`、`demo_data/*` 均已 ls 确认 |
| 行号真实核对 | ✅ 见 §1.2；所有 `文件:行号` 均本轮 grep/read 核对，未照抄旧文档 |
| 中文写作 | ✅ |
| 不删禁删文件 | ✅ `audit_report_20261010.md`、`docs/review/*`、`test0.png` 全部原样 |

**遗留待办（超出本角色范围，移交）**：
1. `.gitignore` 加 `!docs/shots/` 例外（否则远端 README 裂图）—— 需改 `.gitignore`，非我独占文件；
2. `docs/演示视频脚本.md` 的 1523 分块旧口径刷新；
3. 团队信息与在线演示链接两处占位符需在提交前填写；
4. §6 表中 3-6 项代码级小修（Key ASCII 校验 / json_chat 返回值 / 沙箱临时目录清理 / 文件名清洗）已写入技术文档 8.2 Roadmap。
