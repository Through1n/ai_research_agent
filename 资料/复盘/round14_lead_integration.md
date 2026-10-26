# Round 14 集成轮报告（team-lead 亲自执行）

> **背景**：本轮 6 个话术原计划派给 6 个并行子 agent，其中 4 个在报告阶段撞上 **429 额度耗尽**
> （defense-coach / git-narrator / record-engineer / repro-pack）。
> 按 Round 13 血泪纪律「**429 让子 agent 失败 ≠ 代码缺陷；重置时间未到不要重派**」，
> 本轮**未重派任何 agent**，全部剩余工作由 team-lead 亲自吸收完成。
> 另有教训：**repro-pack 曾报告"completed"但磁盘上零文件**——后又发现它其实在 19:51–19:56
> 陆续写出了 4/5 份文件。**结论：agent 的"完成/失败"声明一律不可信，只认磁盘。**

---

## 1. 话术 7 · 在线部署准备（P0）— 已完成

### 1.1 发现并修复一个真实的部署阻断点：HF_ENDPOINT

`config.py:131` 原实现**无条件**设 `HF_ENDPOINT=https://hf-mirror.com`。
Streamlit Cloud 服务器在境外，连国内镜像更慢 / 易超时，chroma 首次下载自带
ONNX embedding 模型时会挂住 → **云部署直接跑不起来**。

```python
# HuggingFace 镜像：仅桌面态（国内）启镜像；云平台留空走 huggingface.co（境外连镜像更慢/易超时）
if DESKTOP_MODE: os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
```

> **行数中性**：注释从 1 行改 2 行会让 `config.py` 之后所有行号 +1，
> 技术文档附录 D 的 7 条引用会全部漂移（Round 13 已因此连吃两次 SUSPECT）。
> 本轮压回 1 行注释，`config.py` 仍为 223 行。

### 1.2 入口文件：`streamlit_app.py`

平台约定入口叫 `streamlit_app.py`，本仓库主脚本是 `app.py`。**没有改主脚本名**，
新增 6 行引导文件，用 `runpy.run_path(..., run_name="__main__")`：

- **为什么不 `import app`**：`app.py` 顶层有 `st.set_page_config` 等 Streamlit 副作用，
  import 会触发"只能调用一次"一类问题。
- **验证**：AppTest 双路对比（直接 `app.py` vs 经 `streamlit_app.py`），**0 异常**。

### 1.3 其余产出

| 文件 | 内容 |
|---|---|
| `.streamlit/secrets.toml.example` | Secrets 模板；`HF_ENDPOINT` 显式留空 |
| `资料/部署操作手册.md` | Streamlit Cloud 五步 + 前置条件表 + R1–R6 报错速查 |
| `资料/评委视角自检清单.md` | 9 导航项逐页验收表 + 耗时基准 + "正常现象别误判" |

---

## 2. 话术 8 · 录制工程化脚本（P0）— 已验收，保留子 agent 版本

record-engineer 在 429 前交付了 794 行 `资料/录制工程化脚本.md`。
**没有覆盖重写**，而是逐条核实它的坐标表：

| 它的声明 | 实测 |
|---|---|
| `ui/sidebar.py:13-23` MENU 9 项 | ✅ `MENU` 9 项在 13–21 行，`MENU_MAIN = MENU[:7]` / `MENU_ABOUT = MENU[7:]` 在 25–26 行 |
| `ui/sidebar.py:416-419` 演示按钮 | ✅ `"🚀 一键加载演示数据（v1/v7/BERT）"` 在 **417** 行 |
| `ui/sidebar.py:370-374` radio | ✅ `st.radio("🧭 功能导航", MENU_MAIN, ...)` 在 372 行 |
| `ui/sidebar.py:383-389` 折叠区按钮 | ✅ `MENU_ABOUT` 按钮在 `st.expander("ℹ️ 关于本系统")` 内 |

坐标表可信 → **保留原文件**。

---

## 3. 话术 9 · 决赛答辩压力测试（P0）— 已验收

defense-coach 在 429 前交付 `资料/答辩问答手册.md`（219 行）+ `资料/答辩PPT大纲.md`（142 行）。

**关键更正**：其初稿把 embedding 写成 `BAAI/bge-small-zh-v1.5`，**实际是 chroma 自带的
`ONNXMiniLM_L6_V2`**（384 维，英文预训练），在 `services/state.py:43` 的 `_build_embed_fn()` 里
实例化。已按实际改写，并把这个取舍（跨语言语义弱 → 必须做混合检索）写成了答辩正面答案。

`资料/决赛答辩问答手册.md`（20 问标准答案，142 行）是早前版本，**两份互补、均保留**
（新那份自己也在开头写明了与旧版的关系）。

---

## 4. 话术 10 · Git 提交历史叙事化（P1）— 已验收

git-narrator 在 429 前交付 `资料/提交历史分析.md`（363 行）+ `资料/提交历史阅读指南.md`（194 行）。
核心数字已复测：87 个提交 / 98.9% 语义前缀 / 5 天 / 9 处叙事断层 / 14 个超大提交。
**未重写任何历史**（team-lead 明令禁止 rebase/amend）。

其点名的一项已由本轮处理：技术文档「提交版本说明」里的 `对应 Git commit = 697cc85`
是**自指陷阱**（记录"本版对应提交号"要求该提交包含本文档，而文档要先写完才能提交）。
已改为不写死的自指表述（见 §5.4）。

---

## 5. 话术 11 · 技术文档最终版（P1，30 页定稿）— 已完成，页数打了三轮

### 5.1 加了什么

- 标题下「提交版本说明」表（版本号 / 日期 / 对应提交 / 团队成员）
- **附录 D** 完整环境变量清单、**附录 E** 部署命令速查、**附录 F** 常见问题速查
  （不与既有 附录 A/B/C 重编号，A/B/C 有内部交叉引用不能动）

### 5.2 页数门禁实战：32 → 31 → 30

| 快照 | md 去空白字符 | 实测页数 | 触发动作 |
|---|---|---|---|
| Round 13 基线 | 59,813 | **29** | — |
| 加版本说明 + 附录 D/E/F 后 | 63,336 | **32** ❌ | 超硬门禁 2 页 |
| 压 附录 E/F + §6.7/6.6/7/9.3 | 62,438 | 31 ❌ | 附录 F 表格整体迁出（`资料/评委FAQ.md` 已独立成文，附录只留指针） |
| 再压 §8.1.2 / §9.1 / §9.2 | 62,311 | 31 ❌ | 附录 D 删 8 行与 §6.4 重复的检索参数 |
| `--img-max 94 --mermaid-h 42` | 62,339 | **30** ✅ | 见 §5.3 |

**没有从第 1–8 章正文砍任何一段**（doc-audit 第 3 节已逐章取证 8 章全部达标）。

### 5.3 两个版面旋钮的取值依据（不是拍脑袋）

- `--img-max 100 → 94`：按宽高比换算 PDF 内实际宽度，p4（2880×2250，ratio 1.28）最窄。
  94mm 时 p4 宽 **109.4mm**，其余 122.9–128.2mm，**全部 ≥100mm 可读性下限**。
  （反算：img-max 不得低于 86mm，否则 p4 跌破 100mm。）
- `--mermaid-h 55 → 42`：5 个 mermaid 块在 PDF 里**本来就是占位框**
  （`[Mermaid 图：PDF 引擎未渲染，按占位框计高度]`），不承载信息，纯预留空白。
- ⚠️ **这两个旋钮改变了"官方命令"本身**。下文 §5.5 的固定检查清单已同步更新，
  **下次必须用新命令重测**，否则会得到 31 页的过期读数。

### 5.4 顺手修掉的可被 `git log` 证伪的陈述

| 位置 | 原陈述 | 实际 | 处置 |
|---|---|---|---|
| `资料/technical_doc.md` 版本表 | `对应 Git commit = 697cc85` | 自指陷阱，下次提交即失效 | 改为"以仓库 HEAD 为准（`git log -1 --format=%h`）" |
| `资料/technical_doc.md` L79/816/822/§6.1 | 阅读页专项 **59/59** | 复跑 `scripts/_smoke_read.py` = **PASS 60 / FAIL 0** | 全部改为 60/60（先复跑再改，不是照抄） |
| `README.md:74/183/292` | 24.6s / 1301 分块 / 峰值 283MB | 官方四用例实为 **25.32s / 1305 分块 / 峰值 RSS 893.1MB**（依据 `round4_worker9_official.md` + Round 12 审计 6 处一致性） | 三处全部改对 |
| `README.md:322` | 技术文档 **25 页** | 实为 **30 页** | 改 30 |
| `资料/提交前必办清单.md:11` | "本地已领先远端 **28 个提交**" | `git rev-list --left-right --count origin/main...HEAD` = **0 0**（已同步，累计 87） | 整节重写为"已同步，每次交付后再推一次" |
| 同文件 §① | "本机没有存储 GitHub 凭据 / 交互式输入被禁用" | Round 13 已实测 push 成功，凭据在 Windows 凭据管理器 | **按 Round 13 纪律复测后证伪**，改写 |

### 5.5 出图前检查（话术 11 要求的三项）

| 检查项 | 方法 | 结果 |
|---|---|---|
| 图片清晰 / 有图注 | 正则扫 `![alt](path)` | 7/7 有 alt，且每张在 §5 有逐图对照表 |
| 代码块语法高亮 | 统计 fence 语言标注 | 8 个代码块全部标注（mermaid 5 + bash 3），无裸 fence |
| 全中文 | 去行内代码/表格后扫英文残句（英文词 ≥4 且少于中文字数） | **0 处** |
| PDF 内图片宽度 | PyMuPDF 读 `get_image_rects` 换算 mm | 109.4 / 122.9 / 128.2mm，**均 ≥100mm** |

---

## 6. 话术 12 · 一键复现包（P1）— 已完成（含一次"假完成"打假）

### 6.1 repro-pack 的假报告

repro-pack 先报"completed"但**磁盘零文件**；team-lead 发更正指令后它**又失败于 429**。
但事后 `ls` 发现 4 份文件其实在 19:51–19:56 陆续落盘了（`start.bat` / `start.sh` /
`Dockerfile.quick` / `资料/5分钟快速体验指南.md`）。

> **纪律再确认：agent 的完成/失败声明一律不可信，只认磁盘 + 亲自读内容。**

### 6.2 我亲自做的验收（不是看文件存在）

| 项 | 方法 | 结果 |
|---|---|---|
| 无真实密钥 | 正则扫 `sk-`/`ghp_`/`api_key=...` | **0 命中** |
| `start.sh` 语法 | `bash -n start.sh` | ✅ 通过 |
| `Dockerfile.quick` 引用的符号 | grep `DEMO_PAPERS` / `Container` / `add_text_corpus` / 侧边栏 demo 逻辑 | `config.py:147` / `services/state.py:65` / `services/library.py:854` / `ui/sidebar.py:431` 全部属实 |
| `.env.example` 存在 | `ls` | ✅ 存在（两个脚本都指向它） |
| 5 分钟指南的按钮文案 | grep 5 个按钮标签 | 5/5 与代码逐字一致 |

### 6.3 我补的部分

- **`资料/评委FAQ.md`（新建，本轮唯一从零写的复现包文件）**：22 条问答，全部带 `文件:行号` 依据，
  每条行号都逐个 `sed -n` 核过（首版有 2 处差 1 行：`ui/sidebar.py:430→431`、`services/state.py:58→59`，已改）。
- 修 `资料/5分钟快速体验指南.md` 一处乱码：`**已/amd有 Python 就用 A / B**` → `**本机有 conda 就用 A / B**`。
- 同文件补一条诚实说明：在线演示地址若还是空白，说明部署未执行完，改用本地三条路径验收。
- README「演示与文档」表补 8 行，指向本轮新增交付物。

---

## 7. 五条验收门禁（全绿）

```
① compileall  core services tools ui app.py config.py     ✅ 通过
② unittest discover -s tests                              ✅ Ran 18 tests, OK
③ scripts/_smoke_ui.py                                    ✅ 9 页面 exception=0
④ scripts/_smoke_read.py                                  ✅ PASS 60 / FAIL 0
⑤ scripts/_check_doc_refs.py                              ✅ 229 条：OK 229 / SUSPECT 0 / UNKNOWN 0
```

> ③④ 未并行跑（Round 12 教训：并行会导致偶发 FAIL）。
> 引用总数从 238 降到 229，是因为附录 D 删了 8 行重复参数、附录 F 表格迁出——**不是丢了引用**。

---

## 8. 诚实风险清单（未修 / 待人工）

| # | 项 | 性质 |
|---|---|---|
| 1 | ~~**在线演示尚未真正部署**~~ → **已部署；两个线上访问问题已定位并修复，待重部署复验**：https://ai4s-research-agent.app.workbuddy.host/ （传输层已实测：`/_stcore/health` 200、首页 Streamlit 壳、`/app/static/mermaid.min.js` 3,335,717B 与本地逐字节一致；但 2026-10-14 浏览器级验收为全白、9/9 页点不到——根因 WebSocket Origin 403 与冷启动模型下载堵首帧均已修复，见 `资料/线上验收报告.md`）。未注入 API Key，默认离线演示态 | 🟡 部分解决（待重部署复验） |
| 2 | `资料/团队信息.md` 与 README「👥 团队信息」仍是占位模板（README 实测 12 行 / 26 个 `【占位符】` + 1 处 `TODO`，团队信息.md 实测 29 行 / 71 个占位 token，两文件合计 98 处） | **待用户填写**（赛题硬性要求，唯一剩余人工项） |
| 3 | git 作者是占位符 `ai_research_agent <author@local.dev>`，88 个提交全是同一作者。若团队信息填多人，评委可能问"协作在哪" | 已知，`资料/提交历史分析.md` 已如实记录 |
| 4 | `.workbuddy/` 目录在磁盘上存在（`du -sh` = **88K**，内部记忆与临时记录），已被 `.gitignore` 忽略不会入库；但**若交付方式是整个目录拷贝而非 clone，评委会看到它** | 已知，建议交付用 clone / 打包时排除 |
| 5 | `requirements.txt` 的 4 条跨平台建议（plotly/arxiv/pillow 加上界、chromadb 在 ARM 上的告警说明）**尚未落地**，文件未改动 | 待决，非阻断 |
| 6 | `--img-max` / `--mermaid-h` 两个旋钮改变了官方出图命令，旧命令会量出 31 页 | 已在 §5.3 与本轮 memory 记录中同步 |
| 7 | 部署包在仓库外（`C:/Users/Administrator/ai4s_deploy_bundle`，精简副本 + 88MB 预置 ONNX 模型后约 96M），**不在 git 里**；若之后要改线上版本，需重新拷贝再发布 | 已知，`start_app.sh`（PORT 兜底 + 模型预置）只存在于部署包中；收编进仓库 `scripts/` 为待办（验收报告 F3） |

---

## 9. 自律声明

- 本轮**未派任何子 agent**（429 未重置，不重派）。
- 所有"已完成"的声明都经磁盘 + 亲自读内容 + 逐条 grep/`sed -n` 核实；两处行号差 1 的错误是这轮抓出来的。
- 所有数量断言（页数 / 引用条数 / 测试数 / 报告份数 / 提交数）均以当次命令输出为准，未沿用旧值。
- `git` 操作由 team-lead 在本轮结束时统一执行，未 `git add -A`。

---

## 10. 补充节 · 在线演示已部署（Round 14 收尾追加）

用户问"GitHub 账号交互授权怎么给你"，先查证再回答：

| 通路 | 实测状态 |
|---|---|
| `git push` | ✅ 通（Windows 凭据管理器，账号 `Through1n`，本轮 push 成功） |
| GitHub 连接器 | ✅ `connected / bound / enabled`，实测 `get_me` → `login: Through1n` |

**两条路都不需要用户再授权。** 但 Streamlit Cloud 那条约不到——它要的是"浏览器里用
GitHub 账号登录 share.streamlit.io"的 OAuth 动作本身，给 agent 任何 token 都替代不了。

### 10.1 改走应用发布，先解决"仓库 919M 传不上去"

整包 919M（`dist/` 498M PyInstaller 产物 + `storage/` 200M 运行时 + `build/` 137M）。
在仓库外建精简部署包（8.2M），只留运行时必需：

```
app.py config.py requirements.txt streamlit_app.py
core/ services/ tools/ ui/          # 四层代码
static/ lib/                        # mermaid.min.js、vis-network、tom-select
演示语料/                          # 3 个演示文件（Transformer v7 / BERT / data.csv）
scripts/                            # ← 关键，见下
```

> **`scripts/` 不能漏**：`ui/page_reading.py:81` 运行时 `from scripts._memory_probe import ...`，
> 漏掉整个目录会在阅读页导入时炸。已 grep 全仓确认这是唯一一处运行时依赖 scripts/ 的地方。

### 10.2 部署前先本地实测（不能只跑 smoke_ui）

在部署包里实跑 `streamlit run app.py --server.port=8599 --server.headless=true`
+ AppTest → **0 异常、演示按钮在位**；测完清掉产生的 `storage/`、`logs/`、`论文样本/` 与 `__pycache__`。

### 10.3 第一次部署失败，原因是 `$PORT` 空串

```
Error: Invalid value for '--server.port' (env var: 'STREAMLIT_SERVER_PORT'): '' is not a valid integer.
```

依赖安装是**成功的**（streamlit 已可用），纯粹是沙箱没把 PORT 注入 startCmd 执行环境。
修法：部署包内加 `start_app.sh`，`PORT="${PORT:-8501}"` 兜底，startCmd 改 `sh start_app.sh`。
**没有改用户项目里任何文件。**

### 10.4 部署后验证（不信工具的 verified，自己探）

| 检查 | 结果 |
|---|---|
| `/_stcore/health` | HTTP 200，0.46s |
| 首页 | HTTP 200，返回真 Streamlit 壳（Apache 许可头 + `<title>Streamlit</title>`） |
| `/app/static/mermaid.min.js` | HTTP 200，**3,335,717B，与本地逐字节相同** → 同时证明整包上传完整 + `--server.enableStaticServing` 生效 |
| 浏览器级首帧 | ~~183s 超时未出现~~ → 双根因（WS Origin 403 + 冷启动下载堵首帧）已修复，判决性测试首帧 **1.4s** 到达；**待重部署后线上复验**（见 `资料/线上验收报告.md` §6） |

**地址**：https://ai4s-research-agent.app.workbuddy.host/

### 10.5 已知边界（如实告知用户）

- 未注入 API Key → 默认离线演示态（点「🚀 一键加载演示数据」全链路可用）；
  ONNX embedding 模型已随发布包预置进镜像缓存目录（chroma 的跳过判据是 6 个文件齐全），
  正常情况下**不发生下载**；即便预置失败，代码层也已改为后台预热 + 有界等待 + 人话降级，
  不再堵死首帧（注意：该模型源站在境内网络"连接建立但 body 传输挂死"，旧版同步下载
  会**永不完成**，"等一会儿就好"的说法不成立，详见 `资料/线上验收报告.md` §6-F2）。
- 这是应用发布服务托管的域名；若赛务更认 streamlit.io，可按 `资料/部署操作手册.md`
  五步另行部署（入口文件与 secrets 模板已就绪）。
- 占位符数量已 grep 后写入文档：`资料/团队信息.md` 29 行 / 71 个占位 token + `README.md`
  12 行 / 26 个 `【占位符】` + 1 处 `TODO` = 98 处，与 `scripts/_check_placeholders.py`
  实测一致（当前退出码 1，全部填完归 0）。初稿写"约 20 处"、round14 正文曾写"13 处"，
  均被后续 grep 实测打回，以 98 处为准。
