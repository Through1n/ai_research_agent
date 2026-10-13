# Round 12 · 部署前只读审计（评委视角）

- 角色：评委 / 只读审计。**未修改任何代码文件**，本文件是唯一产物。
- 审计对象：`C:/Users/Administrator/ai_research_agent` @ `9147ccd`，目标环境 Linux 服务器（Docker）+ Streamlit Cloud。
- 解释器：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`（仅做 import/版本/配置只读探测，**未起 streamlit、未跑 smoke**）。
- 标注约定：凡未实测的推断一律写「**未实测**」，不凭印象下结论。

> **📌 复核纪要（第二轮，改动落地后复验）**：初稿发出后 team-lead / deploy-docker / deploy-doc 已按本报告的 F1/F2/F3/H1/H7 动手修改。我对改动做了只读复验，结果见 **[§7 复核纪要](#7-复核纪要改动落地后的复验)**。**请先看 §7 再看 §0 结论**，§0 是改动前的原始判断。

---

## 0. 先给结论

**现在不能直接部署。** 按阻塞程度排序，还差 **3 件必办 + 6 件应办**：

| # | 必办 | 对应条目 |
|---|---|---|
| 1 | 给 Key 读取加 `st.secrets` 回退（目前全仓 0 处 `st.secrets`） | F1 |
| 2 | 把 ONNX embedding 模型预置进镜像 / 或准备离线兜底（否则「离线演示」按钮首次必联网下 79.3MB） | F2 |
| 3 | `docker-compose.yml` 去掉 `env_file: .env`（全新克隆没有 .env，`docker compose up` 直接失败） | F3 |

其余按「高 / 中 / 低」分级列在下面。

---

## 1. 致命（P0）

### F1. Streamlit Cloud 上极可能拿不到 API Key —— 代码只读 `os.getenv` / `.env`，**全仓零 `st.secrets`**

证据：

```
# 全仓搜索（*.py）
core/providers.py:122   return os.getenv(prov.key_env, "") or os.getenv("LLM_API_KEY", "")
config.py:58            load_dotenv(ROOT / ".env")
config.py:97            LLM_API_KEY = _env_str("ZHIPU_API_KEY", "") or _env_str("LLM_API_KEY", "")
services/state.py:79    self.provider_id = os.getenv("LLM_PROVIDER", "zhipu")
services/extern.py:14   GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "") or ...
grep -rn "st\.secrets" --include=*.py  →  0 命中
```

现象：`.gitignore:2` 排除了 `.env`，从 GitHub 部署到 Streamlit Cloud 时**仓库里根本没有 `.env`**。Key 只能来自 Cloud 的 Settings → Secrets。

为什么会炸：Streamlit 现行官方 Secrets 文档只保证 `st.secrets` 可读，**没有承诺把 secrets 注入 `os.environ`**（旧 blog/2021 年文档说过 root-level secrets 会同时成为环境变量，但现行文档已不提）。本项目没有任何 `st.secrets` 回退，等于**把整个大模型半壁江山押在一个官方不再承诺的机制上**。一旦环境变量没注入：侧边栏永远显示「📌 尚未配置 API Key」（`ui/sidebar.py:265-268`），问答/Agent/综述/OCR/翻译全走伪响应（`core/llm.py` 的 DEGRADED 通道），评委看到的是"假答案"。

> **未实测**：Streamlit Cloud 当前版本是否把 root-level secrets 注入 `os.environ`。这是我唯一无法在本地验证的一条，请在真实 Cloud 上部署后立刻用一行 `st.write(bool(os.getenv("ZHIPU_API_KEY")))` 验证。

修复建议（精确到行）：

1. `config.py:61` `_env_str()` 加一层回退，最小改动：
```python
def _env_str(name, default=""):
    v = os.getenv(name)
    if v is None:
        try:                                   # Streamlit Cloud secrets 兜底
            import streamlit as _st
            v = _st.secrets.get(name)          # 只在环境变量缺失时读，本地无 secrets.toml 也安全
        except Exception:
            v = None
    if v is None:
        return default
    v = str(v).strip()
    return v if v else default
```
2. `core/providers.py:122` 同样处理：
```python
def _secret(name):
    v = os.getenv(name, "")
    if not v:
        try:
            import streamlit as st; v = st.secrets.get(name, "")
        except Exception:
            v = ""
    return v or ""

def resolve_key(prov, manual=""):
    if manual and manual.strip():
        return manual.strip()
    if not prov.needs_key:
        return "local"
    return _secret(prov.key_env) or _secret("LLM_API_KEY") or ""
```
3. 部署时在 Cloud 的 Secrets 里填**根级**键（不要放进 `[section]`）：
```toml
LLM_PROVIDER = "zhipu"
ZHIPU_API_KEY = "<真实Key>"
```
4. 兜底告知：把「如何在 Cloud 填 Secrets」写进部署文档；同时保留侧边栏手动填 Key 的通道（`ui/sidebar.py:211-213`，**已存在**）——但注意它只存在 `session_state`，刷新即丢，不能当主方案。

---

### F2. 冷启动必联网下载 79.3MB ONNX 模型（AWS S3 源）；「一键加载演示数据」宣称离线，实际首次不联网就废

证据链：

```
ui/sidebar.py:381-383   按钮文案「🚀 一键加载演示数据（v1/v7/BERT）」
                        help="向文献库写入 3 篇内置示例论文，用于离线演示各项功能"
ui/sidebar.py:397       library.add_text_corpus(ctx, fn, info.get("text",""), info)
services/library.py:857 embedding = _paper_embedding(ctx, full_text) if full_text else None
services/library.py:68  return ctx.embed([full_text[:4000]])[0]
services/library.py:79  return ctx.embed(samples)          # 每篇再采样 5~6 段指纹
services/state.py:99    vecs = self.embed_fn(texts)
services/state.py:57    fn = ONNXMiniLM_L6_V2()
services/state.py:58    fn(["warmup"])    # ← 首次调用触发下载
services/state.py:67-69 Container.__init__ 里同步执行（app.py:86 get_container() 就在启动路径上）
```

下载源（chromadb 1.5.9 源码，实测 `inspect.getsource`）：

```python
MODEL_DOWNLOAD_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
DOWNLOAD_PATH = Path.home() / ".cache" / "chroma" / "onnx_models" / MODEL_NAME
@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_random(min=1, max=3), ...)
```

**体积（已实测，非估算）**：

| 项 | 数值 | 取证方式 |
|---|---|---|
| 压缩包 `onnx.tar.gz` | **79.3 MB**（83,178,821 B） | `HTTP HEAD` 的 `content-length` = 83178821；本机磁盘 `ls -l` 一致 |
| 解压后 `onnx/` | 88 MB | 本机 `du -sh` |
| 整个缓存目录 | **167 MB** | 本机 `du -sh ~/.cache/chroma` |

> 订正：初稿写的"90MB"是估值，实测为 79.3 MB，以本表为准。

为什么会炸：
- 这是 **AWS S3，不是 HuggingFace**，所以 `config.py:103` 配的 `HF_ENDPOINT=hf-mirror.com` **对它无效**（全仓 grep `from_pretrained` / `sentence_transformers` 0 命中，HF 镜像是无效配置）。
- 国内服务器访问 `chroma-onnx-models.s3.amazonaws.com` 常慢或被墙；**无外网 / 比赛现场断网**时 3 次重试后抛异常。
- 异常被 `services/state.py:60 except Exception` 吞掉 → fallback `DefaultEmbeddingFunction()`，而实测 chromadb 1.5.9 的 `DefaultEmbeddingFunction.__call__` **内部还是 `ONNXMiniLM_L6_V2()`** → 后续每次 `ctx.embed` 照样抛。
- 抛异常的位置在 `ui/sidebar.py:400-402` 的 per-item try 里 → 结果不是崩溃，而是**三篇全部"⚠️ 失败"，文献库仍是空的**，而所有页面此时都只有「点左侧一键加载演示数据」这一条出路 —— 死锁。

实测数据（本机，模型已缓存）：
```
ONNXMiniLM_L6_V2 预热（含 import onnxruntime）：6.77 s
单次 embed：0.139 s / 384 维
```
**冷启动耗时预估（未实测，仅为量级估计）**：模型下载 79.3 MB 在国内服务器 1–5 min 甚至失败；下载后每次新会话（每个新浏览器 tab 一个 `Container`）仍需 ~7 s 预热（含 onnxruntime import）。首个评委进页面会白等 7 s+。

**⚠️ 补充（复核时补上，deploy-doc 指出）**：这一步**不在"点演示数据时"才发生，而是应用一启动就发生** —— `app.py:86 get_container()` 就在启动路径上。完整时序是：
1. 启动 → `_build_embed_fn()` → `fn(["warmup"])` → **首次启动即触发下载**；
2. 下载失败被 `services/state.py:60 except Exception` **静默吞掉**，界面零报错，只是 fallback 到 `DefaultEmbeddingFunction()`；
3. **直到用户点「🚀 一键加载演示数据」**，`ctx.embed` 才真正抛异常 → 三篇全"⚠️ 失败"。

即"启动必须出网"是根因，"点按钮失败"是现象，两句都要说，否则运维对不上号。

修复建议（三选一，推荐 1+3）：
1. **预置模型到镜像**：宿主上先跑一次让 `~/.cache/chroma/onnx_models/all-MiniLM-L6-v2` 生成，Dockerfile 里
   ```
   COPY onnx_models /root/.cache/chroma/onnx_models
   ```
   （注意 `Path.home()` 在 root 容器里是 `/root`；若用非 root 用户要相应改路径。）
   建议顺手把 `~/.cache` 挂成 volume，避免每次重建镜像重下。
2. 或改 `services/state.py:42-61`：下载失败时给一个**确定性哈希 embedding 兜底**（例如把 tokenize 后的词频向量 L2 归一到固定维度），保证检索链路"能跑通、结果可解释"，并在 UI 明说"降级为无模型检索"。
3. `services/state.py:58` 的 `fn(["warmup"])` 建议加超时/失败标记：`self.embed_ok = False`，让 `ui/common.py` 的横幅能提示"向量模型未就绪，检索质量下降"，而不是让用户在"加载失败"里瞎猜。

---

### F3. `docker-compose.yml` 依赖 `.env`，全新克隆 `docker compose up` 直接失败

证据：
```
docker-compose.yml:7-8
    env_file:
      - .env
.gitignore:2     .env
.dockerignore:2  .env
```
`env_file` 指向不存在的文件时，Docker Compose **直接报 `env file not found` 并中止**，不会静默跳过。评审/复现者 `git clone` 后拿不到 `.env`（它不在仓库里），第一条命令就卡住。

修复建议：`docker-compose.yml` 改成显式 `environment:` + `${VAR:-}` 默认值，或删掉 `env_file` 改由 `docker run -e` / systemd `EnvironmentFile` 注入：
```yaml
services:
  ai4s-agent:
    build: .
    container_name: ai4s_research_agent
    ports:
      - "8501:8501"           # 见 H-? 关于暴露面的建议：生产改为 "127.0.0.1:8501:8501" + Nginx
    environment:
      LLM_PROVIDER: ${LLM_PROVIDER:-zhipu}
      ZHIPU_API_KEY: ${ZHIPU_API_KEY:-}
      LLM_API_KEY: ${LLM_API_KEY:-}
    volumes:
      - ./storage:/app/storage
    restart: unless-stopped
```
（宿主 `.env` 仍可用于 `docker compose --env-file`，但不再是硬依赖。）

---

## 2. 高（P1）

### H1. `.streamlit/config.toml` 里 `address = "127.0.0.1"`，会被 git 带到 Streamlit Cloud

```
.streamlit/config.toml:14   address = "127.0.0.1"
git ls-files .streamlit     → .streamlit/config.toml   （已跟踪，会进 Cloud）
```
Docker 路径没问题（`.dockerignore:44` 排除了 `.streamlit/`，且 `Dockerfile:16` CMD 显式 `--server.address=0.0.0.0`）。**但 Streamlit Cloud 会读到这份 config**：容器/沙箱里只监听回环，平台健康检查与路由转发会连不上 → 应用起不来或一直 "Starting"。

> **未实测**：Cloud 是否会用自身配置覆盖 `server.address`。历史上有"被覆盖"的说法，但不应赌。

修复建议：把 `.streamlit/config.toml` 的 `[server]` 整段（第 9–18 行）删掉只留 `[theme]` —— 这段本来就是给桌面壳（pywebview）用的（`ui/desktop_chrome.py`、`desktop/launcher.py:32` 自己会传 `--server.address=127.0.0.1`）。
- `enableStaticServing` 实测 Streamlit 1.63 **默认值就是 True**，删掉不影响 `static/mermaid.min.js` 的本地分发。
- 若担心桌面版受影响，可在 `desktop/launcher.py` 的 `STREAMLIT_CLI_ARGS` 里补显式参数（它已经传了 address）。

### H2. 多评委并发访问 → chroma SQLite "database is locked"

```
services/state.py:172-176  get_container()  → 每个 session_state 一个 Container
services/state.py:67       self._chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
```
实测：
```
storage/chroma/chroma.sqlite3  PRAGMA journal_mode → delete   （非 WAL）
chromadb/db/impl/sqlite.py：主连接只设了 foreign_keys / case_sensitive_like，
                            未见 busy_timeout（仅 vacuum() 里临时设 5s）
```
为什么会炸：Streamlit 每个浏览器会话 = 一个 `Container` = 一个独立 SQLite 连接。**两个评委同时点「一键加载演示数据」→ 同时 `collection.upsert`** → 一方拿不到写锁 → `sqlite3.OperationalError: database is locked` → 被 `ui/sidebar.py:400` 捕获，界面显示「⚠️ xxx 失败」，用户完全不知道是并发冲突。Streamlit Cloud 上会话数更多，概率更高。

修复建议（推荐 1，成本最低）：
1. 启动时给 chroma 的 SQLite 开 WAL + busy_timeout：
   ```python
   # services/state.py 顶部或 Container.__init__ 之前
   import sqlite3
   _db = CHROMA_DIR / "chroma.sqlite3"
   if _db.exists():
       c = sqlite3.connect(str(_db)); c.execute("PRAGMA journal_mode=WAL")
       c.execute("PRAGMA busy_timeout=15000"); c.close()
   ```
2. 或者把 `PersistentClient` 改成 `@st.cache_resource` 单例（跨会话共享一个客户端），同时把 `ctx` 里"会话私有"的字段（LLM key 等）拆出来。改动较大，慎用。
3. 至少：UI 上把 "database is locked" 识别成人话提示（"另一位访客正在写入文献库，请 3 秒后重试"）。

### H3. 空库首屏就摆着「🔧 一键重建文献库（pdf/ 官方测试件，含 541 页教材）」，点了就是几分钟长任务 + 需要 Key

```
ui/page_library.py:88-104   health["empty"] 为真时也会渲染这个按钮
services/library.py:40-55   OFFICIAL_LIBRARY_PDFS = 5 件，含
                            · UnderstandingDeepLearning_02_09_26_C.pdf  22 MB / 541 页
                            · 1706.03762v1_img.pdf（扫描件，无文本层 → 需视觉模型 OCR）
services/library.py:22-23   OCR_PARALLEL=3（并发 3 路视觉调用）
services/library.py:645     ThreadPoolExecutor(max_workers=OCR_PARALLEL)
services/library.py:15      PDF_PAGE_TIMEOUT_SECONDS = 30（单页超时）
```
为什么会炸：评委第一次打开应用、还没加载演示数据时，📁 页**第一眼就是这个按钮**。点下去 = 解析 541 页 + 扫描件逐页 OCR（需有效视觉 Key）+ 上千次 embed。
- 无 Key → 扫描件 OCR 全失败（`ui/page_library.py:171-175` 的提示里已经承认"扫描件 OCR 需要可用的多模态模型 Key"）；
- Streamlit Cloud 有请求时长限制与休眠机制，几分钟的同步任务极易被掐断，且**没有断点续跑**。

修复建议：
1. `ui/page_library.py:104` 加条件：空库时不要把它和"加载演示数据"并列摆出来，或至少改成 `type="secondary"` + 明确耗时警示：
   ```python
   st.caption("⚠️ 该操作需解析 541 页教材并对扫描件做视觉 OCR，需有效 Key，预计 5–15 分钟；"
              "只想快速演示请改用左侧「🚀 一键加载演示数据」（约 10 秒）。")
   ```
2. 更好：把 `1706.03762v1_img.pdf` 与教材从默认重建清单里拆成"可选"，默认只重建 3 篇主论文。

### H4. Streamlit Cloud 文件系统易失 —— 演示数据随时归零

```
.gitignore:12   storage/
.dockerignore:15 storage/
```
Cloud 上 `storage/` 随实例重建丢失（休眠唤醒/重启/重部署）。评委点了「一键加载演示数据」，睡一觉回来库又空了。Docker 路径 OK（`docker-compose.yml:10` 挂了 `./storage`）。

修复建议：Cloud 上要么（a）接受"每次演示前点一次按钮"并在 README 明写；要么（b）在 `app.py` 启动后、文献库为空且 `st.secrets`/环境变量里存在某个 `AI4S_AUTOLOAD_DEMO=1` 开关时**自动灌入演示数据**，一次点击都不需要。推荐 (b)，成本低。

### H5. `matplotlib` 未在 requirements.txt 声明，Graphviz 二级降级必然失效

```
requirements.txt  （13 行，无 matplotlib）
services/writing.py:1445-1474  _dot_via_matplotlib()  ← 二级降级
services/writing.py:1453       import matplotlib
本机实测：import matplotlib → ModuleNotFoundError
```
现象：容器里 Graphviz 三级降级链（dot → matplotlib+networkx → 纯 Python）实际只剩 **dot（没有，见 H6/H7）+ 纯 Python**，中间那级永远走不到。功能不崩（`services/wcopilot_dotrender.py` 零依赖 SVG 渲染可用，实测 import OK），但和文档宣称的"三级降级"不符。

修复建议：要么在 `requirements.txt` 加 `matplotlib>=3.8`（+ 若标签含中文需装 CJK 字体，见 M7），要么把文档里的"三级"改成"两级"并删掉 `_dot_via_matplotlib` 死代码。

### H6. TikZ 真实编译在容器里必然失败（镜像无任何 TeX）

```
services/writing.py:72    LATEX_ENGINES = ("tectonic", "pdflatex", "xelatex", "lualatex")
services/writing.py:1323-1332  _detect_latex_engine()  用 shutil.which 逐个找
services/writing.py:1399-1405  engine 为空 → res["reason"] = "本机未检测到 LaTeX 编译器…无法本地渲染"
services/writing.py:1406       tmp = tempfile.mkdtemp(prefix="wcopilot_tikz_")   （依赖 /tmp 可写）
Dockerfile                     无 apt-get install texlive / tectonic
```
降级是优雅的（返回 `ok=False` + 人话 reason，不崩），所以**不致命**；但如果提交材料 / 技术文档宣称"TikZ 编译成 PNG 真实渲染"，评委在部署版上只会看到源码 + 一句"未检测到编译器"。

修复建议（二选一）：
1. 装：Dockerfile 加 `RUN apt-get update && apt-get install -y --no-install-recommends texlive texlive-latex-extra && rm -rf /var/lib/apt/lists/*`（**约 +1~2 GB 镜像**，谨慎）；或只装 `tectonic` 的静态二进制（约 100 MB，推荐）。
2. 不装：在 UI 与文档里把 TikZ 明确定位为"生成源码 + 有条件真实编译"，别在演示脚本里演示"点一下出 PNG"。

> **未实测**：目标 Linux 服务器是否已预装 TeX。请在服务器上 `which tectonic pdflatex xelatex lualatex` 确认。

### H7. Graphviz `dot` 可执行文件容器里没有

```
services/writing.py:1516-1521   shutil.which("dot") → subprocess [dot_exe, "-Tsvg"]
services/writing.py:1529        "未找到 dot 可执行文件（PATH 中无 graphviz）。"
Dockerfile                      无 apt-get install graphviz
```
同 H6，不崩但"真实 Graphviz 渲染"这条卖点在部署版上拿不到。修复：`apt-get install -y graphviz`（约 30 MB，很便宜，**建议装**），或文档改为"零依赖降级渲染"。

---

## 3. 中（P2）

### M1. `HF_ENDPOINT=hf-mirror.com` 是无效配置，且境外部署语义相反
```
config.py:103   os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
Dockerfile:5    HF_ENDPOINT=https://hf-mirror.com
```
全仓 grep `from_pretrained` / `sentence_transformers` / `huggingface_hub` → **0 命中**。唯一的模型下载走的是 AWS S3（见 F2），与 HF_ENDPOINT 无关。境外（Streamlit Cloud）部署时这行还会让人误以为模型会走国内镜像。
修复：删掉这两处，或改成注释说明"本项目不使用 HuggingFace 下载，唯一模型来自 chroma 的 S3"。

### M2. `core/llm.py` 用**相对路径**写日志目录，依赖 CWD
```
core/llm.py:364-368
    log_path = os.path.join("storage", "llm_config_errors.log" if category=="config" else "llm_failures.log")
    os.makedirs("storage", exist_ok=True)
```
若以 systemd / supervisor 启动且未设 `WorkingDirectory`，日志会写到 `/` 或别处（root 下能写就静默写错地方，非 root 就 `PermissionError`——虽然被 try 吞掉）。
修复：`from config import STORAGE_DIR` 失败时的兜底也用绝对路径，例如 `Path(__file__).resolve().parent.parent / "storage"`。

### M3. `app.py` / `ui/common.py` 的 `LOG_DIR="logs"` 同样是相对路径，且 `.dockerignore` 排除了 `logs/`
```
app.py:15-16        LOG_DIR = "logs"; LOG_FILE = os.path.join(LOG_DIR, "errors.log")
ui/common.py:190-191 同上
.dockerignore:16    logs/
```
容器重建即丢日志，排障时看不到。修复：改为 `config.STORAGE_DIR / "logs"`（storage 已被 volume 持久化），并挂出来。

### M4. requirements.txt **全部无上界**，跨大版本漂移风险
```
requirements.txt 现状（13 行）：chromadb>=0.5 / numpy>=1.26 / pandas>=2.0 / PyMuPDF>=1.24.3 ...
本机实测实际解析版本：
    streamlit 1.63.0  chromadb 1.5.9  numpy 2.4.6  pandas 3.0.5
    plotly 7.0.0      openai 3.13.0   pymupdf 1.28.2  arxiv 4.0.1
```
`chromadb>=0.5` 会解析到 **1.5.9**（跨了一个大版本）、`pandas>=2.0` → **3.0.5**（已移除/变更若干 API）、`numpy>=1.26` → **2.4.6**。
> **未实测**：在 Docker 里全新解析 + 全页面跑通。这是"能装 ≠ 能跑"的典型风险点。

修复：把 requirements.txt 锁定到本机实测通过的版本（生成一份 `pip freeze` 子集并写上界），例如
```
streamlit>=1.63,<1.64
chromadb>=1.5.9,<2
numpy>=2.4,<3
pandas>=3.0,<4
PyMuPDF>=1.28,<2
...
```
最低限度：给 `chromadb` 和 `pandas` 加上界。

### M5. `services/parsing.py:7` 硬导入 `pymupdf`，无回退
```
services/parsing.py:7   import pymupdf as fitz
对比 services/translate.py:40-46 做了 try: import pymupdf as fitz / except: import fitz / except: fitz=None
```
`pymupdf` 这个模块名只在 PyMuPDF ≥ 1.24.3 才有。若将来镜像解析到更低版本 → `ModuleNotFoundError` → `services.parsing` 导入失败 → `app.py:41-48` 直接 `st.error("加载依赖模块") + st.stop()`，**整个应用起不来**。
修复：照抄 `services/translate.py:40-46` 的双导入回退。

### M6. 公网部署的安全面（评委能看到 traceback + 公网实例开放子进程执行）

> **【状态：已闭环 / 已决策，见 §7.11】** 以下为**提出当时的原始取证**，保留作档案。现状：`showErrorDetails` 已改为 `false`（`.streamlit/config.toml:35`）；`t_run_python` 经 team-lead 拍板**保留功能、不新增开关**，风险转由文档提示。

```
.streamlit/config.toml:25   showErrorDetails = true        → 公网访客看得到完整 traceback（当时）
.streamlit/config.toml:17   enableXsrfProtection = false
.streamlit/config.toml:16   enableCORS = false
tools/definitions.py:309-341  t_run_python()：AST 白名单后起子进程 sys.executable 执行，timeout=25
                              work = tempfile.mkdtemp(prefix="ai4s_code_")
core/sandbox.py:10-20        白名单 import + 禁用属性/函数黑名单
```
- `showErrorDetails=true` 在**公网**上是信息泄露，建议部署版设 `false`（同时保留 `logs/errors.log` 落盘，代码已有）。
- `t_run_python` 是**公网服务器上的代码执行面**。AST 静态检查（`core/sandbox.py`）挡住了 `os.system`/`subprocess`/`eval` 等，但黑名单式护栏 historically 容易被绕过，且子进程以容器用户权限跑、可写 `/tmp` 与 `storage/`。
  建议：公网部署时在 `tools/definitions.py:355+ build_registry()` 里按开关禁用 `run_python`（例如 `if os.getenv("AI4S_PUBLIC_DEMO") == "1": 不注册该工具`），并在文档里说明。

### M7. 中文字体：PDF 导出**没问题**，但 matplotlib 路径有方框风险
```
services/translate.py:53    _CJK_FONTNAME = "china-s"   # PyMuPDF 内置简体中文（Droid Sans Fallback）
services/translate.py:636   f = mod.Font(_CJK_FONTNAME) # 内置字体，无需外部字体文件
services/writing.py:1469    nx.draw_networkx_labels(... font_size=9 ...)   ← matplotlib，会用系统字体
```
- **原位 PDF 导出 / 中文 PDF**：用的是 PyMuPDF **内置** 字体，不依赖系统安装 → Linux 容器**不会乱码**。这条我确认是安全的，不要为了它去装 fonts。
- matplotlib 路径（H5 提到当前环境根本没装 matplotlib）：若按建议装上，且图节点标签含中文，容器缺 CJK 字体 → 方框。修复：`apt-get install -y fonts-noto-cjk` 或在 `_dot_via_matplotlib` 里设 `rcParams["font.sans-serif"]`。
- 浏览器侧渲染（`services/graph.py` 的 vis-network HTML、`ui/page_misc.py` 的架构图 HTML）用的是客户端字体（`Microsoft YaHei, sans-serif` fallback），**不依赖服务器字体** → 安全。

### M8. 镜像体积：`pdf/` 31 MB 全量进镜像，其中 22 MB 是测试用教材
```
pdf/ 合计 31 MB：
  UnderstandingDeepLearning_02_09_26_C.pdf   22 MB
  LongBook_540p_fixture.pdf                 486 KB（明显的测试夹具）
  1706.03762v1_img.pdf                      3.6 MB（扫描件）
  ...
.dockerignore 未排除 pdf/（这是对的，演示需要）也未排除单个夹具
```
`LongBook_540p_fixture.pdf` 看名字就是压力测试夹具，`.dockerignore` 里加一行排除它即可省 486 KB（顺带减少 H3 误触面）。教材 22 MB 是 H3 重建清单用的，若保留重建功能就必须进镜像。

### M9. `ui/page_reading.py` 清理 `fig_*` 临时目录会扫共享 `/tmp`
```
ui/page_reading.py:218-245  cleanup_stale_figure_dirs()
    for root in (roots or [tempfile.gettempdir()]):      # Linux 上 = /tmp
        ... if str(name).startswith("fig_"): shutil.rmtree(p, ignore_errors=True)
ui/page_reading.py:361 / 1315  在页面渲染路径上被自动调用
```
在 `/tmp` 与其他进程共享的宿主上，会删掉**别人**的 `fig_*` 目录（虽然 `ignore_errors=True` 且超时>24h，概率低）。
修复：把 fig 目录建到 `storage/scratch/fig_*`（`tempfile.mkdtemp(prefix="fig_", dir=<storage/scratch>)`），清理只扫自己的目录。

---

## 4. 低（P3）

- **L1** `core/providers.py:68` Ollama `base_url="http://localhost:11434/v1"`：容器里没有 Ollama，选它就失败（有"需先 ollama serve"提示，`ui/sidebar.py:221`）。可接受，但部署文档里应注明"预设平台里有 Ollama，公网实例上不可用"。
- **L2** `storage/scratch/_s2_ui_verify.py:10` 等开发脚本里含 `C:/Users/Administrator/...` 硬编码路径 —— 位于 `storage/`，被 `.gitignore`/`.dockerignore` 排除，**不会随 git/镜像分发**；但 `docker-compose.yml:10` 会把宿主 `./storage` 挂进容器，建议部署前清空宿主 `storage/` 再起容器。
- **L3** `scripts/setup_real_library.py:9`、`assets/make_icon.py:4` 注释里有 `C:\Users\...` / `C:/Users/...` —— **纯注释/文档字符串，不影响运行**。
- **L4** 硬编码并发与内存假设：`services/library.py:23 OCR_PARALLEL=3`、`:21 PDF_BATCH_SIZE=20`、`:22 PDF_PAGE_TIMEOUT_SECONDS=30`、`ui/page_reading.py:440 ThreadPoolExecutor(max_workers=workers)`。Streamlit Cloud 免费档约 1 GB 内存，跑 H3 的 541 页教材可能 OOM。**未实测**。
- **L5** `docs/` 被 `.dockerignore:47` 排除 —— 容器内无文档，不影响运行，但运维排障时少一份手册。
- **L6** 无 `HEALTHCHECK`：`Dockerfile` 只有 `EXPOSE 8501`。加一行 `HEALTHCHECK CMD curl -f http://localhost:8501/_stcore/health || exit 1`（需先装 curl）能让 `docker ps` 直接看出存活状态。

---

## 5. 逐条回你给的审计清单（结论先行）

### ① Windows 路径 / 跨平台 —— ✅ **无隐患**
我用了三层搜索，全部零命中于业务代码：
```
grep -rn 'os\.startfile|msvcrt|winreg|ctypes\.windll|CREATE_NO_WINDOW|STARTF_USESHOWWINDOW'
   → 命中全部集中在 desktop/（桌面壳，Streamlit 部署路径根本不 import）
grep -rn '[A-Za-z]:\\\\' --include=*.py core services tools ui app.py config.py scripts
   → 仅 scripts/setup_real_library.py:9、assets/make_icon.py:4 两处注释
grep -rn '\\\\[A-Za-z0-9_.-]*\\\\' --include=*.py core services tools ui app.py config.py scripts
   → 仅 services/library.py:829 一条 docstring（"\\n\\n" 是转义说明）
grep -rn 'os\.sep|ntpath|posixpath|platform\.system|sys\.platform|os\.name'
   → 仅 scripts/release.py:99（发布脚本，非运行时）
```
- `ui/desktop_chrome.py:29-37`：`DESKTOP_MODE` 为假时**直接 return**，不会注入桌面 CSS。
- `config.py:22` 的 `APPDATA` 用的是 `os.environ.get(...)` 且只在 `FROZEN`（PyInstaller）分支，Linux 上走不到。
- 你说的 `services/translate.py` 里大量 `\\n` —— 我确认是 JS/CSS 模板字符串转义，**不是路径**，未误报。
- 结论：**跨平台这块是干净的**，不用改。

### ② 文件系统大小写敏感 —— ✅ **无隐患**
逐一核对代码引用 vs 磁盘实际大小写（Linux 区分大小写）：
| 代码引用 | 位置 | 磁盘实际 | 一致 |
|---|---|---|---|
| `RESOURCE_ROOT / "demo_data"` | config.py:35 | `demo_data/` | ✅ |
| `DEMO_DIR / "data.csv"` | ui/page_writing.py:106 | `demo_data/data.csv` | ✅ |
| `ROOT / "lib" / "vis-9.1.2" / "vis-network.min.js"` | services/graph.py:528-529 | `lib/vis-9.1.2/vis-network.min.js` | ✅ |
| `ROOT / "pdf"`（PDF_DIR） | config.py:34 | `pdf/` | ✅ |
| `/app/static/mermaid.min.js` | ui/page_writing.py:495 | `static/mermaid.min.js` | ✅ |
| DEMO_PAPERS 的三个 key | config.py:116/145/163 | `pdf/` 下同名文件均存在 | ✅ |
全部小写且与磁盘一致。**无需修改**。

### ③ storage 空库初始化
**config.py 的 mkdir 覆盖**：`config.py:38` 只建 4 个 —— `storage/`、`storage/uploads/`、`storage/chroma/`、`storage/pdf/`（桌面态另加 `logs/`、`config/`）。
- `storage/cache/`：**未建，但 `services/citation_quality.py:169` 自己 `os.makedirs(_CACHE_DIR, exist_ok=True)`** → 安全。
- `storage/scratch/`：**未建，但 grep 显示只有开发者脚本用**（`storage/scratch/_s2_verify.py`，该目录被 gitignore/dockerignore 排除）→ 运行时不依赖，安全。
- `logs/`（app.py:15）：`app.py:22` `os.makedirs(LOG_DIR, exist_ok=True)` → 安全。
- 结论：**空目录覆盖完整，不需要补 mkdir。**

**逐页空库守卫**（逐个读代码确认，非 grep 推测）：
| 页面 | 空库表现 | 结论 |
|---|---|---|
| 🤖 Agent（page_agent.py:305-316, 350） | 有引导 `st.info` + 空库 caption + `empty_state("还没有执行记录")` | ✅ 安全 |
| 📁 文献库（page_library.py:285/325/485/531） | 三个 tab 各有 `empty_state`；顶层还有 `require_library` 兜底 | ✅ 安全（但见 H3 的重建按钮） |
| 💬 问答（page_qa.py:250） | `if scope == "单篇" and ctx.files:` 有守卫，`ctx.files[0]` 只在非空分支 | ✅ 安全 |
| 📖 阅读（page_reading.py:1214-1219） | `_resolve_doc` 返回空 → `empty_state("请先在文献库导入论文…")` | ✅ 安全 |
| 🔍 检索（page_search.py:38/95/119/154/219/231/334） | 各子功能均有 `empty_state` + `net_error` | ✅ 安全（但见下） |
| 🕸️ 图谱（page_graph.py:147/259/286） | `empty_state("图谱为空节点")` + 需 ≥2 篇的明确报错 | ✅ 安全 |
| ✍️ 写作（page_writing.py:129/268） | CSV 区与框架区各有 `empty_state` | ✅ 安全 |
| 🏗️/💡（page_misc.py:61/117） | 纯静态 HTML/文案，不读 ctx | ✅ 安全 |
| 框架层（app.py:136 `safe_render`） | 任何页面异常都被 `ui/common.py:390-401` 兜住 → 不会红屏 | ✅ 安全 |
- 唯一"裸索引"风险点 `ui/page_qa.py:253 ctx.files[0]` 经确认**有 `and ctx.files` 前置守卫**（第 250 行），**不是隐患**。

**🚀 一键加载演示数据 —— 关键结论（这条你要特别注意）**：
- 位置：`ui/sidebar.py:381-427`，按钮在侧边栏底部「🧪 演示与维护」区。
- 点下去做什么：遍历 `config.py:115-182` 的 `DEMO_PAPERS`（3 篇：1706.03762 v7 / v1、1810.04805 v2），逐篇调 `library.add_text_corpus()`，写 `ctx.papers/chunks` → `save_library()` → `st.rerun()`。
- **内置语料还是外网下载？→ 语料是内置的**（`config.py` 里硬编码的论文正文，不走网络）✅
- **但它需要 embedding** → `services/library.py:857 → 68 → ctx.embed` → ONNX MiniLM 模型 → **首次必须从 AWS S3 下载 79.3 MB** ❌
- 也就是说：**"无 Key / 离线也能演示"这句文案是错的**（`ui/sidebar.py:383` help、`:268`、`:244`、`ui/common.py:285` 都这么写）。离线首次点它会三篇全失败，页面只剩"再点一次"。→ 详见 **F2**，这是我认为**第二致命**的问题。

### ④ 首次运行的隐式联网依赖
| 依赖 | 触发时机 | 无外网/冷启动表现 | 实测 |
|---|---|---|---|
| ONNX all-MiniLM-L6-v2（79.3MB 压缩包/167MB 落盘） | `Container.__init__`（`app.py:86` → `services/state.py:58`） | 3 次重试后失败；后续所有 embed 抛异常 → 演示数据加载失败、检索失效 | 来源 AWS S3；缓存 `~/.cache/chroma/`；预热 6.77 s（已缓存）；下载耗时 **未实测** |
| chromadb 本身 | 无联网需求（本地 PersistentClient） | — | ✅ |
| HuggingFace | **全仓无使用** | — | `HF_ENDPOINT` 是死配置（M1） |
| LLM（智谱等） | 用户发起问答/OCR/综述 | 降级为伪响应，有 `llm_failure_banner` 横幅 | ✅ 已处理 |
| arXiv / PubMed / DuckDuckGo / GitHub | 用户点🔍页按钮 | 有 `net_error` + 离线提示 | ✅ 已处理 |
| mermaid.min.js | 渲染架构图 | 本地 `static/` 优先（实测 `enableStaticServing` 默认 True），失败回退 jsdelivr CDN（**国内可能慢**） | ⚠️ 建议确认 |
| LaTeX / graphviz | 点 TikZ/Graphviz 渲染 | 三级降级，不崩 | H6/H7 |

### ⑤ 端口与地址绑定
- Docker：✅ `Dockerfile:16` 已显式 `--server.address=0.0.0.0`。
- **Streamlit Cloud：⚠️ `.streamlit/config.toml:14 address="127.0.0.1"` 会随 git 上传** → H1。
- 其余 `127.0.0.1` 命中全部在 `desktop/`（桌面壳）与 `docs/`（文档示例），不影响部署。
- `core/providers.py:68` 的 `localhost:11434` 是 Ollama 预设端点（L1）。

### ⑥ 密钥读取路径 —— 结论：**只认 `os.getenv` + `.env`，零 `st.secrets`**
- `config.py:45-58`：`load_dotenv(ROOT / ".env")`（非桌面态只认项目根 `.env`）。
- `core/providers.py:122` / `config.py:97` / `services/state.py:79` / `services/extern.py:14`：全部 `os.getenv`。
- 全仓 `st.secrets` **0 命中**。
- `.env` 被 `.gitignore:2` 排除 → **Cloud 上一定没有 .env 文件**。
- **结论：这是致命项（F1）。** 修复方案见 F1，核心是给 `_env_str` 和 `resolve_key` 加 `st.secrets` 回退。
- 附：仓库里**没有真实密钥泄露**——实测 `git ls-files | grep -x .env` 为空（`.env` 从未被跟踪）、`git grep` 扫 `sk-`/32 位 hex 点分/Google 前缀，仅在 `tests/test_core.py:204`（redact 自测夹具）与 `docs/review/round5_judgeF.md:145`（引用该夹具）命中，**均为假值**。`.env.example` 所有 Key 值为空。

### ⑦ 其它部署杀手（按你的要求重点查了系统级命令）
| 系统级依赖 | 是否必需 | 容器内有没有 | 是否崩 | 条目 |
|---|---|---|---|---|
| LaTeX（tectonic/pdflatex/xelatex/lualatex） | TikZ 真实渲染 | **无**（Dockerfile 未装） | 不崩，优雅降级为展示源码 | H6 |
| Graphviz `dot` | DOT 真实 SVG 渲染 | **无** | 不崩，降到纯 Python SVG | H7 |
| Tesseract / OCR 命令行 | **未使用** —— OCR 走多模态大模型（`services/library.py:645` 并发调 VL 模型），**没有 pytesseract** | — | — | ✅ 无风险 |
| matplotlib | Graphviz 二级降级 | **未声明也未装** | 不崩，降到三级 | H5/M7 |
| 中文字体 | PDF 导出用 PyMuPDF **内置** `china-s` | 不需要 | 不乱码 | ✅ 无风险（M7） |
| `/tmp` 可写 | `tempfile.mkdtemp`（TikZ / 代码沙箱 / 图片抽取） | 容器默认可写 | — | ✅ |
| 线程/并发假设 | `OCR_PARALLEL=3`、`PDF_BATCH_SIZE=20` | — | 小内存实例可能 OOM（**未实测**） | L4 |
| 子进程执行（代码沙箱） | `tools/definitions.py:327` | — | 公网部署的安全面 | M6 |

---

## 6. 总评

**现在能不能直接部署？—— 不能。**

- 走 **Docker / Linux 服务器**：F2（模型冷启动）和 F3（`docker compose up` 直接失败）必挡路；H6/H7 会让"TikZ/Graphviz 真实渲染"两个卖点在评委面前变成降级提示。修完 F2/F3 + 装 graphviz（`apt-get install -y graphviz`，30 MB，很便宜）后基本可用。
- 走 **Streamlit Cloud**：F1（拿不到 Key）是最大风险，H1（`address=127.0.0.1`）和 H4（`storage/` 易失）紧随其后；且 Cloud 上不能 `apt-get`，H6/H7 无解，只能改文档口径。

**还差几件事？** 必办 **3 件**（F1/F2/F3），强烈建议再办 **7 件**：H1（删 `[server]` 段）、H2（chroma WAL）、H3（重建按钮加警示/拆分）、H4（空库自动灌演示数据开关）、H5（matplotlib 声明或删死代码）、H7（装 graphviz）、M4（requirements 加上界）。全部修完约需改动 **6 个文件**（`config.py`、`core/providers.py`、`services/state.py`、`docker-compose.yml`、`Dockerfile`、`.streamlit/config.toml`），`requirements.txt` 与 `ui/page_library.py` 各一处小改。

**好消息**：跨平台路径（①）、大小写（②）、目录初始化（③ 前半）、空库守卫（③ 后半，逐页确认过）、中文字体、Tesseract 依赖 —— 这几项**全部是干净的**，不用动。工程底子比预期扎实，主要风险集中在"部署环境假设"而非"代码质量"。

---

## 7. 复核纪要（改动落地后的复验）

初稿发出后 team-lead（`config.py` / `core/providers.py`）、deploy-docker（`Dockerfile` / `docker-compose.yml` / `.streamlit/config.toml` / `.dockerignore`）已按 F1/F2/F3/H1/H7 动手修改。我对**改动后的代码**做了只读复验（仍未改任何文件）。

### 7.1 已闭环 ✅

| 项 | 改动 | 我的复验结论 |
|---|---|---|
| **F1** Cloud 拿不到 Key | `config.py:61-78 _st_secret()`；`config.py:81-94 _env_str()` 加 `os.getenv → st.secrets → default` 链；`core/providers.py:116-138 resolve_key()` 同链 | ✅ **实测通过**。裸 Python（无 `streamlit run`）下 `import config` 无异常无 warning（`-W error::Warning` 未触发），`_st_secret()` 返回 `""`，`_env_str(未设变量,"DEF")` 正确回落 `"DEF"` → 桌面版/脚本/单测路径不受影响。回退顺序正确。 |
| **F2** ONNX 冷启动 | `Dockerfile` 第 4 层构建期预热 + 6 文件 assert；`HOME=/home/app` 与 `Path.home()` 对齐；不挂 `$HOME/.cache/chroma` 卷（避免覆盖已烤入的模型） | ✅ **路径验过是对的**。我用本机真实缓存对照：chromadb 1.5.9 的 `_download_model_if_not_exists()` 判定条件是 `<DOWNLOAD_PATH>/onnx/` 下 6 个文件，本机实际布局与 assert 列表**完全一致** → 不会误判；"缺文件就让构建失败"这个设计是对的，不会造出"能起来但功能全废"的镜像。 |
| **F3** `docker compose up` 失败 | `env_file: - path: .env, required: false` + `environment: ${VAR:-}`；并显式 `AI4S_DESKTOP: "0"` | ✅ 解决。 |
| **H1** `address=127.0.0.1` | `.streamlit/config.toml` 已注释掉并附说明 | ✅ 解决。 |
| **H7** 无 graphviz | `apt-get install -y --no-install-recommends graphviz` + `--server.enableStaticServing=true` 显式化 | ✅ 解决，且比我原建议"删 [server] 段"更稳妥（命令行显式，不依赖 config.toml 是否被忽略）。 |

### 7.2 数据订正

- 模型压缩包实测 **79.3 MB**（HTTP `content-length` = 83,178,821 B），**初稿的"90 MB"是估值**，以 79.3 MB / 落盘 167 MB 为准。已同步 deploy-doc 与 deploy-docker。
- F2 的时序补充：下载发生在**应用启动时**（`app.py:86`），但失败被静默吞掉，**现象**出现在点击「一键加载演示数据」时。两句都要写，详见 F2 条目内的补充段。

### 7.3 仍未闭环 ⚠️（按重要性）

1. ~~**M4 `requirements.txt` 全部无上界**~~ → **已闭环**（本轮其他成员已加 `PyMuPDF<2.0` / `chromadb<2.0` / `openai<4.0` / `pandas<4.0` / `numpy<3.0`，并新增 `matplotlib>=3.8`；deploy-docker 用新 requirements 重跑二进制核验通过）。
   ⚠️ 但新增的 `matplotlib` 在云平台路径上引入了中文方框回归，见 **§7.8**。
2. ~~**M6 公网安全面 —— 未改。**~~ → **已闭环 / 已决策**（本节为历史快照，保留作档案）：`showErrorDetails` 已落盘为 `false`（`.streamlit/config.toml:35`，复验属实）；`t_run_python` 经 team-lead 拍板**保留功能、不新增开关**，风险改由 DEPLOY.md 公网部署章节显式提示。**不再作为未闭环项。**
3. **H2 chroma 并发锁 —— 未改。** 走 Docker 单实例风险较低；走 Cloud（多会话）建议加 WAL + `busy_timeout`。
4. 其余 H3 / H4 / H5 / H6 / M1–M3 / M5 / M7–M9 均未处理，均为"不崩但体验/口径不符"级，可择期。

### 7.4 新引入 1 处（低危，建议补）

deploy-docker 为保留主题色与 `enableStaticServing`，把 `.streamlit/` 从 `.dockerignore` 中放开了（合理），但**未排除 `.streamlit/secrets.toml`**：

```
.gitignore  已加 .streamlit/secrets.toml   ✅
.dockerignore 未加                          ❌
```

两个密钥入口不对称：`.env` 被明确排除，而 `.streamlit/secrets.toml` 若被写入真实 Key 做本地调试，会被 `docker build` 烤进镜像。建议 `.dockerignore` 补一行 `.streamlit/secrets.toml`。（当前仓库**不存在**该文件，属潜在风险而非现存泄露。）

### 7.5 卷挂载语义澄清（deploy-docker 请我复核的分歧点）

争议：是否要把 `$HOME/.cache/chroma` 挂成卷。deploy-docker 决定**不挂**，理由是"挂载会覆盖掉镜像内已烤入的模型，预热白做"。

**结论：决定正确，但理由需要修正。** 我查了 Docker 官方存储文档原文：

| 场景 | 是否复制镜像内容到卷 | 文档原文 |
|---|---|---|
| **新建的命名卷**（挂载时为空卷） | **会**（默认行为） | "If you mount an _empty volume_ into a directory in the container in which files or directories exist, these files or directories are propagated (copied) into the volume by default." |
| **已存在且非空的命名卷** | **不会**，卷内容**遮盖**镜像目录 | "If you mount a _non-empty volume_ into a directory in the container in which files or directories exist, the pre-existing files are obscured by the mount." |
| **bind mount** | **不适用**复制；宿主机目录直接遮盖容器路径 | 复制语义仅属于 volume；bind mount "dependent on the directory structure and OS of the host machine" |

所以：
- 首次 `docker compose up` 建卷时，镜像里烤好的 167MB 模型**会被复制进新卷**，功能是正常的 —— "挂载必然覆盖"这个说法**对空卷不成立**。
- 但他的担心在**这个场景**下成立：卷一旦被创建过（`down` 不删卷），之后重建镜像时**不会重新复制**，卷里还是旧内容。若模型版本变了，就会读到旧模型。
- 若换成 **bind mount**（`- ./xxx:/home/app/.cache/chroma`），则**一定**是空的、必然遮盖 —— 这才是"预热白做"的确定场景。

**为什么"不挂"仍然是对的**：模型既然已烤进镜像，挂卷**没有任何收益**，只带来两个风险 —— ① 镜像重建后卷内容陈旧（上表第二行）；② 有人改成 bind mount 就直接失效。零收益 + 有风险 → 不挂是正确决策。

**一个能自证的内部证据**：他给 `/app/logs` 用的 `app_logs:/app/logs` **命名卷**，恰恰**依赖**上面"空卷复制"这条行为 —— `/app/logs` 在镜像层已 `chown app:app`，只有把目录（连同属主）复制进新卷，卷的根目录才会是 `app:10001` 属主，容器内才能写。如果"卷会无脑遮盖、不复制"是真的，`app_logs` 卷会是 root 属主、他注释里担心的"日志静默丢失"反而会真实发生。
> **未实测**：本机无 Docker 守护进程（`docker: command not found`），无法实测卷的属主继承。以上"复制会保留属主"是按文档语义**推断**，不是实测结论。

**给未来读者的提示**：万一以后需要刷新模型，正确姿势是 `docker compose down -v`（删卷）或直接不挂卷重建，而不是"在容器里删缓存让它重下"。

### 7.6 关于 `pdf/LongBook_540p_fixture.pdf` 不排除 —— 复核后同意

我原建议 `.dockerignore` 排除它（486KB，看着像压测夹具）。deploy-docker 不采纳，理由是有脚本引用。我核过了，**他的引用是对的**，决定成立：
```
scripts/verify_official.py:68   lp = load(P("pdf", "LongBook_540p_fixture.pdf"), "LongBook_540p.pdf")
scripts/_smoke_read.py:366      files = [..., "UnderstandingDeepLearning_02_09_26_C.pdf", "LongBook_540p_fixture.pdf"]
```
且 `scripts/` 确实会进镜像（`.dockerignore` 未排除），排除该 PDF 会让这两个脚本在容器内跑不了。486KB 的体积收益可忽略，**撤回我的原建议**。

顺带记一笔（低）：`scripts/make_longbook.py:4` 里有硬编码 Windows 路径 `DST = r"C:\Users\Administrator\ai_research_agent\pdf\LongBook_540p_fixture.pdf"`。该脚本只在需要**重新生成**夹具时才手动跑，运行时不调用，不影响部署 —— 但它会随 `scripts/` 进镜像，公开镜像里能看到这台机器的用户名。与 `scripts/setup_real_library.py:9` 同类（已在 L3 记录）。

### 7.7 F1 残余风险收窄：Secrets 未注入环境变量时，受影响的只剩 1 个可选变量

deploy-doc 有一项待确认是"三个裸 `os.getenv` 变量能否通过环境变量注入读到"。这条**不用等部署就能定论** —— 我重新枚举了当前代码里所有环境变量读取点，发现 team-lead 在改 F1 时已经把其中两个改成 `_env_str` 了。

**实测（清空相关环境变量后 import 各模块）**：
```
LLM_PROVIDER  -> 'zhipu'     （state.py:80，已走 _env_str，正确回落默认值）
GITHUB_TOKEN  -> ''          （extern.py:14，已走 _env_str）
resolve_key 含 _secret 回退  -> True
```

**当前完整清单**：

| 读取点 | 变量 | 有无 `st.secrets` 兜底 | 平台不注入环境变量时的影响 |
|---|---|---|---|
| `config.py:81-94 _env_str` | 全部 config 变量（含 `ZHIPU_API_KEY` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` …） | ✅ 有 | 无影响 |
| `services/state.py:80` | `LLM_PROVIDER` | ✅ 有（已改 `_env_str`） | 无影响（回落 `zhipu`） |
| `services/extern.py:14` | `GITHUB_TOKEN` / `GITHUB_API_TOKEN` | ✅ 有（已改 `_env_str`） | 无影响（回落空，GitHub 限流 60 次/时） |
| `core/providers.py:135-138` | 各 Provider 的 Key | ✅ 有（`_secret()` 回退） | 无影响 |
| **`core/providers.py:79`** | **`LLM_PROVIDERS_EXTRA`** | ❌ **无**（裸 `os.getenv`） | **唯一受影响**：追加自定义 Provider 的可选功能失效（实测 0 个额外 provider）。主演示链路不依赖它 |
| `core/llm.py:626` | `LLM_API_KEY` / `OPENAI_API_KEY` | ❌ 无 | **实际无影响**：这行在 `from config import ...` 失败时的 `except` 兜底分支里，正常情况下永远走不到 |
| `config.py:10` | `AI4S_DESKTOP` | ❌ 无 | **故意如此，不要加兜底**。这是桌面模式开关，云上必须保持未设置/0；若被 Secrets 设成 1，数据目录会跑到 `APPDATA`/`AI4SResearchAgent`，容器里路径全错 |
| `config.py:22` | `APPDATA` | ❌ 无 | 仅在 `FROZEN`（PyInstaller）分支，云上不触发 |

**结论**：即使 Streamlit Cloud **完全不**把 Secrets 注入 `os.environ`，唯一受影响的是 `LLM_PROVIDERS_EXTRA`（可选功能）。主 Key 链路（F1 原本的致命点）已由 `_env_str` + `resolve_key` 双层兜底覆盖。
→ deploy-doc 的这条待确认可以**从"待实机确认"改为"已核实：仅 `LLM_PROVIDERS_EXTRA` 受影响"**，其 8 条待确认降为 7 条。
→ 建议文档仍保留"根级键、不要放 `[section]`"的要求（那是 `_env_str` 取值方式的硬约束，与是否注入环境变量无关）。

### 7.8 ✅ 已闭环 —— matplotlib 回归（team-lead 选方案 ②「移除依赖」）

> **✅ 状态：已闭环。** team-lead 已把 `matplotlib` 从 `requirements.txt` **移除**（选 ② 而非我推荐的 ①），并在原位置留下"故意不装"的说明注释。本节分析留档备查。
>
> **移除后的最终链路**：
> | 环境 | 走到的分支 | 中文 |
> |---|---|---|
> | Docker | `dot -Tsvg`（第 2 级，apt 已装 graphviz） | ✅ 正常（Graphviz 自身处理） |
> | 云平台 | 跳过第 3 级（matplotlib 缺失 → `import` 失败 → `except` 返回 `None`）→ 落到第 4 级零依赖 DOT→SVG | ✅ 正常（浏览器渲染） |
>
> 与我原分析一致：**不装 matplotlib 在两条路径上都更好**。也确认不需要 `fonts-wqy-microhei`（用不上了）。
>
> ⚠️ **DEPLOY.md 有 6 处残留旧表述需同步**（team-lead 给的行号已因 C-6b 插入而漂移，以下为**当前实测**）：`DEPLOY.md:18`（"依赖 14 个，matplotlib 本轮新增"）、`:213`（"装 14 个包"）、`:601`（"三级降级全活"）、`:608`（"第二级 matplotlib 现在在云平台也是活的"）、`:815`（"会走 matplotlib 第二级"）、`:838`（"matplotlib 已在 requirements.txt"）。已转达 deploy-doc。

本轮有成员在 `requirements.txt` 加了 `matplotlib>=3.8`（意图是激活 H5 那级降级）。**这个改动在 Docker 上无害，但在 Streamlit Cloud 上会把"中文正常显示"变成"中文方框"**，属于新引入的回归风险。

本轮有成员在 `requirements.txt` 加了 `matplotlib>=3.8`（意图是激活 H5 那级降级）。**这个改动在 Docker 上无害，但在 Streamlit Cloud 上会把"中文正常显示"变成"中文方框"**，属于新引入的回归风险。

**降级链现状**（`services/writing.py:1506-1544`）：

```
1) Python graphviz 包 + dot 可执行文件
2) dot -Tsvg 直接调用          ← Docker 装了 graphviz，走到这里就返回，matplotlib 用不到
3) networkx + matplotlib 降级  ← Cloud 无 graphviz，会走到这里（新增 matplotlib 后现在会成功）
4) 零依赖纯 Python DOT→SVG     ← 新增 matplotlib 之前，Cloud 走的是这里
```

**关键差异**：

| | 改动前（Cloud） | 改动后（Cloud） |
|---|---|---|
| 走到的分支 | 4) 纯 Python SVG | 3) matplotlib PNG |
| 中文渲染方 | **浏览器**（客户端字体） | **服务器端**（容器内字体） |
| 中文能否显示 | ✅ 能（`wcopilot_dotrender.py:259` 输出 `font-family="Segoe UI,Microsoft YaHei,sans-serif"`，由客户端浏览器解析） | ❌ 方框（matplotlib 默认 DejaVu Sans **无 CJK 字形**；容器无 Noto CJK） |

**为什么标签会是中文**：`services/writing.py:1457-1460` 的 `labels` 直接取 DOT 里的 `node["label"]`，而 DOT 由大模型按中文提示生成（`ui/page_misc.py:103-104` 的架构图需求、`tools/definitions.py:415 draw_diagram`），节点名大概率是中文（如"交互层""Agent 内核"）。

> **未实测**：本机未装 matplotlib 且非 Linux，无法实测方框。但"matplotlib 默认字体无 CJK 字形 → 渲染为方框"是确定机制，非推测。

**修复建议（三选一，推荐 1）**：
1. 在 `_dot_via_matplotlib()`（`services/writing.py:1445`）入口加一道判断——标签含 CJK 且容器无 CJK 字体时**返回 `None`**，让它自然落到第 4 级纯 Python SVG（浏览器渲染，中文正常）：
   ```python
   has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in "".join(labels.values()))
   if has_cjk and not _has_cjk_font():   # _has_cjk_font: 查 matplotlib font_manager 里是否有含 CJK 的字体
       return None
   ```
2. 仓库内置一个 CJK TTF，用 `matplotlib.font_manager.fontManager.addfont()` 注册（约 +10~20MB 仓库体积）。
3. Dockerfile 加 `fonts-noto-cjk` —— **只解决 Docker，而 Docker 根本走不到这级**，性价比最低。

**⚠️ 更正（deploy-docker R22 指出，我复核成立）**：我上面写的"Docker 上用不到第 3 级"**过于绝对**。

`services/writing.py:1517-1527` 的第 2 级，只在 `dot -Tsvg` **成功且输出含 `<svg>`** 时才 `return`；以下两种情况下**不会 return，会继续穿透到第 3 级**：
```python
if dot_exe:
    try:
        proc = subprocess.run([dot_exe, "-Tsvg"], ...)
        svg = proc.stdout or ""
        if "<svg" in svg:
            return {...}                      # ← 只有这里返回
        res["log"] = ...                      # ← 畸形 DOT：dot 报错，不返回，继续往下
    except Exception as e:
        res["log"] = ...                      # ← 超时/异常：同样不返回，继续往下
```
即 **Docker 上遇到畸形 DOT 或 `dot` 超时时，同样会落到 matplotlib 第 3 级 → 中文方框**。所以准确说法是：**Docker 上"通常"走不到，但不是"绝对"走不到**。这条加强了第 ① 项修复的必要性（它是唯一在两条路径上都生效的修法）。

**⚠️ 另一个关键事实：光装中文字体是没用的。**

我 grep 了全仓，**代码中没有任何 matplotlib 字体配置**：
```
grep -rn 'rcParams|font.sans-serif|fontfamily|font_manager' core services tools ui  →  0 命中
```
matplotlib 默认字体族是 `DejaVu Sans`，且**不会因为某个字形缺失就自动换字体去找字形**（它按 `font.sans-serif` 列表取第一个可用字体，缺字形即出方框）。所以：
- 只 `apt-get install fonts-wqy-microhei`（约 5MB）而**不改代码** → **仍然是方框**，等于白装；
- 要装就必须同时在代码里 `rcParams["font.sans-serif"]` 前置该字体并清字体缓存 —— 改动比第 ① 项更大，且只解决"渲染"不解决"布局"。

> **未实测**：本机未装 matplotlib，无法实测方框与 fallback 行为；"缺字形不自动换字体"按 matplotlib 既有行为判断。

**结论**：第 ③ 项（装字体）的实际收益比 deploy-docker 估计的还要低 —— 它不是"5MB 买个保险"，而是"5MB + 改代码 + 清缓存"才可能生效。**第 ① 项（CJK 判断返回 None，落回第 4 级浏览器渲染 SVG）依然是最优解**：3~5 行、两条路径都覆盖、不需要任何字体。

**顺带（无关紧要）**：`.dockerignore` 里 `.streamlit/secrets.toml` 出现了两次（第 10 行和第 60 行），功能无害，是两次编辑叠加的结果，可以删掉重复的一行。

### 7.9 ✅ 已闭环 —— 文档与 compose 的端口冲突（原 🔴 阻塞级）

> **✅ 状态：已闭环，无需处理。** deploy-doc 已按下面四条建议全部改完，并经 deploy-docker 与我**各自独立复核**确认。以下内容**留档备查，不是待办**。
>
> **当前磁盘实测（DEPLOY.md 862 行，行号已随后续插入更新）**：
> | 行 | 内容 |
> |---|---|
> | `:12` | 基线表已改为 `127.0.0.1:8501:8501`，标注"默认只绑宿主回环" |
> | `:429` | C-4 标题直接写成「**方案 C 的必配项**」 |
> | `:431` | 「不配 Nginx 就没有任何访问入口，外部根本连不上（**这不是故障，是设计如此**）」 |
> | `:522` | 安全组表 8501 改为「✅ 无需在安全组放行」 |
> | `:539` | 「C-4 的 Nginx 反代在方案 C 里是**必配项，不是推荐项**」 |
> | `:551` | 新增 **C-6b「没有域名怎么办」**（本条由我的决策框架引出，见下） |
> | `:35` | §〇 对比表补注「纯 IP 签不了证书，见 C-6b」 |
> | `:798` | 故障表新增「本机 curl 通、外部浏览器不通 = **预期行为**」，并写明**不要去改 `--server.address`** |
>
> **C-6b 补上的逻辑漏洞**：我原建议只说了"回环"和"改回 8501:8501"两条路，没区分有无域名。而 **Certbot 签不了纯 IP 证书** —— 没域名时"绑回环 + 无法签证书"= 评委完全访问不到。C-6b 把它定性为「**无域名时的唯一可行路径，不是不推荐**」，并在 §〇 决策建议加了「有云服务器但没域名 → 别选方案 C，去做方案 A」。这一条比我原建议更完整。
>
> **复验方式**：`docker-compose.yml:23` = `127.0.0.1:8501:8501`；`DEPLOY.md` 中「发布到宿主所有网卡」「有意保留」均已消失，「必配项」已出现。两处一致。

本轮 `docker-compose.yml` 的端口被改成了**只绑宿主回环**，但 `DEPLOY.md` 还是按"发布到所有网卡"写的，而且**明确论证过不该绑回环**。两处直接打架，且撞在"选错就打不开"的位置上。

本轮 `docker-compose.yml` 的端口被改成了**只绑宿主回环**，但 `DEPLOY.md` 还是按"发布到所有网卡"写的，而且**明确论证过不该绑回环**。两处直接打架，且撞在"选错就打不开"的位置上。

**compose 现状**（`docker-compose.yml:17-23`）：
```yaml
ports:
  - "127.0.0.1:8501:8501"     # 只绑宿主 127.0.0.1
```

**DEPLOY.md 现状**（三处，全部与新 compose 矛盾）：
| 位置 | 文档原文 | 与现状冲突 |
|---|---|---|
| `DEPLOY.md:12` | 「端口 `8501`（宿主:容器 = `8501:8501`）」 | 事实错误 |
| `DEPLOY.md:496` 安全组表 | 「8501 ⚠️ **默认发布到宿主所有网卡，可直接访问**」 | 事实错误 |
| `DEPLOY.md:498-501` | 「**默认就是 `8501:8501`**…这是**有意保留**的…**绑成回环会砍掉"云服务器上直接开端口演示"这条快速路径**」，并标注「**现状即最终决定，不是待办项**」 | **结论与新决定相反**，且用"最终决定"的措辞堵住了读者的怀疑 |
| `DEPLOY.md:510-514` | 把 `127.0.0.1:8501:8501` 写成「**可选加固（不是必须，配了更稳）**」 | 它现在是**默认**，不再是可选 |

**会怎么炸**：部署者按 DEPLOY.md 做完 `docker compose up -d`，然后从自己电脑访问 `http://<host>:8501` → **连接被拒**。回头查文档，§498 白纸黑字写着"默认发布到宿主所有网卡，可直接访问""这是有意保留的"——读者会去查安全组、查防火墙、查 `--server.address`，**唯独不会想到是 compose 绑了回环**。这个排查路径很长，且文档在主动误导。

**修复建议**（team-lead 既然已拍板绑回环，就按 (b) 统一）：
1. `DEPLOY.md:12` 改成 `127.0.0.1:8501:8501`，并注明"默认只绑宿主回环"。
2. `DEPLOY.md:496` 安全组表 8501 一行改成「不发布到公网（默认只绑 127.0.0.1），无需在安全组放行」。
3. `DEPLOY.md:498-514` **整段重写**：删掉"有意保留 8501:8501"的论证，改为：
   （注：以下 1–4 项为**当时提出的建议**，现已全部落实，行号为冲突发生时（DEPLOY.md 840 行）的旧值，当前行号见本节顶部表格。）
   - **Nginx 反代（C-4）现在是必配项，不是推荐项** —— 不配 Nginx 就没有访问入口；
   - 想要 `http://<host>:8501` 直连的，必须手动把 compose 改成 `"8501:8501"`，并同时限制安全组来源 IP。
4. 故障表加一条：`本机 curl 127.0.0.1:8501 通、外部浏览器不通` → **这是预期行为**（默认绑回环），走 Nginx；不要去改 `--server.address`（Dockerfile CMD 已经是 `0.0.0.0`，改它没用）。

**注**：这个判断（绑回环）本身是对的 —— 应用无任何鉴权，`--server.address=0.0.0.0` + 公网端口 = Key 可被盗刷。错的是**文档没跟上**。

> **⚠️ 归属于我的一处失误（诚实记录）**：我在给 team-lead / deploy-docker 的消息里写"team-lead 已拍板改成回环"，这个**归属我没有独立核实**，是转述 deploy-docker 的原话。事后 deploy-doc 反馈：team-lead 给他的消息是"维持 `8501:8501`、不要再改"——**三方消息互相矛盾**。
> 好在本条的**实质结论不受影响**：它是基于 `docker-compose.yml:23` 的磁盘实际内容取证的，不是基于"谁拍板"。deploy-doc 采用"以磁盘文件为准"的原则处理，是正确做法。
> **教训**：涉及"谁决定了什么"这类二手信息，我应标注来源或直接查文件，不应直接当事实转述。

**当前状态已复验一致**（两处均对齐回环）：
```
docker-compose.yml:23   - "127.0.0.1:8501:8501"
DEPLOY.md  "发布到宿主所有网卡" → 已删除   "有意保留" → 已删除   "必配项(Nginx)" → 已加入
DEPLOY.md 残留的两处裸 8501:8501 均带上下文（:425"唯一绕过方式…但不推荐"、:538"手动改，默认不是这样"），不构成误导
```

### 7.10 镜像体积：三方口径收敛，**结论 ≈ 1.2 GB（区间 1.1–1.3 GB）**

deploy-docker 请我挑他估算里"最大的不确定项"——即 **wheel 解压系数 2.0–3.0× 这个经验值**。这一项**不用猜，可以实测**，我做了。

#### 演进过程（三轮，含我自己一处实质性错误）

**第 1 轮 · 我的实测**：用 `importlib.metadata` 遍历 13 个直接依赖的**传递闭包**（本机解析出 122 个包），按 `RECORD` 累加实际落盘文件大小：

```
961,447,970 B = 916.9 MiB（122 包）  →  膨胀系数 916.9 / 220.1 = 4.17×
```

**第 2 轮 · team-lead 指出测量对象错位，我接受**：916.9 MiB 测的是**本机 conda 开发环境**（已安装态、含平台相关产物、含镜像不装的 dev 依赖），**不是镜像**，只能作上限参考。据此我**撤回**了原先"应改成 1.2–1.4 GB"的修正建议，采信 deploy-docker 的 0.9–1.2 GB。

**第 3 轮 · deploy-docker 反过来推翻自己的 0.9–1.2 GB**（本节以此为准）：他按 **cp311 / manylinux 解析出的 109 个包名**逐个复测（非本地闭包）：

```
Linux 解析集 109 个包，本地已装可测 107 个（缺 aiohttp-retry /
opentelemetry-exporter-otlp-common，均小包）
落盘合计 783,734,647 B = 747.4 MiB   →  膨胀系数 747.4 / 220.1 = 3.40×
```

**关键分歧点：scipy 到底装不装？—— deploy-docker 对，我错。**

我的 916.9 里含 `scipy` **132.4 MiB**，且我的归因表写它"由 altair / jsonpickle / networkx / pandas / plotly 多路径引入、难裁"。**这句是错的**，我实测复核了这些包的 `requires`：

```
altair     -> scipy; extra == 'doc'
plotly     -> scipy; extra == 'dev' / extra == 'dev-optional'
networkx   -> scipy>=1.11.2; extra == "default"
pandas     -> scipy>=1.14.1; extra == "computation" / extra == "all"
jsonpickle -> scipy>=1.9.3; python_version > "3.10" and extra == "testing"
chromadb   -> 无 scipy 声明
streamlit  -> 无 scipy 声明
```

**全部带 `extra ==` 标记 → 都是 extras 声明，pip 默认不装。** 我进一步扫了**全机所有已安装包**的 `requires`，**没有任何一个包无条件声明 scipy**（不带 `extra ==` 的 scipy 声明：0 条）→ **scipy 只能经 extras 进入，Linux 解析集不含它，这一点被彻底坐实。** 我的 916.9 因此虚高 132.4 MiB。

（我曾写"剔除 scipy 后 784.5 MiB 可作上限参考"——**该说法已撤回**，见下方收敛表与更正段：闭包含 extras，剔一项不干净。）

#### 收敛后的三个数

| 来源 | 依赖层 | 膨胀系数 | 说明 |
|---|---|---|---|
| deploy-docker 第 1 版 | 440–660 MiB | 2.0–3.0×（经验值） | ❌ 已被其本人推翻 |
| **deploy-docker 第 2 版（实测）** | **747.4 MiB** | **3.40×** | ✅ **唯一可用值**（按 Linux 解析集 109 包名逐个测） |
| **我（独立复现）** | **747.4 MiB** | **3.40×** | ✅ 拿他那 109 个名单独立跑，得 **783,734,647 B**，**与其字节级完全一致** |
| ~~我（剔除 scipy 后）~~ | ~~784.5 MiB~~ | ~~3.57×~~ | ❌ **撤回**：916.9 的闭包**含 extras**，除 scipy 外还有其他 extras 带入的包，剔一项不干净 |

#### 总量（分项一致，差异只在依赖层那一档）

```
依赖层                      747 – 785 MiB
python:3.11-slim 基础       130 – 150 MiB   ← 未实测
graphviz（apt）               ~30 MiB
ONNX 模型                    166.42 MiB
pdf/ + static/ + 代码          ~35 MiB
------------------------------------------------
合计                    1108 – 1166 MiB  =  1.16 – 1.22 GB
```

> **对外口径建议：≈ 1.2 GB，安全区间 1.1–1.3 GB。**（deploy-docker 给 1.1–1.3 GB；我按分项正确加总得 1.16–1.22 GB，他的区间是取整后的包络，两者不冲突，取 **1.2 GB** 作中心值最稳。）
>
> **注**：我原先的 1.2–1.4 GB 偏高，根因就是 scipy；team-lead 裁定的 0.9–1.2 GB 偏低，根因是 2.0–3.0× 系数。**两个数各有一次错误，现已各自纠正并收敛。**

#### 我先前「多出的 13 个是 pywin32 一类 Windows 平台包」这句是**错的**，已更正

把 extras 排除后重算闭包：**114 个包**，与 Linux 名单的差集不是 pywin32：

- **多出 8 个**：`async-timeout`、`exceptiongroup`、`graphlib-backport`、`httpx2-jsfetch`、`importlib-metadata`、`tomli`、`typing`、`zipp` —— 全部是**带 `python_version < X` 标记的条件依赖**（cp311 上根本不装）。实测这 8 个在本机**均未安装、合计 0 MiB**，只是我的 BFS 把"声明过的包名"也收进了集合。
- **缺失 3 个**：`aiohttp-retry`、`opentelemetry-exporter-otlp-common`（本机未装，与他一致）、`watchfiles`（本机解析不到，疑为 uvicorn 的 `standard` extras——他的 pip dry-run 里有，以他为准）。
- `pywin32` / `wmi` **只在"含 extras"的闭包里出现**，那正是 dev 环境的形态，不是镜像形态。

→ **结论：不存在"13 个 Windows 平台包虚高"这回事。** 我 916.9 偏高的原因**只有一个**——闭包把 extras 当成了硬依赖（scipy 是最大一块，132.4 MiB）。把 extras 排除后，本机解析集与 Linux 名单**基本一致**，这也解释了为何我能字节级复现他的 747.4。

#### 完整性交叉验证：109 名单有没有漏掉"会被装的 extras"？

extras 是否安装，取决于**有没有上游显式写出 `pkg[extra]`**（`extra == 'xxx'` 只是包内部标签，无人点名则不装）。我扫了 109 名单内所有包的 `requires`，找含 `[` 的声明：

```
chromadb      -> uvicorn[standard]>=0.18.3            ← 唯一【无条件】的一条
altair        -> pytest-xdist[psutil]~=3.5; extra == 'dev'
fsspec        -> dask[dataframe,test]; extra == 'test-downstream'
huggingface_hub -> safetensors[torch]; extra == "torch"
narwhals      -> dask[dataframe] / pyspark[connect] / narwhals[duckdb]; extra == …
plotly        -> polars[timezone]; extra == 'dev'
psutil        -> validate-pyproject[all]; extra == 'dev'
requests-oauthlib -> oauthlib[signedtoken]; extra == 'rsa'
streamlit     -> snowflake-snowpark-python[modin]; extra == "snowflake"
streamlit     -> streamlit[auth,charts,pdf,…]; extra == "all"
tokenizers    -> tokenizers[testing]; extra == 'dev'
（共 15 条，其中 14 条自身就带 extra == 标记 → 不装）
```

**唯一会真正生效的是 `chromadb → uvicorn[standard]`**，其产物 `httptools`、`watchfiles` **均在 109 名单内**（`httptools==0.9.0` / `watchfiles==1.3.0`）。

→ **109 名单在 extras 维度上是完备的，747.4 不漏项。** 这也顺带解释了 `watchfiles` 为何在我的闭包里解析不到——它经 `uvicorn[standard]` 进入，而我的 BFS 没解析方括号语法。

> **~~未核~~ → 已闭环（本轮）**：`aiohttp-retry`、`opentelemetry-exporter-otlp-common` 本机未安装，无法查它们的 `requires`。改由**直接向 PyPI 取 `requires_dist`**，两人独立核查结论一致：
> ```
> aiohttp-retry==2.9.1                       -> aiohttp（唯一无条件依赖，已在 109 名单）
> opentelemetry-exporter-otlp-common==0.66b1 -> opentelemetry-sdk~=1.45.1（已在名单）
>                                               opentelemetry-exporter-http-transport; extra == "http"（不装）
> ```
> **既不引入名单外的新包，也不激活任何 extras。** 体积缺口实测：`9,981 B + 12,385 B = 22,366 B = 0.021 MiB`，按 3.40× 折算安装体积 **0.073 MiB ≈ 747.4 的 0.0097%** —— **可忽略，且是有数的"可忽略"**。

> **⚠️ 未验证项（不得当作容器实测值引用）**：
> - 747.4 MiB 是**本机 Windows 二进制**（`.dll`/`.pyd`）测得的；Linux 是 `.so`，**精确值未实测**。deploy-docker 尝试直接 `pip download` 109 个 manylinux wheel 用 `zipfile` 累加（那才是纯 Linux 解压体积），跑 10 分 42 秒无产出后 kill。
> - **916.9 / 784.5 两个数均已作废**，不得再引用（闭包含 extras）。
> - **`.pyc` 增量可视为 0**：`Dockerfile:47` 已设 `PYTHONDONTWRITEBYTECODE=1`（我复验属实），故我先前"+10~20%"的顾虑在 Docker 路径上不成立（Cloud 路径不适用该 ENV）。
> - 基础镜像层 **未实测**。
> - 🔴 **镜像从未实机构建**，以上全部只是估算/旁证。

**顺带归因了几个大块**（`importlib.metadata` 反向依赖查询）：

| 包 | 体积 | 谁引入的 | 能否裁 |
|---|---|---|---|
| ~~`scipy`~~ | ~~132.4 MiB~~ | **❌ 此行有误，已作废**：那些声明全部带 `extra ==` 标记（doc / dev / default / computation / testing），是 **extras**，pip 默认不装。**Linux 解析集内无 scipy，镜像不会装它**，故不计入体积。 | — |
| `pyarrow` | 86.3 MiB | pandas / streamlit / plotly / altair / pillow… | ❌ 难裁 |
| **`kubernetes`** | **77.5 MiB** | **chromadb 的硬依赖**（metadata 里 `kubernetes>=28.1.0`） | ⚠️ 本项目只用 `chromadb.PersistentClient`（本地），这个 K8s SDK **纯属白占 77.5 MiB**。但它是 chromadb 硬依赖，`--no-deps` 装会破坏 chromadb，**不建议动**，仅作体积归因 |
| `chromadb` | 67.2 MiB | 直接依赖 | — |
| `pandas` | 64.4 MiB | 直接依赖 | — |
| `plotly` | 56.3 MiB | 直接依赖 | — |
| `numpy` | 53.5 MiB | 直接依赖 | — |
| `pymupdf` | 53.3 MiB | 直接依赖 | — |
| **`pydeck`** | **45.3 MiB** | **streamlit**（地图可视化） | ⚠️ 本项目未用到，但同属 streamlit 硬依赖 |
| `onnxruntime` | 44.3 MiB | chromadb | ✅ 必需 |

### 7.11 更新后的总结论

| 路径 | 能否部署 | 说明 |
|---|---|---|
| **Docker / Linux 服务器（方案 C）** | ✅ **可以部署** | F1/F2/F3/H1/H4/H7 + M4 + §7.9 端口文档冲突**全部已闭环**。剩余建议项不阻塞。 |
| **Streamlit Cloud** | ⚠️ **建议先小步验证** | 不能 `apt-get`（graphviz 装不了 → H7 复发）、`storage/` 易失（H4）、多会话并发无法规避（H2）。若评委只走 Docker，Cloud 可忽略。 |

**已闭环清单（本轮累计）**：F1 · F2 · F3 · H1 · H5(依赖已移除，见 §7.8) · H7 · M4 · §7.9 端口文档冲突 · F1 残余风险收窄（§7.7）· **M6-a（`showErrorDetails = false` 已落盘，`.streamlit/config.toml:35` 复验属实）**。

**已决策（不再作为未闭环项挂着）**：
- **M6-b `t_run_python` 公网开放** → team-lead 拍板：**保留功能、不新增开关**（Agent 能力是赛题得分点，砍了等于自废；加开关会在演示时忘开）。风险改由 DEPLOY.md 公网部署章节**显式提示**（鉴权、限来源 IP），不做代码改动。

> **M6 整体已从未闭环清单划掉**（team-lead 2026-xx 裁定）：`showErrorDetails=false` 属实落盘 → 闭环；`t_run_python` → 已决策保留 + 文档提示风险。

**仍未闭环（均不阻塞部署）**：
1. **H2 chroma 并发锁** — 仅 Cloud 多会话场景，建议加 WAL + `busy_timeout`。
2. **H3 / H6 / M1–M3 / M5 / M7–M9** — "不崩但口径/体验不符"级，可择期。

> 说明：§7.8（matplotlib 中文方框）已随 team-lead 拍板的「选 ② 从 requirements.txt 移除 matplotlib」闭环，**不再列入未闭环项**。

**收尾建议**：① 真实构建一次镜像 + 全页面冒烟（**这是唯一还没做过的事**，也是唯一能证伪 0.9–1.2 GB 与全部"已闭环"结论的手段）→ ② 视最终部署形态决定是否处理 H2。

---

### 7.12 复核 deploy-docker 的行号全量校正（19 对 / 4 错的复核）

deploy-docker 在 doc-audit-5-8 提醒后全量核了 compose/Dockerfile 注释里的行号，报「19 处对、4 处错」。我对这 4 处 + 他回赠的 `extern.py` 更正**逐条复验**：

| # | 他的更正 | 我的复验 | 结论 |
|---|---|---|---|
| 1 | `config.py:61-71` 的 `_env_str` 实为 `:81` | `config.py:61` 是 `_st_secret`，`_env_str` 定义在 **`config.py:81`** | ✅ 对 |
| 2 | `config.py:95` 默认 Provider 实为 `services/state.py:81` | `services/state.py:81` = `self.provider_id = _env_str("LLM_PROVIDER", "zhipu")` | ✅ 对（连文件都指错这点他抓得准） |
| 3 | `config.py:103` 的 `HF_ENDPOINT` 实为 `:126` | 实际是 **`config.py:131`**（`os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")`）；`:126` 是 `LLM_TIMEOUT` 的注释行 | ❌ **他的更正本身仍差 5 行** |
| 4 | `config.py:106-112` 实为 `:97`/`:105` | `_env_int` 在 `:97`、`_env_float` 在 `:105` | ✅ 对 |

**#3 的成因（不是他粗心，是文件在漂移）**：`git status` 显示 `config.py` 是**未提交的工作区改动**，且仍被多人持续编辑；上一轮我核到的是 `:131`，他核到 `:126`，两次都对——中间被插入了 5 行。**建议**：compose/Dockerfile 注释里对 `config.py` 这类仍会被改的文件**只写函数名（如 `config._env_str` / `HF_ENDPOINT` 的 setdefault 处），不写行号**，否则每次改动都要重核一遍。

**顺带复验他回赠的 `extern.py` 更正** —— 成立，且比他原判断更稳：
```python
# services/extern.py:16
GITHUB_TOKEN = _env_str("GITHUB_TOKEN", "") or _env_str("GITHUB_API_TOKEN", "")
```
`_env_str` 把空串/空白视为未设置（`config.py:88-94`），所以 `GITHUB_TOKEN=""` **不会**顶掉后面的 `GITHUB_API_TOKEN` 回退链。他 R17 原先写的「裸 `os.getenv`」确实错了，结论方向（空串安全）不变但依据更硬。

**其余同步项复验结论**：
- `requirements.txt` 现为 **13 项，无 matplotlib**（`:12-19` 写了「故意不装」的完整理由，引用的 `services/writing.py:1506-1544` ✅ 精确）。
- `_dot_via_matplotlib()`（`services/writing.py:1452-1455`）在 `import networkx` / `import matplotlib` 任一失败时 `except → return None`；requirements 里**有** networkx（`:11`）、**无** matplotlib → 第 ③ 级恒定返回 `None` → 落到第 ④ 级零依赖 SVG。**「Docker 侧链路实际是两级」这个判断成立。**
- `Dockerfile:70` 注释的四级行号 `1507/1517/1531/1537` 全部落在对应代码块内（① `1506-1513`、② `1516-1529`、③ `1531-1535`、④ `1537-1544`）✅ 无错。
- `docker-compose.yml` 端口保持回环 `127.0.0.1:8501:8501`，且把「有域名 / 无域名·境外 / 无域名·大陆未备案」三分支写进注释 ✅ 与 team-lead 选 A 一致。

🔴 以上全部是**静态读文件**结论。**镜像从未实机构建、容器从未运行、构建机能否访问 AWS S3 未验证。**

---

### 7.13 degrade-fix 改动复核：`_as_exception` 全站归一化（回归面优先）

> 受 team-lead 指派复核 degrade-fix 的改动。复核对象为**当前盘上代码**（degrade-fix 报告尚未转来，若其后续还有改动需重跑）。**验收口径按功能结果**（红框 2→1 且文案为服务层原话），不按实现路径。

**改动本体**：`ui/common.py:195-215` 新增 `_MsgError(RuntimeError)` + `_as_exception()`，把非异常入参（str/None）归一化为异常对象，`str(exc)` 与原文本一致。

**① 双红框根因 —— 实测复现（不是推断）**

改动前 `show_error` 第 321 行对 str 入参取 `.__traceback__`：
```
str 入参 RAISE -> AttributeError: 'str' object has no attribute '__traceback__'
```
该异常在 `st.error` 已渲染第 1 个红框**之后**抛出 → 冒泡到调用方外层 `except` → **再渲染一个一模一样的红框**。7a/7b/8 的「2 个红框 + 真因被吞」机制由此坐实。归一化后 7 类入参全部 `OK`，无一抛错。

**② 回归面（本次第一优先级）：真异常对象是否受影响 —— 结论：不受影响**

`_as_exception` 第 208-209 行 `if isinstance(exc, BaseException): return exc` —— **已是异常的入参原样返回**。实测：

| 入参 | 归一化后 type | 提示文案 | 判定 |
|---|---|---|---|
| `URLError(gaierror(11001))`（S5/S6 形态） | `URLError`（**未被包装**） | 网络连接失败，请检查网络后重试（外网不可达或被代理拦截） | ✅ 无回归 |
| `URLError(gaierror(11004))` | `URLError` | 同上 | ✅ |
| `TimeoutError('timed out')` | `TimeoutError` | 网络请求超时，请检查网络后重试 | ✅ |
| `FileNotFoundError` | `FileNotFoundError` | 所需文件不存在或已被移动 | ✅ |
| `'打开 PDF 失败：文件已损坏'`（7a/7b/8 形态） | `_MsgError` | **打开 PDF 失败：文件已损坏**（原话透出） | ✅ 双红框已消灭 |
| `None` | `_MsgError` | 未提供具体错误原因 | ✅ 不再崩 |

→ **S5 arXiv（`page_search.py:43`）、S6 PubMed（`:123`）传的都是 `except Exception as e` 的真异常对象，`_is_network_exc` 判定链完全未被触碰，网络话术保持原样。** degrade-api 的红框基线 **1** 不受影响。

**③ `log_exception` 可读性 —— 结论：改善，但 type 名变钝**

- 改动前：str 入参在第 223 行同样抛 `AttributeError`，被外层 `except Exception: pass` 吞掉 → **str 类错误此前根本不落盘**。
- 改动后：`type(exc).__name__` 对新包装的入参显示为 `_MsgError`（不如原类名直观），但日志行格式 `%s: %s` 中第二个 `%s` 是 `exc` 本身 → **原始错误文本完整保留，可读**。属"从无到有"的改善。

**④ 成功路径是否被动到 —— 结论：没有**

`_as_exception` 全仓仅 3 处调用（`ui/common.py:222` / `:272` / `:311`），分别位于 `log_exception`、`friendly_hint`、`show_error` 内，**全部在错误处理链上**，无任何成功路径调用。

**⑤ 一处残留不一致（低，非阻塞，记录备查）**

`ui/page_search.py:147` 传的是 **str**：`net_error("网页搜索", err[0]["error"], ...)`。`friendly_hint` 第 274 行对 `_MsgError` 直接短路返回原文，**绕过了 `_is_network_exc`**，于是同一场网络故障会呈现两种文案：
- arXiv / PubMed 路径（真异常）→ "网络连接失败，请检查网络后重试（外网不可达或被代理拦截）"
- 网页搜索路径（str）→ 原始 "urlopen error [Errno 11001] getaddrinfo failed"

不算回归（改动前该路径是「发生内部错误 + 双红框」，现在更优），且 `net_error` 的 `_NET_REASONS/_NET_ACTIONS` 仍会渲染。**若要统一，只需在 `_MsgError` 分支也对文本跑一次 `_is_network_exc`** —— 属增强，不建议放进本轮验收。

🔴 本次复核为**纯函数级实测**（未启动 Streamlit、未走真实 UI 渲染），红框计数以代码路径推演为准，**未做端到端界面计数**。

---

### 7.14 复核 team-lead 修改的测试断言 + 独立复算技术文档页数

> 受 team-lead 指派。**只读复核，未修改任何文件**（`scripts/_smoke_read.py` 归 team-lead，`docs/technical_doc.md` 归 doc-fix）。

#### A. `_smoke_read.py` 场景 C 断言：把墙钟改成记录器 —— **① 结论：断言被改弱，需修**

**team-lead 的推理**：「若有人删掉 sleep，记录器不会被调用，`slept` 为空，断言直接失败」。

**复验结论：该推理不成立。** `_recording_backoff` 是**替换** `_sleep_backoff`，不是包装：

```
scripts/_smoke_read.py:320   _real_sleep_backoff = L._sleep_backoff        ← 仅保存
scripts/_smoke_read.py:332       L._sleep_backoff = _real_sleep_backoff     ← 仅还原
```

`_real_sleep_backoff` **全文件仅此两处、中间从未被调用**；记录器函数体（`:322-324`）也无任何委托。故真实 `_sleep_backoff` 在场景 C 中**从未执行**，`core/llm.py:316 time.sleep(secs)` 自始至终没跑过 → **删掉它，`slept` 仍是 `(1.5, 3.0, 4.5)`，`:335`/`:337` 两条断言全部 PASS**。

| 改动 | 能否抓到 |
|---|---|
| 删 `core/llm.py:490` / `:569` 的 `_sleep_backoff(attempt, info)` 调用点 | ✅ |
| 改 `RETRY_BACKOFF`（`llm.py:42`）/ `_backoff_for`（`:281-291`）/ `MAX_RETRIES` | ✅ |
| **删改 `core/llm.py:316 time.sleep(secs)`** | ❌ **掩盖** |
| 破坏 `retry_after` 分支（`llm.py:310-313`） | ❌ 记录器绕过该分支 |

**② 记录值是否等于真实睡眠值 —— 本场景相等，但断言名不副实**

`_sleep_backoff` 的 `secs` 可能经 `:310-313` 被 `retry_after` 覆盖（下限 0.5、上限 `RATE_LIMIT_RETRY_AFTER_CAP=10.0`，`:47`），记录器直接取 `_backoff_for(attempt)`，绕过该段。但场景 C 的 stub 抛 `RuntimeError`（`_smoke_read.py:306`），而 `_classify_error`（`llm.py:220`）**只在 429/rate_limit 分支**（`:254`）设 `retry_after` → 本场景 `retry_after=None` → `secs == _backoff_for(attempt)`，**不失真**。但断言名「**实际**被请求」应改为「**理论**退避序列」，且**对 429 分支零覆盖**（非本轮引入的退步，原墙钟断言同样只用 RuntimeError）。

**③ monkeypatch 无泄漏 ✅**：`:326` 替换、`:331-332` `finally` 无条件还原，异常路径也会还原。
（次要观察：`:330` 抛异常时 finally 只还原不吞，脚本会以崩溃结束；且 `r` 未赋值导致 `:339-341` 的 `UnboundLocalError`。异常路径不会给出 FAIL 报告。）

**建议改法**：patch `L.time.sleep` 而非替换 `L._sleep_backoff`，让真实函数照跑——既补上 ① 的漏洞（删 `:316` → `slept` 空 → FAIL），又记录经 `retry_after` 加工后的真实 secs。代价是 patch 期间进程内所有 `time.sleep` 受影响（需 finally 还原、不可并发），冒烟脚本单线程可接受。
另：`:337` 的 `abs(sum(slept)-9.0)<1e-6` 被 `:335` 完全蕴含，属冗余断言。

#### B. `docs/technical_doc.md` 页数独立复算

**方法（与 team-lead 不同）**：不用估密度，而是用仓库内既有产物 `docs/技术文档.pdf` 反推。先验证该 PDF 归属：

```
PDF 命中「HEAD 独有片段」 45/220 ；命中「当前独有片段」 0/222   → PDF 是 HEAD 版渲染，标定合法
```

**实测基线**：PDF **29 页**、A4(595.9×842.9pt)、去空白 **53,842** 字符；HEAD md 去空白 **58,288** / 原始 **66,749**；当前 md（我测量时快照）去空白 **61,568** / 原始 **69,718**、970 行。

> ⚠️ 与 team-lead 给出的当前版（971 行 / 61,825 / 70,007）不一致，**HEAD 那组完全一致** → 非方法差异，是 **doc-fix 在两次测量之间又删了 257 个去空白字符**。文件仍在动，以下均为快照值。

| 口径 | 密度（29 页 / HEAD 反推） | 当前推算 |
|---|---|---|
| 去空白 | 2,010 字符/页 | **30.63 页** |
| 原始字符 | 2,302 字符/页 | **30.29 页** |
| 边际法（k=53,842/58,288=0.924，正文页中位密度 2,162/页） | — | **30.40 页** |

> **结论：当前约 30.5 页（30.3–30.6）**，低于 team-lead 给的 30.8（差值来自文件又变小）。

**压到 29.5 页需减**：去空白口径 **≈2,270 个**（推荐，折合原始 ≈2,570）；原始口径 ≈1,820 个；边际法 ≈2,340 个。
两口径不一致的原因：当前版「去空白/原始」= **0.883**，HEAD = **0.873**（当前版文字更密），故换算基数不同。**推荐用去空白口径。**

**未验证**：① 假设 PDF 管线与页面几何不变；② 密度法线性，未考虑代码块/图表占比变化（新增若多为代码块，实际页数会更高）；③ 全部为测量时刻快照。

🔴 页数与断言结论均为**静态测量与代码推演**，**未实机运行冒烟脚本**（按分工由 team-lead 执行）。
