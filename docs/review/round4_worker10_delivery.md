# Round4 · 考生10号 交付报告：演示视频脚本 + 在线演示部署 + README 补全

- **独占改动文件（仅此 5 个）**：
  - `README.md`
  - `docs/演示视频脚本.md`（覆盖重写）
  - `docs/在线演示部署指南.md`（新建）
  - `docs/团队信息.md`（新建）
  - `.env.example`（核对补全，未填入任何真实密钥）
- **未触碰**：任何 `.py` 文件、`Dockerfile`、`docker-compose.yml`、`.dockerignore`、
  `docs/technical_doc.md`、`docs/review/` 其他报告。
- **自验环境**：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（streamlit 1.63.0）
- **自验方式**：
  - 代码层面：全仓 `grep` 核实环境变量、界面文案、导航菜单、按钮文案、登录鉴权逻辑
  - 文件层面：`ls` 确认 `docs/shots/`、`pdf/`、`demo_data/` 文件存在
  - 数据层面：用 `PyMuPDF` 核对 6 个官方测试 PDF 的页数与文件大小

---

## 0. 现状核实结论（先核实再写，逐条来自任务要求）

| 任务要求 | 核实结果 | 处理 |
|---|---|---|
| 演示视频脚本 5–8 分钟，分镜表格式，六大模块 + AI 亮点 + 总结 | ✅ 已按当前 UI 代码重写为 7 分 20 秒、24 镜分镜表 | 重写 `docs/演示视频脚本.md` |
| 脚本按钮文案 / 菜单位置须与当前界面一致 | ✅ 已读 `ui/sidebar.py`（9 项菜单）及 7 个页面代码，逐条替换旧脚本过时文案 | 旧脚本"1523 分块"等已修正为"1301 分块 / 24.56 秒 / 283MB" |
| 部署指南 ≥3 条路径，真可执行 | ✅ 已给出 Docker / 云服务器 / Streamlit Community Cloud 三条路径 | 新建 `docs/在线演示部署指南.md` |
| README 补团队信息、三条安装路径、环境变量模板、依赖清单、AI 协同说明 | ✅ 已补；团队信息占位符指向 `docs/团队信息.md` | 编辑 `README.md` |
| `.env.example` 覆盖所有 `os.getenv` | ✅ 已核对 23 个用户可配置变量，全部写入并带注释 | 编辑 `.env.example` |
| 不修改任何 `.py` 等禁止文件 | ✅ 未触碰 | —— |

---

## T1：演示视频脚本

### 交付内容

- 文件：`docs/演示视频脚本.md`（全文重写）
- 总时长：**7 分 20 秒**（6.5–7.5 分钟目标内）
- 分镜数：**25 条分镜**（含开场 2 条、六模块演示 17 条、AI 技术亮点 4 条、总结 1 条）
- 录制技术参数建议：1920×1080 / 30fps / H.264 / MP4 / 8–12 Mbps
- 翻车预案：**10 条**（覆盖网络中断、API 限流、文件太大、去重未命中、图谱无真实边、LLM 超时等）

### 覆盖的赛题六大模块

| 赛题模块 | 视频段 | 操作的精确路径（已核实 UI 代码） |
|---|---|---|
| ① 多模态与细粒度文档感知 | 0:50–2:55 | `📁 1. 文献库管理` → `🔍 解析并查重`（v7→v1 去重 / 扫描件 OCR / 541 页长文档） |
| ② 抗幻觉 RAG | 2:55–3:55 | `💬 2. 跨文献问答与溯源` → 示例按钮 `训练用了什么 GPU？` → `🔎 提问` → 展开溯源卡片 |
| ③ 版式阅读与智能翻译 | 3:55–4:35 | `📖 3. 版式阅读与智能翻译` → `📄 原生渲染` → `🎨 渲染该页` / `🔍 检测本页栏数` → `🌐 中英同步滚动` |
| ④ 自主工具调用（检索/追踪） | 4:35–5:10 | `🔍 4. 论文自动检索与追踪` → `🔍 搜索 arXiv` / `✨ 刷新并推荐` |
| ⑤ 图谱挖掘 | 5:10–5:45 | `🕸️ 5. 引用图谱与综述` → `🕸️ 构建引用图谱` / `🎭 演示模式示意图谱` / `📝 一键生成领域综述` |
| ⑥ 学术 Copilot | 5:45–6:20 | `✍️ 6. 写作辅助与可视化` → `📂 载入示例数据 demo_data/data.csv` / `🧱 生成框架（引用真实文献）` |
| AI 技术亮点 | 6:20–7:00 | `💡 技术创新亮点`（12 项）+ `🤖 0. Agent 总助手`（计划/路由/思考过程） |

### 关键实测数字（已核对，写入脚本）

| 指标 | 数值 | 来源 |
|---|---|---|
| 541 页《Understanding Deep Learning》入库 | 24.56s / 1301 分块 / 峰值 283MB / 0 失败页 | `README.md`、实测 PDF 属性 541 页 / 21.3MB |
| v1 / v7 语义去重阈值 | `DEDUP_THRESHOLD=0.90`（界面显示实际百分比） | `config.py:79` |
| 拒答闸门阈值 | `RELEVANCE_FLOOR=0.35` | `services/rag.py:16` |
| 幻觉修复环上限 | `MAX_REPAIR=1` | `services/rag.py:17` |
| 官方测试文件页数 | v1=15 页 / v7=15 页 / v1_img=15 页 / BERT=16 页 / UDL=541 页 | 用 `PyMuPDF` 实跑 |

---

## T2：在线演示部署指南

- 文件：`docs/在线演示部署指南.md`（新建）
- 给出的部署路径：**3 条**

| 路径 | 适用情况 | 页内章节 |
|---|---|---|
| **A. Docker 自部署** | 自己已有机器；或内网穿透后对外 | §2 |
| **B. 云服务器（腾讯云轻量 / 阿里云 ECS）** | 推荐主用：公网 IP + 域名 + HTTPS | §3 |
| **C. Streamlit Community Cloud** | 免费但有限制；建议做备用地址 | §4 |

### 给出的关键安全配置

1. **API Key 只经环境变量注入，绝不写进仓库**（§1.1、§1.2）。
2. **进阶零密钥方案**：自建 OpenAI 兼容网关 + 侧边栏「自定义端点」取消勾选 `needs_key`，真实 Key 只在网关侧。
3. **登录鉴权说明**：系统当前无任何登录功能；如需给评审账号，推荐 **Nginx Basic Auth**（提供命令）。
4. **Streamlit 公网配置建议**：给出 `/opt/ai4s/deploy-config.toml`，开启 `enableXsrfProtection = true`、关闭 `showErrorDetails`、放宽 `maxUploadSize = 300`。
5. **服务可用性**：涵盖 Docker `restart: unless-stopped`、systemd unit、supervisor、日志命令、10 项常见问题排查表。
6. **评审交付清单**：10 项检查表，包括 URL、是否需要登录、是否需要自备 Key、服务可用时段等。

### 与当前代码/配置的重要发现（已写入指南，未改代码）

- `.dockerignore` 排除了 `.streamlit/`：镜像内不会带本地 `enableXsrfProtection = false`，公网默认更安全，但学术风主题也没了；需自行挂载部署专用 `config.toml`。
- `.dockerignore` **没有**排除 `pdf/` 和 `demo_data/`：官方测试 PDF 会复制进镜像，镜像偏大，但评审打开即有现成素材。
- `docker-compose.yml` 的 `ports: - "8501:8501"` 会把端口暴露到公网所有网卡；指南建议改为 `127.0.0.1:8501:8501` 并通过 Nginx 反代（指南 §3.3 已写）。
- Streamlit Community Cloud 的 Secrets（`st.secrets`）与本代码的 `os.getenv` 读取方式**不自动打通**；指南给出三种零改码方案（手填 Key / 自建网关 / 纯离线演示）。

---

## T3：README 补全

- 文件：`README.md`（编辑，非推倒重来）
- 主要改动：
  1. **团队信息**：改为表格占位符，并指向 `docs/团队信息.md`（§7 行、§336–358 行）。
  2. **安装运行指南**：拆成三条路径：conda 本地运行 / Docker / 在线访问（§13–58 行）。
  3. **环境变量模板**：新增 §189 起「配置与环境变量」小节，列出 23 个变量、Provider 名与环境变量名对应表，强调安全红线。
  4. **依赖清单**：新增 §210 起「依赖清单」小节，列出 13 个核心包 + 4 个可选包，并说明 `matplotlib`/`graphviz` 不在 `requirements.txt` 中但会自动降级。
  5. **AI 协同说明**：扩展为 §360 起的完整小节，按赛题 3.4 条说明 AI 贡献、人工架构决策、Prompt 调优策略、当前局限。
  6. **演示与文档**：补充指向新文档（§452 起）。

### `.env.example` 核对结果

- 代码中实际用到的用户可配置环境变量：**23 个**
- 原 `.env.example` 覆盖：**15 个**
- 本次补全后覆盖：**23 个**（补齐 `LLM_BASE_URL`、`LLM_API_KEY`、`OLLAMA_API_KEY`、`GITHUB_TOKEN`、`GITHUB_API_TOKEN`、`HF_ENDPOINT`、`CHUNK_OVERLAP`、`AI4S_DESKTOP`）
- **未填入任何真实密钥**：所有 Key 变量保持空值，仅保留注释说明用途与代码读取位置。

### 依赖核对结果

- `requirements.txt` 13 个核心包与代码实际 import 一致：✅
- 代码还引用了以下可选包，不在 `requirements.txt` 中：
  - `matplotlib`：Graphviz 拓扑图三级降级的第 2 级
  - `graphviz`：Graphviz 渲染第 1 级
  - `playwright`：仅 `scripts/resnap.py` 截图脚本
  - `webview`（`pywebview`）：仅桌面壳
- 已写入 README「可选依赖」小节说明安装命令。因 `requirements.txt` 不在独占文件列表，本次未修改。

### 截图路径核对

- README 引用的 `docs/shots/p0.png` 确实存在，且内容为「文献库管理」页（已肉眼核对 p0.png 与 p2.png）。
- `docs/shots/` 目录下共 10 个文件：`p0`–`p8` + `test0.png`。
- 已确认目录名是 `docs/shots/` 而非 `docs/screenshots/`，并在 README 中显式说明。

---

## T4：新建 `docs/团队信息.md`

- 文件：`docs/团队信息.md`（新建）
- 包含：
  - 队伍名称 / 组别 / 高校院系 / 指导教师占位
  - 成员姓名 / 学校 / 专业 / 组别 / 分工矩阵（按七大代码模块划分）
  - 代码定位对照表（便于评委查阅）
  - 指导教师表
  - 项目时间线（6 个阶段）
  - 提交前自查清单

---

## 核实时发现的「文档与当前代码不一致」清单

> 仅记录，未改代码（按约束禁止改 `.py` 等）。

| # | 不一致点 | 旧表述位置 | 当前代码事实 | 本次处理方式 |
|---|---|---|---|---|
| 1 | 长文档分块数与耗时 | 旧演示脚本："1523 分块 / 26 秒 / 290MB" | 实测代码与 README：`1301` 分块 / `24.56` 秒 / `283MB` | 在脚本与 README 中全部改为正确数字 |
| 2 | 创新点数量 | 旧演示脚本结尾："七项技术创新" | `ui/page_misc.py:_INNOV` 实际为 **12 项**创新 | 脚本改为"12 项技术创新" |
| 3 | 检索页区块顺序 | 旧脚本：先演示 arXiv 搜索 | `ui/page_search.py:357` 实际渲染顺序为 `_track → _recommend → _arxiv → _batch → _others → _quality` | 脚本中改为"页面上滚到 arXiv 检索"，并提示页面顶部是追踪区 |
| 4 | 写作页顺序 | 旧脚本：先讲"CSV→出图"，后讲论文框架 | 当前 `ui/page_writing.py:392` 渲染顺序为 CSV → 框架 → 拓扑图 → 翻译 | 脚本时间线按实际顺序调整 |
| 5 | 去重相似度百分比 | 旧脚本："内容匹配 98% 命中" | `DEDUP_THRESHOLD=0.90`，但实际相似度由 embedding 计算决定，不应写死 | 脚本改为"相似度以界面实际显示为准" |
| 6 | 登录状态 | 无明确说明 | 全仓无登录/鉴权代码，只有 API Key 输入框 | README、部署指南均明确写"系统为开放式，无需登录" |
| 7 | 依赖清单完整性 | README 旧版未提 `matplotlib`/`graphviz` 缺失 | `services/writing.py:1151/1207` 实际会尝试 import，未装则降级 | README 新增「可选依赖」与降级说明 |
| 8 | `.env.example` 变量覆盖不全 | 原 `.env.example` 缺 8 个变量 | 代码读 `LLM_BASE_URL`、`CHUNK_OVERLAP`、`GITHUB_TOKEN` 等 | 补全 `.env.example` 到 23 个变量 |
| 9 | 云部署中 `.streamlit/config.toml` 的 XSRF 设置 | 本地 `enableXsrfProtection = false` | `.dockerignore` 排除 `.streamlit/`，线上默认开启 | 部署指南给出公网专用 `config.toml` 建议 |
| 10 | `docker-compose.yml` 端口暴露面 | 旧理解：默认即可 | `ports: - "8501:8501"` 会监听 `0.0.0.0` | 部署指南建议改为 `127.0.0.1:8501:8501` + Nginx 反代 |

---

## 未完成项 / 风险提示

1. **在线演示地址仍为占位符**：需等队伍拿到服务器/域名后，替换 `README.md` 第 8 行与第 57 行，以及 `docs/团队信息.md` 中的在线地址。
2. **团队信息未填真实值**：`docs/团队信息.md` 与 `README.md` 中的姓名、学校、组别、指导教师均为占位符，提交前需人工替换。
3. **`requirements.txt` 未增补可选依赖**：`matplotlib`、`graphviz`、`playwright`、`pywebview` 不在其中；已记录为已知项并在 README 给出手动安装命令，但不影响核心运行（均有降级）。
4. **未启动服务做浏览器实测**：按约束未启动 streamlit。脚本中的操作路径全部来自 UI 代码静态核实，真实录制时若有交互差异，以实际界面为准。
5. **Streamlit Community Cloud 方案与本代码的 `os.getenv` 读取方式未自动打通**：指南已给出三种无需改码的替代方案（手填、自建网关、纯离线）。若坚持使用 `st.secrets`，需改 `app.py` 或 `config.py`，本交付未做。
6. **Nginx Basic Auth 配置未实际执行**：只是作为"如需登录"的可选方案写入指南，未在云服务器上实际部署。
7. **视频本身未录制**：本交付只提供脚本与素材准备清单，视频需队伍按脚本自行录制、剪辑并导出 1080p MP4。

---

## 自查清单（已在本交付中完成）

- [x] `docs/演示视频脚本.md` 重写完成，时长 6.5–7.5 分钟内，含分镜表、官方测试用例、翻车预案
- [x] `docs/在线演示部署指南.md` 新建完成，≥3 条部署路径，含安全配置、服务可用性、评审交付清单
- [x] `README.md` 补全团队信息、三条安装路径、环境变量说明、依赖清单、AI 协同说明
- [x] `.env.example` 核对并补全到 23 个变量，未填入真实密钥
- [x] `docs/团队信息.md` 新建完成
- [x] 未触碰任何 `.py` / `Dockerfile` / `docker-compose.yml` / `.dockerignore` / `docs/technical_doc.md`
- [x] 全仓扫描确认无 API 密钥泄露到 `.env.example` 或新建文档中
- [x] `docs/shots/` 路径与 `pdf/` 官方测试文件存在性已确认
- [x] 界面文案、按钮、菜单位置已读 `ui/` 代码核实，未照抄旧脚本
