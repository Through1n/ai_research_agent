# DEPLOY.md —— 在线演示部署（公网可访问 · 评审期保持可用）

> 赛题硬性要求：**提供可公网访问的在线演示地址，评审期间须保持服务可用**。
> 本文件是**最短可执行路径**；`资料/在线演示部署指南.md` 是深度补充（安全红线、离线降级、故障排查细节），两者配套阅读。

**项目事实基线**（已逐个核实，文档中所有路径/端口均与此一致）：

| 项 | 实际值 | 出处 |
|---|---|---|
| 应用类型 | Streamlit | `app.py:31` `st.set_page_config` |
| 主文件 | `app.py`（仓库根目录） | `Dockerfile` 的 `CMD` |
| 端口 | `8501`（宿主:容器 = **`127.0.0.1:8501:8501`**，**默认只绑宿主回环**） | `Dockerfile` `EXPOSE 8501` / `CMD --server.port=8501`、`docker-compose.yml` `ports` |
| 数据卷 | `./storage:/app/storage` + 命名卷 `app_logs:/app/logs`（共 **2** 个） | `docker-compose.yml` `volumes` |
| env 注入 | `env_file: .env`（`required: false`，**缺文件也能起**）+ `environment` 非密钥默认值 | `docker-compose.yml` |
| 运行用户 | 非 root `app`（uid 10001） | `Dockerfile` `USER app` |
| 镜像内已烤入 | ONNX embedding 模型（缓存目录合计 **166.4 MiB**）、`dot`（graphviz，Dockerfile 第 2 层） | `Dockerfile` 第 5 层 / 第 2 层 |
| 镜像内没有 | TeX（TikZ 编译器） | `Dockerfile` 无对应安装 |
| 依赖数量 | 13 个（多个包补了跨大版本上界） | `requirements.txt` |
| Python | 3.11（`python:3.11-slim`） | `Dockerfile:19` |
| 健康检查 | `/\_stcore/health` | `Dockerfile` `HEALTHCHECK` |
| Git 仓库 | `https://github.com/Through1n/ai_research_agent.git`，分支 `main` | `git remote -v` |
| 开源协议 | MIT | `LICENSE` |
| 是否需要数据库 | **不需要**，全部状态在 `storage/` | `config.py:113-115` |

> 本表在 Round 12 末次核对时与 `Dockerfile` / `docker-compose.yml` 的当前内容一致（这两文件由 deploy-docker 在本次迭代中重写过）。

> 📌 **关于本文出现的「Dockerfile 第 N 层」**：这是**文档分节编号**，对应 `Dockerfile` 里可见的 `# ---------- 第 N 层 ----------` 注释标题（**层数以 `Dockerfile` 实际分节为准**，此处不写死数字——该文件由他人维护，写死就会成为跨文件的漂移点），**不等于 `docker history` 输出的层序号**（例如 `USER app` 会单独占一层）。
> → 想按层定位请 `grep -n "第 N 层" Dockerfile`；**别用 `docker history` 去数**，数出来对不上不是文档写错了。

---

## 〇、我该选哪个（先看这张表）

| | **A. Streamlit Community Cloud** | **B. HuggingFace Spaces** | **C. 自建服务器 Docker+Nginx** |
|---|---|---|---|
| **预计耗时** | 20–40 分钟（首次构建 3–10 分钟，经验值） | 30–60 分钟（含推代码，经验值） | 40–90 分钟（含域名解析与证书） |
| **花费** | ¥0 | ¥0（免费 CPU basic） | ¥24–120/月（云服务器）+ 域名 |
| **公网 HTTPS** | 平台自带 | 平台自带 | 有域名→Certbot 免费签；**只有公网 IP 也能用**（Nginx 监听 80 走 HTTP），见 §C-6b |
| **稳定性风险** | ⭐⭐ 中：免费档共享资源、无访问会休眠、平台随时可能限流 | ⭐⭐ 中：同样会休眠；**免费档文件系统非持久，重启后 `storage/` 数据丢失** | ⭐ 低：自己掌控，唯一风险是机器宕机 |
| **数据持久性** | 较好（平台挂载，但非保证） | **差**（免费档重启即清空） | **好**（宿主 `./storage` + 可备份） |
| **适合什么情况** | 首选主地址：没服务器、不想花钱、想最快拿链接 | 备份地址 / Streamlit Cloud 挂了时的应急 | 最稳：有云服务器，愿意花 1 小时换全程可控 |

### 决策建议（照抄即可）

- **只有 30 分钟 + 不想花钱** → 走 **方案 A**。拿到 `https://xxx.streamlit.app`，立刻填进评审材料。
- **有云服务器（有域名或只有公网 IP 都行）** → **方案 C 做主地址**（最稳、数据可备份），同时 **方案 A 做备份地址**，评审材料里给两个链接。**没域名也能走**：Nginx 只监听 80 即可，见 §C-0。
  ⚠️ 例外：**大陆境内未备案**的机器，80/443 大概率被封，那种情况直接选**方案 A**（见 §C-0 第 3 档）。
- **方案 A 名额/网络不通** → **方案 B** 顶上，但务必注意它重启丢数据，评审前先点一次「🚀 一键加载演示数据」。
- **三条都做**是最稳的，成本约 1 小时。
- ⚠️ **不管选哪条，演示第一动作都是点「🚀 一键加载演示数据」**（文献库初始为空，见 §5.0）；且**首次启动必须能出网**（要拉一次向量模型，见 §1.1）。
- ⚠️ 部署版**没有 TeX**，讲解时别宣称"TikZ 编译成 PNG"（只展示源码）。Graphviz DOT 图**能画出来**，但方案 A/B 拿不到最高质量那级 `dot -Tsvg`（系统包装不了），详见 §5.0b。

---

## 一、部署前 3 分钟准备（三个方案都要做）

### 1.1 准备 API Key（没有也能演示，但建议有）

应用**不配 Key 也能打开并用大部分离线功能**（`config.py:147-334` 的 `DEMO_PAPERS` 内置 6 篇演示论文语料），但问答 / 翻译 / 写作三块会走离线降级。评审要展示完整能力，建议配一个。

> ⚠️ **「离线」的准确含义（别理解错，这条容易翻车）**，完整时序是两句，**两句都要看**：
>
> **① 首次启动即需联网拉模型，且失败是静默的。**
> `app.py:86 get_container()` → `services/state.py` 的 `Container.__init__` → `_build_embed_fn()` → `fn(["warmup"])`，
> **应用一启动就触发下载**。但这次下载失败会被 `except Exception` 吞掉、界面不报任何错，只是 fallback 到 `DefaultEmbeddingFunction()`。
>
> **② 现象要到用户点「🚀 一键加载演示数据」时才暴露** —— 那时才真正 `ctx.embed` 抛异常，表现为**6 篇全部「⚠️ 失败」**。
>
> → 只写第①句，运维不知道故障长什么样；只写第②句，会让人以为可以"先离线进去再说"。
> → 演示**语料**是内置的（不需要联网下载论文），要联网的只是这个向量模型。

**但这个模型已经烤进 Docker 镜像了**（deploy-docker 本轮的改动）：

| 场景 | 是否需要出网 | 说明 |
|---|---|---|
| **方案 C（Docker 镜像）** | **运行期不需要**（构建期需要） | Dockerfile 第 5 层在构建期预热并把模型烤进镜像（`/home/app/.cache/chroma/onnx_models/all-MiniLM-L6-v2`，缓存目录合计 **166.4 MiB**），容器冷启动零外网 |
| **方案 A / B（云平台）** | **需要** | 平台自己装依赖跑应用，不走我们的 Dockerfile，**首次冷启动要下载 79.3 MiB** |
| 本地直接 `streamlit run` | 需要 | 同上 |

> 📏 **模型体积（实测，由 deploy-audit 与 deploy-docker 给出，非估算；我未在本地复现）**
> 统一用双单位，避免数字打架（此前"79.3"和"90"指的是不同文件、且 MiB/MB 混用）：
>
> | 项目 | 字节 | MiB | MB |
> |---|---|---|---|
> | 下载 / 压缩包 `onnx.tar.gz` | 83,178,821 | **79.31 MiB** | 83.2 MB |
> | 解压后 `onnx/` 目录 | 91,332,068 | **87.10 MiB** | 91.3 MB |
> | └ 其中 `model.onnx` 单文件 | 90,387,606 | 86.20 MiB | 90.4 MB |
> | **缓存目录合计（含压缩包）** | 174,510,889 | **166.42 MiB** | 174.5 MB |
>
> ⚠️ 别把两个数搞混：**79.3 MiB 是下载量，87.1 MiB 是解压量，166.4 MiB 是缓存目录合计（含那个没删的压缩包）**。
> 早期流传的"90MB"指的是 `model.onnx` 单文件（90.4 MB），跟"79.3"不是同一个东西，且 MiB/MB 混用——文档已统一为双单位。
>
> 下载地址：`https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz`（`Dockerfile` 注释里的 `MODEL_DOWNLOAD_URL`，与实测 URL 一致）
>
> ⚠️ **云平台要额外注意磁盘配额**：缓存目录合计 **166.4 MiB**，落在**容器可写层 / 缓存目录**，不是镜像层。
> → **方案 A / B**：首次冷启动下载 79.3 MiB、**缓存目录最终占用约 166 MiB**，确保平台配额足够（HF Spaces **免费档磁盘很小**）。
> → **方案 C（自建 Docker）**：已烘进镜像，**不触发下载、用户感知不到**，可以放心写"离线可用"。
>
> 🔴 上述数字均来自当前这台已装好依赖的机器上的**静态实测**（HTTP `content-length` + 本机 `du`）；deploy-docker 明确声明：**镜像从未实机构建、容器从未运行**，这些不是容器内实测值。

**首次启动（或首次构建）需要的外网出口清单**（少一个就少一块功能）：

| 目标 | 用途 | 断了的后果 | 方案 C 什么时候需要 |
|---|---|---|---|
| **chroma 的 ONNX 模型（AWS S3，下载 79.3 MiB）** | 向量模型 | **致命**：连「一键加载演示数据」都失败，离线演示整条链走不通 | **仅构建期**（已烤进镜像，运行期不需要） |
| PyPI（装 13 个依赖） | 依赖安装 | 构建失败 | 构建期 |
| Debian 源（装 graphviz） | `dot` 二进制 | graphviz 那层构建失败 | 构建期 |
| 你的 LLM Provider 端点（默认 `open.bigmodel.cn`） | 问答 / 翻译 / 写作 | 走离线降级，页面顶部出失败横幅 | 运行期 |
| `arxiv.org` | 论文检索与追踪 | 检索页搜不到东西 | 运行期 |
| PubMed / DuckDuckGo | 检索页的另外两路数据源 | 该路源无结果 | 运行期 |
| `api.github.com` | 「个性化推荐」里的开源代码可得性 | 自动降级（`services/extern.py:181` 已有明确提示） | 运行期 |

> ⚠️ 向量模型是**从 chroma 自己的 S3 直下，不走 HuggingFace**，所以 `HF_ENDPOINT` 镜像对它**无效**（结论来自 deploy-docker，本机未装 chromadb 无法复现）。别指望靠配 HF 镜像解决。
> ⚠️ **方案 C 若构建机连不上 S3**：把 Dockerfile 第 5 层的 `RUN` 注释掉，改用 `COPY --chown=app:app ./onnx_models /home/app/.cache/chroma/onnx_models`（宿主机先跑一次 warmup 生成缓存）。详见 Dockerfile 该处注释。**本文档未改动 Dockerfile。**

推荐 **智谱 GLM**：`https://open.bigmodel.cn` 注册 → 控制台 → API Keys → 复制 Key（`glm-4-flash` 文本免费）。它是唯一内置视觉模型 `glm-4v` 的默认 Provider（`core/providers.py:34-41`），扫描件 OCR 能用。

> 🔒 **红线**：Key **只能**走平台 Secrets / 环境变量 / 侧边栏手填，**绝不能**写进任何会被 git 跟踪或被打进镜像的文件。
> 仓库只有 `.env.example` 占位模板，真实 `.env` 已被 `.gitignore:2` 与 `.dockerignore:2` 双重排除。
>
> ✅ 这个口子已经堵上了：`.dockerignore` 已补 `.streamlit/secrets.toml` 的**精确排除**（只排除这一个文件，`config.toml` 照常进镜像，主题配色与 `enableStaticServing` 不受影响）。两个密钥入口现在对称了 —— `.env` 和 `secrets.toml` 在 `.gitignore` 和 `.dockerignore` 里**都被排除**。
>
> 仍然建议保持这个习惯：**别把真实 Key 写进 `.streamlit/secrets.toml` 做本地调试**（万一哪天有人又把它放开）。
>
> ℹ️ **方案 C（Docker）不需要 `secrets.toml`**：容器里没有这个文件也不会崩——`config.py:61-78` 的 `_st_secret()` 整个包在 `try/except` 里，访问 `st.secrets` 抛异常时一律返回空串。方案 C 用 `.env` 就够了。

### 1.2 本地冒烟（可选，但强烈建议）

```bash
cd ai_research_agent
pip install -r requirements.txt
streamlit run app.py --server.port=8501
```

浏览器打开 http://localhost:8501 能看到界面，再上云。
**⚠️ 手动跑时若要给别人（或另一台机器）访问，必须加 `--server.address=0.0.0.0`**：

```bash
streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

（`.streamlit/config.toml` 里的 `address` 现已注释掉，不传参时由 Streamlit 默认值决定；显式传参最保险。详见 §1.3。）

### 1.3 ✅ 已知无风险：`.streamlit/config.toml` 的 `address`

> 这条原先是方案 A / B 的头号阻塞点，**已彻底解决**，留着说明仅供理解背景。

- team-lead 把 `.streamlit/config.toml` 里的 `address = "127.0.0.1"` **注释掉了**（现第 20 行附近，附了说明）
- deploy-docker 把 `.dockerignore` 里 `.streamlit/` 的排除规则**删掉了**（现在那里是说明注释），所以容器里是有 config.toml 的，但里面没有写死的回环地址
- `Dockerfile` 的 `CMD` 另外显式带 `--server.address=0.0.0.0`，命令行优先级本来就高于 config.toml

→ **Docker 和云平台两个方案都不受影响。**

`.streamlit/config.toml` **已被 git 跟踪**（`git ls-files` 确认），云服务会读到它。Streamlit 的配置优先级是「**命令行 > 环境变量 > config.toml > 默认值**」，而云平台是平台自己 `streamlit run`，不会帮你传 `--server.address`，所以只要 config.toml 里写死 `127.0.0.1`，云上就只能绑回环地址 → **页面一直转圈打不开**。

现在的分工（三处都能跑）：

| 场景 | 谁决定监听地址 |
|---|---|
| 本地 / 桌面版 | 启动脚本显式带 `--server.address=127.0.0.1`，行为不变 |
| **Docker（方案 C）** | `Dockerfile` 的 `CMD` 带 `--server.address=0.0.0.0`，命令行优先级高于 config.toml |
| **云平台（方案 A / B）** | config.toml 里该行**已注释**，交由平台决定 |

> ⚠️ 如果你在别的分支 / 旧版本上部署，**先确认 config.toml 里没有生效的 `address = "127.0.0.1"`**。判断方法：部署后一直转圈 / 502 / 连接被拒时，看日志里 Streamlit 绑到了哪个地址。

另外 `enableStaticServing = true`（config.toml）与 Dockerfile 的 `--server.enableStaticServing=true` 是必需的：`ui/page_writing.py` 从 `/app/static/mermaid.min.js` 本地加载 mermaid，关掉会退化到 CDN，离线环境图谱就废了。

### 1.4 ⚠️ 已知安全边界：XSRF 防护当前是关着的（有意保留）

`.streamlit/config.toml:23` 写着 `enableXsrfProtection = false`。这是**主动选择，不是漏配**。

**为什么关着**：Streamlit 的 XSRF 校验要靠 `Origin` / `X-XSRF-TOKEN` 头与反向代理配合。
本仓库交付时**没有真实的 Nginx 环境可验证**改成 `true` 后是否仍能正常交互。
而 XSRF 一旦在代理后面误触发，表现不是"少一层防护"，是**点任何按钮都失败、页面直接不可用**。
两害相权：先交付一个能访问的站点，再谈加固。

**三种方案的影响不同，按你的选型处理**：

| 你的方案 | 建议 | 理由 |
|---|---|---|
| A 云平台 / B HF Spaces | 建议改回 `true` | 平台自己托管、你不控制反代，XSRF 走 Streamlit 默认行为，误触发面小 |
| C 自建（Nginx 反代） | 先保持 `false`，按下面步骤验证后再改 | 这正是**未验证**的那条链路 |

> 对 A / B 的那条建议，风险低但**同样未经本仓库实测** —— 改完必须走下面第 3 步验证，异常就回滚。

**怎么改**：

1. 编辑 `.streamlit/config.toml`，把 `enableXsrfProtection` 改成 `true`
2. `docker compose restart ai4s-agent` —— **不需要重新 build**，`config.toml` 是挂载进容器的
   （方案 A / B 直接在平台上重启应用）
3. **改完只验一件事**：用**无痕窗口**打开站点，点一个会改状态的按钮
   （推荐「🚀 一键加载演示数据」）→ 必须仍然弹出 `✅` 成功提示

若第 3 步变成报错、白屏或无响应 → 代理链路下 XSRF 校验误伤。
**先改回 `false` 恢复可用**，再去查 Nginx 是否透传了 `Origin` 与 `X-XSRF-TOKEN` 头（见 C-4 节）。

> 演示当天不要动这个开关。它的正确使用时机是「从一次性演示转为长期公网运行」。

---

## 二、方案 A：Streamlit Community Cloud（免费首选）

> 下面按钮文案为撰写时对平台的了解，**若页面实际文案不同，以页面实际文案为准**。

### A-1 逐步骤（精确到点哪里）

1. 浏览器打开 **https://share.streamlit.io**
2. 点右上角 **Sign in** → 选 **Sign in with GitHub** → 在弹出的 GitHub 授权页点 **Authorize**
   - 授权范围建议选 **Only select repositories** → 只勾 `ai_research_agent`（最小权限，避免把整个 GitHub 账号暴露给平台）
3. 回到 share.streamlit.io，点右上角 **New app**（若看到下拉，选 **Deploy a public app from GitHub**）
4. 表单按顺序填：

   | 字段 | 填什么 |
   |---|---|
   | **Repository** | `Through1n/ai_research_agent`（输入框支持搜索，打 `ai_research` 即可选到） |
   | **Branch** | `main` |
   | **Main file path** | `app.py` |
   | **App URL**（子域名） | 自定义，例如 `ai4s-agent`，最终地址形如 `https://ai4s-agent.streamlit.app` |

5. 点 **Advanced settings** 展开 → 找到 **Secrets** 大文本框 → 粘贴下面的 TOML（**先看完 A-2 的警告再填**）：

```toml
# ===== Streamlit Community Cloud · Secrets（TOML 格式）=====
# ⚠️ 全部写成【根级键】，不要套 [section] 表头 —— 本应用读的是 os.getenv，
#    套进 [section] 会变成嵌套字典，os.getenv 读不到。
# 只保留你真正用到的行；没用到的变量请【整行删除】，不要留空值或中文占位符。

# 默认 Provider：zhipu | deepseek | qwen | openai | ollama
LLM_PROVIDER = "zhipu"

# 智谱 Key（真实值，纯 ASCII）
ZHIPU_API_KEY = "你的智谱Key"

# —— 以下按需保留，不需要就整行删掉 ——
# DEEPSEEK_API_KEY = "你的DeepSeekKey"
# DASHSCOPE_API_KEY = "你的通义千问Key"
# OPENAI_API_KEY = "你的OpenAIKey"
# LLM_API_KEY = "你的通用兜底Key"

# 可选：HuggingFace 镜像（见下方说明，对 Chroma 的向量模型下载【不一定生效】）
HF_ENDPOINT = "https://hf-mirror.com"

# 可选：GitHub API 提额（只影响「个性化推荐」的开源代码检出率）
# GITHUB_TOKEN = "你的GitHubToken"
```

6. 点右下角 **Deploy**（按钮文案可能是 **Deploy!** / **Save**，以页面为准）
7. **等 3–10 分钟**（经验值）。首次要装 `requirements.txt` 的 13 个包（`chromadb`、`PyMuPDF`、`streamlit` 体积都不小）；页面会显示构建日志滚动。
   ⚠️ **首次冷启动还会下载 79.3 MiB 向量模型，缓存目录最终占用约 166 MiB**（含未删的压缩包，落在容器可写层）。若平台报"空间不足"，多半是这里——见 §1.1。
8. 页面出现应用预览 + 顶部显示公网 URL（形如 `https://ai4s-agent.streamlit.app`），**复制这个 URL**。
9. 立刻按 [第五章 自检清单](#五部署后自检清单可勾选) 走一遍。

### A-2 ⚠️ 两个真实会踩的坑

**坑 1：不要把中文占位符真的填进 Secrets。**
`core/llm.py:133` 会校验 `key.isascii()`，非 ASCII Key 直接抛 `LLMConfigError`，页面顶部弹出降级横幅，功能全残。
→ 要么填**真实纯英文 Key**，要么**整行删掉**。（`config.py:81` 的 `_env_str` 把空串当未设置，留空不会有问题，但不如直接删行干净。）

**坑 2：Secrets 必须写【根级键】，不能套 `[section]`。**
代码读 Secrets 用的是 `st.secrets.get(name)`（`config.py:61-77` 的 `_st_secret`）。如果你写成：

```toml
[api]
ZHIPU_API_KEY = "xxx"      # ❌ 错：变成嵌套字典，st.secrets.get("ZHIPU_API_KEY") 取不到
```

必须写成根级：

```toml
ZHIPU_API_KEY = "xxx"      # ✅ 对
```

**✅ 关于"Secrets 能不能被读到"——已经不需要担心了。**
仓库原先只读 `os.getenv`，云上拿不到 Key；现在 team-lead 已补上 `st.secrets` 兜底层：
- `config.py:61` `_st_secret()`：`st.secrets.get(name)`，非 Streamlit 环境（桌面版/脚本）访问会抛异常并被吞掉，行为不变
- `config.py:81` `_env_str()`：读取顺序 **环境变量（含 .env 注入）→ Streamlit Secrets → 默认值**
- `core/providers.py:116-138` `resolve_key()`：**手动填写 > 环境变量 > Streamlit Secrets**
  → 所以「平台是否把 Secrets 注入环境变量」这件事**不再影响可用性**，两条路都通。

**仍可用的兜底**：侧边栏「🏢 预设平台」选平台后直接在 Key 输入框手填，`resolve_key()` 里 `manual` 优先级最高。
⚠️ 手填的 Key 存在 `st.session_state` 里，**按 F5 刷新 / 换浏览器 / 应用重启都会丢，需要重新粘贴**。只适合临时救急。

**坑 3（已解决）**：`.streamlit/config.toml` 里 `address = "127.0.0.1"` 导致云平台打不开 —— 该行已被注释，见 §1.3。

### A-3 日常运维

- 改 Secrets：应用页 → 右上角 **Manage app**（或 ⚙️ Settings）→ **Secrets** → 改完 **Save** → 应用会自动重启
- 卡死/异常：Manage app → **Reboot app**
- 看日志：Manage app → **Logs**（构建失败、ImportError 都在这里）
- 免费档共享资源，偶发 OOM / 冷启动慢属正常，Reboot 即可

---

## 三、方案 B：HuggingFace Spaces（免费备份）

> 按钮文案以页面实际为准。

### B-1 选 SDK：**强烈建议选 Streamlit，不要选 Docker**

本仓库根目录有 `Dockerfile`，它是给方案 C 用的。HF Spaces 若检测到根目录 `Dockerfile` 可能自动按 Docker SDK 处理（**⚠️ 此行为待实机确认**），而 HF 对 Docker Space 有额外硬性要求（据 HF 文档，**未实机验证**）：

- 监听端口必须是 **7860** —— 本项目的 Dockerfile 是 **8501**（`EXPOSE 8501` + `CMD --server.port=8501`），**不满足**
- 容器内需以 **UID 1000** 运行 —— 本项目 Dockerfile 是 `USER app`（**uid 10001**，deploy-docker 为安全加固有意选的），**也不满足**

走 Docker SDK 大概率起不来。**所以选 Streamlit SDK。**

**根目录的 `Dockerfile` 不要挪**（deploy-docker 已定，我也认同）：`docker compose build` / `docker build .` 认根目录 Dockerfile 是硬约定，挪到 `deploy/` 会让"一键起"多一个 `-f` 参数，得不偿失。

> 若 Space 页面显示 SDK 变成了 `docker` 而非 `streamlit`，或你确实想走 Docker SDK：
> **在 Space 仓库里单独写一份自己的 Dockerfile**（7860 端口 + UID 1000），不要去动根目录那份 —— 参数冲突不是挪位置能解决的。
> 另需注意：新 Dockerfile 第 5 层会在构建期下载 ONNX 模型，HF 的构建环境能否访问 AWS S3 **未实机验证**。

### B-2 逐步骤

1. 打开 **https://huggingface.co** → 注册/登录
2. 右上角头像 → **New Space**
3. 表单：

   | 字段 | 填什么 |
   |---|---|
   | **Owner** | 你的 HF 用户名 |
   | **Space name** | `ai4s-research-agent` |
   | **License** | `mit`（与仓库 `LICENSE` 一致） |
   | **Space SDK** | **Streamlit** |
   | **Space hardware** | **CPU basic**（免费档） |
   | **Visibility** | **Public**（评审要能访问，必须公开） |

4. 点 **Create Space**
5. 取一个写权限 Token：HF 头像 → **Settings** → **Access Tokens** → **New token** → 类型 **write** → 复制
6. 本机推代码（在仓库根目录执行）：

```bash
git remote add space https://huggingface.co/spaces/<你的用户名>/ai4s-research-agent
git push space main
# 弹出认证时：用户名 = HF 用户名，密码 = 上一步的 write token
```

7. 加 Secrets：Space 页面 → **Settings** 标签 → **Variables and secrets** → **New secret**

   | Name | Value |
   |---|---|
   | `LLM_PROVIDER` | `zhipu` |
   | `ZHIPU_API_KEY` | 你的真实智谱 Key（纯 ASCII，**不要填中文占位符**，原因见 A-2 坑 1） |
   | `HF_ENDPOINT` | `https://hf-mirror.com`（可选） |

   > 这里**不是** TOML，是一个个 Name/Value 单独添加。
   > 能读到是因为代码新增了 `st.secrets` 兜底层（`config.py:61` / `core/providers.py:135-138`），原理见 A-2。

8. **等构建 5–15 分钟**（经验值；Docker SDK 更久，Streamlit SDK 主要是装依赖）。
   ⚠️ **首次冷启动还会下载 79.3 MiB 向量模型，缓存目录最终占用约 166 MiB**。HF Spaces **免费档磁盘很小**，这是本方案最可能卡住的一步——见 §1.1。
   Space 页面出现 **Running** 状态 + 顶部 URL（形如 `https://huggingface.co/spaces/<用户名>/ai4s-research-agent`，应用本体在 **App** 标签或直接 `https://<用户名>-ai4s-research-agent.hf.space`）。
9. 走 [第五章 自检清单](#五部署后自检清单可勾选)。

### B-3 ⚠️ 免费档丢数据（最重要的一条）

HF Spaces 免费档的容器文件系统**不是持久的**，Space 重启后 `storage/`（向量库、`library.json`、`memory.json`、上传的 PDF）**会被清空重建**。

对策：
- 每次评审/演示前，先点侧边栏「🚀 一键加载演示数据（6 篇）」重建 6 篇内置论文
- 别指望上传的 PDF 会留到明天
- 若要持久存储，需 HF 的付费 Persistent Storage（本方案按免费档说明）

（`.streamlit/config.toml` 的 `address` 问题已解决，见 §1.3，方案 B 同样受益。）

---

## 四、方案 C：自建服务器（Docker + Nginx + HTTPS）

最稳的方案。前置条件：一台有公网 IP 的 Linux 服务器（腾讯云轻量 / 阿里云 ECS 均可，2 核 4G 够用）+ 一个已解析到该 IP 的域名。

### C-0 🚦 先选档位（**动手前花 30 秒看这张表**）

compose 默认把容器端口**只绑在宿主回环**上（`- "127.0.0.1:8501:8501"`），所以外部要能访问，中间必须有个"入口"。按你的条件选一档：

| 档位 | 你的条件 | 怎么做 | compose 那行要改吗 | 评委访问地址 |
|---|---|---|---|---|
| 🥇 **推荐** | **有已备案域名**，已解析到这台服务器 | Nginx 监听 **443** + Certbot 签 HTTPS（按 C-4 / C-5 走） | **不用改** | `https://你的域名.com` |
| 🥈 **也能用** | 无域名，服务器在**境外**（HK / 新加坡 / 美西等）或境内外已备案 | Nginx **只监听 80**、走 HTTP 即可（C-4 配置里去掉 443 那段，C-5 整节跳过） | **不用改** | `http://<公网IP>` |
| ⚠️ **建议换方案** | 无域名，服务器在**大陆境内且未备案** | **别选方案 C，改走方案 A** | —（无所谓了） | `https://xxx.streamlit.app` |
| 🥉 **不推荐但可用** | 坚持不要反向代理 | 把 compose 改成 `- "8501:8501"` | **要改** | `http://<公网IP>:8501` |

> 🔑 **关键澄清：回环绑定 ≠ 必须有域名。**
> 回环只表示"容器端口不直接对外"，中间用 Nginx 转发即可：
> ```
> 容器内 Streamlit --bind 127.0.0.1:8501
>    ↓ compose "127.0.0.1:8501:8501"
> 主机 127.0.0.1:8501          ← 主机自己访问得到
>    ↓ Nginx listen 0.0.0.0:80; proxy_pass http://127.0.0.1:8501;
> 公网 http://<公网IP>/         ← 评委访问得到 ✅
> ```
> **域名只决定「能不能上 HTTPS」，不决定「能不能被访问」。** Certbot 签不了纯 IP 证书，后果是"只能 HTTP"，**不是"访问不到"**。
> 所以：有域名 → 回环 + HTTPS；无域名 → 回环 + Nginx :80 走 HTTP 照样可达（明文）。

> ⚠️ **但第 2 档有个前提：服务器的 80 端口对公网得可达。**
> **中国大陆机房的云服务器，未备案时云厂商通常默认封禁 80/443 端口（含裸 IP 访问）** —— 这条由 deploy-audit 查证多个 IDC 来源，**口径一致但未经实测**，按"需确认"处理，别当成"一定可行"。
> → 所以上表把"无域名"按**境外 / 大陆未备案**拆成了两档：前者走第 2 档，后者**建议直接改选方案 A**（免费自带公网 HTTPS 域名，一步到位，见 §二）。

> ⚠️ 第 4 档（直接暴露 8501）**必须同时在安全组限制来源 IP**（只放行你/评委的出口 IP）——
> 应用本身**没有任何鉴权**，无限制地暴露等于让别人用你的 Key 调模型，Key 被刷爆 = 服务不可用。

### C-1 装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
docker --version          # 有输出即成功
```

> 💾 **磁盘预留**：构建出来的镜像约 **1.2 GB**（可辩护区间 **1.16 – 1.18 GB**，**均未实测**）；加上构建缓存，建议给 `/var/lib/docker` 留 **5 GB 以上**。
>
> ⚠️ **1.2 不是"算出来的"，是取整后的对外值。** 分项加总是：
> `747.4`（依赖层，109 个包逐个实测）+ `130~150`（基础层）+ `30`（graphviz）+ `166.42`（ONNX 模型）+ `35`（业务代码）= **1,108.4 ~ 1,128.4 MiB ≈ 1.16 ~ 1.18 GB**
> 而 **1.2 GB 是在这个区间上向上取整**，为"Windows 侧 `.dll`/`.pyd` vs Linux 侧 `.so`"这个**尚未实测**的平台二进制差异留的余量。被追问时请按上面这个算式答，**不要说"算出来 1.2"** —— 1.2 不在算式里，直接这么说整条推算的可信度都会掉。
>
> 🔴 **红线：镜像从未实机构建、容器从未运行、构建机能否访问 AWS S3 未验证。** 上面的数是静态推算，不是 `docker images` 的实测输出。
> → 按 **≥ 1.2 GB** 预留；需要确切数字就构建完跑一次 `docker images` 自己回填。
>
> ✅ **其中一项已确定**：`.pyc` 增量为 **0** —— `Dockerfile:47` 设了 `PYTHONDONTWRITEBYTECODE=1`（多行 `ENV` 块第 45 行起，第 47 行即此项），不生成 `__pycache__`。所以上面那个 1.08 GB 算术下限是**硬下限**，不含隐藏的 `.pyc` 膨胀。
>
> 📌 历史口径演进（供追溯，最终以本段为准）：1.8–2.8 GB（粗估）→ 0.9–1.2 GB（**已作废**，低于算术下限）→ 1.1–1.3 GB → **≈ 1.2 GB（当前）**。另：`916.9 MiB` 与 `784.5 MiB` 两个上界**均已作废**（前者是 conda 开发环境、含镜像不装的包；后者是剔掉 scipy 后的错集合，测的不是同一个包集），**不要引用**。

### C-2 拉代码 + 建 `.env` + 修目录属主

```bash
git clone https://github.com/Through1n/ai_research_agent.git
cd ai_research_agent
cp .env.example .env      # 建一份本地 .env（compose 里是 required:false，没有也能起，但没有 Key）
nano .env                 # 或 vi .env
```

在 `.env` 里填（**只填真实 Key，其余保持注释状态**）：

```bash
LLM_PROVIDER=zhipu
ZHIPU_API_KEY=你的智谱Key
HF_ENDPOINT=https://hf-mirror.com
```

> `AI4S_DESKTOP` **必须留空或为 `0`**：设为 `1` 会进入桌面模式（`config.py:10`），数据跑到 `%APPDATA%/AI4SResearchAgent`，Web 部署下路径会错乱。`docker-compose.yml` 已在 `environment` 里显式写了 `AI4S_DESKTOP: "0"`。

### C-2b ⚠️ Linux 宿主必做：给 `storage/` 改属主（漏了会「存储目录不可写」）

`Dockerfile` 现在以**非 root 用户 `app`（uid 10001）**运行。宿主的 `./storage` 若由 `docker` 自动创建会属于 root，容器内 app 写不进去 → 应用报「⚠️ 操作失败：初始化服务容器」（`app.py:88-95`）。

**Linux 宿主在首次启动前执行一次**：

```bash
mkdir -p storage
sudo chown -R 10001:10001 storage
```

> Windows / macOS 的 Docker Desktop 无此问题，可跳过。
> 想直接在宿主上看应用日志，把 compose 里的 `app_logs:/app/logs` 换成 `- ./logs:/app/logs`，并同样 `sudo chown -R 10001:10001 logs`。

### C-3 启动容器

```bash
docker compose up -d --build      # Compose v2；老版本用 docker-compose up -d --build
docker compose ps                 # 状态应为 Up (healthy)
docker compose logs -f            # 看启动日志，Ctrl+C 退出（不会停容器）
```

首次构建约 3–10 分钟（装依赖 + Debian 装 graphviz + 构建期下载 79.3 MiB 的 ONNX 模型）。`HEALTHCHECK` 给了 120s 的 `start_period`，所以 `docker compose ps` 会先显示 `(health: starting)`，等它变成 `(healthy)` 再访问。

本机验证（**不要**在服务器上用浏览器，先用 curl）：

```bash
curl -I http://127.0.0.1:8501                  # 应返回 200
curl -s http://127.0.0.1:8501/_stcore/health   # 健康检查端点，应返回 ok
# ⚠️ 必须写 127.0.0.1：compose 默认只绑宿主回环，用宿主 IP 会"连接被拒"——那是预期行为，不是故障
```

常用运维：

```bash
docker compose restart            # 重启
docker compose down               # 停止并移除容器（数据仍在 ./storage 与命名卷里）
docker compose up -d --build      # 改代码后重新构建
docker compose logs --tail=200    # 看最近 200 行日志
```

**数据备份**：业务数据全在 `./storage`，直接打包即可。

```bash
tar czf storage-$(date +%F).tar.gz storage/
```

> compose 里还有一个**命名卷** `app_logs:/app/logs`（应用异常日志，`app.py:15` 的 `LOG_DIR="logs"`）。
> 用命名卷而非 bind，是为了继承镜像内目录的属主 `app:10001`，避免宿主目录权限不匹配时日志被静默吞掉。
> 想直接在宿主上看日志，把这一行换成 `- ./logs:/app/logs`，并先 `sudo chown -R 10001:10001 logs`。
>
> ⚠️ **不要给 `$HOME/.cache/chroma` 挂卷**：ONNX 模型已烤进镜像，挂卷会覆盖掉它（已有卷不会重新拷贝镜像内容），反而让运行期又去联网下载。上一版文档里提到的 `chroma_model_cache` 卷**已不存在**。
> **尤其别写成 bind mount** —— bind 目录必然是空的、必然遮盖镜像内容，构建期的预热就白做了。

**⚠️ 验一件事：`/app/logs` 的属主**（deploy-docker 标注为**未实测**，按 Docker 文档推断）：

```bash
docker compose exec ai4s-agent ls -ld /app/logs
```

- 属主是 **`app`** → 正常
- 属主是 **`root`** → **日志会静默丢失**（`app.py:15` 的 `LOG_DIR="logs"`，`makedirs/open` 被 `try/except` 吞掉，不报错但什么都不写）
  → 改法：把 compose 里的 `app_logs:/app/logs` 换成 `- ./logs:/app/logs`，并先 `sudo chown -R 10001:10001 logs`

> `app_logs` 是**命名卷**，属主依赖"空命名卷挂载时把镜像内目录连同源属主复制进卷"这一行为。deploy-docker 本机没有 docker，**未能实测**。

> 注：`.dockerignore:15` 排除了 `storage/`，镜像里没有数据；容器启动后 `config.py` 会自建 `storage/uploads`、`storage/chroma`、`storage/pdf` 等目录，**空库不会因缺目录崩溃**。Docker 方案下这些目录在 Dockerfile 里已 `mkdir` + `chown app:app` 预先建好，所以宿主侧**只需**按 C-2b 改 `./storage` 的属主。

### C-4 Nginx 反向代理（**方案 C 的访问入口**；WebSocket 头漏配会"页面能开但交互卡死"）

> 🔑 **这一节是方案 C 的访问入口，不是可选项。** compose 默认把 8501 只绑在宿主回环上，
> 所以**不配 Nginx 外部就连不上**（这不是故障，是设计如此）。
> 但在配之前先看 **§C-0 的四档决策门**：**有域名**走 443 + HTTPS；**只有公网 IP** 就只监听 80 走 HTTP，
> 后者同样不需要改 compose 那一行。这两种都要配 Nginx，都逃不开这一节。
> （**第 4 档裸奔不用配 Nginx** —— 那种情况请直接跳到 §C-6，并务必锁来源 IP。）

Streamlit 用 WebSocket 做前后端通信。只写普通 `proxy_pass` 的话，页面能加载出来，但**点任何按钮都没反应、控制台报 WebSocket 连接失败** —— 这是最常见的坑。

```bash
sudo apt update && sudo apt install -y nginx
sudo nano /etc/nginx/sites-available/ai4s
```

粘贴以下完整配置（把 `你的域名.com` 换成实际域名）：

```nginx
# HTTP Upgrade 映射：有 Upgrade 头就转发 "upgrade"，没有就 "close"
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

upstream ai4s_streamlit {
    server 127.0.0.1:8501;
}

server {
    listen 80;
    server_name 你的域名.com;

    client_max_body_size 100m;      # 允许上传较大的 PDF（**必须与下面两处一致**，见注释）

    # ⚠️ 上传体积有**三道闸门**，从外到内任一处偏小都会先拦：
    #   ① Nginx  client_max_body_size        ← 本行（MB 写 100m）
    #   ② Streamlit 的 --server.maxUploadSize（Dockerfile CMD，现为 100）
    #   ③ 应用层 MAX_UPLOAD_MB（config.py:144，默认 100，见 §七）
    # 应用层（③）是在 `data = f.read()` **整份读进内存之后**才判超限的，
    # 所以把 ①② 设得比 ③ 大没有意义：大文件会先完整上传、占满内存，然后才被拒。
    # 三处保持一致；若调高 ③，必须同步调大 ① 和 ②，否则外层先拦、应用层根本看不到文件。
    #
    # ⚠️ **三处量的都是"请求体"，不是文件本身**：multipart 编码有开销（boundary + 头部，约 0.01%~0.1%），
    # 所以一个标称"正好 100 MB"的 PDF，实际请求体会略大于 100 MB → **在 Nginx 这道就被拦**。
    # → 实际可上传的文件**略小于 100 MB**。现场演示若拿标称 100 MB 的 PDF 上传失败，是这条边界，不是配置坏了。
    # （不把三处改成 101/105 是为了避免重新引入"外层比内层大"；对齐 + 显性说明优于留隐性余量。）

    location / {
        proxy_pass http://ai4s_streamlit;

        # ===== WebSocket 必需（缺一不可，漏了会"页面能开但点了没反应"）=====
        proxy_http_version          1.1;   # HTTP/1.1 是 WebSocket 握手的前提
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 长连接 / 流式输出：读超时给足，关掉缓冲
        proxy_read_timeout  86400s;
        proxy_send_timeout  86400s;
        proxy_buffering     off;
    }

    # Streamlit 的 WebSocket 端点（streamlit 1.63 为 /_stcore/stream；
    # 若你的版本日志显示是 /stream，把下面路径改掉即可，两行都留着也无害）
    location /_stcore/stream {
        proxy_pass http://ai4s_streamlit;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        proxy_read_timeout  86400s;
        proxy_buffering     off;
    }
}
```

启用并校验：

```bash
sudo ln -s /etc/nginx/sites-available/ai4s /etc/nginx/sites-enabled/
sudo nginx -t                     # 必须出现 "syntax is ok" + "test is successful"
sudo systemctl reload nginx
```

此时用 `http://你的域名.com` 应该已经能访问。

### C-5 Certbot 签 HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名.com
# 按提示：填邮箱 → 同意条款 → 选 2（Redirect，把 HTTP 强制跳 HTTPS）
sudo certbot renew --dry-run      # 验证自动续期能跑通
```

证书 90 天有效，Certbot 已自动配置 systemd timer 续期。

### C-6 安全组 / 防火墙端口

| 端口 | 是否放行 | 说明 |
|---|---|---|
| **22** | ✅ 放行（**强烈建议限制来源 IP 为你自己的 IP**） | SSH |
| **80** | ✅ 放行 | HTTP，Certbot 验证与跳转用 |
| **443** | ✅ 放行 | HTTPS，评审访问入口 |
| **8501** | ✅ **无需在安全组放行** | compose 默认 `- "127.0.0.1:8501:8501"`，**只绑宿主回环**，根本不发布到公网。见下方说明 |

**关于 8501：默认只绑回环（team-lead 已拍板）**

`docker-compose.yml` 现在是：

```yaml
ports:
  - "127.0.0.1:8501:8501"     # 只绑宿主回环
```

**为什么这样定：**

1. **方案 C 的设计就是「容器只绑回环 + 外层 Nginx 做入口」** —— Nginx 与容器**同机**，走 `127.0.0.1:8501` 就能反代，**绑回环不损失任何东西**
2. 应用**零鉴权**，8501 对公网开放 = 任何人都能用它上面配的 Key 调模型 → Key 被刷爆 = 服务不可用，直接撞赛题"评审期间须保持服务可用"的硬要求

> 🔑 **由此推出（按 §C-0 四档口径）**：在**第 1、2 档**（有域名 / 无域名·境外）下，**C-4 的 Nginx 是事实上的必配项，不是推荐项** —— 默认配置下不配 Nginx 就没有任何访问入口。
> **唯一的例外是第 4 档**：你主动把 compose 改成 `"8501:8501"` 裸奔，那条路才不需要 Nginx。
> ⚠️ 另外注意：上面第 1、2 档**都要配 Nginx**，但**只有第 1 档才有 HTTPS** —— 第 2 档是 Nginx 只监听 80 走 HTTP。
> **域名决定的是 HTTPS，不是可达性**：有域名 → HTTPS；只有公网 IP → HTTP 照样可达，只是明文。别把"Nginx 必配"读成"必须有域名"。

**如果你确实想用 `http://<host>:8501` 直连**（不走 Nginx）：

```yaml
ports:
  - "8501:8501"     # 手动改，默认不是这样
```

改完 `docker compose up -d` 生效。⚠️ 这是**裸奔路径**（无 HTTPS + 无鉴权），**必须同时在安全组限制来源 IP**（只放行你/评委的出口 IP），否则等于无鉴权公网暴露、Key 会被盗刷。

### C-6b 🔀 没有域名怎么办（**重要分支，别漏看**）

**先纠正一个容易踩的误解：没有域名 ≠ 方案 C 走不通，也 ≠ 要改 compose 那一行。**

只有公网 IP 时的正确做法是 **Nginx 只监听 80、走 HTTP**（见 §C-0 第 2 档）：

```nginx
server {
    listen 80;                    # 只监听 80，不要 443、不要 certbot
    server_name _;                # 没域名就用下划线兜底，匹配任意 Host
    # …… 下面的 location 配置与 C-4 完全一致（含 WebSocket 那几行，别漏）
}
```

评委访问 `http://<公网IP>` 即可，**compose 保持 `127.0.0.1:8501:8501` 不动**。

| 你的条件 | 端口映射 | 访问方式 | 说明 |
|---|---|---|---|
| **有域名**（已解析到本机） | 保持 `"127.0.0.1:8501:8501"` | Nginx :443 HTTPS + Certbot（C-5） | ✅ **推荐** |
| 无域名、服务器在**境外** | 保持 `"127.0.0.1:8501:8501"` | Nginx :80 HTTP + 公网 IP | ⚠️ 可达但明文；**安全组只放行评委出口 IP** |
| 无域名、服务器在**大陆且未备案** | — | **建议别选方案 C，改走方案 A** | 大陆境内 80/443 通常需要 ICP 备案，**裸 IP 开 80 大概率被封**（未实测，见 §C-0 提示）。方案 A 免费自带公网 HTTPS 域名，一步到位 |
| **坚持裸奔**、不要反向代理 | 手动改成 `"8501:8501"` | 直连 :8501 | ⚠️ 无 HTTPS 无鉴权，**必须**在安全组只放行评委出口 IP |

> 🔑 一句话：**域名决定的是 HTTPS，不是可达性。**
> 有域名 → 回环 + HTTPS；无域名 → 回环 + Nginx :80 走 HTTP **照样可达**（明文，需锁来源 IP）；
> **只有坚持裸奔直连，才需要改 `"8501:8501"`。** ※ 唯一例外是大陆境内未备案的机器（80 端口可能被封），那种情况改选方案 A。

> ⚠️ 真正会违反赛题"提供可公网访问的在线演示地址"的只有一种情况：**保持回环、又不配任何入口**——
> 那才等于把服务藏起来。按上面四档任意一档配了入口，就满足要求。

> 另：**连 Nginx 都不想配**的话，说明你要的其实不是方案 C ——
> **方案 A（Streamlit Community Cloud）** 免费自带公网 HTTPS 域名、零运维，反而更合适（见 §二）。

**本文档未改动 `docker-compose.yml`**（归 deploy-docker，翻改只需 1 行）。

云厂商控制台（安全组）与系统防火墙（`ufw`）都要确认：

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

---

## 五、部署后自检清单（可勾选）

> 判据全部对应该项目真实代码，不是泛泛而谈。**建议用手机 4G/5G 网络（断开 WiFi、不用本机）打开再走一遍** —— 这才是"公网可访问"的真正验证。

### 5.0 ⭐ 演示/评审当天必做第一步（漏了会被当成"系统坏了"）

**文献库初始是空的。** 评委第一次打开，问答 / 图谱 / 写作三块全是空状态——代码里已有这条提示（`services/writing.py:73-76` 的 `EMPTY_LIBRARY_HINT`：*"⚠️ 文献库为空，无法生成「无幻觉 References」。请先到「📁 文献库管理」上传 PDF（或在左侧点击「🚀 一键加载演示数据」）"*）。

**动作**：打开应用 → 侧边栏最底部 → 点 **「🚀 一键加载演示数据（6 篇）」** → 等进度走到 6/6 → 再开始演示。

- 之前上传过的论文不会被覆盖（`ui/sidebar.py:466` help 原文："不覆盖已有论文"），重复点是安全的
- **HF Spaces 免费档每次重启后都要重点一次**（见 B-3，文件系统非持久）
- 这一步需要联网（首次要拉向量模型，见 §1.1）

### 5.0b 部署版的功能边界（**别在评委面前宣称下面这些能力**）

以下能力在**容器/云平台部署版**上的实际表现，**讲解时按这张表说，别夸大**：

| 能力 | 方案 C（Docker 镜像） | 方案 A / B（云平台） | 原因 |
|---|---|---|---|
| **Graphviz（DOT 图）渲染** | ✅ `dot -Tsvg`（Graphviz 官方渲染） | ✅ **零依赖 DOT→SVG**（浏览器渲染，**中文标签正常**） | `dot` 是**系统包**：`Dockerfile` 第 2 层 `apt-get install graphviz` 装了它（`services/writing.py` 用 `shutil.which("dot")` 找），云平台装不了所以落到下一级。⚠️ **`matplotlib` 有意不装**：我们的 DOT label 是中文（"交互层""Agent 内核"等），matplotlib 默认字体无 CJK，会渲染成方框 □；而零依赖那级是浏览器渲染的，中文正常——**装了反而更差** |
| **TikZ 编译成 PNG** | ❌ 只展示源码 | ❌ 只展示源码 | 镜像**没装 TeX**（`services/writing.py:72` `LATEX_ENGINES` 要 `tectonic`/`pdflatex`/`xelatex`/`lualatex`，一个都没有）。降级是优雅的、不会崩，但评委只会看到源码 + 一句"未检测到编译器" |
| **向量 embedding** | ✅ 离线可用（模型已烤进镜像） | ⚠️ 需联网首次下载 | 见 §1.1 |

> 🚫 **TikZ 已由 team-lead 拍板：不装 TeX，接受降级**（理由：核对演示分镜后确认 TikZ 不在演示主线）。
> **硬口径：文档和现场讲解都不得宣称"部署版能真实编译 TikZ"**，只能说"降级为展示源码"。这条不是待办项，是已定结论。
> （技术上要装也简单：`Dockerfile` 里加 `tectonic` 即可，但**已决定不做**。）
> 方案 A（Streamlit Cloud）**无法 `apt-get` 装系统包**，所以拿不到 `dot -Tsvg` 那级，会落到**零依赖 DOT→SVG**（浏览器渲染）。
> **图照样画得出来、中文标签也正常**——只是布局不如 Graphviz 官方渲染精致。这不是缺陷，别在评委面前说"图渲染失败"。

### 5.1 基础可用性

- [ ] 公网 URL 能打开，浏览器标签页标题为 **AI4S 科研助手 Agent**（`app.py:31`）
- [ ] 页面**没有红色 Traceback**（应用有兜底：依赖缺失 / 容器初始化 / 侧边栏异常都会给中文提示而非崩溃，`app.py:33-103`）
- [ ] 左侧导航 **9 个菜单项齐全**（`ui/sidebar.py:13-23`）：
      `🤖 智能总助手` / `📁 文献库管理` / `💬 跨文献问答与溯源` / `📖 版式阅读与智能翻译` /
      `🔍 论文检索与追踪` / `🕸️ 引用图谱与综述` / `✍️ 写作辅助与可视化` / `🏗️ 系统架构设计` / `💡 技术创新亮点`
- [ ] **随机点 3 个菜单能正常切换，不是点完没反应** ← 这条专门验证 WebSocket（方案 C 尤其）
- [ ] 浏览器 F12 → Console **无 WebSocket 连接失败报错**

### 5.2 模型连通性（配了 Key 才测）

- [ ] 侧边栏「接入方式」选 **🏢 预设平台** → 「选择平台」能看到 5 项：智谱 GLM（默认）/ DeepSeek / 通义千问 / OpenAI / 本地 Ollama（`core/providers.py:33-74`）
- [ ] 选智谱 → 在 Key 输入框填真实 Key → 出现 **「✅ Key 已填入，模型通道就绪」**
- [ ] 点 **「🔌 测试连通性」** → 出现 **「✅ 连通性正常，模型返回：…」**（约 10 秒）
      > 只出现"已填入"不算成功 —— 填入 ≠ 有效（`ui/sidebar.py:308` 原文注释）。**必须以这条为准。**
- [ ] 侧边栏显示 **「🎯 当前模型：glm-4-flash」**

### 5.3 不依赖 LLM 的链路（**没配 Key 时这几条是底线**）

> ⚠️ 标题里的"不依赖 LLM"**不等于"不需要网络"**：首次启动要联网拉一次向量模型（见 §1.1）。
> 语料是内置的（`config.py:147-334` 的 `DEMO_PAPERS`），不需要联网下载论文。

- [ ] 点侧边栏底部 **「🚀 一键加载演示数据（6 篇）」** → 进度走完 6/6，无报错
      > 失败 = 首次向量模型下载没成功（断网 / 国内服务器被墙）。看 §八 故障表最后一条。
- [ ] 进 `📁 文献库管理` → 能看到 **6 篇论文**（Attention v1 / v7、BERT、Adam、VAE、BatchNorm）
- [ ] 📁 页能看到论文元数据（标题/作者/年份/arXiv ID）
- [ ] 进 `📖 版式阅读与智能翻译` → 能打开 PDF 并翻页（PyMuPDF 正常）
- [ ] 进 `🔍 论文检索与追踪` → arXiv 搜索能返回结果（**这条需要服务器能出网**，国内服务器一般 OK）

### 5.4 需要 Key 的主功能

- [ ] `💬 跨文献问答与溯源` → 提问能出答案 + 溯源到具体文献与段落
- [ ] `🤖 智能总助手` → 提问后能看到 Agent 分步执行并给出结果
- [ ] `🕸️ 引用图谱与综述` → 图谱能渲染出来
      > 图出不来时检查：`static/mermaid.min.js` 是否随代码一起部署了（`.dockerignore` 未排除 `static/`，Docker 方案应有）
- [ ] `✍️ 写作辅助与可视化` → 能生成文本/图表
      > ⚠️ TikZ 与 Graphviz 在部署版上只会显示源码 + "未检测到编译器"，属预期（见 §5.0b），不算失败

### 5.5 写入与持久化（**最容易在云平台上翻车的一条**）

- [ ] 在 `📁 文献库管理` **上传一个自己的 PDF** → 能成功入库
      > 失败 = 容器对 `/app/storage` 无写权限，或 HF 免费档只读文件系统
- [ ] **关掉浏览器标签页，等 2 分钟再打开** → 刚才上传的论文**还在**
      > HF Spaces 免费档这一步必然失败（见 B-3），属已知限制，不是 bug
- [ ] 服务器方案：`ls storage/` 能看到 `library.json` / `chroma/` / `uploads/`

### 5.6 判定标准

**✅ 成功**：手机 4G 网络能打开，5.1 全过 + 5.3 全过 + 5.4 至少过 2 条。
**⚠️ 勉强可用（仍可交）**：5.1 + 5.3 全过，5.4 因无 Key 走降级 —— 评审时务必现场点「一键加载演示数据」并说明是离线模式。
**❌ 不合格**：5.1 任何一条不过，或出现红色 Traceback，或页面能开但按钮点了没反应。

---

## 六、评审期间保活（免费平台会休眠）

两个免费平台在长期无访问后都会休眠，评审老师第一次点开可能要等**冷启动几十秒到几分钟**，观感很差。必须做保活。

### 6.1 方法一：自己服务器上的 cron（最省事，但服务器得一直开着）

```bash
crontab -e
```

加一行（每 10 分钟访问一次；换成你的实际 URL）：

```cron
*/10 * * * * curl -fsS -o /dev/null -w "" https://ai4s-agent.streamlit.app || true
```

> 若发现 curl 首页不足以唤醒，可改用健康检查端点 `https://ai4s-agent.streamlit.app/_stcore/health`
> （Streamlit 的内置健康端点路径随版本变化，`/_stcore/health` 与 `/healthz` 都可能，**以实际返回 200 的为准**）。
> 验证：`curl -I https://你的地址/_stcore/health`

### 6.2 方法二：GitHub Actions 定时访问（**推荐，完全免费、不依赖自己的机器**）

在仓库新建 `.github/workflows/keepalive.yml`（注意：`.dockerignore:12` 已排除 `.github/`，不影响镜像）：

```yaml
name: Keep demo alive
on:
  schedule:
    - cron: "*/15 * * * *"     # 每 15 分钟；GitHub 定时有排队延迟，别信它会精确到点
  workflow_dispatch:            # 允许手动点一次立即触发
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping demo URLs
        run: |
          for u in "https://ai4s-agent.streamlit.app" "https://你的域名.com"; do
            code=$(curl -s -o /dev/null -w "%{http_code}" -m 60 "$u" || echo "000")
            echo "$u -> $code"
          done
```

> ⚠️ GitHub Actions 的 `schedule` 在仓库 **60 天无 commit 后会停止运行**，且高峰期会排队延迟。
> 评审期间**每周手动点一次 Run workflow** 或做一个空 commit，避免定时器失效。

### 6.3 方法三：免费外部监控（最稳，还能顺便知道什么时候挂了）

- **UptimeRobot**（uptimerobot.com）：免费 50 个监控，最小间隔 5 分钟，挂了会发邮件
- **cron-job.org**：免费，支持自定义间隔，能看到每次请求的状态码

配置要点：监控类型选 **HTTP(s)**，URL 填公网地址，间隔选 **5 分钟**。

### 6.4 保活的三条经验

1. **评审前一天手动访问一次**，确认没休眠，别等评委来当第一个访客。
2. **评审材料里给两个地址**（如 Streamlit Cloud + 自建），任一个活着就行。
3. **冷启动期间页面会转圈**——可以在评审材料的链接旁注明"首次打开需等待约 1 分钟完成冷启动"，避免评委以为挂了。

---

## 七、环境变量清单（全仓 grep 核实）

核实方式：① `grep -rn "os.getenv\|os.environ\|st.secrets" --include=*.py .`；② 用 Python `ast` 解析全部 `.py`、抽取所有 `_env_str/_env_int/_env_float` 与 `os.getenv/os.environ.get` 的首个字面量参数（**两种独立手段交叉，AST 那轮补上了 grep 漏掉的 `MAX_UPLOAD_MB`**）。**共 26 个可被用户配置的变量 + 1 个系统自带变量（`APPDATA`）= 下表 27 行。**

> 📌 **关于本节的 `文件:行号`**：这些行号是**本文档定稿时逐条比对磁盘核实过的**（脚本校验：无缺文件 / 越界 / 落空行）。但 `config.py` / `core/llm.py` / `core/providers.py` 是**活跃文件**，后续若有人改动，行号会漂移。
> → **定位请以变量名 / 函数名为准**；行号对不上时，用 `grep -n "符号名" 文件名` 现查即可（例如 `grep -n "CHUNK_SIZE" config.py`）。**行号是便利，不是依据。**

> **在云平台（方案 A / B）上，26 个可配变量里 24 个都能用 Secrets 填**：它们走 `config.py:81` 的 `_env_str`，读取顺序是「环境变量 → `st.secrets` → 默认值」。
>
> **没有 Secrets 兜底的 2 个**：
> ① `LLM_PROVIDERS_EXTRA`（`core/providers.py:79`，裸 `os.getenv`）—— 可选功能（追加自定义 Provider 到侧边栏），主演示链路不依赖它；
> ② `AI4S_DESKTOP`（`config.py:10`，裸 `os.environ.get`）—— **故意不加兜底**，云上必须保持未设置/0，被设成 1 会让数据目录跑错。
> → 即使平台完全不把 Secrets 注入环境变量，受影响的只有 ①（可选），② 本来就不该配。
> `core/llm.py:662` 那处 `os.getenv` 在 `from config import` 失败的 except 兜底分支里，正常永远走不到。
>
> ⚠️ **不管上面这些，Secrets 都必须写【根级键】，不能套 `[section]`** —— 这条约束与"平台是否注入环境变量"**无关**：代码是用 `st.secrets.get(name)` 扁平取值的，套进 `[section]` 会变成嵌套字典，取不到。两种情况都成立。

### 7.1 大模型 Provider 与密钥

| 变量 | 必填 | 默认值 | 读取位置 | 用途 | 不配的后果 |
|---|---|---|---|---|---|
| `LLM_PROVIDER` | 可选 | `zhipu` | `services/state.py:81`（`_env_str`，有 Secrets 兜底） | 启动时的默认 Provider（`zhipu`/`deepseek`/`qwen`/`openai`/`ollama`） | 用智谱；可在侧边栏下拉切换，不影响使用 |
| `ZHIPU_API_KEY` | **条件必填** | 空 | `config.py:120`、`core/providers.py:38→135-138` | 智谱 GLM Key（`glm-4-flash` + `glm-4v` 视觉） | 该 Provider 不可用；会回落到 `LLM_API_KEY`，都空则走离线降级 |
| `DEEPSEEK_API_KEY` | 可选 | 空 | `core/providers.py:46→135` | DeepSeek Key | 选该平台时不可用（无视觉模型，OCR 自动降级为离线解析） |
| `DASHSCOPE_API_KEY` | 可选 | 空 | `core/providers.py:54→135` | 通义千问 Key | 同上 |
| `OPENAI_API_KEY` | 可选 | 空 | `core/providers.py:62→135`、`core/llm.py:662` | OpenAI Key（也是 `llm.py` 的兜底读取项之一） | 同上；需国际网络 |
| `OLLAMA_API_KEY` | 可选 | 空 | `core/providers.py:70` | 本地 Ollama | **留空也能用**：该 Provider `needs_key=False`，`resolve_key()` 直接返回占位值 `"local"` |
| `LLM_API_KEY` | 可选（**通用兜底**） | 空 | `config.py:120`、`core/providers.py:135-138`、`core/llm.py:662` | 对应 Provider 变量没填时，代码会再来这里取一次 | 只用一个 Provider 时填这个最省事 |
| `LLM_BASE_URL` | 可选 | `https://open.bigmodel.cn/api/paas/v4/` | `config.py:118` | 覆盖 API 端点（自建网关/内网代理才需要） | ⚠️ **填了会覆盖所选 Provider 自带的 base_url**，内置平台请勿填 |
| `LLM_PROVIDERS_EXTRA` | 可选 | 空 | `core/providers.py:79` | JSON 数组，追加自定义 Provider 到侧边栏下拉 | 只有内置 5 家 |

### 7.2 模型与超时

| 变量 | 必填 | 默认值 | 读取位置 | 用途 | 不配的后果 |
|---|---|---|---|---|---|
| `LLM_MODEL` | 可选 | `glm-4-flash` | `config.py:121` | 文本 / 任务规划 / Function Calling 模型名 | 用默认 |
| `VL_MODEL` | 可选 | `glm-4v` | `config.py:122` | 多模态 / 扫描件 OCR 视觉模型 | 用默认；DeepSeek/Ollama 无视觉会自动降级 |
| `LLM_TIMEOUT` | 可选 | `90.0`（秒） | `config.py:123` | **单次** LLM 调用的超时窗口 | 用默认。**不建议为压缩转圈时间而调低**：它按单次调用裁剪，而单次耗时的实测就是 **44.6s**（写作页 References 生成，§9.3），且同一支 API 还有 **158.4s** 的抖动样本——设 45s 会把核心功能的成功调用拦腰截断。要改，请先实测目标链路单次耗时并留 ≥2× 余量 |
| `LLM_RETRY_BUDGET_SEC` | 可选 | `45.0`（秒） | `config.py:128` | 单次用户操作内 LLM **重试总预算** | 用默认。注意它**不裁剪单次调用窗口**——只决定"要不要发起下一次尝试"，所以调它对"单次调用慢但成功"的请求没有任何影响，也**不能**把上一行的 90s 压短。它已把最坏转圈从 ≈369s 压到 ≈90s |

### 7.3 检索 / 解析参数（默认值已调优，一般不动）

| 变量 | 默认值 | 读取位置 | 用途 |
|---|---|---|---|
| `CHUNK_SIZE` | `1200` | `config.py:134` | 切块目标字符数 |
| `CHUNK_OVERLAP` | `150` | `config.py:135` | 相邻分块重叠字符数（保证跨块语义连续） |
| `DENSE_K` | `12` | `config.py:136` | 向量召回条数 |
| `SPARSE_K` | `12` | `config.py:137` | BM25 稀疏召回条数（两路经 RRF 融合） |
| `FINAL_K` | `6` | `config.py:138` | 融合后进入上下文的片段数 |
| `DEDUP_THRESHOLD` | `0.90` | `config.py:139` | 语义去重阈值，超限弹重复入库预警 |
| `AGENT_MAX_ITERS` | `8` | `config.py:140` | Agent 函数调用闭环最大轮数（防死循环） |
| `AGENT_GLOBAL_MAX_ITERS` | 空（→ 随计划步数自动伸缩） | `core/agent.py:48`（`_env_str`，有 Secrets 兜底） | 全局迭代上限硬覆盖；运维/评测可按需收紧或放开。⚠️ 此变量 `.env.example` 未列出，是本次核实新增发现的 |

### 7.4 外部服务与运行模式

| 变量 | 必填 | 默认值 | 读取位置 | 用途 | 不配的后果 |
|---|---|---|---|---|---|
| `GITHUB_TOKEN` | 可选 | 空 | `services/extern.py:14`（`_env_str`，有 Secrets 兜底；**模块导入期**读取） | GitHub REST API 鉴权，提升「个性化推荐」里开源代码可得性的检出率 | 匿名调用，限流 **60 次/小时**，推荐环节检出率下降并自动降级（`services/extern.py:181` 会明确提示，**不致命**）。⚠️ **改了必须重启进程才生效**（导入期读一次，之后不再读） |
| `GITHUB_API_TOKEN` | 可选 | 空 | `services/extern.py:14` | 上一项的备用变量名，二选一即可 | 同上 |
| `HF_ENDPOINT` | 可选（**保留此配置**） | `https://hf-mirror.com` | `config.py:131`（`os.environ.setdefault`，只有外部设了才生效）；`Dockerfile` 的 `ENV`、`docker-compose.yml` 的 `environment` 均已预设 | HuggingFace 镜像（**仅对 HuggingFace 相关路径生效**） | **对 chroma 的 ONNX 向量模型无效**——那个从 chroma 自己的 S3 直下（`services/writing.py` 无关；见 `Dockerfile:34` 注释）。配它解决不了模型下载问题，但**不要因此删掉它**（team-lead 确认保留） |
| `AI4S_DESKTOP` | ⚠️ **Web 部署必须为 `0` 或不设** | 未设置（`docker-compose.yml` 已显式设为 `"0"`） | `config.py:10`、`desktop/paths.py:21`、`ui/desktop_chrome.py:32` | 设为 `1` 进入桌面模式 | **设为 1 会把数据与资源分离到 `%APPDATA%/AI4SResearchAgent`，Web 部署下 `storage` 路径跑偏** |
| `MAX_UPLOAD_MB` | 可选 | `100` | `config.py:144`（`_env_int`，有 Secrets 兜底）；拦截点 `ui/page_library.py:235` | 单个上传 PDF 的**体积上限**（应用层闸门，`f.read()` 之后判超限并 `st.warning` + 停止解析） | 用默认。⚠️ **调高它必须同时调大另外两处**：Nginx 的 `client_max_body_size`（§C-4）和 `Dockerfile` CMD 的 `--server.maxUploadSize`——**否则外层先拦，改了没效果**；反之只调大外层也没意义（文件会先整份读进内存才被拒） |
| `APPDATA` | 系统自带 | — | `config.py` `DATA_ROOT` 计算处（有 `or Path.home()` 兜底） | Windows 系统变量 | Linux 下不存在但有兜底，无需配置 |

### 7.5 读取优先级小结

**Key 的读取链**（`core/providers.py:116-138` `resolve_key()`）：

1. **侧边栏手填** — 优先级最高，但存在 `st.session_state`，F5 刷新会丢
2. **环境变量** `prov.key_env`（如 `ZHIPU_API_KEY`）→ `LLM_API_KEY`
3. **`st.secrets`** `prov.key_env` → `LLM_API_KEY`（云平台专用兜底，`config.py:61` `_st_secret`）
4. 都取不到 → 空 → 离线降级

**其它配置（`config.py:81` `_env_str`）**：`os.getenv` → `st.secrets` → 默认值

**两个易错点**：

- 空串 / 纯空格 **视为未设置**（`config.py:81` `_env_str`）—— 所以 `ZHIPU_API_KEY=` 不会屏蔽 `LLM_API_KEY`
- Provider 专属变量（如 `ZHIPU_API_KEY`）**优先于**通用兜底 `LLM_API_KEY`

---

## 八、常见故障速查

| 现象 | 最可能的原因 | 怎么处理 |
|---|---|---|
| 页面能开，但**点任何按钮都没反应** | WebSocket 没配通（方案 C） | 检查 Nginx 的 `Upgrade` / `Connection` 头与 `/_stcore/stream` location，见 C-4 |
| Linux 上容器起不来 / 报「存储目录不可写」 | 宿主 `./storage` 属主不是 uid 10001（容器以非 root 运行） | `sudo chown -R 10001:10001 storage`，见 C-2b |
| `docker compose ps` 一直是 `(health: starting)` | 首次启动在建库 + 载入已烤进镜像的 ONNX 模型（166.4 MiB） | 等 2 分钟（`HEALTHCHECK start_period=120s`）；超过就看 `docker compose logs -f` |
| 本机 `curl 127.0.0.1:8501` 通，**外部浏览器访问不通** | **这是预期行为**（compose 默认只绑宿主回环） | ✅ 走 Nginx 的 HTTPS 域名访问，见 C-4。<br>❌ **不要去改 `--server.address`** —— Dockerfile CMD 已经是 `0.0.0.0`，改它没用，问题在**宿主绑定侧**（compose 的 `ports`）。<br>❌ 也不用查安全组/防火墙 —— 端口压根没发布到公网 |
| 页面顶部出现黄色降级横幅 | LLM 调用失败（Key 无效 / 额度耗尽 / 429 / 超时 / 断网） | `app.py:124-131` 会按错误类型给中文指引；先点「🔌 测试连通性」确认 |
| 提示「API Key 含非 ASCII 字符」 | 把中文占位符（如 `你的智谱Key`）真填进去了 | `core/llm.py:133` 的 `isascii()` 校验拦截。填真实纯英文 Key，或整行删除 |
| 构建卡住 / 很慢 | 构建期在下载 ONNX 向量模型（79.3 MiB）+ 装依赖 + 装 graphviz | **构建机必须出网**（PyPI + Debian 源 + AWS S3）。`HF_ENDPOINT` 对模型下载无效，别指望靠镜像解决。断网方案见 §1.1 末尾 |
| 「🚀 一键加载演示数据」6 篇全失败 | 向量 embedding 用不了 | **方案 A/B**：首次冷启动时模型没下载成功（断网 / 出口被墙），且**下载失败是静默的**，界面不报错，直到点这里才爆。见 §1.1<br>**方案 C**：模型已烤进镜像，正常情况下不会犯；若犯了，检查是不是给 `$HOME/.cache/chroma` 挂了卷把镜像内的模型覆盖掉了（见 C-3） |
| 文献库是空的，问答/图谱/写作全无内容 | 没点「一键加载演示数据」 | 见 §5.0，这是演示第一动作，不是 bug |
| TikZ 只显示源码 | 镜像没装 TeX | **预期行为，已定案不修**（team-lead 拍板：TikZ 不在演示主线）。见 §5.0b |
| Graphviz 图没走 `dot` 真实渲染 | 方案 A/B 装不了系统包 graphviz（**预期**）；或方案 C 的 `dot` 没装上 | 方案 C 的 `Dockerfile` 已 `apt-get install graphviz`，检查构建是否成功；**方案 A/B 走零依赖 DOT→SVG，浏览器渲染，中文正常，图仍然画得出来**，见 §5.0b |
| 上传 PDF 失败 | 容器对 `/app/storage` 无写权限，或 HF 免费档非持久文件系统 | 检查 volume 权限；HF 免费档见 B-3 |
| 图谱渲染不出来 | `static/mermaid.min.js` 没部署上 | 确认 `static/` 未被 `.dockerignore` 排除（当前未排除） |
| 重启后数据全没了 | HF Spaces 免费档（预期行为） | 点「🚀 一键加载演示数据」重建，见 B-3 |
| Streamlit Cloud 一直转圈 / 502 | ① 冷启动中（含首次下载 79.3 MiB 向量模型）② 旧分支上 config.toml 的 `address` 写死成回环 | ① 等 2–5 分钟 ② 见 §1.3 |
| 依赖装不上 / ImportError | `requirements.txt` 与 Python 版本不符 | 必须是 Python 3.11（`Dockerfile:1`）；Streamlit 锁在 `>=1.63,<1.64` |

---

## 九、本文档的可信度声明

**已通过读代码 / 读配置 / `git ls-files` 逐条核实**（可直接引用）：
- 端口 8501、主文件 `app.py`、卷 `./storage:/app/storage`、非 root 用户 uid 10001、健康检查 `/\_stcore/health`（与 `Dockerfile` / `docker-compose.yml` 当前内容一致）
- 全部 26 个可配环境变量的名称、默认值、读取文件与行号
- 侧边栏 9 个菜单项、5 个 Provider、各按钮原文文案
- `core/llm.py:133` 的非 ASCII Key 校验、`.dockerignore` 排除项（含 `.streamlit/` 与 `static/` **不** 被排除）
- **Key 在云平台读得到**：`config.py:61` `_st_secret()` 与 `core/providers.py:116-138` `resolve_key()` 已加 `st.secrets` 兜底层（本轮由 team-lead 补上，本条在初稿时是"待确认"，现已核实）
- `.streamlit/config.toml` 中 `address = "127.0.0.1"` **已被注释**（本轮由 deploy-docker 修掉，本条在初稿时是"待确认"，现已核实）
- **首次启动/构建的联网需求完整时序**：`app.py:86 get_container()` → `services/state.py` `Container.__init__` → `_build_embed_fn()` → `fn(["warmup"])`，**启动即触发**；失败被 `except Exception` 吞掉、界面不报错，直到用户点「🚀 一键加载演示数据」才表现为 6 篇全失败
- **模型体积（实测，非估算；由 deploy-audit 与 deploy-docker 给出，我未在本地复现）**：下载 `onnx.tar.gz` **79.31 MiB**（83,178,821 B）、解压后 `onnx/` 目录 **87.10 MiB**（91,332,068 B，其中 `model.onnx` 86.20 MiB）、**缓存目录合计 166.42 MiB**（174,510,889 B，含未删的压缩包）。⚠️ **79.3 是下载量、87.1 是解压量、166.4 是合计** —— deploy-docker 特别纠正过：把 166 MiB 说成"解压后占用"是错的。早期流传的"90MB"指 `model.onnx` 单文件（90.4 MB），与"79.3"不是同一个东西，且 MiB/MB 混用——文档已统一为双单位
- **方案 C 的镜像已烤进模型**：`Dockerfile` 第 5 层构建期 warmup，容器**运行期不需要访问 S3**（`docker-compose.yml` 注释亦确认，且刻意不给 `$HOME/.cache/chroma` 挂卷以免覆盖）
- **镜像已装 graphviz**：`Dockerfile` 第 2 层 `apt-get install graphviz`，写作页 DOT 图可 `dot -Tsvg` 真实渲染（证据：`services/writing.py` 的 `render_dot()` 里 `dot_exe = shutil.which("dot")` 与 `subprocess.run([dot_exe, "-Tsvg"])` 两处 —— **按符号名定位，不写行号**：`grep -n "dot_exe\|subprocess.run(\[dot_exe" services/writing.py`。此处原写 `:1496/1517`，本轮该文件已增长至 `:1496/1519`，故改为符号名）
- **镜像没装 TeX**：`services/writing.py:72` `LATEX_ENGINES` 四个引擎一个都没有 → TikZ 只展示源码
- **`matplotlib` 有意不装**（`requirements.txt` 里有专门注释说明理由）：DOT 降级链实际是 `graphviz 包 → dot -Tsvg → matplotlib(PNG) → 零依赖 DOT→SVG`，matplotlib 那级会因默认字体无 CJK 把中文标签渲染成方框 □，**装了反而比下一级更差**。所以真实链路是：**容器里 `dot -Tsvg` + 零依赖兜底（两级）**；云平台无 `dot`，走零依赖 DOT→SVG（浏览器渲染，中文正常）
- 文献库初始为空，代码内置提示 `services/writing.py` `EMPTY_LIBRARY_HINT`

**⚠️ 未经实机验证、需实际部署时确认**（不要当成既定事实）：
1. Streamlit Cloud / HF Spaces **当前页面的具体按钮文案与字段顺序**（平台会改版，以页面实际为准）
2. HF Spaces 检测到根目录 `Dockerfile` 时**是否会自动切到 Docker SDK**，以及 Docker Space 的 7860 端口 / UID 1000 两条要求（本项目是 8501 + uid 10001，两条都不符，所以建议选 Streamlit SDK）
3. **HF 构建环境能否访问 AWS S3**（新 Dockerfile 第 5 层要在构建期下载模型）—— 若不能，需改用 `COPY ./onnx_models` 兜底
4. Streamlit WebSocket 端点路径（`/_stcore/stream` vs `/stream`）—— 健康检查端点 `/\_stcore/health` 已由 `Dockerfile` 的 `HEALTHCHECK` 采用，可视为已确认；WS 路径仍待确认（Nginx 用 `map` 方案可兜住任意路径）
5. 各免费平台的**休眠阈值与冷启动时长**（平台政策会变）
6. Streamlit Cloud 免费档的**内存配额**（`chromadb` + ONNX + Streamlit 同时驻留是否吃紧）
7. `LLM_PROVIDERS_EXTRA` 是唯一无 Secrets 兜底的可配变量，云平台若**不**把 Secrets 注入环境变量，它读不到（属可选功能，主演示链路不依赖）
8. **镜像体积 ≈ 1.2 GB（可辩护区间 1.16–1.18 GB）是静态分项加总，未经 `docker build` 实测** —— 依赖层体积在 Windows 侧 `.dll`/`.pyd` 上逐个实测，Linux 侧 `.so` 未测。🔴 **镜像从未实机构建、容器从未运行、构建机能否访问 AWS S3 未验证。** 另有 `916.9 MiB`（conda 开发环境，含镜像不装的包）与 `784.5 MiB`（剔 scipy 后的错集合）两个上界**均已作废，不要引用**

> 说明：ONNX 模型体积、非 ASCII Key 校验等结论有 deploy-audit 的实测与只读审计（`资料/复盘/round12_deploy_audit.md`）与 deploy-docker 的镜像设计（`资料/复盘/round12_deploy_docker.md`）交叉印证；**凡我无法在本地复现的，均标注了来源，不冒领**。

### 已从"待确认"降级为"已核实"的（供追溯）

| 原待确认项 | 现在的状态 | 依据 |
|---|---|---|
| 云平台 Secrets 能否被读到 | ✅ 已解决 | team-lead 补 `st.secrets` 兜底（`config.py:61/75`、`core/providers.py:135-138`） |
| 平台是否把 Secrets 注入环境变量 | ✅ **已无实质影响** | deploy-audit 实测：`LLM_PROVIDER`（`services/state.py:81`）、`GITHUB_TOKEN`（`services/extern.py:14`）本轮也已改为 `_env_str`。26 个可配变量里 24 个有 Secrets 兜底，**2 个例外：`LLM_PROVIDERS_EXTRA`（可选功能，不进主演示链路）与 `AI4S_DESKTOP`（故意不加，云上本就不该配）**。残余风险从"整个大模型半壁江山"收窄到"一个可选功能" |
| config.toml 的 `address` 是否阻断云平台 | ✅ 已知无风险 | team-lead 注释该行 + deploy-docker 删除 `.dockerignore` 的 `.streamlit/` 排除规则 |
| 健康检查端点路径 | ✅ 已确认 `/\_stcore/health` | `Dockerfile` 的 `HEALTHCHECK`（deploy-docker 引了 streamlit 源码行号） |
| `HF_ENDPOINT` 对模型下载无效 | ✅ 已确认 | deploy-docker 指明走 chroma 自己的 S3 |
| TikZ 要不要装 TeX | ✅ **已定：不装** | team-lead 拍板（TikZ 不在演示主线）。**硬口径：不得宣称部署版能真实编译 TikZ** |
| `.streamlit/secrets.toml` 会被烤进镜像 | ✅ 已堵上 | `.dockerignore` 已补精确排除（只排除该文件，`config.toml` 照常进镜像） |

深度补充材料见 `资料/在线演示部署指南.md`。
