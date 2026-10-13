# Round 12 · 部署交付（deploy-docker）

> 范围：只改了 `Dockerfile`、`docker-compose.yml`、`.dockerignore` 三个文件。
> 未构建镜像、未启动容器、未启动 Streamlit、未提交 git（按纪律要求）。
> 本文中所有"已核验"均为**静态核验**（读源码 / 查 PyPI / YAML 解析）；凡涉及"能否真的构建成功/跑起来"的结论一律标注 **未验证，需实机确认**。

---

## 0. 一句话结论

原三件套可用但有 **6 个**会在容器里真实出问题的坑：

1. `.streamlit/` 被误忽略（mermaid 离线渲染 + 主题丢失）
2. `app.py` 的 `logs` 目录无属主（异常日志被静默吞）
3. 全程 root 运行
4. 无健康检查
5. **容器冷启动要联网从 AWS S3 下 ONNX 模型，下不到就「一键加载演示数据」死锁**（部署审计发现，见 §5.1。体积口径**实测**：下载压缩包 83,178,821 B = **79.31 MiB**；解压后目录 91,332,068 B = **87.10 MiB**（其中 `model.onnx` 单文件 90,387,606 B = 86.20 MiB）；**缓存目录合计 174,510,889 B = 166.42 MiB**（Dockerfile 未删 `onnx.tar.gz`，故镜像承担的是这个 166.42 MiB）。早期口头说的"90MB / 167MB"是估算值，以这三个实测值为准）
6. **`docker compose up` 在全新克隆上直接失败**（`.env` 不在仓库里，见 §5.2）

已全部修掉，另外补了 graphviz 运行时依赖、启动参数、持久化卷、日志轮转、资源上限。

> 第二轮补充：部署审计（deploy-audit）与 team-lead 转来的 3 条致命项，处理过程集中在 **§5**。
> 第三轮补充：按 team-lead 复核意见把 ONNX 预热**挪到 `USER app` 之后**（原在之前 + 事后 chown），
> 并核实了「命名卷是否会遮蔽烤进去的模型」。
> ⚠️ **这一条我前期说错过、已被 deploy-audit 纠正**：空命名卷首次挂载时会**复制**镜像目录内容（含属主），
> 不会遮蔽。所以"会遮蔽"这句是**错的**。我仍然删掉了那个卷，但理由换成了「零收益 + 两个风险」，
> 完整语义表见 **§5.1 末尾**。同理，`app_logs` 命名卷能拿到 `app:app` 属主**正是依赖这个复制行为**——
> 属主未实测，验收时必须跑一次 `ls -ld /app/logs`，见 §7。
> 「哪些验证过 / 哪些没验证」见 **§6**。
>
> 🔴 **镜像至今从未实机构建、容器从未运行** —— 所有结论均为静态核验（读源码 / 查 PyPI / YAML 解析 /
> 本机缓存实测 / pip 依赖解析）。凡涉及"能否真的构建成功、能否跑起来"的，一律见 §6 的 R1/R2/R14。
> **这条警示等级不随改动轮次下调。**

---

## 1. `Dockerfile` 改动

### 1.1 依赖能否在 `python:3.11-slim` 上装成功 —— 结论：可以，**不需要 build-essential**

**核验方法**（静态、未实机构建）：

```
pip install --dry-run --only-binary=:all: \
  --platform manylinux_{2_28,2_17}/manylinux{2014,2010,1}_x86_64 --platform any \
  --python-version 3.11 --target <tmp> -r requirements.txt
```

`--only-binary=:all:` 表示"只要有一个包没有可用二进制 wheel 就失败"。结果 **解析成功**，输出 `Would install ...` 共 130 个包，其中：

| 关注包 | 解析出的版本 | 结论 |
|---|---|---|
| `onnxruntime`（chroma 依赖） | 1.31.0 | 有 cp311 manylinux_2_28_x86_64 wheel |
| `grpcio`（chroma 依赖） | 1.84.0 | 有 wheel |
| `psutil`（最可能踩坑的纯 C 扩展） | 7.2.2 | **有** manylinux wheel（新版已发二进制包，不再是 sdist-only） |
| `PyMuPDF` | 1.28.2 | cp310-abi3 manylinux_2_28 wheel，**不需要编译工具** |
| `tokenizers` / `pyarrow` / `pillow` / `pandas` / `numpy` | — | 均有 wheel |
| ~~`matplotlib`（第四轮新增、第五轮移除）~~ | ~~3.11.2~~ | ⛔ **已不在依赖里**（team-lead 第五轮裁定移除）。第四轮曾核验它有 wheel，该核验**依然成立但已无意义**——包不装了。移除后当前 requirements **109 个包全部有二进制 wheel** |

> 🧪 **matplotlib 专项核验（team-lead 第四轮要求"实测而不是想当然"）**：
> 我用**完全相同的那条命令**、针对**加入 matplotlib 后的新 `requirements.txt`** 重跑了一次
> （不是沿用旧结论）。结果：解析成功、`--only-binary=:all:` 未报错 → 确认 matplotlib 及其传递依赖
> 在 cp311 / manylinux x86_64 下**都有二进制 wheel**，镜像里依然**不需要 build-essential**。
> 新增包清单：`matplotlib-3.11.2`、`contourpy-1.3.3`、`cycler-0.12.1`、`fonttools-4.66.1`、
> `kiwisolver-1.5.1`、`pyparsing-3.3.3`。

> 🔄 **第三轮重跑**：`requirements.txt` 被其他成员改了（加了上界 `<2.0`/`<4.0`/`<3.0` 等 + 新增 `matplotlib>=3.8`），
> 我用**新文件重跑了同一条核验命令**，仍然解析成功、全部二进制（新增 matplotlib 3.11.2 及其 4 个传递依赖都有 wheel）。
> 所以 §1.1 的"不需要 build-essential"结论**对新 requirements 依然成立**。

> 🔄 **第五轮重跑（当前口径，team-lead 已裁定移除 matplotlib）**：`matplotlib>=3.8` 已从 `requirements.txt` 删除
> （文件里留了 8 行注释说明"故意不装"及原因）。我用**删除后的最终版**第三次重跑同一条命令：
> **解析成功，109 个包，全部二进制 wheel**，`--only-binary=:all:` 未报任何错。
> 命令：`pip install --dry-run --only-binary=:all: --platform manylinux_{2_17,2014,2_28,2_5,1}_x86_64 --python-version 3.11 -r requirements.txt`
> **结论：移除 matplotlib 不改变"不需要 build-essential"这一结论**（少装一个包只会更宽松）。

**因此镜像里没有加任何 `apt-get install`。**`python:3.11-slim` 的 Debian slim 基线 glibc ≥ 2.28（bullseye 2.31 / bookworm 2.36 均满足），能装 PyMuPDF / onnxruntime 的 manylinux_2_28 wheel。
（具体 Debian 代号随上游 tag 变化，**未锁定、未核实当前 tag 实际代号**。）

> 反面保障：Dockerfile 第 57-60 行以注释形式保留了"若将来某依赖回落 sdist"的 build-essential + 装完 apt 清理写法，供后续按需启用。

⚠️ **未验证**：这是 pip *解析器*层面的结论，不等于实机 `docker build` 一定成功（还可能受网络、PyPI 镜像、平台架构影响）。需用户实机确认。

⚠️ **体积估算（第五轮：口径换成自下而上实测，见 §1.2b）**：传递依赖里带了 `pyarrow`(44.7 MiB)、`pymupdf`(23.9)、`chromadb`(22.2)、`onnxruntime`(20.4)、`numpy`(15.2)、`pydeck`(10.9)、`pandas`(10.1)、`streamlit`(10.0)、`plotly`(9.2)、`kubernetes`(7.2)、`grpcio`(6.9)、`pillow`(6.0)、`lxml`(4.8)、`hf-xet`(4.0)、`tokenizers`(3.2) 等重型包（括号内**均为实测 wheel 压缩体积**）。
**最终镜像估算 ≈ 1.1–1.3 GB（未实测；第六轮把依赖层从估算换成实测后上调）**，分项见 **§1.2b**。
> ⚠️ **口径变更说明（口径变过两次，都写在这里）**：
> ① 前几轮的「1.8–2.8 / 1.9–2.9 GB」是**拍脑袋的粗估，没有依据** —— 作废。
> ② 第五轮我改成自下而上重算，但依赖层用的是**猜的**解压系数 2.0–3.0× → 得 0.9–1.2 GB —— 偏低。
> ③ **第六轮（当前）**：deploy-audit 实测了膨胀系数，我在他基础上做了口径校正（剔除 Linux 解析集里不存在的 `scipy`），
>    依赖层改为**实测 747.4 MiB / 系数 3.40×** → 得 **1.1–1.3 GB**。
> 三个数**不是**"减掉 matplotlib"的关系，是**换了三次算法**。以 ③ 为准。

### 1.2 层缓存

- 保留原 `COPY requirements.txt .` → `pip install` → `COPY . .` 的顺序：**改业务代码不会重装依赖**，这是正确的分层。
- 新增的 `useradd` + `mkdir/chown` 放在 `COPY . .` **之后**，因为它要 chown 镜像内目录；顺序反了会被 COPY 覆盖属主。
- ⚠️ 已知缺陷：`COPY . .` 会把 `pdf/`（31MB）和 `demo_data/` 打进镜像层，且每次业务代码变更都会重拷。属可接受代价（见 `.dockerignore` §3.2）。

### 1.2b 镜像体积：自下而上估算（第五轮新增，用于替代前几轮的粗估）

> 🔄 **第六轮再修正 —— 依赖层从"估算"变成"实测"，我把自己上一版的系数推翻了**。
> 上一版我写依赖层 = wheel 220.1 MiB × **2.0–3.0×**（我标注为"经验值、全表最大不确定项"）。
> deploy-audit 挑了这一项并实测；我在他的基础上**又做了一次口径校正**（见下方"为什么不是 916.9"），
> 最终采用 **747.4 MiB 实测值**。下面这张表是**当前口径**。

| 分项 | 体积 | 依据 / 可信度 |
|---|---|---|
| `python:3.11-slim` 基础层 | ~130–150 MB | Docker Hub 官方标注值，**未实测** |
| **pip 依赖（109 个包，Linux 解析集）** | **747.4 MiB** | **实测**（按 Linux 解析出的 109 个包名，逐个用 `importlib.metadata` 的 `files` 累加真实落盘字节；107/109 命中）。<br>**缺口的 2 个包已量化**：`aiohttp-retry` + `opentelemetry-exporter-otlp-common` 的 wheel 合计 **22,366 B = 0.02 MiB**，按实测系数 3.40× 折算安装体积 **≈ 0.1 MiB，占 747.4 的 0.01%** —— 可忽略。<br>**不含 `.pyc`** |
| `.pyc` 增量 | **= 0（Docker 路径）** | ✅ **已坐实**：`Dockerfile:47` 的 `ENV PYTHONDONTWRITEBYTECODE=1` 让镜像构建期就不生成 `.pyc`。deploy-audit 复验确认，我原写的"可能 +10~20%"可直接作废。<br>（**Cloud 路径不适用**该 ENV，那边仍可能有 `.pyc`） |
| `graphviz` + apt 依赖 | ~30 MB | 估算，**未实测** |
| ONNX 模型（**含**未删的 `onnx.tar.gz`） | **166.42 MiB** = 174,510,889 B | **实测**（本机缓存目录 `du`）。构成：压缩包 83,178,821 B (79.31 MiB) + 解压目录 91,332,068 B (87.10 MiB，其中 `model.onnx` 单文件 90,387,606 B = 86.20 MiB) |
| `pdf/` 31 MB + `static/` 3.2 MB + 其余仓库代码 | ~35 MB | **实测** |
| **合计（精确加总）** | **1.16–1.18 GB** | 747.4 + 130~150 + 30 + 166.42 + 35 = 1108.4~1128.4 MiB。**这是唯一可辩护的"算出来的"值**。<br>⚠️ 不存在 1.20–1.22 那一档——那是用已作废的 784.5 撑出来的。 |

**实测膨胀系数 = 747.4 / 220.1 = 3.40×**（不是我上一版猜的 2.0–3.0×）。

> ✅ **【二裁已落定 · 本项为最终口径】team-lead 裁定：≈ 1.2 GB（区间 1.1–1.3 GB），0.9–1.2 GB 作废。**
> 裁定理由（他复核后给出的）：`747.4 + 130 + 30 + 166.42 + 35 = 1,108.8 MB ≈ 1.08 GB` 是**算术下限**，
> 配合 `.pyc = 0` 坐实，0.9 GB 比下限还低 208 MB，不可能成立；他原先采 0.9–1.2 是因为**把 747.4 与
> deploy-audit 的 916.9（conda 环境 144 包）混为一谈**——那是两个不同的测量对象。
>
> **对外写法（他对 deploy-doc 的要求，引用时照此措辞）**：写两个数，别只写一个 ——
> ① **可辩护区间 1.16–1.18 GB**（被追问时能拿出每一步算式）；
> ② **对外中心值 ≈ 1.2 GB**（向上取整，留平台二进制差异的余量）。
> ⚠️ **绝不能把 1.2 写成"算出来的"** —— 准确说法是"算出来 1.16–1.18，考虑未实测的平台二进制差异，对外说约 1.2"。

**为什么不是 deploy-audit 的 916.9 MiB（他的系数 4.17×）**：
他的方法（遍历 13 个直接依赖的传递闭包）会把**本机 dev 环境里额外装了的包**算进去，其中最关键的是 **`scipy` 132.4 MiB** ——
但我核对过 pip 对 **cp311 / manylinux** 的解析结果：**109 个包里根本没有 scipy**（`scipy` 不在 `Would install` 列表里，
它只是本地 anaconda 环境的 dev 依赖）。
**所以采用 747.4 MiB，系数 3.40×。**

> ⛔ **注意：不要写成"916.9 − 132.4 = 784.5，与 747.4 同一量级"** —— 我最初就是这么写的，**这是错的**。
> 916.9 的闭包是**含 extras** 的，scipy 只是其中最大的一块；剔掉它之后里面**还留着别的 extras 带入的包**。
> 所以 784.5 **既不是"剔干净的上界"，也不是任何一个可定义对象的测量值**——它不是"偏大一点的 747.4"，
> 而是**测的根本不是同一个集合**。deploy-audit 本人已把 916.9 与 784.5 **双双作废**，我同步作废。

> ✅ **双人独立测量、字节级互证**：deploy-audit 拿到我这份 109 包名单后独立复现，结果 `783,734,647 B` —— **与我一个字节都不差**；未安装的两个包也一致（`aiohttp-retry` / `opentelemetry-exporter-otlp-common`）。
>
> ✅ **scipy 的机制证据（他给的，比我的更硬）**：他扫全机已安装包的 `requires`，找**不带 `extra ==` 的 scipy 声明** —— **零条**。altair(`doc`) / plotly(`dev`) / networkx(`default`) / pandas(`computation`) / jsonpickle(`testing`) 的 scipy 声明**全部带 `extra ==`**，而 chromadb 与 streamlit **根本没有任何 scipy 声明** → scipy 只能经 extras 进入 → pip 默认不装 → 解析集不含它。**不依赖 pip dry-run 单一信源。**
>
> ✅ **109 名单在 extras 维度上的完备性（他验证，我复核）**：他扫了 109 名单**内部所有包**的 `requires`，找含 `[` 的声明共 **15 条** —— 其中 **14 条自身就带 `extra ==`**（altair→`pytest-xdist[psutil]` / fsspec→`dask[...]`、`moto[server]` / huggingface_hub→`safetensors[torch]` / narwhals→`pyspark[connect]` 等）→ 不装；**唯一无条件的一条是 `chromadb → uvicorn[standard]`**，其产物 `httptools` 与 `watchfiles` **都在 109 名单里**。→ **名单不漏项。**
>
> ✅ **最后一个"未核点"已关闭**：他标了"本机未装、查不到 `requires`"的两个包（`aiohttp-retry` / `opentelemetry-exporter-otlp-common`），我改从 **PyPI JSON API 取 `requires_dist`**（不需安装）：<br>· `aiohttp-retry==2.9.1` → 唯一无条件依赖 `aiohttp` ✅ 已在名单<br>· `opentelemetry-exporter-otlp-common==0.66b1` → 唯一无条件依赖 `opentelemetry-sdk` ✅ 已在名单；另一条 `opentelemetry-exporter-http-transport; extra == "http"` **带 extra → 不装**<br>→ **这两个包不会引入名单外的包，也不会激活任何 extras。** 它们的体积缺口也已量化（见上表，0.01%）。
>
> ⛔ **他已撤回两个数**：916.9 与 784.5 全部作废（784.5 "剔掉 scipy" 剔得不干净，闭包里还有别的 extras 带入的包）。我此前写的"即便按他偏高的 784.5 结论也一样"这句话**随之失效**——现在只剩 747.4 这一个来源。

**三个数要分开写，别混用**（deploy-audit 强调，我采纳）：

| 层次 | 值 | 性质 |
|---|---|---|
| **可辩护的分项加总** | **1.16–1.18 GB** | 双人互证实测值的加总。被人追问时**只有这一层能拿出证据** |
| **对外中心值** | **≈ 1.2 GB** | 在 1.16–1.18 基础上**向上取整**，为"Windows `.dll`/`.pyd` vs Linux `.so`"这个**未实测差异**留余量。⚠️ **不能说成"算出来的 1.2"** —— 它是"算出来 1.16–1.18，考虑未实测差异后对外说约 1.2" |
| **取整包络** | **1.1–1.3 GB** | 覆盖全部未实测项的宽区间 |

**仍然存在的三点不确定性**：
1. 747.4 MiB 是在**本机 Windows 环境**测的（`.dll`/`.pyd`），Linux 容器里是 `.so`。量级可信，**精确值未实测**。
2. ~~`.pyc` 增量 10–20% 是经验值~~ —— **已坐实为 0**（Docker 路径，见上表），不再是不确定项。
3. `docker images` 显示的是**压缩后**的层大小，通常小于文件系统展开体积；我没有实测两者差多少。

**大块归因**（实测落盘 MiB，供将来回答"镜像为什么这么大"）：
`pyarrow` 86.3 / `kubernetes` 77.5 / `chromadb` 67.2 / `pandas` 64.4 / `plotly` 56.3 / `numpy` 53.5 / `pymupdf` 53.3 / `pydeck` 45.3 / `onnxruntime` 44.3 / `streamlit` 31.8 / `networkx` 15.8 / `pillow` 15.5 / `openai` 13.9 / `grpcio` 12.6

**两处"白占体积"（deploy-audit 归因，我确认）——但都不建议动手裁**：
- `kubernetes` 77.5 MiB：我们只用 `chromadb.PersistentClient`（本地），K8s SDK 完全用不到；但它是 chromadb 的**硬依赖**， `--no-deps` 装会破坏 chromadb。
- `pydeck` 45.3 MiB：streamlit 的地图可视化，本项目没用到，同属硬依赖。

**瘦身空间**（都不在我的授权文件里，仅列出来）：
- 删 `onnx.tar.gz` 省 79.31 MiB（风险见 §5.1：chromadb 只在模型不存在时才下，删了通常没事，但我没验证它在所有路径下都不重新校验压缩包）
- `pdf/` 31 MB 改成运行期挂载而非打进镜像（要改 `.dockerignore` 策略 + 代码里的 `PDF_DIR`）
- `kubernetes` / `pydeck` 合计 123 MiB 理论可裁，但**风险高于收益，我不建议动**


### 1.3 非 root 用户（新增）

```
RUN useradd --create-home --home-dir /home/app --uid 10001 app \
    && mkdir -p /app/storage /app/logs /app/pdf /home/app/.cache \
    && chown -R app:app /app/storage /app/logs /app/pdf /home/app
USER app
```

**为什么是这三个目录**（逐个按代码定位）：

| 目录 | 依据 | 不 chown 会怎样 |
|---|---|---|
| `/app/storage` | `config.py:30-32` `STORAGE_DIR/UPLOAD_DIR/CHROMA_DIR`；上传落盘 `services/library.py:777` `(UPLOAD_DIR / filename).write_bytes(...)`；`library.json/memory.json/user_profile.json` 全在 `config.py:90-92` | 应用报「存储目录不可写 / 已损坏，应用无法启动」（`app.py:89`），**直接起不来** |
| `/app/logs` | `app.py:15` `LOG_DIR = "logs"`，相对 CWD（容器里 `/app`） | `app.py:22` 的 `makedirs` / `open(...,"a")` 抛异常被 `except: pass` 吞掉 → **异常日志全丢**，排障时看不到任何 traceback |
| `/home/app/.cache` | chromadb 源码 `chromadb/utils/embedding_functions/onnx_mini_lm_l6_v2.py`：`DOWNLOAD_PATH = Path.home() / ".cache" / "chroma" / "onnx_models" / MODEL_NAME` | HOME 不可写 → ONNX 模型下载失败 → embedding 降级/失败，检索不可用 |

`chown` 放在 `COPY . .` 之后、`USER app` 之前，保证属主是运行用户；代码本身保持 root 只读。

### 1.4 `HEALTHCHECK`（新增）—— `/_stcore/health` **已核实**

在**本机装的 streamlit 1.63.0** 里直接查到源码，不是猜的：

- `streamlit/web/server/starlette/starlette_routes.py:153` → `BASE_ROUTE_CORE: Final = "_stcore"`
- `streamlit/web/server/starlette/starlette_routes.py:161` → `ROUTE_HEALTH: Final = f"{BASE_ROUTE_CORE}/health"`
- `starlette_routes.py:429` docstring：「Create health check route handlers for **/_stcore/health**」；`_health_endpoint` 内 `status = 200 if ok else 503`

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4); sys.exit(0 if r.status == 200 else 1)"
```

- 用 `python` 而非 `curl`：`python:3.11-slim` **不带 curl**，用标准库零额外依赖、不增体积。
- 503 会让 `urlopen` 抛 `HTTPError` → 进程非零退出 → unhealthy，语义正确。
- `start_period=120s`：首次启动要建 chroma 库 + 可能下载 ONNX 模型，宽限期内失败不计入 retries。

### 1.5 启动参数补全

| 参数 | 依据 / 为什么 |
|---|---|
| `--server.address=0.0.0.0` | **必须**：`.streamlit/config.toml:20` 的 `address` 已被配置作者注释掉（留给云平台决定），不给则容器只监听回环、容器外访问不到。命令行优先级 > config.toml |
| `--server.headless=true` | 容器无浏览器，且避免启动期 "Welcome to Streamlit" 交互 |
| `--server.enableCORS=false` | 内网反代 / 非同源访问场景需要 |
| `--browser.gatherUsageStats=false` | 关闭遥测（也是 config.toml 的原意） |
| `--server.enableStaticServing=true` | ⚠️ **必须显式加**：该项 streamlit 默认 `False`（`streamlit/config.py:1235` 附近 `default_val=False`）。`ui/page_writing.py:495` 用 `s.src="/app/static/mermaid.min.js"` 本地加载 mermaid，不开会退化到 CDN（`page_writing.py:498`），**断网即出不了图** |
| `--server.maxUploadSize=100` | 单位 MB。🔴 **第八轮推翻我自己上一版的判断（原为 200，且理由写反了）**：<br>我原写「代码里没有任何自定义上传上限（全仓 grep 无 `maxUploadSize`）」——**这是事实错误**。grep `maxUploadSize` 当然搜不到，因为**代码里的上限不叫这个名字**：`config.py` 有 `MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 100)`，`ui/page_library.py` 在解析**之前**判断 `_mb > MAX_UPLOAD_MB` 并 `st.stop()`。<br>**即应用层硬上限就是 100 MB。** 我犯的是"用错误的名字去 grep，然后得出'不存在'的结论"——**grep 为空只能证明这个名字没出现，证明不了这个能力不存在。**<br>改成 100 的实际收益：超限文件是在 `data = f.read()` 处**整份读进内存**之后才走到那个判断的，200 会让一个 150MB 的 PDF 先传完、再占 150MB 内存、然后才被拒；100 能在**上传层**就拦掉（compose 已限 4GB）。<br>⚠️ 联动：若用 `MAX_UPLOAD_MB` 调高应用层上限，**必须同步调大本值**，否则 streamlit 先拦、应用层根本看不到文件 |

**CLI flag 格式已核实**：`streamlit/web/cli.py:49` → `option = f"--{config_option.key}"`，key 形如 `server.enableCORS`，故 `--server.enableCORS=false` 合法；bool 型由 click 解析 `true/false`。

### 1.6 第 2 层：`apt-get install graphviz`（新增，运行时二进制）

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*
```

依据（逐行查证）：
- `services/writing.py:1496` `dot_exe = shutil.which("dot") or ""`
- `services/writing.py` 的 `subprocess.run([dot_exe, "-Tsvg"], ...)` 那级 —— **直接用二进制，不需要 Python graphviz 包**（`find_spec("graphviz")` 那级没有也能走通）
  ⚠️ **此处不写行号**：该文件**已漂过一次**（第八轮实测 `subprocess.run` 从 `:1517` 移到 `:1519`，而同函数的 `:1496` 未动 —— 是函数内插入、不是整文件平移，所以**不能按"整体 +N"去改**）。真值不在我手里，按纪律降级为符号名。
  定位命令：`grep -n "dot_exe = shutil.which\|subprocess.run(\[dot_exe" services/writing.py`
- 没有 `dot` → 跳过真实渲染，走后面两级降级
- 本机实测：`dot` / `tectonic` / `pdflatex` / `xelatex` / `lualatex` 全部 `shutil.which() → None`；Python `graphviz` 包 `find_spec is None`

⚠️ **关于"几级降级"的说法，历经三次变化，以第五轮为准：**
- 第二轮：matplotlib 不在 `requirements.txt` → 我说中间那级（`writing.py:1445` `_dot_via_matplotlib`）是死代码 → 三级链路实为两级。
- 第四轮：其他成员加进 `matplotlib>=3.8` → 该级变活 → 我撤回上条，改口"三级"。
- **第五轮（当前）**：team-lead 裁定**移除 matplotlib** → 该级**再次不存在** → 链路回到**两级**：
  - **容器**：`dot -Tsvg`（apt 已装 graphviz）→ 零依赖 DOT→SVG 兜底
  - **云平台**：装不了系统包 → 直接走零依赖 DOT→SVG（浏览器渲染，中文正常）

完整结论与"为什么有意不装 matplotlib"见下方 **📌 DOT 渲染的最终结论** 节，那一节是全文唯一口径。

⚠️ 这与 §1.1「不需要 build-essential」**不冲突**：那里说的是 Python 依赖不需要编译器，这里是业务功能需要的运行时可执行文件。

### 1.7 第 5 层：构建期预热 ONNX embedding 模型（新增，最关键的修复）

详见 §5.1。要点：
- **位置在 `USER app` 之后**（team-lead 明确要求，第三轮按此调整）→ 缓存文件直接归 `app` 所有，运行时不会因属主是 root 而读不到
- 上一层的 `chown -R app:app /home/app` 正是为了让这一步有地方可写
- 显式 `HOME=/home/app`（`Path.home()` 读 HOME 环境变量；写成 root 的 `/root` 就全错了）
- 末尾 assert 校验 6 个必需文件，缺一个就让**构建失败** —— 构建失败是**有意设计**，逃生开关（注释掉该 RUN / 改 `COPY`）写在 Dockerfile 第 124-130 行注释里

### 1.8 `ENV` 注入点

镜像内固化（**非密钥**）：`PYTHONUNBUFFERED=1`、`PIP_NO_CACHE_DIR=1`、`PIP_DISABLE_PIP_VERSION_CHECK=1`、`PYTHONDONTWRITEBYTECODE=1`、`HOME=/home/app`、`HF_ENDPOINT=https://hf-mirror.com`。

- `PYTHONDONTWRITEBYTECODE=1` 是配合非 root 的：`/app` 对 `app` 只读，不关掉会有一堆写 `__pycache__` 失败的噪音。
- `HOME` 必须显式设：`useradd` 建的用户的 HOME 虽是 `/home/app`，但显式 `ENV` 可避免 `docker run -u` 等场景下 HOME 被重置导致 ONNX 缓存落错位置。
- 密钥类变量**一个都不在镜像里**，全部运行期注入。

### 1.9 三件套注释里的代码行号 —— 全量复核（第六轮，doc-audit-5-8 提醒后做的）

doc-audit-5-8 提醒：`docs/technical_doc.md` 正在做全量行号修正，而我 compose/Dockerfile 的注释里也引用了一堆 `xxx.py:NNN`，别出现"文档修好了、部署文件注释又飘了"。我把三个文件里**所有** `*.py:行号` 引用逐条对了一遍（`sed -n` 取该行比对内容）。

**✅ 核对通过（19 处）**：

| 引用位置 | 内容 | 核验 |
|---|---|---|
| `Dockerfile:86-87` | `config.py:38-42` mkdirs / `app.py:15` `LOG_DIR="logs"` | ✅ |
| `Dockerfile:100` | `services/state.py:55-59` chromadb ONNX import | ✅ |
| `Dockerfile:64/65/69` | `services/writing.py` 的 `dot_exe=shutil.which("dot")` / `subprocess.run([dot_exe,"-Tsvg"])` / `def render_dot` 三处（**第八轮起改为符号名**，不再写行号：该文件已漂过一次） | ✅ |
| `Dockerfile:149`、`.dockerignore:50` | `ui/page_writing.py:495` `/app/static/mermaid.min.js` | ✅ |
| `compose:72` | `app.py:89`「存储目录不可写」 | ✅ |
| `compose:77` | `services/library.py:777` 上传 PDF 落盘 | ✅ |
| `compose:79` | `config.py:36` 非桌面态 `LOGS_DIR = ROOT/"storage"` | ✅ |
| `compose:102/104` | `app.py:15` `LOG_DIR` / `app.py:22` `makedirs` | ✅ |
| `compose:51` | `config.py:10` `AI4S_DESKTOP` | ✅ |
| `.dockerignore:60` | `config.py:34` 非桌面态 `PDF_DIR = ROOT/"pdf"` | ✅ |
| `.dockerignore:61` | `services/library.py:983` 批量导入官方语料读盘 | ✅ |
| `.dockerignore:62-63` | `config.py:35` `DEMO_DIR` / `ui/page_writing.py:105-106` 读 `demo_data/data.csv` | ✅ |

**❌ 核对失败、本轮已改正（4 处，全是我自己写错的）**：

| 原引用 | 实际 | 已改为 |
|---|---|---|
| `compose` 注释 `config.py:61-71` 的 `_env_str()` | `config.py:61` 是 `_st_secret`；`_env_str` 在 **`:81`** | `config.py:81` |
| `compose` 注释 `config.py:95` 默认 Provider | `config.py` 里**根本没有** `LLM_PROVIDER`；默认值在 **`services/state.py:81`**（`_env_str("LLM_PROVIDER","zhipu")`） | `services/state.py:81` |
| `compose` 注释 `config.py:106-112` 调优类 | 实际是 `:97 _env_int` / `:105 _env_float` | `config.py:81 _env_str / :97 _env_int / :105 _env_float` |
| `Dockerfile:32/36` `config.py:103` `HF_ENDPOINT` setdefault | 我改成 `:126` | `config.py:126` |

**🔴 上表第 4 行我改完又错了 —— 而且这次错出了方法论结论（team-lead 与 deploy-audit 复验后共同指出）**：

- 我改完 `:126` 后，team-lead 与 deploy-audit 分别复验，**当前实际是 `config.py:131`**，`:126` 是 `LLM_TIMEOUT` 的注释行。
- 而我核到 `:126` 时它是对的——**中间被别人插了 5 行**。`config.py` 是**未提交的工作区改动且仍被多人并行编辑**。
- 同一个符号我先后核出过 **`:103` → `:126` → `:131` 三个值**。

**结论：问题不在我粗心，在"给仍在被并行编辑的文件写行号"这个做法本身不可持续。** 已按 team-lead 要求**改成不写行号**：

> **新规则（已落实到三个文件）**：凡引用 `config.py` 这类仍在被改的文件，**只写函数名 / 变量名**（如 `config.py 的 _env_str()`、`os.environ.setdefault("HF_ENDPOINT", ...)`），**不写行号**。写行号等于给每个后来人加一条"回头重核"的义务。
> ~~**例外**：`services/writing.py` 的 `render_dot` 链保留行号（理由："已稳定"+"验收清单要断言"）~~
> **🔴 该例外第八轮已撤销 —— 它的两条理由双双不成立，我已把这条链也改为不写行号**：
> ① 「已稳定」**被证伪**：本轮重扫发现 `subprocess.run([dot_exe,"-Tsvg"])` 已从 `:1517` 漂到 `:1519`；
> ② 「验收清单要断言」**不成立**：§7 断言的是**行为**（`mode == "graphviz"` 且 `svg` 非空），**不依赖任何行号**。
>
> ⚠️ **而且差点踩进一个更隐蔽的坑（deploy-doc 发现，我复核属实）**：这次漂移**不是整文件平移**——
> 同一函数里 `:1496`（`dot_exe = shutil.which`）**没动**，只有 `:1517` 漂到 `:1519`，是**函数内局部插入**。
> **若按"该文件整体 +2"去批量改，反而会把本来正确的 `:1496` 改错。** 这解释了为什么"记一个偏移量"这种省事做法不可靠——**同一个文件内不同位置的偏移量可以不同**。
>
> **现在的处理**：`services/writing.py` 的 `render_dot` 链**同样改为符号名**（`def render_dot` / `dot_exe = shutil.which` / `subprocess.run([dot_exe,"-Tsvg"])`），与 `config.py` 一视同仁。
> 唯一保留数字的地方是 **R21 的证据链留档**（`writing.py:1507→1519→1531→1537`），那里是**第八轮实测的当前值**，用于追溯"依据什么得出的结论"；但它同样是**快照**，下次该文件再动就会失效 —— 已在 R21 处标注。
> `grep -n "def render_dot\|dot_exe = shutil.which\|subprocess.run(\[dot_exe\|零依赖纯 Python DOT" services/writing.py`。
> 且 §7 验收清单断言的是**行为**（`mode == "graphviz"` 且 `svg` 非空），**不依赖任何行号** —— 行号再漂也不影响验收。

已清理：`Dockerfile` 2 处、`docker-compose.yml` 4 处、`.dockerignore` 2 处，共 **8 处 `config.py:NNN` 全部改成符号名**。复验：三件套里已无任何 `config.py:行号` 残留。

**连带发现一处事实错误（不是行号错，是判断错）**：R17 里我写过「`services/extern.py:13` 的 `GITHUB_TOKEN` 用的是**裸** `os.getenv`」——**错的**。`services/extern.py:16` 实际是 `_env_str("GITHUB_TOKEN","") or _env_str("GITHUB_API_TOKEN","")`，走的是同一套 `_env_str`，空串视为未设置 → 会**正确回落**到 `GITHUB_API_TOKEN`。结论方向没变（空串安全），但**比我原先判断的更稳**，R17 已改写。（team-lead 与 deploy-audit 均复验成立。）

> 说明：这批引用**只存在于注释里**，改它们不影响任何构建/运行行为。但既然是给后人看的依据，指错文件/行号会误导排查，所以值得修——而修完之后发现，**最稳的修法是干脆不写行号**。

**🔴 第八轮自查补一处同类漂移（我自己的文件里，签退之后才发现的）**：

清理完 `config.py:NNN` 之后，我顺手把三件套里**所有层号自称**也 grep 了一遍，结果在 `Dockerfile` 里查到 **3 处「第 4 层」是漂移的残留**：

| 位置 | 原写 | 实际 | 已改为 |
|---|---|---|---|
| `Dockerfile:31` | 构建期就是在这个位置预热的（见第 4 层） | 预热在**第 5 层**（`:98` 层标题、`:131` RUN） | 第 5 层 |
| `Dockerfile:36` | 真正的解法是**第 4 层**的构建期预热 | 同上 | 第 5 层 |
| `Dockerfile:141` | 模型已在**第 4 层**预热 | 同上 | 第 5 层 |

判据是文件自身的层标题：`:53` 第 1 层依赖 / `:64` 第 2 层 graphviz / `:85` 第 3 层代码 / `:88` 第 4 层非 root+可写目录 / `:98` 第 5 层 ONNX 预热。而 `Dockerfile:15`、`:91` 两处**早就写的是第 5 层**——所以这不是"我统一改错了"，是**改层号时漏改了这 3 处**，同一文件里自相矛盾。

**为什么这一点不算小事**：DEPLOY.md 有 4 处引用「Dockerfile 第 5 层」（`:16`、`:111`、`:279`、`:916`），`docker-compose.yml:88` 也有 1 处，我的报告里也有 **6 处**（`§1.6` `§1.7` `§5.1` `§5.3` `R14` `§8`；`grep -c` 实测，非心算） —— **外部全部引的是"第 2/5 层"且全对**，错的只有我 Dockerfile 内部这 3 处。也就是说：如果有人按 DEPLOY.md 的指引去 Dockerfile 找"第 5 层"的逃生开关（`:124-130`），**能找到**；但同一文件里 3 处注释会把他指向第 4 层（`useradd` 那一层），指向错就会以为开关在别处。**这类"内部自相矛盾"比"外部引用飘了"更难被人发现**，因为它不会被跨文档 diff 抓到。

> ⚠️ **教训（与 §1.9 同一条，但作用域要扩）**：我上一轮定的规则是"不给**正在被别人改的文件**写行号"——这条只对了一半。**我自己拥有、自己独占的文件同样会漂**，因为漂的不是"别人插了几行"，而是"**我自己改编号时漏改了引用点**"。所以规则应补一句：
> **凡在一处定义了编号（层号 / 章节号 / 风险号），改动后必须全量 grep 该编号的所有出现处，而不只改"我记得的"那几处。** 这次就是靠 grep（不是靠记忆）才捞出来的。

**复验**（`grep -c "第 *[0-9] *层" <file>`，改完立刻跑、不靠记忆数）：

| 文件 | 命中数 | 说明 |
|---|---|---|
| `Dockerfile` | **10** | 5 个层标题 + 5 处正文引用（`:15` `:31` `:36` `:91` `:141`），全部与层标题自洽 |
| `docker-compose.yml` | 1 | `:88`，引第 5 层 |
| 本报告 | 14 | 其中 3 处是本节自查表里**故意保留的历史错误值**（`第 4 层`），其余指向第 2/5 层 |

> ⚠️ **我第一版把 Dockerfile 的命中数写成 8，是错的（实际 10）**——写的时候按"我记得改了 3 处"心算，没跑 grep。**这正是上一条教训的第二次发作，且发作在写下教训的同一段里。** 已改为以 `grep -c` 输出为准，并把这条事故本身留在这里：以后凡在报告里写"共 N 处"，N 必须来自命令输出，不能来自心算。

三件套行数未变（`Dockerfile` 164 / `docker-compose.yml` 146 / `.dockerignore` 80）。

> 附一条约定说明（避免后人误读）：这里的「第 N 层」是**文档分节编号**，对应文件里可见的 `# ---------- 第 N 层 ----------` 标题，**不等于 `docker history` 里的层序号**（`USER app` 单独占一层）。跨文档引用时以层标题为准。

### 1.10 报告自身引用的全量校验（第七轮，deploy-doc 提醒后补做）

deploy-doc 报了他自己文档里 **60+ 处 `文件:行号` 引用从未被扫过**、补扫后揪出一整批漂移 + 一处事实错误。**我意识到自己有同一个洞**：上面 §1.9 只扫了三个交付文件的注释，**从没扫过本报告正文里的 `文件:行号` 引用**（补扫后实测共 **86 处**，见本小节下方统计）。

于是写了校验脚本，把报告里所有 `xxx:NNN`（含 `NNN-MMM` 区间）抽出来，逐条打印磁盘上的实际行内容，检查三类问题：文件不存在 / 行号越界 / 落空行。

**结果：共 86 处引用，9 处被标记。**

| 类别 | 数量 | 处置 |
|---|---|---|
| **真正的漂移（活的引用，已修）** | **5** | §4.3 的 `config.py:129-130`/`131-133`/`134`/`135` → 实际 `134-135`/`136-138`/`139`/`140`（**整块 +5 行**，与 deploy-doc 发现的同一批漂移同源）；`services/extern.py:13` → 实际 `:16` |
| **落空行但是故意引用旧值** | 4 | `config.py:64`（R13 引 `.env.example` 里的过时原文）、`config.py:95`/`:103`（§1.9 里"我改错过"的历史记录）、`config.py:129`（同上）。**这些是引文不是结论**，保留并已标注 |
| **外部包引用（脚本查不到，已手工核实）** | 4 | `streamlit/web/server/starlette/starlette_routes.py:153`/`:161`/`:429`、`web/cli.py:49` —— 都在**本机 site-packages** 里，逐条 `sed` 核实过：`BASE_ROUTE_CORE: Final = "_stcore"` / `ROUTE_HEALTH: Final = f"{BASE_ROUTE_CORE}/health"` / `"""Create health check route handlers for /_stcore/health.` / `option = f"--{config_option.key}"`。**全部对得上，且这就是 HEALTHCHECK 端点 `/_stcore/health` 的证据来源** |

**§4.3 的修法不是"改数字"，而是"不写数字"**：那四个漂移项连同相邻的 `LLM_BASE_URL`/`LLM_MODEL`/`VL_MODEL`/`LLM_TIMEOUT` 一起，全部从 `config.py:NNN` 改成 `config.py 的 XXX 定义处`。理由就是 §1.9 那条规则——`config.py` 一直在被多人并行编辑，**我看到它漂了三次（103→126→131、检索块 +5），改数字是给后来人加义务**。

> 📌 **这条经验来自 deploy-doc，我原样记下**：他也踩了同一类坑，并且额外给了两条操作纪律 ——
> **① 批量改行号必须逆序替换**（他正序替换导致 `config.py:134` 的新旧两处都被改，`CHUNK_SIZE` 串位）；
> **② 改完必须脚本复验**，肉眼比对在这个量级下不可靠。
> 我这轮就是靠脚本才发现的——**§4.3 那 5 处漂移我自己读的时候完全没看出来**，因为内容读起来是连贯的。

**🔴 第八轮重扫（deploy-doc 提醒后做的，结论：第七轮那次校验已经过期，又抓到 5 处）**

deploy-doc 报告他在**自己的复核报告**里残留 2 处 `core/llm.py:124`（实际 `:133`）——原因是"把改完扫结构只做在了主交付物上，没做在自己的报告上"。**我立刻意识到我有同一个洞，而且洞更深**：§1.10 那次校验是**第七轮做的**，之后源文件又被多人改过多轮，我当时"全绿"的结论**只在那个时点成立**。于是用同一脚本重扫。

结果（`grep -c` / 脚本统计，非心算）：

| 引用 | 我原写 | 实际 | 处置 |
|---|---|---|---|
| `core/llm.py:301` | `_fake_response` 定义处 | 现为 `def _fake_response` 在 **`:323`**（`:301` 现在是 `v = float(LLM_RETRY_BUDGET_SEC)`） | 改成符号名 `def _fake_response`，括注现为 `:323` |
| `core/llm.py:626` ×3 | "`626` 之后的 `_fake_response` 降级文案" | `:626` 现在是 `self.last_error = str(e)[:300]`（视觉分支内），**该引用已无意义** | 3 处全改成符号名 `_fake_response` |
| `services/writing.py:1517` ×6 | `subprocess.run([dot_exe,"-Tsvg"])` | 现为 **`:1519`**（漂 +2） | 先改成 `:1519`；**随后按 deploy-doc 的第 1 条纪律再次降级为符号名**——该文件已漂过一次，写成 `:1519` 只是把负债续期。同时**撤销 §1.9 给该链开的例外**（理由"已稳定"已被证伪） |

**两条方法论后果**：

1. **"校验过"是一个时点状态，不是持久属性。** 我第七轮写"共 86 处引用，9 处被标记，全部有解释"，读起来像是闭环了；但**源文件一改，这个闭环就失效，而报告里没有任何东西会提醒我**。已把 §1.9 的 `writing.py` 例外条款改写为"**快照值，不是现值**"，并给了 `grep` 定位命令。
2. **例外条款本身就是最容易过期的地方。** §1.9 我给 `writing.py` 开的例外理由是"已稳定"——**这个理由本轮被证伪**。教训：**凡写"这个不会变"的地方，都要顺手给出"万一变了怎么定位"的命令**，否则它就是下一个漂移点。

> ⚠️ 另需说明：本次重扫**只覆盖了报告正文里的 `文件:行号` 引用**（共 73 个唯一引用，逐个打印磁盘实际行内容比对）。三件套注释里的引用见 §1.9，**本轮未再重扫三件套注释** —— 这是本次重扫的**已知边界**，不是全部。

**⚠️ 本报告所有"结构检查通过 / N 块 0 异常"的结论，其工具本身的已知边界（第八轮补，deploy-doc 第 9 条纪律要求）**

我用来做表格列数 / 标题结构检查的脚本，本轮**第一次被验证**（此前一直是"跑出来 0 异常就信了"）。验证方法与结果：

| 验证项 | 方法 | 结果 |
|---|---|---|
| 会不会**误报** | 喂已知好样例 | 2 块 / **0 异常** ✅ |
| 会不会**漏报** | 喂已知坏样例（含列数不符 + 单元格内管道符 + 相邻两表 3 类缺陷） | 4 块 / 报 **2 处** —— 命中"列数不符"与"单元格内管道符"，**漏报"相邻两表"** |
| 数字是否可复现 | 换手段：三种不同 `grep` 直接数分隔行 | **23 / 23 / 23**，与脚本的 23 一致 ✅（**当前值**；上表写作时是 22，之后新增 1 块）。⚠️ 但这三个 grep **共享同一个行首锚定假设、失败模式相同，按"独立性"判据只算一个手段**，见下方 |
| **会不会漏报（第三种方法）** | **残差 / 孤立行检查**（deploy-doc 第八轮提出）：穷举"形似表格行但未被任何块覆盖"的行 | **0 处孤立行**（23 块共覆盖 182 行）✅ |

**已确证的漏报（写在这里，不掩盖）**：脚本在切分**相邻两张表**时，会把第二张表的表头吸进第一张表当数据行（实测切块为 `[0,1,2,3]` 与 `[2,3,4,5]`，行 2、3 被重复计入）。

对结论的影响我已逐条算过：
- 相邻两表**列数相同** → 静默重叠、不报警，但**不构成假阴性**（本来就没有列数错）
- 相邻两表**列数不同** → 混合后管道符数不一致 → **会报警**（行区间可能跨表、看着别扭，但不会漏）

→ 所以**"0 异常 = 无列数不一致"这个结论仍然成立**；但**"22"这个数是分隔行数**，存在相邻表时块边界不准，**不要拿它当"表格个数"**。

> **为什么需要第三种方法（残差检查）**：前两种方法（脚本切块、grep 数分隔行）**都建立在"表格有分隔行"这一前提上**。若表格被 blockquote / 空行 / 标题**从中断开**，断开后的段落内部列数依然自洽，**前两种方法一个都查不出来**（deploy-doc 用残差检查在他的文档里查出 3 处断表 + 2 处缺管道符，而前两种方法均报 0）。残差检查不依赖分隔行模式，只问"**有哪些管道符行没被算进任何表**"，失败模式与前两种完全不同，**才算真独立**。
> 本报告残差检查：**0 处孤立行** ✅。

> ⚠️ **上面那三个 `grep` 用的是 `^|` 行首锚定，它漏掉缩进表格和引用块内表格**（deploy-doc 第八轮发现：他的 DEPLOY.md 有 3 张表缩进在列表项内，`^|` 数出来比实际少 3）。
> **本报告之所以能用 `^|`，是因为我核过它确实没有这两类表**：`grep -cE "^[[:space:]]+\|"` 与 `grep -cE "^>[[:space:]]*\|"` **均为 0**；改用允许缩进的正则重数也是 **23**，与 `^|` 一致。
> **这是核过的属性，不是默认值** —— 若本报告将来出现缩进表格，这三个 `grep` 就会少算，必须换成 `^[[:space:]]*\|[[:space:]]*:?-+(\|[[:space:]]*:?-+)*[[:space:]]*\|?[[:space:]]*$`。

> 📌 **教训（与 deploy-doc 互补，两人事故方向相反）**：他那次差点**误报**（先怀疑正确的 18，实际错的是他随手那行 `grep`）；我这次是**漏报**（输出合理、看不出问题）。
> **"喂已知样例"验的是会不会误报，"检查它没报警的地方"验的是会不会漏报——这是两件事，都要做。**
> 再加他补的第三层：**"换手段交叉验证"时，两个手段给出不同数字，别默认信看起来更"正式"的那个** —— 他这次第一反应怀疑脚本（因为复杂），结果错的是那行简单 grep。**复杂度不等于可信度。**

---

## 2. `docker-compose.yml` 改动

| 项 | 改动 | 依据 |
|---|---|---|
| `env_file` | `- path: .env` + `required: false` | **保证没有 `.env` 也能 `docker compose up`**。旧写法 `env_file: - .env` 在文件缺失时会直接报 `env file not found` 而启动失败，与"一键起"冲突 |
| `environment` | 25 个变量全量列出，统一用 `${VAR:-默认}` | 密钥类默认**留空**（`${ZHIPU_API_KEY:-}`），策略类给真实默认值，调优类留空让代码用内置默认值。⏩ 详见 §5.2 |
| `AI4S_DESKTOP: "0"` | 显式写死 | `config.py:10` `DESKTOP_MODE = ... os.environ.get("AI4S_DESKTOP") == "1"`。一旦为 1，数据目录会跑到 `%APPDATA%/AI4SResearchAgent`，**容器里会彻底跑偏**。显式置 0 防止宿主环境意外污染 |
| 卷 1 | `./storage:/app/storage`（保留） | 主数据目录，见 §1.3 |
| 卷 2（**故意不挂**） | `$HOME/.cache/chroma` **不挂卷** | ONNX 模型已烤进镜像，挂卷反而会把它覆盖掉（已存在的命名卷不会重新拷贝镜像内容），导致运行期又去联网下载。这是我在采纳审计建议后的**反向决定**，理由写在 compose 注释里 |
| 卷 3（新增） | `app_logs:/app/logs` | 用**命名卷**而非 bind：命名卷会继承镜像内目录的 `app:10001` 属主，规避宿主目录权限不匹配导致日志静默丢失。想直接看日志就换成 `./logs:/app/logs` 并先 `chown -R 10001:10001 logs` |
| `healthcheck`（新增） | 与 Dockerfile 同参数 | 便于 `docker compose ps` 直接观察 healthy 状态 |
| `logging`（新增） | `json-file` + `max-size: 10m` / `max-file: "3"` | 日志轮转，防止容器 stdout 无限增长 |
| `deploy.resources.limits`（新增，可选） | `cpus: "2.0"`、`memory: 4g` | 是**上限不是预留**，宿主配置低于此值也能正常启动。不需要可整段删除 |
| `restart: unless-stopped`（保留） | — | 原配置已合理 |
| 宿主权限提示（新增注释） | `mkdir -p storage && sudo chown -R 10001:10001 storage` | **Linux 宿主必做**：bind 挂载目录要对 uid 10001 可写，否则 `app.py:89` 报"存储目录不可写"。Windows/macOS Docker Desktop 无此问题 |

YAML 已用 `yaml.safe_load` 解析通过（本机 Python 静态校验，非 `docker compose config`）。

---

## 3. `.dockerignore` 改动

### 3.1 修掉一个真实故障：取消忽略 `.streamlit/`（说明注释在 `.dockerignore` 第 47-55 行）

原文件第 43-44 行把 `.streamlit/` 整个排除，理由是"Docker CMD 已显式指定端口/地址"。这个理由只对一半：

- `address` / `port` 确实被 CMD 覆盖了 → 没问题；
- 但 **`enableStaticServing = true` 也会一起丢**，而它是 `ui/page_writing.py:495` 本地加载 `mermaid.min.js` 的开关 → 容器里 mermaid 会退化到 CDN，离线/内网环境出不了图；
- `[theme]` 学术风配色也会丢，评审观感降级。

已改为不忽略，并在注释里写明"其中 `address=127.0.0.1` 会被 CMD 的 `--server.address=0.0.0.0` 覆盖"。

### 3.2 显式标注"不能忽略"的目录

- `pdf/`（31MB）——`config.py:34` 非桌面态 `PDF_DIR = ROOT / "pdf"`，`services/library.py:983` 的批量导入官方语料从这里读盘。**已知体积代价，未优化**（需要改代码或改 `.dockerignore` 策略，超出授权）。
- `demo_data/`——`config.py:35` `DEMO_DIR` + `DEMO_PAPERS` 演示语料；`ui/page_writing.py:105-106` 读 `demo_data/data.csv`。
- `static/`——mermaid.min.js（3.2MB），理由见 §3.1。

### 3.2b `.streamlit/secrets.toml` 单独排除（第四轮补，安全口子）

放开 `.streamlit/` 之后连带漏了一个密钥入口 —— deploy-audit 发现、deploy-doc 复核，我确认属实：

| 密钥入口 | 读取代码 | `.gitignore` | `.dockerignore`（补之前） |
|---|---|---|---|
| `.env` | `config.py:58` `load_dotenv` | 已排除 ✅ | 已排除 ✅ |
| `.streamlit/secrets.toml` | `config.py:75` `st.secrets.get()` | 已排除 ✅ | **没排除 ❌** |

有人为本地调试把真实 Key 写进 `secrets.toml` 的话，`docker build` 会把它烤进镜像并可能推到镜像仓库。
已补 `.streamlit/secrets.toml` 精确排除（**不动 `.streamlit/` 整体**），config.toml 照常进镜像，主题配色与 `enableStaticServing` 不受影响。

> 📌 位置：该规则在 `.dockerignore` **第 10 行**（「密钥 / 环境变量」一节内，与 `.env` 并列），
> 语义上本就该和 `.env` 放一起。**期间出现过两处重复**（我在 §3.1 那段注释下又写了一次），
> deploy-doc 发现后已删除重复项、只保留第 10 行，并在 §3.1 注释里留了一句"不要再在这里重复写"。

补充确认：容器里**没有** secrets.toml 也不会出错 —— `config.py:73-77` 的 `_st_secret()` 整个包在 `try/except` 里，异常一律返回 `""`。

### 3.3 其它

- 新增 `.workbuddy/`（52K 工具元数据）。
- 确认 `desktop/` 可安全忽略：全仓 grep `import desktop` 只出现在 `desktop/` 包内部自引用和 `scripts/release.py`（非运行时），Web 模式下 `DESKTOP_MODE=False` 不会触及。
- 确认 `docs/`、`tests/`、`version_history/` 忽略安全：运行时代码无引用。

---

## 4. 必须配置的环境变量清单（DEPLOY.md 上游输入）

全仓 grep `os.getenv` / `os.environ` / `st.secrets` 结果。

> ⚠️ **本条已更正（第四轮）**：我第一轮写的是「`st.secrets` 全仓 0 处——所有配置走环境变量/`.env`，无 secrets.toml 依赖」。
> **这个结论已过时**：team-lead 之后给 `config.py` 与 `core/providers.py` 补了 `st.secrets` 兜底层
> （deploy-doc 复核后指出，我确认属实）。当前的读取链是：
> - **Key**（`core/providers.py:135-138` `resolve_key`）：界面手填 > 环境变量 > `st.secrets`
> - **其它配置**（`config.py:81-94` `_env_str`）：`os.environ`（含 .env 注入）→ `st.secrets` → 默认值
>
> **对 Docker 方案的影响（我核过了，无负面影响）**：
> 1. `config.py:73-77` 的 `_st_secret()` 整个包在 `try/except` 里，任何异常都返回 `""`。
>    容器里没有 `.streamlit/secrets.toml` 时不会抛错，行为与改动前完全一致。
> 2. 我的 compose 用 `${VAR:-}` 传**空串**时：`os.getenv` 返回 `""` → `_env_str` 判定 `not v.strip()`
>    为真 → 落到 `_st_secret` → 返回 `""` → 回落默认值。**空串语义依然安全**。
> 3. 我在 `.dockerignore` 里补了 `.streamlit/secrets.toml` 的精确排除（见 §3.1），
>    防止有人本地调试时把真实 Key 写进去后被烤进镜像。
>
> 云平台上 Secrets 一定能读到，不再赌"平台是否把 Secrets 注入环境变量"——这是好事。
> 注意 deploy-doc 提醒的一点：代码用 `st.secrets.get(name)` 取值，**Secrets 必须写根级键，
> 不能套 `[section]`**。

### 4.1 运行必需（不配会明显降级或报错）

| 变量 | 必填 | 默认值 | 读取位置 | 用途 | 不配会怎样 |
|---|---|---|---|---|---|
| `ZHIPU_API_KEY` | **强烈建议**（默认 Provider） | 无 | `config.py:120`；`core/providers.py:38` 声明 `key_env`；回退链 `core/providers.py:135-138` | 智谱 GLM-4-Flash 文本 / GLM-4V 视觉（唯一内置多模态，扫描件 OCR 依赖它） | 不配则 LLM 调用全部走 `core/llm.py` 的 `_fake_response` 降级文案（`def _fake_response` 那处，**现为 `:323`**）；应用能起但**问答/Agent 全是假答案**，顶部会出现 LLM 失败横幅 |
| `LLM_API_KEY` | 兜底（推荐作为通用位） | 无 | `config.py:120` 回退；`core/providers.py:136`；`core/llm.py` 的 `_fake_response` | 所有 Provider 的通用兜底 Key | 同上（若对应 Provider 专用 Key 也没配） |
| `LLM_PROVIDER` | 否 | `zhipu` | `services/state.py:79` | 启动时默认 Provider：`zhipu`/`deepseek`/`qwen`/`openai`/`ollama` | 不配默认智谱，行为不变；也可在侧边栏下拉切换 |
| `AI4S_DESKTOP` | **必须保持 0/不设** | 不设 | `config.py:10`、`desktop/paths.py:21`、`ui/desktop_chrome.py:32` | 桌面模式开关 | ⚠️ **设为 1 会进入桌面模式**，数据目录跑到 `%APPDATA%/AI4SResearchAgent`，容器里路径全错。compose 已显式置 `"0"` |
| `HF_ENDPOINT` | 建议（`https://hf-mirror.com`） | 无（代码 `setdefault`） | `config.py:126` | HF 镜像 | 国内服务器不配，任何走 HF 的下载可能卡死。**注意**：chroma 默认 ONNX embedding 在 chromadb 1.5.9 下是从 `chroma-onnx-models.s3.amazonaws.com` 下载，**不走 HF**，故该变量对 chroma 首次加载实际无效（见 §6 R4；真正的解法是 §5.1 的构建期预热） |

### 4.2 按 Provider 可选的 Key

| 变量 | 对应 Provider | 读取位置 | 不配会怎样 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | deepseek | `providers.py:46` | 选 DeepSeek 时无 Key → 降级文案 |
| `DASHSCOPE_API_KEY` | qwen | `providers.py:54` | 同上 |
| `OPENAI_API_KEY` | openai | `providers.py:62`、`core/llm.py` 的 `_fake_response` | 同上（且需国际网络） |
| `OLLAMA_API_KEY` | ollama | `providers.py:70` | `needs_key=False`，`resolve_key()` 直接返回占位 `"local"`，**留空也能跑**（需先 `ollama serve` + pull 模型） |

### 4.3 可选调优（都有默认值，改前先读代码）

| 变量 | 默认 | 读取位置 | 用途 |
|---|---|---|---|
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | `config.py` 的 `LLM_BASE_URL` 定义处 | 覆盖端点（自建网关/内网代理）。**填了会覆盖所选 Provider 自带 base_url** |
| `LLM_MODEL` | `glm-4-flash` | `config.py` 的 `LLM_MODEL` 定义处 | 文本/规划/Function Calling 模型 |
| `VL_MODEL` | `glm-4v` | `config.py` 的 `VL_MODEL` 定义处 | 视觉/OCR 模型 |
| `LLM_TIMEOUT` | `90.0` | `config.py` 的 `LLM_TIMEOUT` 定义处（`_env_float`）| 单次调用超时（秒） |
| `LLM_PROVIDERS_EXTRA` | 空 | `providers.py:79` | JSON 数组追加自定义 Provider（内网网关） |
| `GITHUB_TOKEN` / `GITHUB_API_TOKEN` | 空 | `services/extern.py` 的 `GITHUB_TOKEN` 定义处（`_env_str("GITHUB_TOKEN","") or _env_str("GITHUB_API_TOKEN","")`）（**模块导入期读取，改了要重启**） | GitHub REST 提额；不配匿名调用，60 次/小时限流，自动降级 |
| `AGENT_GLOBAL_MAX_ITERS` | 无（自动伸缩） | `core/agent.py:48` | Agent 全局迭代上限硬覆盖。**⚠️ `.env.example` 里漏了这个变量**，建议 DEPLOY.md 补上 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 1200 / 150 | `config.py` 的 `CHUNK_SIZE` / `CHUNK_OVERLAP` 定义处 | 切块参数 |
| `DENSE_K` / `SPARSE_K` / `FINAL_K` | 12 / 12 / 6 | `config.py` 的 `DENSE_K` / `SPARSE_K` / `FINAL_K` 定义处 | 召回与融合条数 |
| `DEDUP_THRESHOLD` | 0.90 | `config.py` 的 `DEDUP_THRESHOLD` 定义处 | 语义去重阈值 |
| `LLM_RETRY_BUDGET_SEC` ⚠️**第八轮补** | `45.0` | `config.py` 的 `LLM_RETRY_BUDGET_SEC` 定义处（`_env_float`） | 重试总时长预算（秒），累计超时即放弃后续重试直接降级。**此前清单漏了它**（AST 全仓扫描发现） |
| `MAX_UPLOAD_MB` ⚠️**第八轮补** | `100` | `config.py` 的 `MAX_UPLOAD_MB` 定义处（`_env_int`） | **单个上传 PDF 的硬上限**。`ui/page_library.py` 在解析**之前**判断 `_mb > MAX_UPLOAD_MB` 并 `st.stop()`。**此前清单漏了它**（AST 全仓扫描发现，deploy-doc 用同一方法在他的文档里先查出）<br>⚠️ **与 Dockerfile 的 `--server.maxUploadSize` 联动**：调高本值必须同步调大该 flag，否则 streamlit 先拦 |
| `AGENT_MAX_ITERS` | 8 | `config.py` 的 `AGENT_MAX_ITERS` 定义处 | Agent 单轮函数调用上限 |

> ⚠️ **本节已改为不写 `config.py` 行号，只写变量名** —— 原因见 §1.9 的那条规则：
> `config.py` 一直在被多人并行编辑，行号**改完就漂**（本表原来写的 `:129-130`/`:131-133`/`:134`/`:135`
> 经脚本复核发现**全部漂移 +5 行**，实际是 `:134-135`/`:136-138`/`:139`/`:140`）。
> 变量名不会漂，所以改用变量名；需要精确定位时用 grep 符号名即可。

### 4.4 非配置项（勿动）

- `APPDATA`：Windows 系统变量。`config.py:22`/`:30` 仅 `FROZEN` 分支用，`ui/sidebar.py:94` 有 `if not DESKTOP_MODE: return ""` 守卫 → **Linux 容器里不会崩**，无需配置。
- `NEW_ZHIPU_KEY`：只被 `scripts/_verify_and_set_key.py` 使用，非运行时。

### 4.5 读取方式的坑（写 DEPLOY.md 时请带上）

1. **空串被视为未设置**：`config.py:81-94` 的 `_env_str()` 把空串/纯空白也当作未设置，先回落 `st.secrets`（:90），再回落默认值。规避了 `os.getenv(name, default)` 在"变量存在但为空"时返回 `""` 的经典陷阱。所以 `.env` 里写 `ZHIPU_API_KEY=`（空）不会把 `LLM_API_KEY` 的兜底顶掉——这是**有意为之**，不要在文档里建议用空串做"屏蔽"。
2. **Secrets 必须写根级键**：代码用 `st.secrets.get(name)` 取值（config.py:75），套 `[section]` 会变成嵌套字典取不到（deploy-doc 复核结论，我确认属实）。
3. **Key 的三级优先序**：`core/providers.py:135-138` —— 界面手填 > 环境变量 > `st.secrets`。云平台上如果 Secrets 和环境变量都配了，**环境变量赢**。

---

## 5. 审计发现的部署隐患 → 我在 Dockerfile / compose 里如何处理

> 来源：deploy-audit 的 `docs/review/round12_deploy_audit.md` + team-lead 转来的 3 条致命项。
> 每条都标注了「我在哪个文件怎么处理」与「是否验证过」。

### 5.1 【致命】容器冷启动要联网下 ONNX 模型（79.31 MiB 压缩包 / 166.42 MiB 缓存目录），否则演示数据死锁 —— 已采纳并修复

**证据链（我逐条复核过，不是转述）**：
- `services/state.py:55-59`：`_build_embed_fn()` 里 `ONNXMiniLM_L6_V2()`（:58）+ `fn(["warmup"])`（:59）
  （行号以我最后一次读取为准；该文件在本轮被其他成员改动过，行号已同步）
- chromadb 源码 `onnx_mini_lm_l6_v2.py`：`MODEL_DOWNLOAD_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"`，`DOWNLOAD_PATH = Path.home()/".cache"/"chroma"/"onnx_models"/"all-MiniLM-L6-v2"`
- **走 AWS S3，不走 HuggingFace** → `HF_ENDPOINT` 对它无效（我在 Dockerfile 注释里明确了这一点，没删这行，理由见 §6 R4）
- 下载失败 → `state.py:60-61` 回退到 `chroma_ef.DefaultEmbeddingFunction()`；审计员用 `inspect.getsource` 证实该函数内部**依然**是 `ONNXMiniLM_L6_V2`，**我复核确认**：这是 chromadb 的默认 EF，同样要下载同一个模型 → 回退等于没回退
- 后果：「一键加载演示数据」→ `add_text_corpus` → `_paper_embedding`（`library.py:66-68`）→ `ctx.embed` → 三篇全失败且文献库为空 = 死锁

**本机实测数据（我亲自跑的）**：
```
C:\Users\Administrator\.cache\chroma\onnx_models\all-MiniLM-L6-v2\
  onnx.tar.gz          83,178,821 B  = 79.3 MiB / 83.2 MB   （解压后仍被保留）
  onnx/model.onnx      90,387,606 B  = 86.2 MiB / 90.4 MB
  onnx/tokenizer.json     711,661 B
  其余 4 个 json/txt
  合计              174,510,889 B  = 166.4 MiB / 174.5 MB
```
> 📏 **单位与归因订正（采纳 deploy-audit 的第二轮更正）**：
> - 单位：早期数字混用了 MiB / MB。统一口径为
>   **下载 83,178,821 B = 79.3 MiB / 83.2 MB；落盘 174,510,889 B = 166.4 MiB / 174.5 MB**。
> - **归因修正**：流传的"下 90MB"**不是指 `model.onnx`**（我上一版这么归因是错的）——
>   它是最初的一个**估值**一路传下来的，真实下载量是 83,178,821 B；
>   与 `model.onnx` 的 90.39 MB 数值接近纯属巧合。
> - compose 注释已改成「下载 79.31 MiB / 解压后 87.10 MiB / 缓存目录合计 166.42 MiB」三个量分开写，避免与早期估算值"约 167MB"混淆。
另外我用 `inspect.getsource` 读到 `_download_model_if_not_exists()` 的逻辑：它先检查 `DOWNLOAD_PATH/onnx/` 下 6 个文件（`config.json`、`model.onnx`、`special_tokens_map.json`、`tokenizer_config.json`、`tokenizer.json`、`vocab.txt`）**是否齐全**，齐全就直接跳过下载、也不校验 `onnx.tar.gz`。

**我的处理（Dockerfile 第 5 层，Dockerfile:131）**：
```dockerfile
USER app                                  # ← 预热在切换用户之后
RUN HOME=/home/app python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as M; \
  import os; M()(['warmup']); \
  d = os.path.join(str(M.DOWNLOAD_PATH), 'onnx'); \
  need = ['config.json','model.onnx','special_tokens_map.json','tokenizer_config.json','tokenizer.json','vocab.txt']; \
  miss = [f for f in need if not os.path.exists(os.path.join(d, f))]; \
  assert not miss, 'ONNX warmup failed, missing files: %s' % miss; \
  print('[build] ONNX embedding warmed at', d)"
```

**层顺序（第三轮按 team-lead 要求调整过）**：
```
第 4 层  useradd app + mkdir + chown -R app:app /home/app ...
第 5 层  USER app  →  RUN 预热 ONNX
```
为什么放在 `USER app` **之后**：这样缓存目录与文件直接归 `app` 所有，运行时不会因为属主是 root 而读不到（读不到就会再去联网下载，预热白做）。上一层的 `chown -R app:app /home/app` 则是让这一步有地方可写。
（我第二轮曾放在 `USER app` 之前 + 事后 chown，功能上也成立，但 team-lead 要求的这种顺序更直观、也不依赖 chown 时序，已按此改。）

**其余三个设计决定**：
1. **显式 `HOME=/home/app`**：`DOWNLOAD_PATH` 在 import 时求值 `Path.home()`，而 `Path.home()` 读 HOME 环境变量；顶层 `ENV HOME=/home/app` 已设，这里再显式传一次避免依赖声明顺序 —— **写成 root 的 `/root` 就全错了**（`ENV HOME` 的存在正是这个坑最容易踩的地方）
2. **末尾 assert 校验 6 个必需文件** → 缺一个就**构建失败**。这是**有意设计**：宁可构建期炸掉让使用者立刻知道，也不要产出"能起来但 AI 功能全废"的镜像（后者要到评审现场才暴露）
3. **逃生开关写在 Dockerfile:124-130 注释里**：
   - 方案 A（推荐）：注释掉该 RUN，改 `COPY --chown=app:app ./onnx_models /home/app/.cache/chroma/onnx_models`（先在能联网的机器上跑一次 warmup 生成该目录）
   - 方案 B：直接注释掉继续构建，代价是首次运行仍需联网下载（国内服务器访问 S3 可能很慢或直接失败，不建议）

我没删 `onnx.tar.gz`（可省 83MB）。原因：当前 chromadb 1.5.9 不需要它，但 `requirements.txt` 里 `chromadb>=0.5` **没有上界**，万一将来版本改成校验归档 sha256，删了就会在离线环境重新触发下载死锁。83MB 换这个确定性我认为值得；想省就把 `rm -f .../onnx.tar.gz` 加进同一层（风险自负）。

**⚠️ 关于「命名卷会不会遮蔽烤进去的模型」—— 结论（含一次措辞订正）：**

Docker 命名卷语义（deploy-audit 查官方文档取证、我确认采纳）：

| 场景 | 是否复制镜像内容 | 后果 |
|---|---|---|
| 挂载**空的新命名卷** | **会**复制 | 烤进去的模型能存活 |
| 卷**已存在且非空** | **不**复制，卷内容**遮盖**镜像目录 | 用旧镜像跑过一次后，重建镜像**不会刷新**卷内容，模型版本变了读到旧的 |
| **bind mount** | 不适用复制语义，宿主目录直接遮盖 | **必然**为空 → 预热**一定**白做 |

> 📝 **措辞订正**：我此前跟 team-lead 简写成"挂卷会覆盖掉它"，**对空卷不成立**。准确说法见上表。
> 由 deploy-audit 纠正，我已采纳并同步改写了 compose 注释。

**我的决定：根本不挂这个卷。** 第二轮的 `chroma_model_cache:/home/app/.cache/chroma` 已删除（compose 现在只有 `./storage` + `app_logs`）。
**不挂的真正理由**（不是"会覆盖"，而是）：模型已烤进镜像，挂卷**零收益**，只多两个风险 —— ① 镜像重建后卷内容陈旧；② 后人误改成 bind mount 直接失效。零收益 + 有风险 → 不挂。

compose 注释里补了两条实操提示：将来刷新模型用 `docker compose down -v`（连卷删）再 up，而不是"进容器删缓存让它重下"；以及**千万别写成 bind mount**。

**↩️ 一个连带发现（deploy-audit 指出，我认为很关键）**：我自己给 `/app/logs` 用的 `app_logs:/app/logs` **命名卷，恰恰依赖"空卷复制"这条行为** —— 镜像里 `/app/logs` 是 `app:10001`，只有把目录**连同属主**复制进新卷，容器内才写得进日志。如果"卷无脑遮盖、不复制"是真的，`app_logs` 卷就是 root 属主，我原本想避免的"日志静默丢失"反而会发生。
**"复制是否保留属主"是按文档语义推断的，未实测。** 已在 compose 注释里写明首次 up 后必须验证一次：
`docker compose exec ai4s-agent ls -ld /app/logs`（属主必须是 `app`），若为 root 则改用 `- ./logs:/app/logs` + 宿主 `chown`。该断言也已加进 §7 验收清单。

**验证状态**：⚠️ **未验证，需实机构建确认**。我验证的是「chromadb 的下载逻辑与缓存路径」（读源码 + 本机缓存实测：缓存目录合计 174,510,889 B = 166.42 MiB（含未删的 onnx.tar.gz 79.31 MiB + 解压目录 87.10 MiB）），**没有**在 Linux 容器里跑过这条 RUN，也**没有**验证构建机能否访问 AWS S3。

### 5.2 【致命】`docker compose up` 在全新克隆上直接失败 —— 已采纳并修复（做了加强）

审计与 team-lead 都指出：`env_file: - .env` 在 `.env` 缺失时 compose 直接 abort，而 `.env` 被 `.gitignore` 排除不在仓库里。

**我的处理 —— 两手都要**，不是二选一：

1. `env_file` 改成 `- path: .env` + `required: false` → 有 `.env` 就加载，没有也不报错。**好处**：用户 `.env` 里任何我没列到的变量也能进容器。
2. 再加 `environment:` 块，25 个变量全量用 `${VAR:-默认}`：
   - **密钥类**（`ZHIPU_API_KEY` / `LLM_API_KEY` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_API_KEY` / `GITHUB_TOKEN` / `GITHUB_API_TOKEN`）：默认**留空**。`${ZHIPU_API_KEY:-}` 会被 compose 从宿主环境或项目根 `.env`（compose 用 `.env` 做变量插值）取值，取不到就是空串。空串是安全的：`config.py:81` 的 `_env_str()` 把空串视为"未设置"并回落默认值，不会顶掉 `LLM_API_KEY` 的兜底链。
   - **策略类**给真实默认值：`LLM_PROVIDER=zhipu`、`HF_ENDPOINT=https://hf-mirror.com`、`AI4S_DESKTOP="0"`、`TZ=Asia/Shanghai`
   - **调优类**留空（`CHUNK_SIZE` 等）→ 空串让代码用内置默认值，避免 compose 把代码默认值"钉死"在旧值上造成漂移

⚠️ `environment` 优先级高于 `env_file`，所以密钥绝不能在这里写真值 —— 只写 `${VAR:-}`，真值永远来自宿主环境或 `.env`。

**验证状态**：YAML 已 `yaml.safe_load` 解析通过；`${VAR:-}` 的插值语义与优先级是 Compose 规范，**未实机验证**。

### 5.3 【高】Graphviz —— 已装；TeX —— 未装（team-lead 已拍板：接受降级，见 R15）

- **graphviz 已装**（Dockerfile 第 2 层，`apt-get install -y --no-install-recommends graphviz` + `rm -rf /var/lib/apt/lists/*`，~30MB）。依据见 §1.6。
- **TeX 没装**。理由：team-lead 明确说"不要未经说明就塞几百 MB"。`services/writing.py:72` `LATEX_ENGINES = ("tectonic","pdflatex","xelatex","lualatex")`，本机实测四个全无。
  - 不装的后果：TikZ 走降级（展示源码），**不崩**，但与文档宣称的"编译 PDF→PNG 真实渲染"不符。
  - 若要装，推荐 tectonic 静态二进制（约 100MB），不要装 TeX Live 全套（约 2GB）。
  - ✅ **team-lead 第三轮已拍板：不装，接受降级，写进文档即可。** 我据此在 Dockerfile 里**没有**加任何 TeX 层，并把这条作为 R15 记入 §6，同时在上面的「DOT 渲染最终结论」表里据实写明 TikZ 的真实表现是降级而非渲染。
- **matplotlib —— 已由 team-lead 第五轮拍板「移除」，不是我推荐的「加守卫」**：
  - 我第二轮实测时它不在 `requirements.txt` 且本机未装 → 判定 `writing.py:1445` 的 `_dot_via_matplotlib` 是死代码；
  - 第四轮被其他成员加进 `requirements.txt`（`matplotlib>=3.8`）→ 该级变活 → 我报了 R21（云平台中文方框）；
  - **第五轮 team-lead 裁定直接从 `requirements.txt` 移除**（方案 ②），理由见下方「DOT 渲染的最终结论」节。
  - 我已用**移除后的最终版 requirements.txt 第三次重跑**二进制 wheel 核验（109 包全通过），见 §1.1 第五轮重跑。
  - **R21 / R22 由此关闭**，`fonts-wqy-microhei` 也不再加。

---

### 📌 DOT 渲染的最终结论（全报告以此为准，前后表述不一致时看这一节）

> 🔒 **本节已按 team-lead 第五轮裁定重写，是报告的最终口径。**
> 裁定：**从 `requirements.txt` 移除 `matplotlib>=3.8`**（方案 ②），而不是"装上 + 加 CJK 守卫"（方案 ①）。
> `requirements.txt` 已由 team-lead 改完，里面留了 8 行注释说明原因。**我没有动那个文件。**

**`render_dot` 现在是两级，不是四级**（`services/writing.py:1480`，我逐行读过 :1495-1545）：

| 实际生效顺序 | 级别（`writing.py` 行号） | 触发条件 | 容器（方案 C） | 云平台（方案 A/B） |
|---|---|---|---|---|
| ① | Python `graphviz` 包 + `dot`（:1507） | `find_spec("graphviz")` **且** `which("dot")` | ⚪ 跳过（Python 包不在 requirements） | ⚪ 跳过 |
| ② | **`dot -Tsvg` 子进程直调**（`subprocess.run` 那级） | `which("dot")` | ✅ **在这里返回**（apt 装了 graphviz） | ⚪ 跳过（云上装不了系统包） |
| ~~③~~ | ~~networkx + matplotlib（:1531）~~ | ~~`matplotlib` 可 import~~ | ❌ **不存在** | ❌ **不存在**——matplotlib 已移除，import 失败直接落到下一级 |
| ③（原第 4 级） | **零依赖纯 Python DOT→SVG**（:1537） | 恒可用 | 兜底（`dot` 调用失败时） | ✅ **在这里返回** |

**一句话结论：容器走 `dot -Tsvg`（真实 Graphviz 渲染），云平台走零依赖 DOT→SVG（浏览器渲染，中文正常）。**

**matplotlib 那一级是有意不装的，原因是"装了更差"**：
- matplotlib 默认字体族 `DejaVu Sans` **无 CJK 字形**，且 matplotlib **不会因缺字形而自动 fallback 到其他字体**（deploy-audit grep 全仓确认：代码里**没有任何** `rcParams` / `font.sans-serif` / `font_manager` 配置，0 命中）。
- 本项目的 DOT 标签由 LLM 按**中文 prompt** 生成（"交互层""Agent 内核"这类），**必然含中文** → 只要 matplotlib 那级生效，中文标签就渲染成方框 □。
- 不装 matplotlib，云上会自然落到下一级「零依赖 DOT→SVG」：`wcopilot_dotrender.dot_to_svg` 第 259 行输出 `font-family="Segoe UI,Microsoft YaHei,sans-serif"`，由**浏览器**渲染 → 中文正常。
- 容器侧因为 apt 装了 graphviz，本来就在第 ② 级返回，matplotlib 用不到。

**为什么 team-lead 选"移除"而不是"加 CJK 守卫"**（我原先推荐 ①，被推翻，我认为推翻得对）：
加守卫会让 matplotlib **几乎永远不生效**——标签必然含中文 → 守卫一路放行到零依赖 SVG → matplotlib 装上也是摆设，白付镜像体积与构建时间。**直接移除更干净。**

**这条裁定同时关掉了 R21 和 R22**：
- R21（云平台中文变方框）——matplotlib 那级不存在了，云平台走浏览器渲染，**已消除**。
- R22（容器里 `dot -Tsvg` 失败时穿透到 matplotlib 出方框）——那条路径根本不存在了，失败会**直接落到零依赖 SVG**（同样是浏览器渲染，中文正常），**已消除**。
- 由此 **`fonts-wqy-microhei` 也不需要加**。而且即便要加也**单独不生效**：必须同时改代码设 `rcParams["font.sans-serif"]` 并清字体缓存，改动量比移除 matplotlib 更大，还只解决渲染不解决布局（中文字宽/换行）。

**已作废的历史表述**（看到请忽略，别再引用）：
- ❌ "第 3 级是死代码"（第二轮）——当时 matplotlib 不在 requirements，后来被加进去过。
- ❌ "容器走第 2 级，云平台走第 3 级"（第四轮）——那是 matplotlib 还在时的临时状态。
- ❌ "建议方案 ① 加 CJK 守卫"、"可以考虑装 fonts-wqy-microhei 当保险"——均已被本次裁定取代。

- 第 ② 级**不需要 Python graphviz 包**：`services/writing.py` 里直接 `subprocess.run([dot_exe, "-Tsvg"])`，只装 apt 的 `dot` 二进制就够。这一点**不受本次变更影响**。

### 5.4 审计其它条目我的处理

| 审计条目 | 我的决定 | 理由 |
|---|---|---|
| requirements.txt 全部无上界（chromadb/pandas/numpy 应加上界） | **我没改，但已被其他成员改了**：现为 `PyMuPDF<2.0` / `chromadb<2.0` / `openai<4.0` / `pandas<4.0` / `numpy<3.0`。R7 已按此更新 | 上界挡住了跨大版本漂移，但**不锁小版本**，"全 wheel"结论仍随时间变化；我已用**最新版**（含第五轮移除 matplotlib 后）重跑核验三次，见 §1.1 |
| `.dockerignore` 排除 `pdf/LongBook_540p_fixture.pdf` | **不采纳 → 审计员已主动撤回原建议** | 只有 486KB，收益可忽略；而 `scripts/verify_official.py:68` 与 `scripts/_smoke_read.py:366` **引用了它**，且 `scripts/` 会进镜像 —— 排除会让容器内跑不了这两个脚本（deploy-audit 复核确认属实并撤回） |
| 建议把 `~/.cache` 也挂 volume | **反向决定：不挂**（决定获审计员认可，但**理由已订正**） | 原写"挂卷会覆盖掉它"**不严谨**（空卷会复制镜像内容）。准确理由：模型已烤进镜像，挂卷**零收益**，只多两个风险（① 镜像重建后卷内容陈旧；② 后人改成 bind mount 直接失效）。完整语义表见 §5.1 |
| 建议加 `HEALTHCHECK`（用 curl） | 已采纳，但**用 python 不用 curl** | `python:3.11-slim` 不带 curl，用标准库零额外依赖 |
| `cp .env.example .env` 写成必需步骤 | 请 deploy-doc **改成可选** | §5.2 修完之后，全新克隆不需要先建 `.env` 也能 `docker compose up` |

### 5.5 给 deploy-doc 的两个回复

1. **`.dockerignore` 已经不再排除 `.streamlit/` 了**（你看到的 `.dockerignore:44` 是旧版）。我在第一轮就取消了这条排除，现在 43-49 行是说明注释。所以容器里**有** `.streamlit/config.toml`。这不影响 Docker 方案（CMD 的 `--server.address=0.0.0.0` 优先级更高），且 `address` 那行已被 team-lead 注释掉，云平台那条风险也一并解了。
2. **HF Spaces 的 Dockerfile 位置**：我的意见是**不要为了 HF 把根目录 Dockerfile 挪走**。`docker compose build` / `docker build .` 的默认路径是硬约定，挪到 `deploy/` 会让"一键起"多一步 `-f` 参数，得不偿失。如果确实要用 HF Docker SDK，更省事的做法是在 Space 里新建**单独的** `Dockerfile`（HF 要求 7860 + UID 1000，与我们的 8501 + uid 10001 冲突，本来就得单独写一份），根目录这份留给 docker compose 用。**这是我的建议，最终听 team-lead 的。**

### 5.6 端口改为只绑回环后，DEPLOY.md 的同步状态（我复核过，结论：已同步）

> ⚠️ **这一节是 854 行版本的历史快照，已经过期。** 结论（"已同步"）当时成立、现在仍成立，
> 但**下面的行号和"必配项"措辞都已过时**：team-lead 随后把 Nginx 从「方案 C 的必配项」改成了
> **四档决策门**（有域名→443 / 无域名·境外→Nginx :80 / 无域名·大陆未备案→**建议别选方案 C 改走方案 A** /
> 坚持裸奔→才改 `8501:8501`），deploy-doc 已全量落地且行号整体漂移。
> **以 DEPLOY.md 当前内容为准，本节只作留档。**<br>
> 🔗 **指向最新依据**：上面的"必配项"措辞已被 **§C-0 四档决策门** 取代（见 DEPLOY.md §C-0 / §C-6b；
> 四档内容同步见本报告 **R18 的第六轮补正**）。我自己的 compose 注释已同步为四档（见 §2）。
> （留档格式学 deploy-doc：保留原文 + 指明"被哪一条推翻"，读者能顺藤找到最新依据，而不是只看到"已过时"。）

deploy-audit 报「DEPLOY.md 没跟上 `127.0.0.1:8501:8501`，是文档主动把人带进死路」。我按他给的行号核了**当时磁盘上的** `DEPLOY.md`（854 行），结论相反——**已经是同步状态**，判断为消息交叉（他发出报告时 deploy-doc 已改完）：

| 位置 | 内容 |
|---|---|
| `:12` | 参数表写 `宿主:容器 = **127.0.0.1:8501:8501**，默认只绑宿主回环` |
| `:382` | `curl` 验收入口加了「必须写 127.0.0.1，用宿主 IP 会连接被拒——那是预期行为」 |
| `:421` | 章节标题已改为「C-4 Nginx 反向代理（**方案 C 的必配项**）」 |
| `:423-425` | 正文明确「不配 Nginx 就没有任何访问入口」+ 唯一绕过方式 |
| `:444` | nginx 配置 `server 127.0.0.1:8501;` |
| `:514` | 安全组表：`8501` → **无需放行**（只绑回环） |
| `:516` / `:522` | 「默认只绑回环（team-lead 已拍板）」+ 贴出 compose 原文 |
| `:531` | 「Nginx 反代在方案 C 里是必配项，不是推荐项」 |
| `:798` | 排障表：「本机 curl 通、外部不通」= **预期行为**，并明确禁止去改 `--server.address` |

我已回复 deploy-audit 请他按当前文件重核、把 §7.9 那条改成「已同步，无需处理」，避免终稿里留一条**假的未闭环项**。同时给 deploy-doc 补了一条数字建议：向量模型**下载压缩包 83,178,821 B = 79.31 MiB**、**解压后 91,332,068 B = 87.10 MiB**、**缓存目录合计 174,510,889 B = 166.42 MiB**。云平台上这 166.42 MiB 落在**可写层/缓存目录**，配额不足会报错；自建 Docker 方案已烘进镜像，不触发下载。
> 📝 **订正**：我第四轮给 deploy-doc 的"176 MiB 解压落盘"把**压缩包也算进了解压量**。正确拆分见上。

> 这一节只是**同步状态记录**，不改变我自己的任何文件。如果你读到这里时 DEPLOY.md 又被改过，请以文件实际内容为准。

---

## 6. 风险与未核实项（⚠️ 请逐条过目）

| # | 级别 | 内容 |
|---|---|---|
| R1 | **未验证** | **镜像从未实机构建**。§1.1 只是 pip 解析器层面的"存在全二进制解"，不等于 `docker build` 一定成功。请用户实机 `docker build` 确认。 |
| R2 | **未验证** | **容器从未运行**。非 root + chown + HEALTHCHECK + 启动参数的组合未实跑；尤其 `/app/storage` 在 bind 挂载后的实际属主行为未验证。 |
| R3 | 高 | **Linux 宿主权限**：`./storage` bind 目录必须对 uid 10001 可写，否则应用起不来（`app.py:89`）。已在 compose 注释写明，但**无法在配置文件里自动解决**（除非改成命名卷，代价是数据不好取）。Windows/macOS 无此问题。 |
| R4 | 低（已降级） | **`HF_ENDPOINT` 对 chroma 默认 embedding 无效，但变量本身保留**。chromadb 1.5.9 的 `ONNXMiniLM_L6_V2` 从 `chroma-onnx-models.s3.amazonaws.com` 直下，不走 HF。原 Dockerfile 把它当"chroma 下载加速"是**误解**（注释已改正）。**team-lead 第四轮拍板：保留该变量**，理由（我认同）：它是 `config.py:126` 的 `os.environ.setdefault`，`.env.example` 明确写着"国内加速"可选；"对 chroma 无效"只对**默认 embedding 这一条路径**成立，不是全局结论，其它走 HF 的库仍然受益。Dockerfile 注释已按"仅 HuggingFace 相关路径生效、不影响 chroma ONNX"改写。**真正的解法是 §5.1 的构建期预热**，运行时不再需要 S3。 |
| R5 | 中 | **首次启动仍需外网出口**（但已不再是死锁项）：arXiv（`services/extern.py:25`）、PubMed、DuckDuckGo、api.github.com、LLM Provider。ONNX 模型**已烤进镜像**，离线也能 embed。离线环境下应用本体应仍可起来——**此降级路径未实跑验证**。 |
| R6 | 中 | **`env_file` 的 `required: false` 语法需要 Compose ≥ v2.24**（2024-01 起）。更低版本会报"env_file 必须是字符串"。若需兼容旧版，把第 23-25 行改回 `- .env` 并要求用户先 `cp .env.example .env`。**未验证用户环境的 compose 版本。** |
| R7 | 低（已降级） | **依赖未锁小版本**（无 `pip freeze` 锁文件）。team-lead 第三轮决策：**不锁**，理由是他实测本地 conda 环境与我的 pip 解析结果基本一致 → "装了没测过的版本"这个风险目前不成立。另外第三轮期间**其他成员给 requirements.txt 加了上界**（`PyMuPDF<2.0`/`chromadb<2.0`/`openai<4.0`/`pandas<4.0`/`numpy<3.0`）并新增 `matplotlib>=3.8`（**该包第五轮又被 team-lead 移除**，跨大版本上界不受影响）；我随即用新文件重跑了全二进制核验，结论不变（见 §1.1）。我把实测对照列在这里备查（本地 → pip 解析）：`chromadb 1.5.9→1.5.9`、`numpy 2.4.6→2.4.6`、`PyMuPDF 1.28.2→1.28.2`、`streamlit 1.63.0→1.63.0`、`networkx 3.6.1→3.6.1`、`arxiv 4.0.1→4.0.1`、`pillow 12.3.0→12.3.0`、`psutil 7.2.2→7.2.2`、`rank-bm25 0.2.2→0.2.2`；**有偏差的**：`openai 3.13.0→3.29.0`（minor 跨越，唯一显著项）、`pandas 3.0.5→3.0.6`、`plotly 7.0.0→7.1.0`、`onnxruntime 1.30.0→1.31.0`、`python-dotenv 1.2.3→1.2.4`（后四个均为 patch 级）。⚠️ 这是**时间点快照**：不锁版本意味着将来任何一次构建都可能解析出不同的版本，R1（能否构建成功）会随时间重新变成未知数。 |
| R8 | 中 | **镜像体积估算 ≈ 1.1–1.3 GB（未实测）**。⚠️ **口径换过两次**：① 前几轮「1.8–2.8 / 1.9–2.9 GB」是**拍脑袋粗估、无依据**（作废）；② 第五轮自下而上重算，但依赖层用**猜的** 2.0–3.0× 系数 → 0.9–1.2 GB（偏低）；③ **第六轮（当前）依赖层换成实测**：按 Linux 解析出的 109 个包逐个 `importlib.metadata` 累加真实落盘字节 = **747.4 MiB**，**实测膨胀系数 3.40×**（deploy-audit 先测得 916.9 MiB / 4.17×，我做了口径校正：剔除了 **Linux 解析集里根本不存在的 `scipy` 132.4 MiB**，那是本机 dev 环境 extra）。完整分项表见 **§1.2b**。剩余不确定项：① 747.4 MiB 是**本机 Windows 二进制**（`.dll`/`.pyd`）测得，Linux 是 `.so`，精确值未实测；② `.pyc` 增量 10–20% 是经验值，且 Dockerfile 设了 `PYTHONDONTWRITEBYTECODE=1` 可能使其为 0；③ `docker images` 显示压缩层大小，与展开体积的差值未实测。可优化点：删 `onnx.tar.gz` 省 79.31 MiB（风险见 §5.1）、`pdf/` 改运行期挂载；`kubernetes` 77.5 + `pydeck` 45.3 MiB 属硬依赖，**不建议裁**。 |
| R9 | 低 | `--server.enableCORS=false` 与 `.streamlit/config.toml` 里的 `enableXsrfProtection = false` 叠加，是**偏宽松**的组合（与项目本地开发配置一致，故保留）。若部署到公网，建议改回 `enableXsrfProtection=true`。 |
| R10 | 低 | `.streamlit/config.toml:20` 的 `address` 已被其他人注释掉（我未改，见 §8），且 `.dockerignore` 现在**不再**排除 `.streamlit/`（见 §3.1），所以该文件会进镜像；地址**完全依赖** Dockerfile CMD 的 `--server.address=0.0.0.0`。若有人绕过 CMD 直接 `docker run ... streamlit run app.py`，会只在容器内监听。已在 Dockerfile 注释标明。 |
| R11 | 低 | `deploy.resources.limits` 的 `memory: 4g` / `cpus: "2.0"` 是上限不是预留，宿主配置更低也能启动；但如果实测解析 541 页 PDF 时内存打满，需要调高。**未实测实际占用。** |
| R12 | 低 | `TZ=Asia/Shanghai`：`python:3.11-slim` 是否预装 `tzdata` 未核实；未装则时区回落到 UTC，仅影响日志时间戳，不影响功能。 |
| R13 | 信息 | `.env.example` 第 45 行写"读取位置：config.py:64"，实际是 **`services/state.py:81`**（`_env_str("LLM_PROVIDER","zhipu")`）—— 既不在 `config.py`，行号也不对。另外我自己在 compose/Dockerfile 注释里写过的 `config.py:61-71 _env_str`（实为 `:81`）、`config.py:103 HF_ENDPOINT`（实为 `:126`）、`config.py:95 默认 Provider`（实为 `services/state.py:81`）**均已本轮修正**。不是我改的范围，提请注意。 |
| R14 | **未验证 / 高** | **构建期新增了对 AWS S3 的强依赖**。Dockerfile 第 5 层（:131）要联网下 `onnx.tar.gz`（实测 83,178,821 B = 79.31 MiB），且我加了 assert —— **下载失败 = 构建失败**（这是有意设计，team-lead 已确认"宁可构建期炸掉"）。若构建机在国内访问 S3 不畅，**构建会直接卡住或失败**。逃生开关写在 Dockerfile:124-130（改 `COPY ./onnx_models`，或直接注释掉该 RUN）。**未实机验证 S3 可达性。** |
| R15 | 低（**已结案**：不装，接受降级） | **TeX 未装**。team-lead 第四轮拍板并给了理由，我照办并记录：① 装 ~100MB 的 tectonic 换来的能力**不在演示脚本主线上**（他核对过分镜，写作与可视化段落只讲 CSV 出图 / Figure Caption / Mermaid；Mermaid 走本地 `mermaid.min.js` 纯前端，不受影响，TikZ 根本没进分镜）；② 这 100MB 花下去评委在视频里看不到，只有手动乱点才会碰到，性价比不成立。**结论**：部署版 TikZ 会降级为展示源码，**不崩**，但**文档与演示都不得宣称部署版能真实编译 TikZ**（口径已同步 deploy-doc）。`services/writing.py:72` 的四个引擎（tectonic/pdflatex/xelatex/lualatex）本机实测全部 `shutil.which() → None`。 |
| R16 | 中 | **graphviz 让镜像多约 30MB，且构建期需访问 Debian 源**。`apt-get update` + `install graphviz` 若源不可达会构建失败。这是为「DOT 图真实渲染」付出的代价，我认为值；若要去掉，删 Dockerfile 第 81-83 行，写作页 Graphviz 退回零依赖兜底。 |
| R17 | 低 | **`environment` 里 25 个变量的空串语义**：密钥类 `${VAR:-}` 未取到值时传空串进容器。已确认 `config.py:81` 的 `_env_str()` 会把空串当"未设置"回落默认值 → 安全。`GITHUB_TOKEN` 我原先写"用的是裸 `os.getenv`"，**本轮核实为错**：`services/extern.py:16` 实际是 `_env_str("GITHUB_TOKEN","") or _env_str("GITHUB_API_TOKEN","")`，走的是同一套 `_env_str`，空串同样视为未设置 → 会**正确回落**到 `GITHUB_API_TOKEN`。所以空串语义**全部安全**，且比我原先判断的更稳。**均已静态确认，未实跑。** |
| R18 | 低（**已结案**：改为只绑回环） | **端口绑定面**：team-lead 第五轮拍板，compose 现已改为 **`"127.0.0.1:8501:8501"`**（只绑宿主回环）。决策依据（我原先的判断被推翻，理由更硬）：① 方案 C 的设计就是「**容器只绑回环 + 外层 Nginx 做入口**」，Nginx 与容器同机，走 `127.0.0.1:8501` 即可反代，**绑回环不损失任何东西**；<br>（📝 **措辞订正，第六轮**：我原写「方案 C 的完整定义就是「Docker + Nginx + HTTPS」」——**这是定性错误**，它把第 ② 档（无域名·境外，Nginx 只监听 80 走 HTTP）直接排除了。已按 deploy-doc 在 DEPLOY.md 的改法统一为「只绑回环 + 外层 Nginx 做入口」。）② 应用**零鉴权**，8501 对公网开放 = 任何人都能用你的 Key 调模型，Key 被刷爆 = 服务不可用，而"评审期间须保持服务可用"是赛题硬要求 —— 为省一行配置冒掉硬要求不划算；③ 我说的"云服务器上评审方直接开 8501 是常见演示场景"在方案 C 里**不成立**。compose 注释里保留了回退方法，并**强制要求**：若改回 `"8501:8501"`，必须同时在安全组限制来源 IP。<br><br>⚠️ **第六轮补正（team-lead 最终裁决，我原先的表述需要收窄）**：裁决把"必配 Nginx + 必须 HTTPS 域名"改成了**四档决策门** —— ① 有已备案域名 → 保持回环，Nginx 对外 443 + certbot；② **无域名 · 境外机房 → 保持回环，Nginx 只监听 80 走 HTTP 即可**（**不需要域名**）；③ **无域名 · 大陆未备案 → 建议别选方案 C，改走方案 A**（境内机房 80/443 大概率被云厂商封禁，绑回环 + 无入口 = 评委访问不到）；④ 坚持裸奔直连 → 才改 `"8501:8501"` 且必须锁来源 IP。<br>因此我上面 ① 里"绑回环不损失任何东西"**只在 ①② 档成立**，③ 档下方案 C 本身就不该选；而"评审方应走 HTTPS 域名"应改为"**域名决定的是 HTTPS，不是可达性**"（② 档可达但无 HTTPS）。compose 的 ports 注释已按四档改写并加了这句澄清。 |
| R19 | 信息 | **`.streamlit/config.toml` 的 `address` 已被 team-lead 注释掉（第 20 行）**，且 `.dockerignore` 现已**不再**排除 `.streamlit/`（见 §3.1）——所以该文件**会**进镜像。Docker 侧结论仍成立：CMD 的 `--server.address=0.0.0.0` 优先级高于 config.toml，容器外可正常访问；云平台侧也因 `address` 被注释而不再有绑死回环的风险。**Docker 与云平台两个方案都不受影响。** |
| R21 | ✅ **已关闭（第五轮）** | ~~新增 `matplotlib>=3.8` 在云平台路径上是回归，不是改善~~。**已于第五轮关闭**：team-lead 裁定从 `requirements.txt` **移除** matplotlib（方案 ②，非我推荐的 ① 加 CJK 守卫）。matplotlib 那级不存在 → 云平台直接落到「零依赖 DOT→SVG」（浏览器渲染，中文正常）。证据链保留备查（⚠️ **下列行号是第八轮实测的快照值、非现值**；`services/writing.py` 已漂过一次，定位命令见 §1.9）：`render_dot` 跳转 `writing.py:1507→1519→1531→1537`；第 3 级 `_dot_via_matplotlib` 用 `nx.draw_networkx_labels`（:1469）**服务器端**渲染 PNG，matplotlib 默认 DejaVu Sans **无 CJK 字形**且不自动 fallback（deploy-audit grep 全仓确认无任何 `rcParams`/`font.sans-serif`/`font_manager` 配置，0 命中）→ 中文标签必变方框；第 4 级 `wcopilot_dotrender.dot_to_svg` :259 输出 `font-family="Segoe UI,Microsoft YaHei,sans-serif"`，浏览器渲染、中文正常。**裁定理由**（我原先推荐 ① 被推翻，我认为推翻得对）：标签由中文 prompt 生成**必然含中文** → CJK 守卫会让 matplotlib 永远不生效 → 装上也是摆设，白付体积与构建时间。⚠️ 未实测（本机无 matplotlib 且非 Linux），但"默认字体无 CJK 字形→方框"是确定机制。见「DOT 渲染的最终结论」节。 |
| R22 | ✅ **已关闭（第五轮）** | ~~容器里 `dot -Tsvg` 失败时穿透到 matplotlib 出中文方框~~。**已于第五轮随 R21 一并关闭**：matplotlib 那级已不存在，`dot -Tsvg` 失败会**直接落到零依赖 SVG**（同样浏览器渲染，中文正常），不再有方框路径。**`fonts-wqy-microhei` 确定不加**——且补一条 deploy-audit 的取证：即便加了也**单独不生效**，必须同时改代码设 `rcParams["font.sans-serif"]` + 清字体缓存，改动量比移除 matplotlib 更大，还只解决渲染不解决布局（中文字宽/换行）。 |
| — | — | 📌 **编号说明**：R20 **跳号，历史上未分配**，无内容缺失；R21/R22 是第五轮新增并已关闭的两条（见「DOT 渲染的最终结论」节）。编号保留不重排，因为它们已在本报告及与 deploy-audit / deploy-doc 的往来消息中被引用。 |

---

## 7. 建议的实机验收清单（交给用户执行）

```bash
# ===== 1) 构建（验证 R1 / R14 / R16）=====
docker build -t ai4s-research-agent .
# 构建日志里应能看到： [build] ONNX embedding warmed at /home/app/.cache/chroma/...
# 如果卡在下载 onnx.tar.gz 或 assert 失败 → 见 §5.1 的「构建机连不上 S3 的兜底」
docker images ai4s-research-agent --format '{{.Size}}'    # 回填 R8 的真实体积

# ===== 2) 全新克隆可起（验证 §5.2，重点）=====
# 故意不建 .env，验证 compose 不会 abort
docker compose up -d
docker compose ps                        # 期望 STATUS = Up (healthy)，不出现 "env file not found"

# ===== 3) 宿主权限（验证 R2 / R3，仅 Linux 宿主）=====
mkdir -p storage && sudo chown -R 10001:10001 storage
docker compose restart

# ===== 4) 断言检查 =====
docker compose exec ai4s-agent id                       # 期望 uid=10001(app) gid=10001
docker compose exec ai4s-agent ls -ld /app/storage /app/logs      # 期望属主 app
# ⚠️ 上一条里 /app/logs 是**命名卷**，属主依赖"空卷复制时保留属主"这一行为（按文档推断，未实测）。
#    若显示 root → 日志会静默丢失，按 compose 注释改成 `- ./logs:/app/logs` + 宿主 chown。
# 端口只绑回环（验证 R18）：宿主上 ss/netstat 应只看到 127.0.0.1:8501
docker compose port ai4s-agent 8501                                # 期望 127.0.0.1:8501
docker compose exec ai4s-agent python -c "import os; print(os.environ.get('HOME'))"   # 期望 /home/app
# ONNX 模型确实烤进去了、且属主正确（验证 §5.1 的 chown 顺序）
docker compose exec ai4s-agent ls -l /home/app/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx
# graphviz 装上了（验证 §1.6）
docker compose exec ai4s-agent sh -c 'which dot && dot -V'
# matplotlib 确实没装（验证 R21/R22 已关闭：那级不存在才会走中文正常的 SVG）
docker compose exec ai4s-agent python -c "import importlib.util as u; print('matplotlib:', u.find_spec('matplotlib'))"   # 期望 None
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8501/_stcore/health     # 期望 200

# ===== 5) 功能抽查 =====
# 浏览器打开 http://localhost:8501
#   a) 写作页画一张 mermaid 图（验证 §3.1 的静态资源本地加载）
#   b) 写作页画一张 DOT 图（验证 §1.6 的 graphviz）—— 应走"真实 Graphviz 渲染"
#   c) 不填 Key 点「🚀 一键加载演示数据」→ 应能成功入库（验证 §5.1：离线不再死锁）
#   d) TikZ 图 → 预期**降级为展示源码**（R15 已结案：不装 TeX，文档/演示都不得宣称能编译 TikZ）

# ===== 6) DOT 中文渲染（验证 R21/R22 已关闭）=====
# 6a) 容器侧：画一张节点名为中文的 DOT 图，应走真实 Graphviz（dot -Tsvg）
docker compose exec ai4s-agent python -c "
import shutil, sys
sys.path.insert(0, '.')
from services.writing import render_dot
print('dot 存在:', bool(shutil.which('dot')))                       # 期望 True
r = render_dot('digraph G { A[label=\"交互层\"]; B[label=\"Agent内核\"]; A -> B; }')
print('mode =', r.get('mode'))                                      # 期望 graphviz
print('svg 非空:', bool(r.get('svg')))                              # 期望 True
print('png 为空:', r.get('png') is None)                            # 期望 True（R21 已消除，不会再出 PNG）
"
# 说明：`render_dot` 返回的是 **dict**（`services/writing.py:1480`，键 ok/mode/svg/png/note/log），
# 不是字符串 —— 我第一版清单里写成字符串是错的，已按实际签名改正。
# ⚠️ 上面这条仍是**我按代码路径推演写的，未实跑**。
#
# 6b) 云平台侧（A/B 方案）—— 无法在容器里直接模拟，因为容器装了 dot。
#     云上的等价验证只有一条：**确认 matplotlib 没装**（见第 4 段那条 find_spec 断言，期望 None）。
#     matplotlib 缺席 → `render_dot` 在 :1531 那级 import 失败 → 直接落到零依赖 DOT→SVG
#     → 输出含 font-family="Segoe UI,Microsoft YaHei,sans-serif"，浏览器渲染，中文正常。
#     若要在容器里强制复现这条分支，可临时把 dot 移出 PATH 再跑 6a 那段，但**我没验证过这样做安全**。
```

---

## 8. 变更文件清单

| 文件 | 改动性质 |
|---|---|
| `Dockerfile` | 重写（第二轮追加第 2 层 graphviz、第 5 层 ONNX 构建期预热；第三轮把预热挪到 `USER app` 之后）：非 root 用户 + 可写目录属主、HEALTHCHECK、启动参数补全（7 个 flag）、ENV 注入点、依赖层注释与 build-essential / S3 不可达的兜底写法 |
| `docker-compose.yml` | 重写（第二轮追加）：`env_file` `required:false` + 25 个变量的 `${VAR:-}` `environment` 块、2 个持久化卷（**故意不挂** ONNX 缓存）、healthcheck、日志轮转、资源上限、宿主权限提示 |
| `.dockerignore` | 取消忽略 `.streamlit/`；显式标注 `pdf/`、`demo_data/`、`static/` 不可忽略；新增 `.workbuddy/` |
| `docs/review/round12_deploy_docker.md` | 本报告（新增） |

未改动：`requirements.txt`、`config.py`、`app.py`、`.streamlit/config.toml`、`.env.example` 及任何其它业务文件；未 `git commit` / `git push`。

> 📌 **第五轮边界重申**：`requirements.txt` 的历次改动（加上界 → 加 matplotlib → **移除 matplotlib**）**全部不是我做的**，
> 我只是在每次他人改动后**重跑二进制 wheel 核验**（共三次）并把结论写回 §1.1。
> 我自始至终只写 `Dockerfile` / `docker-compose.yml` / `.dockerignore` 这三个文件 + 本报告。

> 协作提示：`.streamlit/config.toml` 与 `config.py`、`.env.example` 在我工作期间被其他成员改动过（`git status` 可见）。
> 本报告的行号引用以我最后一次读取时的磁盘内容为准；若这些文件继续变动，行号可能漂移。其中
> `.streamlit/config.toml:20` 的 `address` 被注释掉是本轮他人改动，我已同步调整 Dockerfile 与本报告的说法。
