# Round 12 · deploy-doc 交付报告

## 一、交付物

| 文件 | 状态 | 说明 |
|---|---|---|
| `DEPLOY.md`（仓库根目录） | 新建，**732 行** | 你点名要的交付物 |
| `资料/在线演示部署指南.md` | **仅顶部 +3 行指针** | 未改动原有任何内容（549 行 → 549 行，只增加 2 行引用块 + 1 行空行） |
| `资料/复盘/round12_deploy_doc.md` | 新建 | 本报告 |

**未触碰任何其它文件**，`git status --short` 只显示上述两个条目，无代码文件改动，无 commit / push，未读写 `.env`。

---

## 二、核实过的事实（逐个给出证据，可复核）

> 📌 **关于本节（含 2.1–2.x）里的 `文件:行号`**：这些是**本轮各时点核实的快照值**，不是现值。`config.py` / `core/llm.py` / `core/providers.py` / `services/extern.py` 等源文件在本轮被多人改过，行号已整体漂移（详见第 48 条的漂移清单）。
> → **现值以 `DEPLOY.md` 为准**（已做脚本全量校验）；本节保留快照值是为了让"当时依据什么得出的结论"可追溯，**不要拿本节的行号去定位现在的源码**。

### 2.1 部署参数基线

| 事实 | 结论 | 证据 |
|---|---|---|
| 主文件 | `app.py`（根目录） | `Dockerfile:16`、`git ls-files` |
| 端口 | `8501` | `Dockerfile:14/16`、`docker-compose.yml:6` |
| 数据卷 | `./storage:/app/storage` | `docker-compose.yml:9-10` |
| env 注入 | `env_file: .env` | `docker-compose.yml:7-8` |
| Python | `3.11-slim` | `Dockerfile:1` |
| 仓库 / 分支 | `Through1n/ai_research_agent` / `main` | `git remote -v`、`git branch --show-current` |
| 协议 | MIT | `LICENSE:1` |
| 依赖数量 | 13 个包 | `requirements.txt` 逐行数 |
| Streamlit 版本区间 | `>=1.63,<1.64` | `requirements.txt:1` |

### 2.2 环境变量清单（全仓 grep 核实，共 24 个用户可配变量）

核实命令：
```bash
grep -rn "os.getenv\|os.environ" --include=*.py .
grep -n "_env_str\|_env_int\|_env_float" config.py
```

| 变量 | 默认值 | 读取位置（已逐行核对） |
|---|---|---|
| `LLM_PROVIDER` | `zhipu` | `services/state.py:79` |
| `ZHIPU_API_KEY` | 空 | `config.py:97`、`core/providers.py:38→122` |
| `DEEPSEEK_API_KEY` | 空 | `core/providers.py:46→122` |
| `DASHSCOPE_API_KEY` | 空 | `core/providers.py:54→122` |
| `OPENAI_API_KEY` | 空 | `core/providers.py:62→122`、`core/llm.py:626` |
| `OLLAMA_API_KEY` | 空 | `core/providers.py:70`（`needs_key=False`，留空也可用） |
| `LLM_API_KEY` | 空（通用兜底） | `config.py:97`、`core/providers.py:122`、`core/llm.py:626` |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | `config.py:95` |
| `LLM_MODEL` / `VL_MODEL` / `LLM_TIMEOUT` | `glm-4-flash` / `glm-4v` / `90.0` | `config.py:98/99/100` |
| `CHUNK_SIZE` `CHUNK_OVERLAP` `DENSE_K` `SPARSE_K` `FINAL_K` `DEDUP_THRESHOLD` `AGENT_MAX_ITERS` | `1200` `150` `12` `12` `6` `0.90` `8` | `config.py:106–112` |
| `AGENT_GLOBAL_MAX_ITERS` | 空 → 随计划步数伸缩 | `core/agent.py:48` |
| `LLM_PROVIDERS_EXTRA` | 空 JSON | `core/providers.py:79` |
| `GITHUB_TOKEN` / `GITHUB_API_TOKEN` | 空（匿名 60 次/小时） | `services/extern.py:13` |
| `HF_ENDPOINT` | `https://hf-mirror.com` | `config.py:103`（`setdefault`）、`Dockerfile:5` |
| `AI4S_DESKTOP` | 未设置（Web 部署必须留空） | `config.py:10`、`desktop/paths.py:21`、`ui/desktop_chrome.py:32` |
| `APPDATA` | 系统自带（有 `or Path.home()` 兜底） | `config.py:22` |

**本次核实的新发现 1**：`AGENT_GLOBAL_MAX_ITERS`（`core/agent.py:48`）**未收录在 `.env.example`**。`.env.example:12` 自称"共 23 个"，按我的核对应为 23 + 1 = 24 个用户可配变量。已在 DEPLOY.md §7.3 单独标出该差异，**未改 `.env.example`**（不在我授权范围内）。

### 2.3 三个会影响部署成败的代码事实（都在文档里写了）

1. **全仓没有任何 `st.secrets` 调用**（grep 全库确认，仅历史文档里提到过）。
   密钥只走 `os.getenv`：`config.py:97`、`core/providers.py:122`、`core/llm.py:626`。
   → 直接影响方案 A（Streamlit Cloud 的 Secrets 是 TOML）。文档给出「Secrets 会同时注入环境变量（官方说明）」+「侧边栏手填」两条路，后者依据 `core/providers.py:116-122` `resolve_key()` 手填优先级最高，**100% 有效且零改码**。

2. **`core/llm.py:133`（原文写 `:124`，本轮漂移，已订正） 校验 `key.isascii()`**，非 ASCII Key 直接抛 `LLMConfigError`。
   → 所以"中文占位符不能真的填进 Secrets"，否则整站降级。这条写进了 A-2 坑 1 和故障速查表。

3. **`.streamlit/config.toml` 已被 git 跟踪**（`git ls-files` 确认），其中 `address = "127.0.0.1"`（第 14 行）、`enableCORS = false`（第 16 行）。
   - 方案 C 不受影响：`.dockerignore:44` 排除了 `.streamlit/`，且 `Dockerfile:16` 命令行 `--server.address=0.0.0.0` 优先级高于 config.toml。
   - 方案 A / B 由平台直接 `streamlit run`，不传该参数 → 会读到 `127.0.0.1`。
   - Streamlit 配置优先级为「命令行 > 环境变量 > config.toml > 默认值」，这是 Streamlit 的既定机制。
   - **⚠️ 我没法确认云平台是否会用自己的启动参数覆盖它，因此标为"待实机确认"**，并在 §1.3 给出判断方法（看 Logs 里绑到哪个地址）与处理方式（改 `address` 为 `0.0.0.0` 或删行）。
   - **这一步需要改仓库文件，我没有擅自执行**，已作为决策项上报 team-lead。

### 2.4 Docker 方案的两个必写陷阱（已核实）

1. **`cp .env.example .env` 是必需步骤**。`.env` 被 `.gitignore:2` 排除、不在仓库里，而 `docker-compose.yml:8` 声明了 `env_file: .env`。全新 clone 后直接 `docker compose up` 会报 `env file not found`。
2. **`.dockerignore:15` 排除了 `storage/`**，镜像内无数据；容器启动后由 `config.py:38-42` 自建 `storage/uploads`、`storage/chroma`、`storage/pdf` 等目录 —— 与 team-lead 给的"空库不会因缺目录崩溃"一致，已写进 C-3。
3. `static/mermaid.min.js` 未被 `.dockerignore` 排除（已逐行读完 `.dockerignore` 59 行确认），图谱渲染所需的本地资源会进镜像。

### 2.5 UI 事实（自检清单里每一条的出处）

- 页面标题 `AI4S 科研助手 Agent`：`app.py:31`
- 侧边栏 9 个菜单项原文：`ui/sidebar.py:13-23`
- 5 个 Provider：`core/providers.py:33-74`（智谱/DeepSeek/通义/OpenAI/Ollama）
- 「✅ Key 已填入，模型通道就绪」：`ui/sidebar.py:215-218`
- 「🔌 测试连通性」按钮 + 「✅ 连通性正常，模型返回：」：`ui/sidebar.py:233-240`
- 「填入 ≠ 有效」的原文注释：`ui/sidebar.py:216-217` —— 自检清单据此把"测试连通性通过"定为唯一判据
- 「🚀 一键加载演示数据（v1/v7/BERT）」：`ui/sidebar.py:381`
- 3 篇内置演示论文（无 Key 也能演示）：`config.py:114-182`
- LLM 失败横幅按错误类型给中文指引：`app.py:124-131`

---

## 二·补 Round 12 后半程的同步更新（DEPLOY.md 已与改后的代码对齐）

Round 12 后半程，team-lead（改 `config.py` / `core/providers.py`）与 deploy-docker（重写 `Dockerfile` / `docker-compose.yml` / `.streamlit/config.toml` / `.dockerignore`）改了部署相关文件。我重新读了一遍，**把 DEPLOY.md 里因此失效的内容全部同步了**。以下是变更点，可复核：

| # | 原先我的写法 | 现在的事实 | 文档已改成 |
|---|---|---|---|
| 1 | `.streamlit/config.toml` 含 `address = "127.0.0.1"`，是方案 A/B 的头号阻塞点（标"待实机确认"） | **该行已被 deploy-docker 注释掉**，本地脚本 / Docker CMD / 云平台三方分工明确 | §1.3 改为"✅ 已解决"，保留原理说明；A-2 坑 3 标为已解决；§九 从待确认移入已核实 |
| 2 | "全仓无 `st.secrets`，云上可能读不到 Key" | team-lead 补了 `config.py:61` `_st_secret()` + `core/providers.py:119-138` `resolve_key()` 的 Secrets 兜底 | A-2 坑 2 完全重写：不再是"能否读到"的问题，改为强调 **Secrets 必须写根级键、不能套 `[section]`**；§7.5 读取链更新为「手填 > 环境变量 > st.secrets」 |
| 3 | `cp .env.example .env` 是必需步骤，否则 `env file not found` | compose 改为 `env_file: - path: .env, required: false` | C-2 改为"建议建但不是硬失败" |
| 4 | Docker 以 root 运行 | Dockerfile 改为 **非 root `app`（uid 10001）** | **新增 C-2b**：Linux 宿主必须 `sudo chown -R 10001:10001 storage`，否则应用报「存储目录不可写」。这是新出现的高频坑，已加进故障速查表 |
| 5 | 只有一个 `./storage` 卷 | 新增两个命名卷 `chroma_model_cache`（缓存 90MB 模型，避免重复下载）与 `app_logs` | C-3 备份说明更新，并给了命名卷的迁移命令 |
| 6 | 健康检查端点路径"待实机确认" | `Dockerfile` 的 `HEALTHCHECK` 明确用 `/\_stcore/health`，并引了 streamlit 源码行号 | 升级为已核实；C-3 加了 `curl /\_stcore/health` 验证步骤与 `start_period=120s` 等待说明。WS 路径仍标待确认 |
| 7 | "容器只监听 127.0.0.1:8501" | 实际 `Dockerfile` CMD 是 `--server.address=0.0.0.0`，且 `"8501:8501"` 会发布到宿主**所有网卡** | C-6 修正，并给出更稳的 `"127.0.0.1:8501:8501"` 改法（注明需 deploy-docker 确认，我未改 compose） |
| 8 | 环境变量行号（旧 `config.py`） | `config.py` 前移，行号全变 | §7 全部行号重新采集：`118/120/121/122/123/126/129-135`，`core/providers.py:38/46/54/62/70/79/135-138` |
| 9 | 基线表引 `Dockerfile:14/16`、`compose:6/7-8/9-10` | 两个文件已重写，行号失效 | 基线表改为按语义引用（`Dockerfile` 的 `CMD` / `EXPOSE`、`compose` 的 `ports` / `volumes`），并新增"运行用户 uid 10001""健康检查"两行 |
| 10 | 首次启动出网范围只写了向量模型 | deploy-docker 给出完整清单 | §1.1 新增「首次启动需要的外网出口清单」表（chroma S3 / LLM Provider / arXiv / PubMed / DuckDuckGo / api.github.com），逐项标注断了的后果 |
| 11 | `HF_ENDPOINT` 对模型下载"不一定生效"（我自己的推测） | deploy-docker 确认：**从 chroma 的 S3 直下，不走 HF** | 措辞由"不一定生效"升级为"**无效**"，并注明结论来源不是我本人复现 |
| 12 | `GITHUB_TOKEN` 未标注重启要求 | `services/extern.py:13` 是**模块导入期**读取，之后不再读 | §7.4 加"⚠️ 改了必须重启进程才生效" |
| 13 | 数据卷写的是 3 个（含 `chroma_model_cache`） | deploy-docker 删掉了模型缓存卷（模型已烤进镜像，挂卷反而覆盖它） | C-3 改为 **2 个卷**，并加"不要给 `$HOME/.cache/chroma` 挂卷"的警示 |
| 14 | "首次启动必须出网"（笼统） | 模型**构建期已烤进镜像**，方案 C 运行期不再需要 S3 | §1.1 改为按方案区分的三行表（C 仅构建期 / A·B 需要 / 本地需要）；出网清单表加"方案 C 什么时候需要"列 |
| 15 | 模型体积"约 90MB"（沿用 deploy-docker 的估计） | deploy-audit **实测**：下载 79.3MB（HTTP `content-length` = 83,178,821 B）、解压 88MB、落盘 167MB | 全文 90MB → 79.3MB/167MB，并注明"实测，非估算"；我初稿的偏大已改正 |
| 16 | "镜像无 TeX / graphviz"（两项都无） | **graphviz 已装**（`Dockerfile` 第 2 层 `apt-get install graphviz`），**TeX 仍未装** | §5.0b 改为按方案分的四行表：Graphviz 在方案 C **可用**、在 A/B 不可用；TikZ 两者都不可用 |
| 17 | 出网清单没写"方案 C 需要 PyPI / Debian 源" | 构建期要装依赖 + graphviz | 出网清单表补全，并加了"构建机连不上 S3 时改用 `COPY ./onnx_models`"的兜底（引自 Dockerfile 注释） |
| 18 | 未覆盖 `.streamlit/secrets.toml` 被打进镜像的风险 | deploy-audit 发现：`.dockerignore` 为放行 `config.toml` 删掉了 `.streamlit/` 排除规则，**但没精确排除 `secrets.toml`** | §1.1 红线处加了醒目警示："别把真实 Key 写进 `.streamlit/secrets.toml` 调试，会被 `docker build` 烤进镜像"；并在报告里建议 deploy-docker 补一行精确排除 |
| 19 | HF Spaces 里写了"可能需要把 Dockerfile 挪出根目录" | deploy-docker 定：**不挪**（`docker build .` 认根目录是硬约定）；Space 若有需要就**单独写自己的 Dockerfile** | B-1 改为"不要挪"+ 给出 Space 单独建 Dockerfile 的建议 + 提示 HF 构建环境能否访问 S3 未验证 |
| 20 | "DOT 只有两级降级，别写三级"（deploy-docker 上一轮的原话） | **已被他本人撤回**：本轮其他成员给 `requirements.txt` 加了 `matplotlib>=3.8`（并给 PyMuPDF/chromadb/openai/pandas/numpy 补了跨大版本上界） | §5.0b 改为：容器**三级全活**（`dot -Tsvg` → matplotlib → 零依赖），云平台因无 `dot` 是**后两级**。基线表"镜像内没有 matplotlib"一行删除，改为"依赖数量 14 个" |
| 21 | C-6 写成"8501 不要对公网放行 + 必做加固改回环" | deploy-docker 决定**保持 `"8501:8501"` 不变**，理由：改回环会让"云服务器上直接 `http://<host>:8501`"这个最常见演示场景打不开，与"一键 up 就能起"冲突 | C-6 重写为"现状 + 取舍 + 按部署形态二选一"的决策表，并注明决定权在 team-lead、翻改只需 1 行。我**不再把它写成"必做"**，因为那是与 deploy-docker 决定相反的建议 |
| 22 | ONNX 预热层写"第 4 层" | 该层已挪到 `USER app` **之后**，现为**第 5 层**（`Dockerfile:120`），功能等价但缓存文件归 app 所有 | 全文统一为"第 5 层" |
| 23 | 模型体积写"79.3 MB 下载 / 167 MB 落盘" | 单位混淆：79.3 是 **MiB**（指 tar.gz），此前流传的 90 是 **MB**（指 `model.onnx`），两者不是同一个文件 | 全文改为双单位三行表：tar.gz 83,178,821 B = **79.3 MiB** / 83.2 MB；model.onnx 90,387,606 B = 86.2 MiB / 90.4 MB；**合计 174,510,889 B = 166.4 MiB / 174.5 MB** |
| 24 | TikZ 写成"需 team-lead 拍板要不要装 TeX" | team-lead **已拍板：不装，接受降级**（TikZ 不在演示主线） | §5.0b 改为**硬口径**："文档和现场讲解都不得宣称部署版能真实编译 TikZ"，并标注"这不是待办项，是已定结论"。待拍板项移除 |
| 25 | `.streamlit/secrets.toml` 警示写成"口子未堵" | deploy-docker 已补 `.dockerignore` 精确排除（只排除该文件，配 `config.toml` 照常进镜像） | §1.1 改为"✅ 已堵上 + 仍建议保持习惯"，并新增"方案 C 不需要 secrets.toml"（`_st_secret` 在 try/except 中，容器无该文件不崩） |
| 26 | `HF_ENDPOINT` 只写"对模型下载无效" | team-lead 确认**保留该配置**，注释口径为"仅 HuggingFace 相关路径生效" | §7.4 补上"但**不要因此删掉它**（team-lead 确认保留）" |
| 27 | "三个裸 `os.getenv` 变量（LLM_PROVIDER / GITHUB_TOKEN / AGENT_GLOBAL_MAX_ITERS）不走 Secrets"，列为待实机确认 | deploy-audit 实测 + 我复核：team-lead 改 F1 时**已把前两个改成 `_env_str`**（`services/state.py:80`、`services/extern.py:14`） | §7 开头改为"24 个变量里 **23 个**有 Secrets 兜底，**只剩 `LLM_PROVIDERS_EXTRA` 一个例外**（可选功能，主链路不依赖）"；§九 待确认 **8 条 → 7 条**，并把"平台是否注入环境变量"移入追溯表（已无实质影响）。追溯表新增该项 |
| 28 | 端口写"deploy-docker 保持现状、决定权在 team-lead、你拍板后我同步" | **team-lead 已定案：维持 `"8501:8501"`，并撤回此前让 deploy-docker 改回环的指令** | §C-6 重写：标题改为"**现状即最终决定，不是待办项**"，说明"有意保留、Nginx 是推荐而非唯一路径、真实风险取决于安全组"；改回环降级为**可选加固项**；同时把"安全组只开 22/80/443、8501 一律不放行"写成配了 Nginx 时**最关键的一条** |
| 29 | TikZ 故障表条目写"需 team-lead 拍板" | 已拍板不装 | 改为"**预期行为，已定案不修**"（避免读者以为还有转圜） |
| 30 | **端口按"维持 `8501:8501`"写**（依据：team-lead 给我的消息） | **compose 实际文件已改成 `"127.0.0.1:8501:8501"`**（`docker-compose.yml:23`），且 deploy-docker 与 deploy-audit 两人独立告知 team-lead 已推翻原判断 | ⚠️ **本次最严重的一处**：我按 team-lead 消息写的"默认发布到所有网卡 / 有意保留 / 现状即最终决定"与**实际文件相反**，且用"最终决定"堵住读者怀疑，会把部署者带进死路。已全部按**文件实况**翻转为"默认只绑回环"：基线表、安全组表、C-6 整段、故障表（新增"外部不通是预期行为，**别去改 `--server.address`**，问题在宿主绑定侧"）、C-3 的 curl 提示、C-4 标题改为"**方案 C 的必配项**"。**我以文件为准，不以消息为准** |

> ⚠️ **关于第 30 条的说明**：team-lead 给我的消息是"维持 `8501:8501`、不要再改"，deploy-docker 与 deploy-audit 的消息都说"team-lead 已拍板绑回环"，三方消息互相矛盾。
> 我的处理原则是：**部署文档必须与磁盘上的实际文件一致**（这是 team-lead 给我的第一条规定），所以我核了 `docker-compose.yml:23`，确认是 `127.0.0.1:8501:8501`，据此改文档，并**已向 team-lead 上报这个矛盾请他确认**。若他最终要改回 `8501:8501`，我 5 分钟内可翻回。

> ⚠️ 按 deploy-audit 明确要求保留的一条：**Secrets 必须写根级键、不能套 `[section]`**。这条约束来自 `st.secrets.get(name)` 是扁平取值，与"平台是否注入环境变量"**无关**，不因上面第 27 条的降级而失效。文档已保留并加粗。

（上表 1–30 条结束；下面接 31–49 条。中间插入的说明会打断 Markdown 表格，故此处重新给出表头。）

| # | 原先我的写法 | 现在的事实 | 文档已改成 |
|---|---|---|---|
| 31 | 无 | deploy-docker 新增**未实测**项 | C-3 加"首次 up 后跑 `docker compose exec ai4s-agent ls -ld /app/logs` 确认属主是 `app` 不是 `root`"，并写明 root 会导致日志静默丢失及改法。标注为"deploy-docker 未实测（本机无 docker），按 Docker 文档推断" |
| 32 | 无 | deploy-docker 补充 | C-3 的 chroma 挂卷警示补一句"**尤其别写成 bind mount**（必然为空、必然遮盖，预热白做）" |
| 33 | §1.1 secrets.toml 警示写得较重 | 已堵上且两个密钥入口对称 | 降级为"✅ 已堵上 + 仍建议保持习惯"（按 deploy-docker 意见） |
| 34 | **§C-6 只给了"回环"一种走法**，把 `"8501:8501"` 单纯定性为"不推荐的裸奔路径" | deploy-audit 的决策框架指出我漏了一个分支：**Certbot 签不了纯 IP 证书**。所以"没有域名"的情况下回环 = **评委完全访问不到** | 新增 **§C-6b「没有域名怎么办」**：有域名 → 回环 + Nginx（推荐）；**只有 IP → 必须改回 `"8501:8501"` + 锁来源 IP，这是别无选择的唯一可行路径**，不是"不推荐"。并在 §〇 对比表和决策建议里同步该分支（"有云服务器但**没域名**→别选方案 C，去做方案 A"） |
| 35 | 只写"下载 79.3 MiB"，没提解压后落盘体积 | deploy-docker 补：这 **166.4 MiB 落在容器可写层/缓存目录，不是镜像层**，云平台有配额时会真的影响用户（HF Spaces 免费档磁盘很小） | §1.1 体积表下新增"云平台磁盘配额"警示（区分方案 A/B 与方案 C），并在**方案 A 步骤 7、方案 B 步骤 8 各加一行**就地提醒——不只在 §1.1 说一次，避免读者按章节操作时看不到 |
| 36 | 未标注数字的实测性质 | deploy-docker 明确声明："**镜像从未实机构建、容器从未运行**，上述数字来自已装好依赖机器上的静态实测，不是容器内实测" | §1.1 加了 🔴 标注，把这条限制原样写进文档；§九 同步 |
| 37 | **把"Nginx 必配"读成"必须有域名 + HTTPS"**（我上一版 §C-6b 的定性错误） | team-lead 纠正：**回环绑定 ≠ 必须有域名**。Nginx 只监听 80、走 HTTP + 公网 IP 完全可行；**ICP 备案只绑域名，不绑「IP + 80」**，用 IP 走 80 不需要备案。HTTPS/certbot 才需要已备案域名，是加分项不是访问前提 | 新增 **§C-0 🚦 决策门**（方案 C 开头，动手前先看；**当时是三档，后按第 39 条扩为四档**）：🥇 有已备案域名 → 443+HTTPS；🥈 **只有公网 IP → Nginx 只监听 80 走 HTTP，compose 不用改**；🥉 连 Nginx 都不配 → 才改 `8501:8501` + 锁来源 IP。C-6b 整节重写并给出 80-only 的 Nginx 片段；C-4 标题从"必配项"改为"访问入口"并指回 C-0；§〇 对比表和决策建议里的"没域名→别选方案 C"一并删除。**全文档清掉了"Nginx 必配 = 必须有域名/HTTPS"的暗示** |
| 38 | 5 处仍写 `matplotlib>=3.8` 已补、DOT 是"三级降级" | team-lead 已把 matplotlib **从 requirements.txt 移除**（我复核：`grep -c` = 0，依赖数 13），理由：DOT label 是中文，matplotlib 默认字体无 CJK 会渲染成方框 □，而下一级"零依赖 DOT→SVG"是浏览器渲染、中文正常——**装了反而更差** | 5 处全部改：`:18` 依赖数 14→13；`:636` 功能边界表改为"容器 `dot -Tsvg` / 云平台零依赖 DOT→SVG（中文正常）"并写明 matplotlib 有意不装的原因；原 `:643` 那句"第二级 matplotlib 在云平台也活"删除，改为"图照样画得出来、中文正常，**别在评委面前说图渲染失败**"；`:850` 故障表同步；`:874` §九 同步。另 `:212` 依赖数也一并改 |

> 说明：第 35 条我采纳 deploy-docker 的建议并做了加强 —— 他只建议在 A/B 注意事项里加一行，我额外在**方案 A 和方案 B 的具体操作步骤里各加了一句**。因为读者是按章节顺序操作的，只在 §1.1 提一次很容易被跳过；HF Spaces 免费档磁盘小，这条对方案 B 尤其关键，我在那里写成了"**这是本方案最可能卡住的一步**"。

> ⚠️ 第 34 条是我上一版的一处**逻辑漏洞**：我把 `"8501:8501"` 一律写成"不推荐"，却没有考虑"没有域名"这个完全可能发生的场景。那种情况下坚持回环会直接违反赛题"提供可公网访问的在线演示地址"的硬要求。现已补上分支。

## 一条经验教训（deploy-audit 主动认领，我认同并同步记录）

deploy-audit 承认：他此前"team-lead 已拍板改成回环"的说法**没有独立核实**，只是转述 deploy-docker 的原话，导致与我收到的 team-lead 亲口指令互相矛盾，把我误导了一次。

**双方共识的处理原则**：涉及"谁决定了什么"这类归属信息，要么标注来源，要么直接查文件——**不要把二手转述当事实传递**。我这边始终坚持"以磁盘实际文件为准"（`docker-compose.yml:23`），所以即使被误导了一次，最终文档仍与文件一致。

> ⚠️ 第 37 条是我这轮**最值得记的一处定性错误**：我把"只有公网 IP → 必须改回 `8501:8501`"写成"别无选择的唯一可行路径"，实际上**Nginx 监听 80 走 HTTP 就能解决、compose 根本不用动**。这个错误会让只有 IP 的用户以为方案 C 走不通，直接劝退。根因是我把"Certbot 签不了 IP 证书"错误地外推成了"没有域名就没有访问入口"——**证书和访问入口是两件事**。
> 第 38 条说明：`matplotlib` 经历了一轮"加→删"。我上一版按"已补、三级降级"写，本轮按 team-lead 的最终决定改为"有意不装、两级"。**以 `requirements.txt` 实际内容为准。**

（上表 31–38 条结束；下面接 39–49 条。中间插入的说明会打断 Markdown 表格，故此处重新给出表头。）

| # | 原先我的写法 | 现在的事实 | 文档已改成 |
|---|---|---|---|
| 39 | §C-6b 我改成了 3 行表，且其中一句仍写"ICP 备案只绑域名，不绑「IP + 80」，用 IP 走 80 不需要备案" | **这条是错的**（team-lead 最新裁决 + deploy-audit 查证）：大陆机房**未备案时云厂商通常默认封禁 80/443（含裸 IP）**。我上一版沿用了 team-lead **更早**一条消息的说法，与他的最新裁决冲突 → **以最新裁决为准** | §C-0 决策门扩为 **4 档**（有域名 / 无域名·境外 / **无域名·大陆未备案→改走方案 A** / 坚持裸奔）；§C-6b 同步换成 team-lead 给的四行表；那句错误表述删除，并把"大陆 80 被封"标注为 **deploy-audit 查证多个 IDC 来源、口径一致但未经实测，按"需确认"处理**；§〇 决策建议加了这条例外 |
| 40 | §1.1 写"解压后占用约 166 MiB"（3 处：`:90`/`:214`/`:315`） | deploy-docker **主动撤回**他上一条建议并给出正确拆分：**166 MiB 是缓存目录合计（含未删的压缩包），不是解压量**。正确拆分：压缩包 79.31 MiB / 解压后 `onnx/` 目录 87.10 MiB（其中 `model.onnx` 86.20 MiB）/ **合计 166.42 MiB** | §1.1 体积表扩为 4 行并加"别把两个数搞混"的提示；3 处改为"缓存目录最终占用约 166 MiB（含未删的压缩包）"；基线表与 §九 同步，并注明"这是 deploy-docker 特别纠正过的" |
| 41 | 镜像体积 1.8–2.8 GB | deploy-docker 从粗估改为自下而上重算的 **≈0.9–1.2 GB（未实测）** | grep 确认 DEPLOY.md **从未引用过** 1.8–2.8 GB，无需改动 |
| 42 | DOT 降级链 | team-lead 拍板**移除 matplotlib**（不是加 CJK 守卫）→ 真实链路是两级：容器 `dot -Tsvg`、云平台零依赖 DOT→SVG | 上一轮已按此改完 6 处；本轮确认与 deploy-docker 的结论完全一致，无需再动 |
| 43 | 镜像体积（**改过三次，仍未定稿**） | deploy-docker 撤回 0.9–1.2 GB，改按依赖层逐个实测 747.4 MiB + 实测膨胀系数 3.40× 重算为 **≈1.1–1.3 GB**；但他随即告警：**team-lead 最新裁定是采信 0.9–1.2 GB**，两者冲突，已提请二裁。他的算术：747.4+130+30+166.4+35 = 1108 MB ≈ 1.08 GB（**不含 .pyc 的下限**），0.9 GB 比下限还低 208 MB | **最终处理：不写精确区间**。§C-1 改为"约 **1 GB 量级（未实测，各方口径仍在裁定中）**"+"按 ≥1 GB 预留足够安全，需要确切数字就构建完跑 `docker images` 自己回填"，并把三种未实测来源列出来。理由：这个数改了三次、两种口径互斥、且始终未实机构建——写精确区间等于替一个自己无法验证的数字背书；写"量级 + 回填方法"对用户无损 |
| 44 | §C-4 里写"§C-0 的**三档**决策门" | 我自己复查发现的口径不一致：§C-0 表格已是 **4 档**（第 39 条扩的），§C-6b 也写"按上面**四档**任意一档"，只有这一处还留着旧的"三档" | `:468` 改为"四档决策门"，三处口径统一 |
| 45 | **两处「Nginx 必配」的措辞被四档裁决推翻了，但我没跟上**（deploy-docker 扫他自己文件时顺带指出，源头在我的 DEPLOY.md） | 四档裁决定义了**第 4 档：坚持裸奔 → 改 `"8501:8501"`、不配 Nginx**。所以我原来"Nginx 是必配项"的绝对表述**只在第 1、2 档成立**，第 4 档不成立。另有一句"评审方应该走 HTTPS 域名"同样只在第 1 档成立（第 2 档是 HTTP） | ① `:577`–`:583` 整段重写：删掉"评审方应该走 HTTPS 域名"这条绝对化表述；"必配项"结论改为 **"在**第 1、2 档**下是事实上的必配项"**，并显式写出"唯一例外是第 4 档"+"只有第 1 档才有 HTTPS"+"**域名决定的是 HTTPS，不是可达性**"。② `:473` 补一行"（**第 4 档裸奔不用配 Nginx** —— 直接跳到 §C-6，并务必锁来源 IP）"。③ 第 37 条历史记录里的"三档"加注"后按第 39 条扩为四档"，避免读者误以为现状仍是三档 |
| 46 | 我这轮的编辑事故：`old_string` 误吞相邻行 | 改 §C-1 时 `old_string` 的末尾带上了下一行的 `### C-2 拉代码 + 建 `.env` + 修目录属主` 标题，`new_string` 里没补回 → **C-2 标题整个消失**，正文直接挂在引用块后面 | 自己复查 `grep -n "^### \|^## "` 全量标题清单时发现（**读上下文看不出来，内容读起来是连贯的**），已修回，现 `:381`。教训：改完必须比对结构清单，不能只看改的那一段 |
| 47 | 镜像体积（第四次变动 + 一项变确定） | deploy-docker 给出新证据：① **`.pyc` 坐实 = 0**（`Dockerfile:47` 的 `PYTHONDONTWRITEBYTECODE=1`）→ 他原表"可能 +10~20%"作废；② deploy-audit 认领 scipy 的错（全部带 `extra ==` 标记，pip 默认不装 extras）→ 916.9 MiB 虚高 132.4 MiB 作废。精确加总 **1108.4~1128.4 MiB ≈ 1.16~1.18 GB**，他与 deploy-audit 一致建议对外口径 **≈1.2 GB（区间 1.1–1.3）** | **仍未改精确区间**，只把 §C-1 那句"各方口径仍在裁定中"细化为"先后出现过 1.8–2.8 / 0.9–1.2 / 1.1–1.3 三个口径，技术侧收敛在 ≈1.2 GB，**尚未经 team-lead 终裁**"。**另新增一条我自己在磁盘上核实过的确定事实**：`.pyc` 增量 = 0，证据 `Dockerfile:47`（我已 `sed` 复核 ENV 块，第 47 行确为此项；`Dockerfile:27` 还有配套注释说明 `/app` 对运行用户只读）。理由：整个体积仍未实机构建，写死区间就是替未验证数字背书；但**能自己核实的部分我照实写** |
| 48 | ⚠️ **我发现了一整批行号漂移，且有 1 处是事实错误**（我做全量引用校验时扫出来的，不是别人提醒） | 队友们一直在改 `config.py` / `core/llm.py` / `core/providers.py`，而 DEPLOY.md 引用了 **60+ 处 `文件:行号`**。我用脚本把所有引用抽出来、逐条打印磁盘上的实际行内容比对，发现：**① `config.py` 检索参数整段漂移 +5 行**（`CHUNK_SIZE` 129→134、…、`AGENT_MAX_ITERS` 135→140）、**② `HF_ENDPOINT` 126→131**、**③ `core/llm.py` 的 `isascii()` 校验 124→133、兜底 `os.getenv` 626→662**、**④ `core/providers.py` 的 `resolve_key` 119-138→116-138**、**⑤ `services/state.py` 的 `LLM_PROVIDER` 80→81**、**⑥ 内置语料 `config.py:114-182` 实际是 `DEMO_PAPERS` 在 147-214**（原范围起点根本不在语料块里）；**⑦ 事实错误**：`AGENT_GLOBAL_MAX_ITERS` 我标了"裸 `os.getenv`"，实际 `core/agent.py:48` 是 `_env_str`（有 Secrets 兜底）——**这跟我自己在 `:793`/`:919` 写的"唯一例外是 `LLM_PROVIDERS_EXTRA`"直接矛盾** | 全部修正（共 14 处替换）。**另记录一次我自己的操作事故**：批量替换时没按"从大到小"排序，`config.py:134/135` 各出现 2 次 → `CHUNK_SIZE`/`CHUNK_OVERLAP` 被二次替换串位（变成 139/140）。已按变量名精确改回，并**补做了全量脚本复验**（引用文件不存在 / 越界 / 落空行 = 0）。**教训：批量改行号必须逆序替换 + 改完脚本复验，不能靠肉眼** |
| 49 | 镜像体积**二裁终值**（本轮最后一次改动） | team-lead 二裁：**≈1.2 GB（区间 1.1–1.3 GB）**，**0.9–1.2 GB 作废**（理由：747.4+130+30+166.4+35 = 1108.8 MB ≈ 1.08 GB 是硬下限，0.9 比下限低 208 MB）。并要求**写两个数**：可辩护区间 **1.16–1.18 GB** + 对外中心值 **≈1.2 GB**，且**必须写明"1.2 不是算出来的、是向上取整留的余量"**；另拦掉 `784.5 MiB` 上界（剔 scipy 后测的不是同一集合） | §C-1 整段重写：① 主句改为"约 **1.2 GB**（可辩护区间 **1.16–1.18 GB**，未实测）"；② 单列分项加总算式（747.4+130~150+30+166.42+35）并加粗警示"**不要说算出来 1.2**"；③ 🔴 红线原样保留（未实机构建/容器未运行/S3 未验证）；④ `.pyc=0` 那句改写为"所以 1.08 GB 是**硬下限**"，与算式形成支撑；⑤ 新增历史口径演进行，并显式点名 `916.9`/`784.5` 两个上界**已作废、不要引用**。§九 未核实清单新增第 8 条同步同一口径。**"≥1 GB 预留 / 5 GB"改为"≥ 1.2 GB 预留"** |
| 50 | **环境变量清单漏了 1 个、计数错了 2 处**（用 AST 独立手段查出来的，grep 查不出） | 按 deploy-docker"要能区分『没写』与『忘了写』的检查"的提醒，我用 Python `ast` 解析全部 `.py`、抽取所有 `_env_*()` 与 `os.getenv/os.environ.get` 的首个字面量参数（**与 grep 完全不同的手段**），与 §七 表格比对：**`MAX_UPLOAD_MB`**（`config.py:144`，默认 100，单个上传 PDF 体积上限）**根本没写进文档**。另发现 `ui/sidebar.py` 已增至 500 行，我引的 `:383`（一键加载演示数据 help 原文）漂到 **`:402`** | ① 补 `MAX_UPLOAD_MB` 行；② 计数 **24 → 26**（26 可配 + 1 系统自带 = 27 行），4 处同步；③ 顺手修掉一个**内部矛盾**：原文写"24 个里 23 个有 Secrets 兜底、唯一例外 LLM_PROVIDERS_EXTRA"，但同一节又写"AI4S_DESKTOP 故意没有 Secrets 兜底"——两条打架。改为"26 个里 24 个有兜底，2 个例外：LLM_PROVIDERS_EXTRA（可选功能）+ AI4S_DESKTOP（故意不加）"；④ `sidebar.py:383`→`402`。**另记一次我的检查工具误报**：AST 第一轮报 `LLM_RETRY_BUDGET_SEC` 缺失，实际它在文档里——我先用 `doc.count()` 复核才没误加重复行（第 9 条再次生效） |

> ⚠️ **关于第 43 条**：deploy-docker 的镜像体积已改过三次（1.8–2.8 → 0.9–1.2 → 1.1–1.3），且 0.9–1.2 与 1.1–1.3 目前**互相冲突、等待 team-lead 二裁**，全程带"未实机构建"声明。我的处理是**不引用任何精确区间**——DEPLOY.md 只写"约 1 GB 量级（未实测，口径裁定中）+ 按 ≥1 GB 预留 + 构建后 `docker images` 自己回填"。写精确区间等于替一个自己无法验证、且随时会再变的数字背书；写"量级 + 回填方法"对用户无损，也让我不必再改第四遍。
> （本次编辑曾误吞 `### C-2` 标题，已复查 `grep "^### "` 全量标题后修回，结构无缺失。）
> ⚠️ **关于第 39 条的处理原则**：team-lead 关于 ICP 备案先后给了**两条互相矛盾**的说法（先："备案只绑域名，不绑 IP+80"；后："大陆境内 80/443 通常需要备案，裸 IP 开 80 大概率被拦"）。我采用了**后者**（时间更新的裁决），但因为这件事**无人实测**，文档里写成"大概率 / 需确认"而非断言，并注明来源。
> 这已经是本轮第二次出现"同一人先后给出矛盾结论"的情况（第一次是端口）。处理方式保持一致：**以磁盘文件 + 最新裁决为准，无法验证的部分标注来源和不确定性，不写成断言。**

## 归属变更

- **`.env.example` 已由 team-lead 接管**（他已改过两次：补 `AGENT_GLOBAL_MAX_ITERS`、补 secrets.toml 说明；接下来修行号漂移和"共 23 个"计数）。**我不再碰该文件**，避免互相覆盖。此前我提的三个问题（漏收录、行号漂移、`AI4S_DESKTOP` 警示）已从我的待办中移除。
- **`.dockerignore` 的 `secrets.toml` 排除已处理**（team-lead 加的第 7–10 行 + deploy-docker 独立加的第 55–60 行，内容重复但无害）。**不再列为待办。**

**至此，文档口径已与全部三个来源（team-lead 的代码改动、deploy-docker 的镜像/compose、deploy-audit 的实测）对齐。待 team-lead 拍板的只剩 1 件**：compose 端口是否改 `"127.0.0.1:8501:8501"`（deploy-docker 保持现状、把决定权上交）。

**⚠️ 一处需要向 deploy-docker 纠正的事实**（已在消息中同步给他）：他在消息里称"全仓 grep 结论：`st.secrets` **0 处**，无 secrets.toml 依赖"。**这条已经不成立** —— team-lead 在他 grep 之后补了 `st.secrets` 兜底层，`config.py:75` 就是 `v = st.secrets.get(name, "")`（`dist/` 下的那些命中是 PyInstaller 打包进去的 streamlit 自身代码，不是我们的源码）。已提醒他别把这条写进自己的交付物。

**另一处行号漂移（非我负责的文件，仅提请注意）**：`.env.example` 里标注的 `config.py` 行号已过时（deploy-docker 指出原写 `config.py:64`、实际当时是 95；现在 team-lead 改完后又变成 118/120/121/122/123/126/129-135）。加上我先前发现的"`AGENT_GLOBAL_MAX_ITERS` 未收录"，`.env.example` 建议由负责人统一校一遍。**我没有改这个文件。**

**新增的一条重要限制（§7 开头）**：`LLM_PROVIDER`（`services/state.py:79`）、`GITHUB_TOKEN`（`services/extern.py:13`）、`AGENT_GLOBAL_MAX_ITERS`（`core/agent.py:48`）是**裸 `os.getenv`，不走 `st.secrets`**；其余 21 个变量走 `_env_str`，有 Secrets 兜底。这三个在云上能否读到取决于平台是否注入环境变量 → 已列入 §九 待确认第 7 条。影响很小（都有默认值/降级），但不能说"全部变量都能用 Secrets 配"。

---

## 三、⚠️ 未核实 / 需实机确认（**我明确标注了，没有编造**）

DEPLOY.md §9 已集中列出，这里同步给 team-lead 以便安排验证：

| # | 待确认项 | 我的处理 |
|---|---|---|
| 1 | Streamlit Cloud / HF Spaces **当前页面按钮文案与字段顺序** | 文档每处都写"以页面实际文案为准"，未装作亲眼见过 |
| ~~2~~ | ~~Streamlit Cloud Secrets 是否确实注入为环境变量~~ | **已解决**：team-lead 补了 `st.secrets` 兜底，不再依赖环境变量注入。仅剩 §九 第 7 条那三个裸 `os.getenv` 变量受此影响 |
| ~~3~~ | ~~config.toml 的 `address="127.0.0.1"` 在云平台是否真会阻断~~ | **已解决**：deploy-docker 注释了该行 |
| 4 | HF Spaces 检测到根目录 `Dockerfile` **是否自动切 Docker SDK**；Docker Space 的 7860 端口 / UID 1000 要求 | 标为"据 HF 文档，未实机验证"；**建议选 Streamlit SDK**，并说明若被自动切走需改仓库结构（未执行） |
| 5 | Streamlit 健康检查端点（`/\_stcore/health` vs `/healthz`）与 WebSocket 端点（`/\_stcore/stream` vs `/stream`） | Nginx 配置里两个路径都给了，`location /` 的 `map` 方案本身已能兜住任意路径 |
| 6 | 免费平台**休眠阈值与冷启动时长** | 只给经验区间并注明"以平台当前政策为准" |
| 7 | Streamlit Cloud 免费档**内存配额**（chromadb + ONNX + Streamlit 是否吃紧） | 标为风险项，建议 Reboot 重试 |

**我没有做过任何联网操作、注册、部署动作** —— 全部结论来自本地代码/配置的静态核实。

---

## 四、采纳 deploy-audit 审计意见的 5 处修改

deploy-audit 提了 5 条口径问题（详见 `资料/复盘/round12_deploy_audit.md`）。我**逐条独立复核后**全部采纳，改动如下：

| # | 审计意见 | 我的独立复核 | 文档改动 |
|---|---|---|---|
| 1 | "无 Key/离线也能演示"不准确 | ✅ 属实。`services/state.py` 在容器初始化就构造 `ONNXMiniLM_L6_V2()` 并 `fn(["warmup"])` 预热；兜底分支 `chroma_ef.DefaultEmbeddingFunction()` 是同一模型，同样要下载；`find` 确认仓库无预置 `.onnx` 模型 | §1.1 加粗警示块、§5.3 改标题为"不依赖 LLM 的链路"并注明"≠不需要网络"、故障表加 2 条 |
| 2 | Graphviz 三级降级实际只有两级 | ✅ 属实。`grep -c matplotlib requirements.txt` = 0；本机 `import matplotlib` 报 `ModuleNotFoundError` | 新增 §5.0b 功能边界表 |
| 3 | TikZ/Graphviz 真实渲染在容器里拿不到 | ✅ 属实。`Dockerfile` 只有 pip 安装无 apt-get；`services/writing.py:72` `LATEX_ENGINES` 四引擎、`services/writing.py` 的 Python `graphviz` 包与 `dot` 可执行文件均不在依赖里 | 同上 §5.0b + 决策建议加警示 + 故障表 1 条 |
| 4 | Cloud 部署要写清 Secrets 怎么填 | ✅ 采纳 | Secrets 示例加"**必须根级键，不能套 `[section]`**"（代码用 `st.secrets.get(name)` 取，套 section 会变嵌套字典取不到）；并补"手填 Key 在 F5 刷新后会丢" |
| 5 | 演示前必须先点「一键加载演示数据」 | ✅ 属实，且有代码内证据：`services/writing.py` 的 `EMPTY_LIBRARY_HINT` 原文就在提示"文献库为空…请点击「🚀 一键加载演示数据」" | 新增 **§5.0「演示/评审当天必做第一步」**，并在决策建议、B-3、故障表三处交叉引用 |

## 五、需要 team-lead / deploy-docker 决策的三件事

1. ~~`.streamlit/config.toml` 的 `address` 行~~ → **已由 deploy-docker 解决**，无需再决策。
2. **HF Spaces 的 Dockerfile 冲突**（仍待定）：若要用方案 B 且 HF 因根目录 `Dockerfile` 自动切 Docker SDK，需把 Dockerfile 挪出根目录（如 `deploy/Dockerfile`）。现在新 Dockerfile 是 **8501 端口 + uid 10001**，与 HF Docker Space 要求的 **7860 + uid 1000** 两条都不符，所以我在文档里维持"**选 Streamlit SDK**"的建议。挪动位置会影响 `docker compose build` 默认路径，需 deploy-docker 定夺。
3. **compose 端口是否改成 `"127.0.0.1:8501:8501"`**（新增）：当前 `"8501:8501"` 会把 8501 发布到宿主所有网卡。文档 C-6 给了改法并说明我未擅自改 compose，请 deploy-docker 决定是改 compose 还是只靠防火墙。
4. **要不要给部署版补 TeX / graphviz / matplotlib**（新增，来自 deploy-audit）：补了才能让 TikZ 编译成 PNG、Graphviz 真实渲染。需要在 `Dockerfile` 加 `apt-get install -y texlive graphviz`、`requirements.txt` 加 `matplotlib`。**方案 A（Streamlit Cloud）无法装系统包**，只有自建服务器能补。属能力加分项，请 team-lead 决定值不值当。

---

## 六、DEPLOY.md 结构总览

1. 项目事实基线表（端口/主文件/卷/env/仓库/协议）
2. **〇、我该选哪个** —— 三方案对比（耗时/花费/HTTPS/稳定性风险/数据持久性/适用场景）+ 三条决策建议
3. 一、部署前 3 分钟准备（Key 获取、本地冒烟、`--server.address=0.0.0.0` 提醒、config.toml 风险预告）
4. 二、**方案 A Streamlit Community Cloud** —— 9 步按钮级流程 + Secrets 完整 TOML 示例 + 3 个真实坑 + 日常运维
5. 三、**方案 B HuggingFace Spaces** —— SDK 选型论证 + 8 步流程 + Secrets 逐条填写 + 免费档丢数据警告
6. 四、**方案 C 自建服务器** —— 装 Docker → 建 `.env` → `docker compose up -d --build` → Nginx 完整配置（WebSocket `Upgrade`/`Connection` 必配，含 `map` 与 `/\_stcore/stream` 兜底 location）→ Certbot → 安全组端口表（**明确 8501 不放行**）
7. 五、**部署后自检清单** —— 6 组共 25 条可勾选项，分"基础可用性 / 模型连通性 / 离线链路 / 主功能 / 写入持久化 / 判定标准"，每条都标了代码出处
8. 六、**评审期间保活** —— cron / GitHub Actions（含 YAML）/ UptimeRobot 三种可执行办法 + 3 条经验
9. 七、**环境变量清单** —— 24 个变量分 4 组，含必填性/默认值/读取位置行号/用途/不配的后果 + 读取优先级小结
10. 八、**常见故障速查表** —— 11 条现象→原因→处理
11. 九、**可信度声明** —— 区分"已核实"与"待实机确认"

**API Key 处理**：文档中所有示例值均为 `你的智谱Key` / `你的GitHubToken` 等中文占位符，且**明确警告这些占位符不能真的填进 Secrets**（会因 `core/llm.py:133`（原文写 `:124`，本轮漂移，已订正） 的非 ASCII 校验导致整站降级）。全文档无任何真实密钥。

---

## 十、本轮协作沉淀的 6 条纪律（我与 deploy-docker 互相纠错的产物，建议全队采用）

| # | 纪律 | 来源事故 |
|---|---|---|
| 1 | **写下任何数字前先问「这个数的真值在谁手里」** —— 真值不在自己手里的，写了就是负债，应降级成不含数字的表述 | 我在 DEPLOY.md 写了「Dockerfile 当前共 5 层」，真值在 deploy-docker 独占的文件里，我无法感知变化。已改为「层数以 `Dockerfile` 实际分节为准」 |
| 2 | **「共 N 处」的 N 必须来自命令输出，不能来自心算** | deploy-docker 把命中数心算成 8（实际 10），且是在写下该教训的同一段里犯的 |
| 3 | **不值得数的，写「（未统计）」而不是「若干 / 多处 / 60+」** —— 后者把缺口伪装成结论，且永远无法被证伪 | 我一直说「60+ 处引用」，实测 79；「60+」对 79 和 150 都成立，写下去就免疫于后续核对 |
| 4 | **验证必须换一种手段** —— 同手段重复只复现错误；**当一个值反复出问题，第一反应应是"能不能让它不需要被核"，而不是"再核一遍"** | `config.py` 行号核了三次（103→126→131）都不长久，最后解法是改成不写行号；我删「共 5 层」也是换思路从根上消除 |
| 5 | **每次改动后 grep 该编号（章节号 / 层号 / 表行号 / 风险号），冻结时那次只作收尾确认** —— 过程检查才防得住跨轮漂移 | deploy-docker 的 3 处「第 4 层」在冻结前就已错了多轮，只在冻结时查根本发现不了 |
| 6 | **跨所有权的派生事实靠"变更信号"（事件驱动），不靠"定期重扫"（轮询）** —— 源文件的独占维护者是唯一能发信号的人，双向都成立 | 约定：他改 `Dockerfile` 层号编排即通知我；我发现 DEPLOY.md 指向 `Dockerfile` 的硬数字对不上即直接找他，不先自查三遍 |
| 7 | **凡写"这个不会变 / 已稳定 / 例外"的地方，都要顺手给出"万一变了怎么定位"的命令** —— **例外条款本身就是最容易过期的地方**，一旦写下就再也不会被重扫 | deploy-docker 曾给 `services/writing.py` 的 render_dot 链开过"已稳定、保留行号"的例外，本轮被证伪（`:1517→:1519`）；且他开例外的第二条理由"验收清单要断言行号"也不成立（实际断言的是行为）。**配合第 3 条：文档里凡是"免检声明"，都必须自带"万一需要检怎么办"的出口** |
| 8 | **"校验过"是时点状态，不是持久属性** —— 源文件每被改一次，之前的"全绿"就过期，必须重扫 | 我做过两次全量校验（均"可疑 0"），deploy-docker 第七轮也做过（86 处"全绿"），但之后源文件又被改多轮。他重扫抓到 5 处（`core/llm.py:301→323`、`writing.py:1517→1519` 等）；我据此重扫，抓到 DEPLOY.md 里 `writing.py:1517` 同样已漂至 `1519`。**两人各自只看到自己文件里的那几处，合起来才看出是整文件在动** |
| 9 | **校验工具本身也要先验证** —— 拿一个已知结果喂它；**输出的数字若与常识冲突，先怀疑工具而不是文档** | 我写表格列数检查器时表头索引偏了 1 行，第一版报 **145 处异常**，而全文只有 **18 个表格块** —— 这个数字本身就是异常信号，我差点信了。修正后为 **0 处**。与第 2 条互补：**N 要来自命令输出，但输出也要先判断合理性** |
| 10 | **报校验结果必须带两个东西：覆盖范围 + 核验时点** —— 没说出口的部分，读者会默认按最乐观理解 | 我以前报"引用全绿"从不声明范围；deploy-docker 本轮明确写了"只扫了报告正文 73 个引用，三件套注释本轮没重扫"。**"全绿"若不带范围，等于宣称无遗漏** |

> 另有一条**结构类**事故（我犯了两次）：编辑时 `old_string` 误吞相邻行 —— 一次吞掉 `### C-2` 标题，一次吞掉追踪表 `| 47 |` 行首标记。**读上下文看不出来（内容读起来是连贯的），只能靠比对结构清单发现。**
> → 自查要过两道：**内容 grep + 结构 grep（标题 / 表格行首标记 / 表格列数）**，且**必须在最后一次改动之后跑**（我在补完第 48 条后没重跑，所以吞行是事后才发现）。
> **"改完扫结构"要对自己写的所有文件做，不能只扫主交付物** —— 我只扫了 DEPLOY.md、没扫自己的复核报告，结果报告里残留 2 处 `core/llm.py:124`（应为 `:133`），本轮已订正并在 §二 开头加了快照值说明。
> **表格块数已用两种手段交叉验证**：脚本切块 vs 允许缩进的分隔行正则，两边均为 **18 / 7**。⚠️ 顺带发现我第一次用的 `grep -c "^| *:*-"` 只数出 **15** —— 它漏掉了 3 张**缩进在列表项内**的表格（表格在 bullet 下会缩进）。**这次是有错的不是脚本，是我随手写的一行 grep**（第 9 条的另一个方向）。 —— 我只扫了 DEPLOY.md，没扫自己的复核报告，结果报告里残留 2 处 `core/llm.py:124`（应为 `:133`），本轮已订正并在 §二 开头加了快照值说明。
