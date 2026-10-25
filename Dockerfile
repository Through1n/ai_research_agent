# ============================================================================
#  AI4S 科研助手 Agent —— Streamlit 应用镜像
#  基线：python:3.11-slim（Debian slim；glibc ≥ 2.28，满足 PyMuPDF/onnxruntime 的
#        manylinux_2_28 wheel 要求。具体 Debian 代号随上游 tag 变化，未锁定）
#
#  构建：docker build -t ai4s-research-agent .
#  运行：docker compose up -d   （推荐，见 docker-compose.yml）
#
#  设计要点（逐条依据见 docs/review/round12_deploy_docker.md）：
#   1. 依赖层与代码层分离，改业务代码不重装依赖（层缓存）
#   2. 全依赖均有 cp311 manylinux wheel，无需 build-essential（已用 pip 解析器核验）
#   3. 构建期预热 ONNX embedding 模型并烤进镜像 → 容器冷启动零外网（否则演示数据加载死锁）
#   4. 非 root 用户 app(uid 10001) 运行，仅运行时目录可写
#      ⚠️ 预热必须在 `USER app` **之后**做，这样缓存文件直接归 app 所有
#      ⚠️ 预热失败 = 构建失败（有意设计），逃生开关见第 5 层注释
#   5. HEALTHCHECK 走 Streamlit 官方 /_stcore/health 端点
#   6. 启动参数显式化（address/静态服务/上传上限等不再依赖 .streamlit/config.toml）
#
#  ⚠️ 构建期需要外网：pip（PyPI）+ graphviz（Debian 源）+ ONNX 模型（AWS S3）
# ============================================================================

FROM python:3.11-slim

# ---------- 运行期环境变量（构建期同样生效）----------
# PYTHONUNBUFFERED          : streamlit 日志实时输出到 `docker logs`，不缓冲
# PIP_NO_CACHE_DIR          : 不落 pip 缓存，镜像更小
# PYTHONDONTWRITEBYTECODE   : 不生成 __pycache__（/app 对运行用户只读，避免写失败噪音）
# HOME                      : 非 root 用户主目录。chroma 默认 ONNX embedding 模型缓存在
#                             $HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2
#                             （chromadb 源码 onnx_mini_lm_l6_v2.py: DOWNLOAD_PATH = Path.home()/...）
#                             构建期就是在这个位置预热的（见第 5 层）
# HF_ENDPOINT               : **故意不在本文件设置**（Round 16 修正，曾误设过）。
#                             config.py 的设计是「仅桌面态（国内）启用 HF 镜像」：
#                               if DESKTOP_MODE: os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
#                             setdefault 的语义是「外部注入优先」——在 ENV 里设它等于用
#                             镜像层**绕过代码里的环境判断**，把云平台容器也拖去走
#                             hf-mirror.com；而 config.py 注释写明「境外连镜像更慢/易超时」，
#                             云平台留空走 huggingface.co 才是有意为之。
#                             Dockerfile.quick 同样不设（见其第 35-39 行注释），两个
#                             Dockerfile 从此口径一致。
#                             chroma 的 ONNX 模型不受该变量影响（走 AWS S3），
#                             真正的解法是第 5 层的构建期预热。
#
# PYTHONUTF8 / LANG        : 显式锁定 UTF-8（Round 13 采纳 crossplat 的建议，见
#                            docs/CROSS_PLATFORM.md §6）。两点说明：
#                            · 本项目所有文件读写都**显式**带 encoding="utf-8"
#                              （state.py:193/198、memory.py:35/40/43、citation_quality.py:153/175、
#                               llm.py:414、app.py:24、common.py:471），所以即使不设也不会乱码；
#                              设它是为了覆盖**没有显式声明编码**的那几处——中文文件名的上传
#                              落盘（library.py:903 的 `(UPLOAD_DIR / filename).write_bytes`）
#                              与 `docker compose logs` 里的中文输出。
#                            · 只给 LANG，**不给 LC_ALL**：Debian slim 不设任何 LC_* 时 LANG 即
#                              为生效 locale，LC_ALL 在这里不提供额外信息，反而会**覆盖**将来
#                              有人在 compose 里单独设的 LC_TIME / LC_COLLATE 之类，故不设。
#                            · PYTHONUTF8=1 是 PEP 540 的 UTF-8 模式，它**不依赖 locale 数据是否
#                              存在**（locale 数据缺失时 PEP 538 的 coercion 会失效，它不会），
#                              所以这两个变量是互补的，不是重复。
#                            ⚠️ 本机无 docker，未实测「设与不设的差异」；代价为零，故采纳。
#
# ⚠️ 业务密钥（ZHIPU_API_KEY / LLM_API_KEY / ...）一律运行期注入，
#    严禁写进本文件或打进镜像（.dockerignore 已排除 .env）
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    HOME=/home/app

WORKDIR /app

# ---------- 第 1 层：依赖（仅 requirements.txt 变化时重建）----------
COPY requirements.txt .
# 核验结论：requirements.txt 的直接依赖 + 全部传递依赖在 cp311 / manylinux x86_64 下
# 均存在二进制 wheel（含 onnxruntime、grpcio、psutil、PyMuPDF、tokenizers、pyarrow），
# 因此不需要 apt 安装 build-essential / gcc / python3-dev，保持 slim 基线体积。
# 若将来某个依赖回落到 sdist，需要在此层临时加：
#   RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev \
#       && pip install -r requirements.txt \
#       && apt-get purge -y --auto-remove gcc python3-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------- 第 2 层：系统级运行时依赖（graphviz）----------
# ⚠️ 这与上一层"Python 依赖能否编译"是两回事：Python 依赖全部有 wheel，不需要 gcc；
#    这里装的是**运行时二进制**，给 services/writing.py 的 Graphviz 渲染用：
#      services/writing.py:1496   dot_exe = shutil.which("dot") or ""
#      services/writing.py:1517-1519   subprocess.run([dot_exe, "-Tsvg"], ...)
#    没有 dot → 写作页的 Graphviz 图跳过真实渲染，只走"零依赖"兜底。
#
# 【为什么故意不装 matplotlib —— 别再把它加回 requirements.txt】
#   render_dot（services/writing.py:1480）的降级顺序：
#     ① Python graphviz 包(:1507) → ② dot -Tsvg 子进程(:1517) → ③ matplotlib(:1531) → ④ 零依赖 SVG(:1537)
#   matplotlib **已被有意从 requirements.txt 移除**，所以第 ③ 级恒不可用，链路实际是两级：
#     容器（有 dot）走 ② 真实 Graphviz；云平台（无 dot）走 ④ 零依赖 SVG（浏览器渲染，中文正常）。
#   装回 matplotlib 的后果：云平台会在第 ③ 级返回**服务端渲染的 PNG**，matplotlib 默认字体
#   DejaVu Sans 无 CJK 字形且不自动 fallback，而本项目 DOT 标签由中文 prompt 生成 → 中文变方框 □。
#   也就是说**装了更差**。同理也不需要装 fonts-wqy-microhei：光装字体不改代码仍然无效
#   （代码里没有任何 rcParams / font.sans-serif 配置）。
#   ~30MB，性价比高。装完清 apt 列表。
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

# ---------- 第 3 层：业务代码 ----------
COPY . .

# ---------- 第 4 层：非 root 用户 + 可写目录 + storage 零崩溃种子 ----------
# config.py 启动时会 mkdir storage/uploads、storage/chroma、pdf（_mkdirs 那段）；
# app.py:15 的 LOG_DIR="logs" 是相对 CWD（/app）的，属主必须是运行用户，否则异常日志会被静默吞掉。
# 这一层先于第 5 层的模型预热执行 —— chown /home/app 之后，app 用户才有地方写模型缓存。
#
# 【为什么要在镜像里预置 storage/ 的内容（Round 13 重点，逐条核过代码才写的）】
#   storage/ 同时被 .gitignore 与 .dockerignore 排除 → 全新克隆的宿主机上**不存在**这个目录。
#
#   ① 「目录/文件缺失」本身**不会**让代码崩（逐行核过，见 docs/review/round13_deploy_finish.md §2）：
#        services/state.py:193  json.loads 包在 try/except 里 → 缺失/空文件/损坏 均回落默认字典
#        core/memory.py:33-37   同上（`_load()` 的 try/except）
#        citation_quality.py:152 先 isfile 再 isinstance(data, dict) → 缺失/损坏都安全
#      → 所以真正的崩溃源**不是空，而是不可写**：/app/storage 对 uid 10001 不可写时，
#        config.py:42 的 `_d.mkdir(parents=True, exist_ok=True)` 抛 PermissionError，
#        发生在 import 期 → 被 app.py:34 捕获后显示成「依赖未安装或版本不匹配」
#        （**误导性文案**，真因是权限，app.py:44/45 那两行 caption 也是照着依赖问题写的）。
#
#   ② 「类型对不上的**合法** JSON」才是启动期崩溃源（Round 13 早期实测，**现已被修复**）。
#      按「修复前形态 → 修复后守卫」对照写，**修复前一律不挂行号**——
#      那些行已经不存在，挂任何行号都是伪证。
#
#        [修复前] library.json 写成 `[]` → `lib.get("papers", {})` 抛 AttributeError，
#                 且该处不在 try 内 → Container 构造失败 → 应用起不来
#        [修复后] `_ensure_lib_dict()` 先把形状规整成 dict，再取字段：
#                 services/state.py:177  `self.papers = lib.get("papers", {})`
#                 （规整发生在 services/state.py:193 的 `_ensure_lib_dict(json.loads(...))`）
#
#        [修复前] memory.json 写成 `{}` → `self.data["kv"][key]` 抛 KeyError
#        [修复后] `_normalize()` 兜住形状：core/memory.py:29-30
#                 `if not isinstance(self.profiles, dict): self.profiles = {}`
#
#        [修复前] user_profile.json 写成 `[]` → `profiles.setdefault` 抛 AttributeError
#        [修复后] 同一处 `_normalize()` 兜底，调用点在 core/memory.py:71
#
#      ⚠️ 更正（Round 13 行号对账时发现）：这里原写「services/track.py:22 与
#         query_expansion.py:305 的 remember **均无兜底**」——**该断言不成立**。
#         track.py:22 确实无兜底；但 query_expansion.py 的 remember 在 392-396
#         已包 try/except（`except Exception: pass`），305 那行是无关的 import。
#         引用与「均」这个量词一并更正。
#
#      → 即便如此，下面的种子仍**按代码实际读取方式写死具体类型**：种子是给
#        copy-up 一个已知形状的起点，不是靠守卫去兜不该出现的东西。
#      ⚠️ 反过来也成立：library.json 必须是 dict 而不是 list，memory.json 必须是
#         带 "notes"/"kv" 两个键的 dict —— 改这里之前先看上面三行引用的读取点。
#
#   ③ 预置成「非空目录」还有第二个用途：docker-compose 用**命名卷**挂 /app/storage 时，
#      Docker 会把镜像内该目录的内容（含属主）复制进新建卷，容器首次启动即可写。
#      目录为空时无内容可依，属主是否跟随镜像**未实测**（本机无 docker）。
#      为此把 uploads/ chroma/ cache/ pdf/ 一并预建并 chown：即使最坏情况下卷根目录
#      属主回落到 root，代码真正要写的子目录仍是 app 的，依然不会崩。
#      首次 up 后的校验命令见 docs/部署后自检清单.md §1。
RUN useradd --create-home --home-dir /home/app --uid 10001 app \
    && mkdir -p /app/storage /app/storage/uploads /app/storage/chroma /app/storage/cache \
                /app/storage/pdf /app/logs /app/pdf /home/app/.cache \
    && printf '%s' '{"papers": {}, "paper_order": [], "chunks": [], "likes": {}}' > /app/storage/library.json \
    && printf '%s' '{"notes": [], "kv": {}}' > /app/storage/memory.json \
    && printf '%s' '{}' > /app/storage/user_profile.json \
    && printf '%s' '{}' > /app/storage/cache/citation_cache.json \
    && : > /app/logs/.keep \
    && chown -R app:app /app/storage /app/logs /app/pdf /home/app

USER app

# ---------- 第 5 层：构建期预热 ONNX embedding 模型（关键：让容器冷启动零外网）----------
# ⚠️ 必须在 `USER app` **之后**执行：这样缓存目录与文件直接归 app 所有，
#    运行时不会因为属主是 root 而读不到模型（读不到就会再去联网下载，预热白做）。
#    上一层的 `chown -R app:app /home/app` 正是为了让这一步有地方可写。
#
# 【为什么必须做】services/state.py:55-59 首次 embed 会从 AWS S3 下载 all-MiniLM-L6-v2：
#     chromadb ONNXMiniLM_L6_V2.MODEL_DOWNLOAD_URL
#       = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
#     DOWNLOAD_PATH = Path.home()/".cache"/"chroma"/"onnx_models"/"all-MiniLM-L6-v2"
#   · 它**不走 HuggingFace**，所以镜像里的 HF_ENDPOINT 对它完全无效
#   · 下载失败 → 回退 chroma_ef.DefaultEmbeddingFunction()，而该函数内部**依然是**
#     ONNXMiniLM_L6_V2，照样抛异常 → 「🚀 一键加载演示数据」三篇全"⚠️ 失败"、
#     文献库仍为空，页面只给"再点一次"= 死锁
#   → 这里在构建期（有网）把模型烤进镜像，运行期不再联网。
#
# 【三个必须遵守的细节】
#   1. 位置已在 USER app 之后（见本层开头），不要再挪到 USER 之前
#   2. 显式 HOME=/home/app：DOWNLOAD_PATH 在 import 时求值 Path.home()，
#      而 Path.home() 读 HOME 环境变量。顶层 ENV 已设为 /home/app，
#      这里再显式传一次，避免依赖 ENV 声明顺序（写成 root 的 /root 就全错了）
#   3. 末尾校验 6 个必需文件（chromadb 的 _download_model_if_not_exists 只认这 6 个），
#      缺失即 assert 失败 → **构建直接失败**
#
# 【构建失败策略：就是要让它失败】宁可构建期炸掉让使用者立刻知道，
# 也不要产出一个"能起来但 AI 功能全废"的镜像 —— 后者要到评审现场才暴露。
#
# 【构建机连不上 S3 时的逃生开关】
#   方案 A（推荐）：注释掉本行 RUN，改为从已备好的宿主机缓存 COPY：
#     COPY --chown=app:app ./onnx_models /home/app/.cache/chroma/onnx_models
#     （宿主机先跑一次 python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 \
#        import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2()(['warmup'])" 生成 ~/.cache/chroma/onnx_models）
#   方案 B：直接注释掉本行继续构建，代价是首次运行仍需联网下载
#          （国内服务器访问 AWS S3 可能很慢或直接失败，不建议）
RUN HOME=/home/app python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as M; import os; M()(['warmup']); d = os.path.join(str(M.DOWNLOAD_PATH), 'onnx'); need = ['config.json', 'model.onnx', 'special_tokens_map.json', 'tokenizer_config.json', 'tokenizer.json', 'vocab.txt']; miss = [f for f in need if not os.path.exists(os.path.join(d, f))]; assert not miss, 'ONNX warmup failed, missing files: %s' % miss; print('[build] ONNX embedding warmed at', d)"

EXPOSE 8501

# ---------- 健康检查 ----------
# Streamlit 1.63 官方健康端点：/_stcore/health
#   来源：streamlit/web/server/starlette/starlette_routes.py:153 BASE_ROUTE_CORE="_stcore"
#        streamlit/web/server/starlette/starlette_routes.py:161 ROUTE_HEALTH=f"{BASE_ROUTE_CORE}/health"
#        ⚠️ 这两条是**第三方包内部路径**，随 streamlit 版本变动，仅作定位提示，
#           不作为验收依据；验收看的是 curl /_stcore/health 返回 200。
#   语义：runtime 就绪返回 200，未就绪返回 503（urlopen 抛 HTTPError → 非零退出）
# start-period 给首次启动留出宽限：初始化 chroma 持久化库 + 载入已烤进镜像的 ONNX 模型
# （模型已在第 5 层预热，运行期不再联网下载，故 120s 是相当宽裕的余量）
# slim 镜像不带 curl，用 python 标准库探测，零额外依赖
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4); sys.exit(0 if r.status == 200 else 1)"

# ---------- 启动 ----------
# 命令行参数优先级高于 .streamlit/config.toml，以下显式覆盖桌面态配置：
#   --server.address=0.0.0.0          必须显式给：config.toml 的 address 已被注释（留给云平台决定），
#                                     不给则容器只监听回环，容器外访问不到
#   --server.headless=true            不开浏览器、不弹 "Welcome to Streamlit"
#   --server.enableCORS=false         允许跨源访问（内网反代 / 非同源前端场景）
#   --server.enableStaticServing=true 必须开：ui/page_writing.py:688 从 /app/static/mermaid.min.js
#                                     本地加载 mermaid；该项默认 false，不开会退化到 CDN（离线即失效）
#   --server.maxUploadSize=100        单位 MB。⚠️ **第八轮更正：此前的 200 是错的，且理由写反了**
#                                     旧注释写"代码无自定义上限（streamlit 默认即 200）"——**这是事实错误**：
#                                     config.py 有 `MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 100)`，
#                                     ui/page_library.py 在解析**之前**做 `_mb > MAX_UPLOAD_MB` 判断并 `st.stop()`，
#                                     即应用层硬上限就是 **100 MB**，超限必然被拒。
#                                     为什么必须改成 100 而不是留 200：超限文件在 `data = f.read()` 处
#                                     **整份读进内存**后才走到那个判断——200 会让一个 150MB 的 PDF
#                                     先上传完、再占 150MB 内存、然后才被拒。设成 100 可在**上传层**就拦掉，
#                                     省带宽也省内存（compose 已限 4GB）。
#                                     ⚠️ 若通过 `MAX_UPLOAD_MB` 调高应用层上限，**必须同步调大此值**，
#                                        否则 streamlit 会先拦住、应用层根本看不到文件。
#                                     ⚠️ 上传体积共**三道闸门**（Nginx client_max_body_size →
#                                        本 flag → 应用层 MAX_UPLOAD_MB），现已全部对齐为 100；
#                                        三处量的是**请求体**而非文件本身，multipart 编码有开销，
#                                        所以"恰好 100MB"的文件会在外层被拦 —— 实际可上传略小于 100MB。
#                                        （Nginx 那道在 DEPLOY.md §C-4，不在本文件。）
#   --browser.gatherUsageStats=false  关闭遥测
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableStaticServing=true", \
     "--server.maxUploadSize=100", \
     "--browser.gatherUsageStats=false"]
