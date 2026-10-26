# 复盘 · Office（docx/pdf）文档流水线

> 2026-10-26 实测沉淀。`MEMORY.md` 只留硬约束指针，细节在此。
> 成品：`资料/项目介绍文档.docx` / `资料/项目介绍文档.pdf`（七段式项目介绍）。
> 可复用脚本：`scripts/_build_intro_doc.py`（图片裁剪 + data URI 内嵌）、
> `scripts/_fix_docx_pagesize.py`（补 sectPr 的 pgSz/pgMar）、
> `scripts/_docx_to_pdf.ps1`（Word COM 导出 PDF）。

## 1. 流水线结构（三段）

| 阶段 | 承担者 | 产物 | 纪律 |
|---|---|---|---|
| S1 | `doc-writer`（Expert 路由） | `stage1/final_draft.md` | 纯 markdown，不排版 |
| S2 | `doc-formatter`（design-token → doc-typeset → html-review） | `stage2/*.html` | **恒产 html**，不直接产 docx |
| S3 | `doc-converter`（`html-to-doc/scripts/convert_doc.py`） | `*.docx` + `present_files` | 中间产物一律不得 present |

- 图片必须落在 `stage2/images/`，HTML 里用相对路径 `images/xxx.png`。
- S2 结束前必须跑三道门禁：`html-review`（6 维加权，安全项一票否决，composite ≥ 80）、
  `preflight_check.py`（PF-01 有 `<style>`、PF-02 `@page` 须在真实规则中、PF-03 CSS 类须有元素挂载）、
  `verify_output.py`（S-1/S-2 TOC 域 + PAGEREF、S-3/S-7/S-8 分节、S-4/S-5 页码重启与节边界）。
- S3 之后还有一道**引擎层修补**（见第 4 节），以及**文档级**修补（见第 6 节）。

## 2. doc-typeset 版式硬契约（违反即门禁红）

- 正文 `font-family` 第一项必须是 `"SimSun"` / `"宋体"`，字号 12pt（16px），`text-indent: 2em`。
- 章节标题必须用 h1~h3 语义标签（不能只挂 class）。
- 目录用 `nav.doc-toc` **独占一个** `<section role="cover">`。
- 封面标题禁用 h1~h6（否则会被 TOC 收进去）。
- 页面级规则必须写成 CSS `@page`；**CSS 注释里不能出现 `@page` 字样**（PF-02 会误判）。
- `section[role=cover]{page:cover}` + 正文 `data-page-restart="1"` ⇒ 封面无页脚、正文页码重起。
- base.md 编号映射：h2 → 「一、二、三…」，h3 → 「（一）（二）…」。
  本项目七章定为 h2（带「一、」～「七、」）、子节定为 h3（带「（一）」～「（七）」），
  既符合映射又规避 TQ-04「文档出现多个 h1」。

## 3. html-review 6 维门禁（本次全踩过）

权重：design_token_compliance 25% / structural_integrity 25% / typographic_quality 20% /
genre_fit 20% / decoration_usage 10%；安全项一票否决，composite ≥ 80 且各维 passed 才过。

| ID | 规则 | 本次踩坑与修法 |
|---|---|---|
| DT-03 | margin/padding 禁裸间距值 | 12 处 `0.8em`/`0.5em`/`1.5em`/`0.2em`/`2em`/`0.45em 0.6em`… ⇒ 全部抽成 `--spacing-*` 变量 |
| SI-02 | 每表须有 thead+tbody | — |
| SI-04 | `<p>` 内禁嵌块级元素 | — |
| SI-05 | `<img>` 须有非空 alt | — |
| SI-09 | `--require-page-furniture` 时须有 `@page` | — |
| TQ-04 | h1 只应出现 1 个 | 章节原用 h1（9 个）⇒ 全改 h2、子节 h3 |
| TQ-06 | 正文 `<p>` 字号 token 须统一 | — |
| SC-06 | **外部 URL 做 `href` 会一票否决** | — |
| PF-03 | CSS 类须有元素挂载，否则报孤岛 | `cover-logo` 孤岛 ⇒ 封面真的插入品牌徽标图（`images/cover_logo.png`） |

**首轮 76 分 → 修完 100 分 / 0 issue**。

## 4. 引擎（`doc_insert_html_content`）三处隐性缺陷

引擎只吃一个 `html_text` 字符串，没有 base path，也没有错误提示：

1. **相对路径图片被静默丢弃**
   `<img src="images/x.png">` 不报错，但 `word/document.xml` 里 `drawing`/`blip` 数为 0，
   只剩图注文字。**正解：data URI 内嵌**（`engine-capabilities` 明列支持 dataURI）。
   验证方式：解包 docx 数 `word/media/image*.png` 与 `w:drawing` / `a:blip` 数量。
   本次 8 张图全部嵌入（drawing=8, blip=8）。

2. **`<figure>/<figcaption>` 段落边界错位**
   在第 7 个 figure 处，引擎把**下一个 h2 的文本并进图 7 的 figcaption 段落**并给它套 Heading 2，
   后果是：目录误收图注、正文丢「六、项目之前是否发表过或获奖」标题。
   诊断手段：解包 docx，数 `w:pStyle val="000005"`（heading 2）段数与内容是否对得上。
   **规避：改用 `<div class="figure">` + `<p class="figure-caption">`**，走通用块级路径。
   修复后 heading 2 恰为 10 段且全部正确。

3. **正文节 body 级 sectPr 漏写 pgSz**
   引擎给前 2 个 sectPr 写了 A4（11906×16838），正文节那份漏写 ⇒ Word 回退默认 Letter（612×792pt）
   ⇒ PDF 前 2 页 A4、后 28 页 Letter 混排。
   **修法**：解包 docx 给**所有** sectPr 补 `pgSz`/`pgMar`，插入位置必须按 OOXML schema 顺序
   （在 `pgMar`/`pgNumType`/`cols` 之前）。修后 3 节全部 A4。

4. **目录域范围与标题层级错位 ⇒ 目录被清空**（本轮最隐蔽的一个）
   引擎把 HTML 的 `<h2>` 映射成样式 "heading 2"（`outlineLvl=1`，即 Word 第 2 级），
   `<h3>` 映射成 "heading 3"（`outlineLvl=2`）；但它生成的目录域是 `TOC \o "1-1"`，
   **只收第 1 级**——文档里一个第 1 级都没有 ⇒ Word 更新域后目录被清空，
   PDF 目录页只剩一行「未找到目录项。」。
   **症状识别**：导出 PDF 后先读第 2 页文本，搜「未找到目录项」这五个字。
   **修法**（`scripts/_fix_docx_toc.py`）：把章节标题提到 `outlineLvl=0`、子节提到 `outlineLvl=1`，
   并把 styleId 换成内置 `Heading1`/`Heading2`、样式名同步改成 "heading 1"/"heading 2"
   ——**「styleId ↔ 样式名 ↔ outline 级别 ↔ 段落级 outlineLvl」四者必须自洽**，
   否则 Word 打开时会按内置样式规范把级别拉回去。
   效果：`\o "1-1"` 正好收 10 个章节标题（一级），30 个子节不进目录，
   与设计口径「目录只列一级」一致。
   **实测过但没采用的替代方案**：只把域改成 `\o "1-2"` 也能出条目，
   但条目会落到 TOC 2 样式，缩进与点线长度都变，与版式设计不符（像素对比可见）。

5. **目录制表位按 Letter 推导，与 A4 版心不符**
   引擎写的右对齐点线制表位是 `w:pos="9072"`（Letter 正文宽推导值），
   而本文档页宽 11906、左右边距各 1800 ⇒ 版心宽仅 **8306 twips**，
   页码会被顶到右边距之外。
   **修法**：从最后一个 sectPr 的 `pgSz`/`pgMar` **实算**版心宽，
   把 toc 1 样式与缓存目录条目里的制表位改成该值。

## 4b. `.ps1` 脚本必须纯 ASCII（与 `.bat` 同源教训）
Windows PowerShell 5.1 读 `.ps1` 时，**没有 UTF-8 BOM 就按 ANSI 读**，
非 ASCII 注释会被拆成乱码 token → `ParserError: UnexpectedToken`（"catch { }"
空 catch 块甚至直接解析失败）。
**结论：`.ps1` 一律整份纯 ASCII**（注释也用英文）。
这与中文 `.bat` 的教训是同一类：**编码不是"显示问题"，是"执行语义问题"**。


## 5. TOC 域范围决定目录层级

引擎生成 `TOC \o "1-2" \h \z \u` ⇒ **只收 heading 1-2**。
h3（outlineLvl 2）超出范围，Word 更新域时会被直接丢弃 ⇒ 目录里二级条目消失。
**结论：目录统一只列一级**（本次 10 条，含「执行摘要」），与域范围保持一致。
另：`updateFields` 写在 `word/settings.xml`（不在 `styles.xml`），初查 styles.xml 会误判为 False。

## 6. 文档级修补（DOCX → PDF）

- **Word COM 导出 PDF**（本机装了 Office，无 LibreOffice）：
  ```powershell
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false; $word.DisplayAlerts = 0
  $doc = $word.Documents.Open($docxPath, $false, $true)
  foreach ($story in $doc.StoryRanges) { $null = $story.Fields.Update() }
  if ($doc.TablesOfContents.Count -gt 0) { $doc.TablesOfContents.Item(1).Update() }
  $doc.Repaginate()
  $doc.ExportAsFixedFormat($pdfPath, 17, ...)
  $pages = $doc.ComputeStatistics(2)
  ```
  保真度最高：目录页码、页码域、图片全部正确。
- **页面尺寸分布必须验**：用 `get_text("dict")`/PyMuPDF 统计每页 bbox 尺寸集合，
  出现 `{(595,842): 2, (612,792): 28}` 这类混合就是漏了 pgSz。
  本次修完为 `{(595,842): 27}`（27 页全 A4）。
- **图片总数要数**：PDF 内嵌图数应与 HTML 内 `<img>` 数一致（本次 8 = 8）。

## 7. 本次交付物自检结果

| 项 | 结果 |
|---|---|
| `资料/项目介绍文档.docx` | 2 168 176 B；8 图内嵌（drawing=8）；342 段落 0 缺失；TOC 域 10 个 PAGEREF；封面/目录分节 ✓；正文页码重起 ✓；heading 样式绑定 40/40；3 节全 A4；`updateFields` 已置位 |
| `资料/项目介绍文档.pdf` | 1 094 625 B；**27 页**；页面尺寸 `{(595,842): 27}`；总图数 8；目录 10 条，页码与实际章节位置**逐条一致** |
| Office 官方 `verify_output.py --require-toc` | `passed: true`，`issues: []` |

## 7b. 一键构建（`scripts/_build_intro_docx.py`）
```
python scripts/_build_intro_docx.py --docx 资料/项目介绍文档.docx --pdf 资料/项目介绍文档.pdf
```
做三件事：**补 pgSz/pgMar** → **修目录域与制表位** → **Word COM 导出 PDF** → **硬门禁自检**。

自检项（任一不过即 exit 非 0）：
- PDF 第 2 页**不得**出现「未找到目录项」；
- 目录条目数必须 = 10；
- **目录页码必须与章节实际所在页逐条一致**（封面=P1、目录=P2、正文从 P3 起页码重起为 1 ⇒ 正文页码 = PDF 页序 − 2）；
- 页面尺寸必须全为 `(595,842)`；
- DOCX 的 media / drawing / PDF 图片数三者相等；
- 标题层级自洽（heading1 + heading2 + 段落级 `outlineLvl=0` 恰 10 个）；
- 每个 sectPr 都有 A4 pgSz。

**反向验证（硬纪律：门禁变绿可能是"测试看不见"）**：
人工构造两种坏产物直接调用 `verify()`，确认都能翻红——
① DOCX 章节 `outlineLvl` 提回 1 ⇒ 「标题层级不自洽」FAIL；
② PDF 目录页换成「未找到目录项」⇒ 「目录域收不到条目」+「条目数 0 ≠ 10」FAIL。


## 8. 内容口径（对外材料）

- **不得引用旧审计快照的完成度百分比**。`资料/audit_report_20261010.md` 里的
  「自主工具调用 6/9 项 = 67%」是历史快照，不代表当前状态；
  改用**当前可核实的工程事实**（如「22 个工具全部接入 6 专家白名单、无孤岛」）。
- 第六章如实写「无获奖、无发表」、第七章如实写「非他人项目改造」——
  不得为凑格式编造获奖或优化点。
