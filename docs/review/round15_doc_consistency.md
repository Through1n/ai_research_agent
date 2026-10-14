# Round 15 部署相关文档一致性审查（独立审查岗）

- 日期：2026-10-14（深夜场；**已在 team-lead 浏览器级复核后更新**，见 §3.5 修订记录）
- 审查人：general-purpose-1（只读审查；写操作仅限计数修正，见 §5）
- 范围：README.md、docs/5分钟快速体验指南.md、docs/评委FAQ.md、docs/评委视角自检清单.md、
  docs/部署操作手册.md、docs/在线演示部署指南.md、docs/提交前必办清单.md、docs/团队信息.md、
  docs/review/round14_lead_integration.md（另观察 DEPLOY.md 与仓库外部署包）

---

## 1. 结论速览（截至 team-lead 浏览器级复核后）

| 严重程度 | 条数 | 摘要 | 当前状态 |
|---|---|---|---|
| P0-1 | 12 处表述 | "已部署 / 打开即可用"未加限定 | **开放**——team-lead 统一改（复核通过前我不动） |
| P0-2 | 1 处误导 | 5 分钟指南把"白屏"归因 90MB 下载、建议"等 30-60 秒" | **开放**——team-lead 统一改；根因已更新（见 §4-P0-2） |
| P0-3 | 文档缺口 | 全仓 md 对 Origin/CORS 与冷启动阻塞首帧两个坑零记载 | **开放**——team-lead 统一改 |
| P1-1 | 3 处计数 | "团队信息.md 13 处"过期计数 | ✅ **已修**（round14 ×2 + 提交前必办清单 ×1，均经授权，见 §5） |
| P2 | 2 条建议 | DOM 级验证口径 / 排查表补行 | 开放，随 lead 定稿 |
| 根因结论 | 2 个 | ① WS Origin 403 → **已修复并经浏览器级确认**；② 冷启动下载阻塞首帧 → **当前白屏真因，team-lead 修复中** | 见 §3 |

**核心纠正（本版更新）**：我此前"正在跑的部署用的是旧启动器（mtime 推断）"**不成立，已撤回**；
我此前 WS curl 的 inconclusive 判断也被 lead 的原始 socket 测试取代——**WS 层确实已通**
（三种 Origin 全 101、JS 错误 10→0）。当前白屏是**另一个根因**：冷启动模型下载阻塞首帧。

---

## 2. 事实基准复核

### 2.1 我实测的（传输层 / 本地 / 源码）

| # | 命令 / 动作 | 结果 | 判定 |
|---|---|---|---|
| 1 | `curl /_stcore/host-config` | `allowedOrigins` 16 项全为 streamlit.io/app/run/test 域，无本域名 | 与 lead 基准一致 |
| 2 | `curl /_stcore/health` | 200 | 一致 |
| 3 | `curl 首页 \| wc -c` | 7459B（Streamlit 壳） | 一致 |
| 4 | `curl -I /app/static/mermaid.min.js` | 200，Content-Length 3,335,717，Range 206 正常 | 一致 |
| 5 | 本地 mermaid md5 | 3,335,717B；前 1KB md5 `f8283bcf…` 与远程相同 | 一致（全量下载超时，未做整文件比对） |
| 6 | `ls start.bat start.sh Dockerfile.quick` | 三件套均在 | 一致 |
| 7 | ONNX 模型实测 | 缓存目录 `du -sh` = **167M**；model.onnx = 90,387,606B；S3 压缩包 onnx.tar.gz = 83,178,821B（sha256 钉版） | "约 90MB / 166MB"量级属实（口径见 §3.2） |
| 8 | demo_data | 3 文件在位 | 一致 |
| 9 | streamlit 版本 | 本地 1.63.0；`requirements.txt:1` 钉 `>=1.63,<1.64` | 源码分析可代表线上 |
| 10 | WS 握手 curl（带 Origin） | 本站/恶意 Origin 均 101 | ⚠️ 当时判定 inconclusive，现已被 lead 原始 socket 测试取代（见 §3.1） |

### 2.2 team-lead 浏览器级实测（权威，我无法执行 JS）

- 修复前：`js_errors_total = 10`（WebSocket handshake failed），页面全白。
- 修复后（原始 socket 握手测试 `scripts/_ws_handshake.py`）：三种 Origin（https / http / 无）全 **101**；
  验收脚本 `_accept_online.py`：`js_errors_total = 0`。
- **但页面依然全白**：9/9 页 `clicked=false`、`body_len=4`（仅右上角 "Stop"）、
  `nav_appeared_sec=null`（等 183 秒导航框未出现）、未找到演示数据按钮、0 JS 错误、0 页面错误。

→ **WS-403 已修复且经浏览器级确认；白屏根因迁移到冷启动（§3.2）。**

---

## 3. 根因链复核（源码级）

### 3.1 根因①：WS Origin 403（已修复，浏览器级确认）

校验链（streamlit 1.63.0 三处源码均已读）：

1. `web/server/starlette/starlette_websocket.py:148` `_is_origin_allowed(origin, host)`：
   Host 不在白名单 → False；Origin 为空 → True；**origin_host == host（同源）→ True**；
   其余 → `is_url_from_allowed_origins(origin)`。
2. `web/server/server_util.py:66-68`：`is_url_from_allowed_origins` 首行
   `if not config.get_option("server.enableCORS"): return True`（CORS 关闭时放行一切）。
3. `web/server/starlette/starlette_routes.py:622-631`：host-config 的 `allowedOrigins` 取自
   `client.allowedOrigins` 选项，**与 enableCORS 无关**——host-config 显示默认列表
   **不能**证明"修复未生效"（此点 team-lead 已采纳）。

反代场景 Host 头与浏览器 Origin 不一致 → 同源放行不命中 → 公网域名不在白名单 → 403。
`--server.enableCORS=false` 让第 2 步直接返回 True。**lead 浏览器级数据（JS 错误 10→0、
三种 Origin 全 101）证实修复已生效。**

### 3.2 根因②：冷启动模型下载阻塞首帧（当前白屏真因，team-lead 修复中）

代码链（我已逐行读证）：

```
app.py:86                模块级 ctx = get_container()（Streamlit 整脚本跑完才发首帧）
  → services/state.py:70     Container.__init__ → self.embed_fn = _build_embed_fn()
    → services/state.py:58-59    fn = ONNXMiniLM_L6_V2(); fn(["warmup"])   # 同步触发下载
      → chromadb onnx_mini_lm_l6_v2.py:269  __call__ → _download_model_if_not_exists()
        → :42-45  从 chroma-onnx-models.s3.amazonaws.com 下 onnx.tar.gz（sha256 钉版）
```

- 下载规模口径：**网络传输** = onnx.tar.gz 约 83MB；**解压落盘** = 缓存目录 167M
  （model.onnx 90,387,606B + tokenizer/vocab）。team-lead 观测值 166MB 与解压后目录一致。
- 线上冷缓存时脚本阻塞在这一行 → 一帧都发不出 → 全白 + "Stop" 按钮（Streamlit 的
  "脚本在跑"标识）+ 零 JS 错误（WS 正常）。与 lead 浏览器实测（body_len=4、nav 183 秒不出现、
  0 报错）完全吻合。
- **讽刺点**：warmup 是话术 7 为"问答提速"**故意**加在启动期的
  （`services/state.py:44-52` docstring 写明"把首问的等待挪到启动期"）——本地热缓存是优化，
  线上冷缓存直接把首帧堵死。**话术 21 的冷启动单点与话术 19 的白屏是同一个病**（team-lead 判断，我代码级认同）。

### 3.3 我此前的 WS curl 判定（已被取代，留档说明）

我当时测出"恶意 Origin 也 101"并判定 inconclusive，提出两种可能（边缘代理终结 / 修复已生效）。
现由 lead 原始 socket 测试（三种 Origin 全 101 + JS 错误 10→0）确定为**修复已生效**。
我当时未能解释的原因是：curl 直连公网 host 时 Host 与 Origin 天然同源，
按 `starlette_websocket.py` 同源分支默认放行，该用例本身无鉴别力；恶意 Origin 用例则被
托管边缘行为干扰。教训：**传输层 curl 对 WS 态判定无鉴别力，须以浏览器级数据为准。**

### 3.4 仓库内外的参数分叉（仍然有效的观察）

- `Dockerfile:204/228` 的 `--server.enableCORS=false` 自 round12 提交 `8cb6b03` 起就在；
  `Dockerfile.quick` 的 CMD 自 round14 提交 `3e0f165` 起就在。
- 仓库外部署包的 `start_app.sh` 直到今晚（mtime 23:23:53）才补上该参数；
  该文件**不在 git**（`git ls-files` 0 命中）。
- **结论**：从仓库 Dockerfile 重建的镜像一直是好的；线上实例坏于仓库外启动器与仓库内
  参数长期分叉。team-lead 将把 start_app.sh 收编进仓库 `scripts/` 并在部署包软链，消除分叉。
- **撤回**：我此前据 mtime 推断"正在跑的部署用的是旧启动器"——mtime 推不出运行时内容，
  且 lead 的运行时证据（WS 403→101、JS 10→0）证明当前运行的已是新启动器。该推断不成立。

---

## 4. 问题清单（按严重程度）

### P0-1（严重）12 处"已部署 / 打开即可用"未加限定

| # | 文件:行 | 陈述原文（摘要） |
|---|---|---|
| 1 | README.md:8 | "评审老师打开链接即可直接使用" |
| 2 | README.md:56 | "主用地址…（已部署，开放式访问）" |
| 3 | README.md:58 | "打开即可用" |
| 4 | README.md:350 | 团队信息表"（已部署；见上方路径三）" |
| 5 | docs/5分钟快速体验指南.md:73 | "已部署并验证可达" |
| 6 | docs/5分钟快速体验指南.md:74 | "页面直接就进去了" |
| 7 | docs/评委FAQ.md:19 | 路径 A "0 分钟 / 所有人首选" |
| 8 | docs/评委视角自检清单.md:3、13、17-21 | §0 整节假设页面可加载 |
| 9 | docs/提交前必办清单.md:78-83 | "在线演示已部署并验证" |
| 10 | docs/团队信息.md:22 | "（已部署，开放式访问，无需登录与 Key）" |
| 11 | docs/review/round14_lead_integration.md:190、256-258 | "已部署并验证" + 验证表三项 |
| 12 | docs/在线演示部署指南.md:86 | "评审老师打开链接即可直接使用"（提交材料模板句） |

依据：lead 浏览器实测（全白、0 交互）+ 两个根因的源码链（§3）。注意 round14 三项传输层指标
**全部复现为真**（我复核过），问题是验证口径缺 WS/DOM 级，不足以支撑"可用"结论。
**处置：team-lead 统一改（复核通过前我不动）；口径拟改为"已部署 + 冷启动优化后可用"。**

### P0-2（严重）5 分钟指南的白屏指引错误（根因已更新）

`docs/5分钟快速体验指南.md:75-76`："如果页面空白在转圈：该地址不休眠，慢的原因是首次要点
『加载演示数据』时需下载 chroma 自带 embedding 模型（约 90MB），等 30–60 秒即可"。

- 原判：WS-403 的白屏完全空白、零交互、永久性。**现勘误**：当前白屏是冷启动下载阻塞首帧
  （§3.2）——症状同样是"完全空白、连转圈都没有"，但等待时间由 S3 下载速度决定（83MB，
  慢网可能数分钟），"等 30-60 秒"依然不够，且评委看到空白页没有任何可操作的等待对象。
- 该指引在两个根因下**都是错的**，建议改为"若空白超过 60 秒且侧边栏都出不来，
  按本地三条路径验收"（与 round14:166 兜底句对齐）。**处置：team-lead 统一改。**

### P0-3（重要）全仓 md 对两个坑零记载

`grep -rn "enableCORS\|corsAllowedOrigins\|CORS\|Origin" --include="*.md"` 仅命中
DEPLOY.md:480-548、896（Nginx WebSocket 头，方案 C），未覆盖：① Origin 白名单 403；
② 冷启动下载阻塞首帧。而代码侧 Dockerfile、Dockerfile.quick、start_app.sh、state.py
全有相关参数与注释——**知识在代码里，没进文档**。建议 DEPLOY.md 排查表与
docs/在线演示部署指南.md §6.3 各补两行。**处置：team-lead 统一改。**

### P1-1（事实错误）"团队信息.md 13 处"过期计数——✅ 已修

原 3 处，均按授权改为统一口径（§5）。此处登记留档。

### P2（建议）两条非阻断

1. round14:190/256-258 在冷启动修复重部署后，补一行浏览器 DOM 级验证
   （radio/button 计数 ≥1、nav 出现秒数）再坐实"可用"结论。
2. 验收脚本 `_accept_online.py` 的导航点击选择器在当前 DOM 下未命中
   （9/9 页 clicked=False）——但主因是首帧从未到达，选择器问题是次要；
   team-lead 决定先修首帧，不修选择器。记录备查。

---

## 5. 我的写操作（三处计数修正，均经授权）

统一口径：**README 12 行 / 26 个 `【占位符】` + 1 处 `TODO`；团队信息.md 29 行 / 71 个占位 token；两文件合计 98 处**。

| # | 文件:行 | 改动 |
|---|---|---|
| 1 | docs/review/round14_lead_integration.md:191 | "12 处 + 13 处" → 双口径 98 处（第一轮授权） |
| 2 | docs/review/round14_lead_integration.md:268-269 | 同左（第二轮授权） |
| 3 | docs/提交前必办清单.md:92-93 | 同左（第二轮授权） |

`git diff --stat` = 2 files changed, 6 insertions(+), 5 deletions(-)，无其他任何文件被改动。
P0-1/P0-2/P0-3 涉及的文件我**一律未动**，等 team-lead 定稿。

---

## 6. 复核无误的陈述（"仍然准确"清单，均有实测证据）

| 陈述 | 出处 | 复核证据 |
|---|---|---|
| 首次加载需下载 embedding 约 90MB | README:11、5分钟指南:75-76、round14:265 | model.onnx 90,387,606B；S3 压缩包 83,178,821B；state.py:55-59 |
| "只慢第一次"（热缓存后快） | 同上 | chroma 落盘 `~/.cache/chroma`，本地缓存即实证 |
| mermaid.min.js 3,335,717B 与本地一致 | round14:258、提交前必办清单:82 | 线上 HEAD = 本地 ls = 3,335,717；前 1KB md5 相同 |
| 启动三件套 start.bat / start.sh / Dockerfile.quick | 评委FAQ:20-21 | 三文件均在，FAQ 描述与实测一致 |
| demo_data 3 篇论文 | 评委自检清单:34 | 3 文件在位且 tracked |
| 开放式访问、无登录鉴权、无 Key | README:8-9、部署指南:86 | grep 无鉴权代码；host-config 无鉴权字段 |
| health 200 / 首页壳 | round14:256-257 | 复现：200 / 7459B |
| **start_app.sh 不在 git，但分叉风险真实存在** | 本轮新观察 | `git ls-files` 0 命中；mtime 推断运行时**已撤回**（§3.4），但"仓库外启动器与仓库内 Dockerfile 参数不一致"这一分叉事实成立（round12 起仓库侧就带 enableCORS，仓库外直到今晚才补）。team-lead 将收编进 `scripts/` 并软链消除分叉 |

---

## 7. 待决 / 跟进事项（状态更新）

1. ~~重部署后浏览器级复核~~ → **已完成**（team-lead）：WS 已通（101 ×3、JS 0 错误），
   白屏根因迁移至冷启动（§3.2）。
2. ~~授权扩修两处"13 处"~~ → **已授权并执行**（§5 三处）。
3. P0-1/P0-2/P0-3 表述定稿 → **team-lead 统一改**，口径"已部署 + 冷启动优化后可用"；
   复核通过前我不动任何相关文件。
4. start_app.sh 收编 → team-lead 处理（§3.4）。
5. 冷启动修复（team-lead 进行中）：建议方向（未审其代码，仅从源码链推测）——
   把 `fn(["warmup"])` 移出 `Container.__init__` 的同步路径（懒加载 / 后台线程预热），
   或镜像内预置模型缓存目录。**是否可行由 team-lead 判断。**
