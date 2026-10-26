# 工程手册 · 设计系统 / 桌面版 / 交付录制

> 2026-10 沉淀。`MEMORY.md` 只留硬约束指针，细节在此，便于查阅与复用。

---

## 一、设计系统（Round 21 西式简洁收口）

### 1.1 令牌
- **唯一真源 `ui/design.py`**：色板/字阶/间距/圆角全部令牌化。
- `.streamlit/config.toml` 的三个中性值须与之一致。
- **强调色恒为 `#1f5fae`**（测试与契约钉死，禁换）。
- **间距必须落 {0,4,8,12,16,24,32,40,48}**；**圆角全站唯一值**。
- 界面层**严禁第三色相**。

### 1.2 文档面豁免
- 阅读页 PDF 文档面保留荧光笔语义色与 Times 衬线（属"文档内容"，不是界面层）。
- Chrome UA 给 button/input 默认字体是 Arial——不显式覆盖 `font-family` 就混入第二种字体。

### 1.3 iframe 孤岛
- **CSS 变量穿不透 `components.v1.html` 的 srcdoc iframe**。
- 孤岛文档必须自带迷你 `:root` 副本，每个属性写成「变量名 + 兜底字面量」。

### 1.4 st.pills 选择器
- **st.pills 在 1.63 渲染为 stButtonGroup**：`.stPills` 类已不存在，
  选择器用 `[data-testid="stBaseButton-pillsActive"]`。

### 1.5 首屏减法口径（每页统一）
```
页头（无装饰 emoji）
  → 一行状态 caption
    → 一个主操作
      → 其余收进默认收起的 expander
```
- `st.subheader` 全站归零。
- `st.info` 每页 ≤1（非阻断信息降级为 `st.caption`）。
- **Streamlit 原生提示条是第二个蓝**（info 默认蓝与 #1f5fae 撞色）⇒ info 统一中性底 + 3px 强调左标；success/warning/error 保留语义色。

### 1.6 emoji 纪律
- **装饰性 emoji 是「杂乱」的最大单一来源**（侧栏 20 → 0）。
- **保留**语义状态符：✅⚠️❌🛠💡📭 与 `empty_state_card` 徽章。
- **去掉**页头/分区标题/按钮/导航上的装饰。
- **`icon=` 与文案自带 emoji 会叠成「🤖 🤖」**：`page_header`/`section_header` 由 `_head_text()` 兜住（忽略 U+FE0F）；`st.info/success/warning/error` 没有兜底，调用点自己别重复。

### 1.7 分区标题
- **只能走 `section_header()`**：`st.subheader` 是 22px，会绕过 18px 字阶，造成一页两套层级（主题门禁 A2 静态扫残留）。
- 搬文案前去掉 `**加粗**`（`_esc` 之后 markdown 不生效）。

### 1.8 文字上色
- **禁用 `inherit`**：Streamlit 在 label 与文字之间还有一层自带正文色的容器，inherit 只往上取一层 → 取到正文黑。
- 写显式状态色（`--c-text-2` / `--c-text` / `--c-accent`）。

### 1.9 杂乱度度量
- **「屏数」会骗人**：折叠区与未激活 tab 不进 `scrollHeight`。
- 主指标用 `scripts/_measure_clutter.py` 的可见元素数 / 文字数 / 色块数 / 控件数。
- 对照图存 `资料/截图/_before/`、`资料/截图/_after/`。

### 1.10 品牌徽标
- **唯一生成器 `品牌资源/make_logo.py`**：令牌与几何都在文件顶部，改徽标只改它再重跑，**不手改 png/ico**。
- **三处同源** = exe 内嵌图标 / 页面 favicon（`app.py:_page_icon` → `config.brand_logo`）/ 侧栏品牌区+徽标区块。
- **三档光学尺寸**：≥64 细模 / 32–63 中模 / <32 剪影模（stroke×0.105）。
  线性缩放必糊，三档是 16px 可读的关键。
- **框内图片必须 data URI**（`ui/common.brand_logo_data_uri`）——`st.image` 是顶层元素，会跑出有界框。
- 徽标卡 `.ai-brand-badge` 是全站唯一允许滚动条的构件，验真 `scripts/_verify_brand_badge.py`。
- 规范见 `资料/brand_logo_design.md`。
- **exe 图标验真用字节比对**：ico 各 PNG 帧 `in exe_bytes`，7/7 才算数。

---

## 二、桌面版与用户环境

### 2.1 打包链
- `build_desktop.bat`（**纯 ASCII**）→ `dist\AI4SResearchAgent\`。
- 验真：`scripts/_verify_dist_desktop.py` + `scripts/_verify_dist_deep.py`
  （两者都靠**临时 APPDATA** 才算全新安装）。
- `dist/` 已 gitignore。

### 2.2 ensure_app_copy 机制缺陷（已根治）
- **症状**：「重打包了但界面还是旧的」。
- **根因**：老实现只判「app.py 存在吗」→ 存在就 return，跑的却是 `%APPDATA%` 里几个月前那份。
- **现逻辑**（用 `.build_id` 比新旧）：
  - bundle 无戳 → 不动；
  - bundle 有戳 + 已装无戳 → **替换**；
  - 两边有戳 → 仅 bundle > 已装才替换（保护更新的热更新版本）。
- 备份留在 `app.backup-<时间>`，替换失败不阻断启动。
- 守门：`tests/test_desktop_app_copy.py`（8 例）+ `scripts/_verify_dist_refresh.py`。

### 2.3 version_info.txt 必须入库
- 曾被 .gitignore 忽略，但 spec 硬依赖 `version="version_info.txt"` ⇒ 新克隆打不出桌面版。
- 现在 .gitignore 里有显式 `!version_info.txt` 例外。

### 2.4 批处理编码
- **UTF-8 批处理 + `chcp 65001` + 中文 = cmd 会把注释尾巴当命令执行**（碎片随行拆分落点变，可能是任意子串）。
- **加 UTF-8 BOM 不是解药**；存 GBK 换成「非 936 环境全乱码」。
- **选最小风险：整份纯 ASCII**（`启动科研助手.bat`、`build_desktop.bat` 已 ASCII 化；`start.bat` 仍中文，保留噪音）。
- 目录中文化**不会**把中文打进这两个 ASCII 批处理（它们只引用 `app.py` / `build` / `dist`）。

### 2.5 端口探测
- **别用 `SO_REUSEADDR` 探占用**：Windows 允许抢绑已监听端口 → 永远返回「可用」。
- 正确顺序：先 `connect_ex` 真连、再裸 bind。

### 2.6 启动与重启
- 用户说「启动桌面版」= 项目根 `启动桌面版.vbs`（pythonw -m desktop，pywebview + WebView2）。
- **agent 侧复现 ≠ 用户侧环境**：agent 子进程在宿主 Job 内，browser 早期初始化被拦。
- **桌面版改代码后须重启桌面版生效**。

### 2.7 WebView2 白屏排查链
1. Crashpad dmp
2. watson_metadata
3. 退出码
4. monkey-patch `on_webview_ready`

**已知根因**：GPU 不可用且无软件回退 ⇒ 设
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--disable-gpu`。

### 2.8 CSS visibility 陷阱
- `visibility:hidden` 沿继承链埋掉后代控件。
- 放行用后代 `visibility:visible!important`（改 `display:none` 静默失效）。
- 外观修复验证范式：无头 Chromium 加载真实渲染产物断言计算样式，**并用旧规则反向复现 bug**。

---

## 三、交付材料与录制口径

### 3.1 四份脚本各司其职
| 文件 | 用途 | 时长 |
|---|---|---|
| `资料/演示视频脚本.md` | 决赛录屏（P0–P11 翻车预案） | 8:00 |
| `资料/演示彩排脚本.md` | 现场真人彩排 | 7:30 |
| `资料/预指导环节介绍.md` | 预指导底稿（目的/适用范围/核心要点/操作步骤） | — |
| `资料/预指导视频脚本.md` | 预指导录屏（7 分镜，顶部「★ 速查卡」可单独打印） | 3:30 |

### 3.2 录制红线只能继承不能自创
来源：`资料/录制工程化脚本.md` 第四节 R1–R14。

- **R10**：全片禁终端 / Key 输入框 / .env 入画 ⇒「可复现的数字」只能靠**旁白**讲。
- **R7**：正片禁点「🎭 演示模式示意图谱」兜底 ⇒ 预指导视频不讲图谱
  （现场讲解可点但须口播「示意非真实引用边」）。
- **R6**：双栏判定必须双栏，**BERT 正文双栏在第 3 页**（第 1 页是单栏）。

### 3.3 旁白字数必须实算，且「逐格」不只「逐段」
- 估的字数偏 5–10%。
- 流程：抓表尾列 → 去 md 标记 → 中文字符数 + 英文单词数 → 与「该格时长 × 220 字/分」比。
- **逐段合格不代表逐格合格**（曾 7 段全合格却有 7 格超限，最紧 325 字/分）。
- 修法顺序：先在**场内重排各格秒数** → 仍不够再最小化改措辞 → 改完把**标称值改成实算值**。

### 3.4 界面文案必须现查代码
- 把文档里「」引号内的短文案**批量与 `ui/*.py` + `config.py` 比对**，比逐条肉眼查快且不漏
  （一次性查出 4 处偏差）。
- **继承既有文档的判据也要复核**（📄 前缀是从旧脚本继承来的错误）。
- 写演示类文档必须用当前真实按钮文案。

### 3.5 对外材料的内容口径
- **不得引用旧审计快照的完成度百分比**（如 `资料/audit_report_20261010.md` 里的 67%）。
  改用**当前可核实的工程事实**（如「22 个工具全部接入 6 专家白名单、无孤岛」）。
- 第六章如实写「无获奖、无发表」，第七章如实写「非他人项目改造」——
  不得为凑格式编造获奖或优化点。
