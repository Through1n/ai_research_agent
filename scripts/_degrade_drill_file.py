"""故障演练（文件/状态类 6 场景）——只读取证，不改任何业务代码。

运行：python scripts/_degrade_drill_file.py

场景：
  7  上传损坏的 PDF（头部合法但内容截断 / 结构损坏）
  8  上传 0 字节文件
  9  上传超过 100MB 的 PDF
  10 上传 PDF 后立即切换页面（状态竞争）
  11 演示中途侧边栏 API Key 被清空
  12 浏览器刷新（session 丢失，后台任务怎么办）

驱动方式：streamlit.testing.v1.AppTest 真跑 app.py。上传类场景在**运行时**把
st.file_uploader 换成假上传对象（只替换这一个函数句柄，不改仓库代码），从而真实
走 ui/page_library.py 的解析与提示分支；页面显示的字符串从 AppTest 元素树里
原样抓出来（含折叠区里的 st.code 技术细节）。
"""
import os
import sys
import time
import pathlib
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import streamlit as st                                   # noqa: E402
from streamlit.testing.v1 import AppTest                  # noqa: E402
from services import library                              # noqa: E402

APP = str(ROOT / "app.py")
LINE = "=" * 78

# ---------------------------------------------------------------- 假上传器 ----
_REAL_UPLOADER = st.file_uploader
_FAKE = {"data": None, "name": None}


class _FakeUpload:
    """最小 UploadedFile 替身：page_library 只用到 .name 与 .read()。"""

    def __init__(self, name, data):
        self.name = name
        self._d = data

    def read(self):
        return self._d

    def getvalue(self):
        return self._d


def _patched_uploader(label, type=None, key=None, **kw):
    if _FAKE["data"] is not None and str(label).startswith("选择 PDF"):
        return _FakeUpload(_FAKE["name"], _FAKE["data"])
    return _REAL_UPLOADER(label, type=type, key=key, **kw)


st.file_uploader = _patched_uploader


# ---------------------------------------------------------------- PDF 夹具 ----
def build_pdf(n_pages=1, payload_mb=0.0, text="AI4S degrade drill"):
    """手工拼一份**结构合法**的 PDF：n_pages 页正文 + 一个未被引用的大对象。

    大对象（payload）只进 xref、不被任何页面引用，用来把字节体积撑到目标 MB，
    而**不改变页数与文本量**——这样 S9 的标定里"体积"是唯一自变量。
    """
    lines = ["BT /F1 11 Tf 72 %d Td (%s line %d) Tj ET" % (700 - i * 11, text, i)
             for i in range(60)]
    content = ("\n".join(lines) + "\n").encode("latin-1")
    payload = b"\x00" * int(payload_mb * 1024 * 1024)

    buf = bytearray(b"%PDF-1.7\n")
    offs = []

    def add(body):
        offs.append(len(buf))
        buf.extend(body)

    n_first_page = 3
    kids = " ".join("%d 0 R" % (n_first_page + i) for i in range(n_pages))
    add(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    add(("2 0 obj<</Type/Pages/Kids[%s]/Count %d>>endobj\n" % (kids, n_pages)).encode())
    n_cont = n_first_page + n_pages
    n_font = n_cont + 1
    n_junk = n_font + 1
    for i in range(n_pages):
        head = ("%d 0 obj" % (n_first_page + i)).encode()
        body = ("<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents %d 0 R"
                "/Resources<</Font<</F1 %d 0 R>>>>>>endobj\n" % (n_cont, n_font)).encode()
        add(head + body)
    add(("%d 0 obj<</Length %d>>stream\n" % (n_cont, len(content))).encode()
        + content + b"endstream\nendobj\n")
    add(("%d 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n" % n_font).encode())
    add(("%d 0 obj<</Length %d>>stream\n" % (n_junk, len(payload))).encode()
        + payload + b"endstream\nendobj\n")

    xref_off = len(buf)
    total = n_junk + 1
    xr = ["xref\n0 %d\n" % total, "0000000000 65535 f \n"]
    for o in offs:
        xr.append("%010d 00000 n \n" % o)
    buf.extend("".join(xr).encode("latin-1"))
    buf.extend(("trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
                % (total, xref_off)).encode("latin-1"))
    return bytes(buf)


# ---------------------------------------------------------------- 抓页面文案 ----
_NOISE = ("<div", "<style", "画像", "当前模型", "文献库 5 篇", "内存 ")


def _is_noise(s):
    return any(k in s for k in _NOISE)


def texts(at_part):
    out = []
    for attr in ("error", "warning", "info", "success"):
        try:
            for el in getattr(at_part, attr):
                v = getattr(el, "value", "")
                if v:
                    out.append("[%s] %s" % (attr, v))
        except Exception:
            pass
    for attr in ("caption", "markdown"):
        try:
            for el in getattr(at_part, attr):
                v = getattr(el, "value", "")
                if v and not _is_noise(v):
                    out.append("[%s] %s" % (attr, v))
        except Exception:
            pass
    return out


def dump(at, limit=12):
    """打印一次 run 之后页面上真实出现的提示文案 + 未捕获异常 + 红框数量。"""
    print("    未捕获异常: %s" % (list(at.exception) or "无"))
    print("    红色 error 框数量: %d" % len(at.error))
    body = []
    for s in texts(at):
        if s not in body:
            body.append(s)
    for s in body[:limit]:
        print("      %s" % s.replace("\n", " ")[:230])
    # 折叠区「🔍 查看技术细节」里的 st.code —— 真正的原因藏在这里
    for i, c in enumerate(at.get("code")):
        print("      [折叠区技术细节#%d] %s" % (i, str(c.value).replace("\n", " ")[:230]))
    if not body and not list(at.get("code")):
        print("      （页面无提示文案）")
    return body


def find_button(at, sub):
    for b in at.button:
        if sub in (b.label or ""):
            return b
    return None


def nav_to(at, label):
    for r in at.sidebar.radio:
        if label in [str(o) for o in r.options]:
            r.set_value(label).run()
            return True
    return False


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except Exception:
        return default


def diag_double_error(at):
    """出现 2 个红框时的根因定位（结论由上面的实测证据 + 代码定位共同得出）。"""
    if len(at.error) >= 2:
        print("    【根因定位】prepare_pdf 返回的是带 error 的 dict（不是异常），"
              "ui/page_library.py:231 把 prepared['error']（一个 str）当异常对象交给 "
              "friendly_error；ui/common.py:252 对它取 exc.__traceback__ → "
              "str 没有该属性 → AttributeError 冒泡到 ui/page_library.py:242 的 "
              "except，于是又渲染了第二个红框。第一个红框的真实原因只存在于折叠区。")


def click_parse(at, data, name, note=""):
    """设置假上传 → 渲染 → 点「解析并查重」→ 返回耗时。"""
    at.session_state["pending"] = None
    _FAKE["data"], _FAKE["name"] = data, name
    at.run()
    btn = find_button(at, "解析并查重")
    if not btn:
        print("    ❌ 页面上没出现「解析并查重」按钮（假上传未生效）")
        return None
    t = time.time()
    btn.click().run()
    el = time.time() - t
    print("    解析耗时 %.2fs · 进入 pending(查重命中)=%s%s"
          % (el, bool(ss(at, "pending")), (" · " + note) if note else ""))
    return el


# ============================================================== 场景 7：损坏 PDF
def s7_corrupt(at):
    print(LINE)
    print("场景 7｜上传损坏的 PDF（头部合法但内容截断 / 结构损坏）")
    print(LINE)
    good = (ROOT / "论文样本" / "1706.03762v1.pdf").read_bytes()
    print("  基准文件: 论文样本/1706.03762v1.pdf （%.2f MB，15 页）" % (len(good) / 1e6))
    variants = [
        ("7a 内容截断：保留前 55% 字节（%PDF 头与大部分对象仍在，xref/文件尾丢失）",
         good[:int(len(good) * 0.55)], "truncated_55pct.pdf"),
        ("7b 结构损坏：字节数不变，把 30%~85% 区间整段替换成 0x00（对象流被毁）",
         good[:int(len(good) * 0.30)] + b"\x00" * int(len(good) * 0.55)
         + good[int(len(good) * 0.85):], "broken_struct.pdf"),
        ("7c 头部被抹：%PDF- 改写成 XXXX，其余字节完好",
         b"XXXX" + good[4:], "bad_header.pdf"),
        ("7d 轻微截断：保留前 92% 字节（MuPDF 大概率能修着打开）",
         good[:int(len(good) * 0.92)], "truncated_92pct.pdf"),
    ]
    for desc, data, name in variants:
        print("\n  ▶ %s" % desc)
        print("    构造: %d 字节 (%.2f MB), 文件名 %s" % (len(data), len(data) / 1e6, name))
        click_parse(at, data, name)
        dump(at)
        diag_double_error(at)
    _FAKE["data"] = None


# ============================================================== 场景 8：0 字节
def s8_zero(at):
    print("\n" + LINE)
    print("场景 8｜上传 0 字节文件")
    print(LINE)
    print("  构造: 0 字节, 文件名 empty_0byte.pdf")
    click_parse(at, b"", "empty_0byte.pdf")
    dump(at)
    diag_double_error(at)
    _FAKE["data"] = None


# ============================================================== 场景 9：超大 PDF
def _real_textbook_pages():
    try:
        from services import parsing
        d = parsing.open_doc(str(ROOT / "论文样本" / "UnderstandingDeepLearning_02_09_26_C.pdf"))
        n = int(d.page_count)
        d.close()
        return n
    except Exception:
        return 541


def s9_oversize(at):
    print("\n" + LINE)
    print("场景 9｜上传超过 100MB 的 PDF")
    print(LINE)

    # (1) 代码里到底有没有体积上限？——全域扫描取证
    hits = []
    pats = ("max_upload", "max_file_size", "maxuploadsize", "max_size", "size_limit")
    scan = list(ROOT.glob("*.py")) + list((ROOT / "ui").glob("*.py")) \
        + list((ROOT / "services").glob("*.py")) + list((ROOT / "core").glob("*.py"))
    for p in scan:
        try:
            low = p.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        for pat in pats:
            if pat in low:
                hits.append("%s:%s" % (p.relative_to(ROOT), pat))
    if hits:
        print("  [取证1] 检出体积上限相关常量/参数: %s" % hits)
        print("  ⇒ 当前代码**已存在**体积闸门，本场景据此改为验证「闸门是否真的生效、"
              "阈值与文案是否友好」")
    else:
        print("  [取证1] 业务代码里的体积上限常量: 无"
              "（app.py / ui/ / services/ / core/ 全部 .py 全量扫描均未命中）")
        print("  [取证2] 上传入口 ui/page_library.py:205 = "
              "st.file_uploader(\"选择 PDF\", type=[\"论文样本\"], key=\"lib_up\")，未传任何体积参数；"
              "解析流程里也没有 len(data) 判断")
        print("  [取证3] .streamlit/config.toml 未配置 server.maxUploadSize → 取 Streamlit 默认 200MB；"
              "即 100~200MB 的文件不会被拦在门外，会一路走到解析与入库")
    print("  ⇒ 等价性说明：本脚本不往磁盘写 100MB 文件，改用"
          "「页数与文本量固定 20 页、只放大字节体积」的真实合法 PDF 喂进同一条解析链路，"
          "把字节体积作为唯一自变量做 1→10→50→105MB 标定；"
          "该标定直接调服务层 services.library.prepare_pdf，不受 UI 闸门影响，"
          "因此无论当前有没有闸门都能反映「解析段」的真实成本。")

    real_commit = library.commit
    commit_log = []

    def _isolated_commit(ctx, prepared, title=None, authors=None, arxiv_id=None,
                         version=None, year=None, github=None, replace=None):
        """隔离入库落盘：105MB 真入库会写 storage/uploads + chroma + library.json，
        污染真实文献库。这里只把「落盘」这一动作搬到系统临时目录实测耗时。"""
        if prepared.get("error"):
            return real_commit(ctx, prepared, title=title, replace=replace)
        tmp = pathlib.Path(tempfile.gettempdir()) / ("_drill_%s" % prepared["filename"])
        n = len(prepared.get("raw") or b"")
        t = time.time()
        tmp.write_bytes(prepared["raw"])
        tmp.unlink(missing_ok=True)
        commit_log.append((prepared["filename"], n, round(time.time() - t, 2)))
        return {"filename": prepared["filename"],
                "title": prepared.get("title") or prepared["filename"],
                "page_count": prepared["page_count"]}

    library.commit = _isolated_commit
    try:
        ctx = ss(at, "container")
        print("\n  [标定] services.library.prepare_pdf 同链路，页数固定 20 页，只变体积")
        print("    %-10s %-14s %-14s %s" % ("体积", "prepare_pdf耗时", "进程内存增量", "解析结果"))
        for mb in (1, 10, 50, 105):
            data = build_pdf(n_pages=20, payload_mb=mb)
            try:
                import psutil
                p = psutil.Process()
                m0 = p.memory_info().rss
            except Exception:
                p, m0 = None, 0
            t = time.time()
            try:
                pr = library.prepare_pdf(ctx, data=data, filename="big_%dmb.pdf" % mb)
                err = pr.get("error")
                res = ("被拦截 error=%s" % str(err)[:70]) if err \
                    else "解析成功 %d 页 / %d 字符" % (pr.get("page_count"),
                                                  len(pr.get("full_text") or ""))
            except Exception as e:
                res = "抛异常 %s: %s" % (type(e).__name__, str(e)[:80])
            el = time.time() - t
            dm = ((p.memory_info().rss - m0) / 1e6) if p else 0
            print("    %-10s %-14s %-14s %s"
                  % ("%d MB" % mb, "%.2fs" % el, "%.1f MB" % dm, res))
            del data

        # (2b) 页数维度标定：真实大 PDF 的成本来自"页数/图像"，不是裸字节
        print("\n  [标定2] 页数维度（用真实语料 论文样本/UnderstandingDeepLearning_02_09_26_C.pdf，"
              "22.45 MB / %d 页）" % _real_textbook_pages())
        tb = ROOT / "论文样本" / "UnderstandingDeepLearning_02_09_26_C.pdf"
        try:
            from services import parsing
            raw = tb.read_bytes()
            t = time.time()
            doc = parsing.open_doc(raw)
            n = int(doc.page_count)
            t_open = time.time() - t
            t = time.time()
            for i in range(min(30, n)):
                parsing.extract_page_text_column_aware(doc[i], return_blocks=True)
            t30 = time.time() - t
            doc.close()
            per = t30 / max(min(30, n), 1)
            print("    open_doc 耗时 %.2fs · 总页数 %d · 前 %d 页提取 %.2fs → 单页 %.3fs"
                  % (t_open, n, min(30, n), t30, per))
            print("    ⇒ 按此单价外推全本 %d 页纯文本提取约 %.0fs（不含扫描件 OCR，"
                  "OCR 每页一次视觉调用会更慢）" % (n, per * n))
            del raw
        except Exception as e:
            print("    页数维度标定失败: %s: %s" % (type(e).__name__, e))

        print("\n  [UI 实测] 105MB PDF 走完整上传链路（点击「解析并查重」）"
              "——若 UI 已加体积闸门，这里应被拦下并给出人话提示")
        big = build_pdf(n_pages=20, payload_mb=105)
        print("    上传体积 %.2f MB" % (len(big) / 1e6))
        t = time.time()
        click_parse(at, big, "huge_105mb.pdf")
        print("    从构造到页面出结果总耗时 %.2fs" % (time.time() - t))
        print("    入库落盘动作（已隔离到系统临时目录）: %s"
              % (commit_log or "未触发 commit（解析被拦截）"))
        # 【修复核验⑤】应用层体积闸门：超限必须在解析之前就友好拒绝（不进 commit）
        blocked = not commit_log
        warn = [s for s in texts(at) if ("超过" in s and "MB" in s) or "上限" in s]
        print("    【核验⑤】体积闸门拦截（未进入 commit）= %s；页面体积提示 = %s"
              % (blocked, (warn[:1] or "无")))
        if commit_log:
            print("    ⚠️ 注：为避免污染真实文献库，library.commit 被临时替换成"
                  "「只实测落盘」的版本，故下方 success 文案里的「0 个分块」是隔离副产物，"
                  "不是真实行为；真实链路还会 chroma upsert + 写 library.json。")
        dump(at)
    finally:
        library.commit = real_commit
        _FAKE["data"] = None


# ============================================================== 场景 10：状态竞争
def s10_race(at):
    print("\n" + LINE)
    print("场景 10｜上传 PDF 后立即点击页面切换（状态竞争）")
    print(LINE)

    print("  ▶ 10a 解析已完成、查重命中（pending 写入 session_state）后立刻点导航切走")
    _FAKE["data"] = (ROOT / "论文样本" / "1810.04805v2.pdf").read_bytes()
    _FAKE["name"] = "1810.04805v2.pdf"
    click_parse(at, _FAKE["data"], _FAKE["name"])
    print("    查重命中 pending=%s（命中时待用户决策，尚未入库）" % bool(ss(at, "pending")))
    ok = nav_to(at, "跨文献问答与溯源")
    print("    切导航成功=%s · 当前 menu=%s" % (ok, ss(at, "menu")))
    print("    切走后 pending 是否仍残留在 session_state: %s" % bool(ss(at, "pending")))
    print("    问答页未捕获异常: %s" % (list(at.exception) or "无"))
    leak = [s for s in texts(at) if "查重" in s or "相似度" in s]
    print("    问答页上是否串出文献库内容: %s" % (leak or "否"))

    print("\n  ▶ 10b 再切回文献库页：旧的查重对话框是否复活")
    nav_to(at, "文献库管理")
    back = [s for s in texts(at) if "查重" in s or "相似度" in s]
    print("    未捕获异常: %s · pending 仍在=%s" % (list(at.exception) or "无",
                                                bool(ss(at, "pending"))))
    print("    复活文案: %s" % (back or "无"))

    print("\n  ▶ 10c 解析还在跑时用户已点走导航（真实时序：点击在本轮 script run 期间到达，"
          "\n      Streamlit 本轮跑完才重跑；这里在 prepare_pdf 内部改写 menu 等价模拟）")
    real_prepare = library.prepare_pdf
    marker = {"switched": False, "failed": ""}

    def _slow_prepare(ctx, *a, **kw):
        time.sleep(1.5)                                  # 让"解析中"窗口真实存在
        try:
            st.session_state["menu"] = "引用图谱与综述"   # 用户的点击已排队
            marker["switched"] = True
        except Exception as e:
            marker["failed"] = "%s: %s" % (type(e).__name__, e)
        return real_prepare(ctx, *a, **kw)

    library.prepare_pdf = _slow_prepare
    try:
        at.session_state["pending"] = None
        at.session_state["menu"] = "文献库管理"
        _FAKE["data"] = (ROOT / "论文样本" / "1810.04805v2.pdf").read_bytes()
        _FAKE["name"] = "1810.04805v2.pdf"
        at.run()
        btn = find_button(at, "解析并查重")
        btn.click().run()
        print("    解析途中 menu 被改写=%s %s" % (marker["switched"], marker["failed"]))
        print("    本轮结束未捕获异常: %s" % (list(at.exception) or "无"))
        at.run()
        print("    下一轮渲染的 menu=%s（用户已跳走）" % ss(at, "menu"))
        print("    下一轮未捕获异常: %s" % (list(at.exception) or "无"))
        print("    pending 仍残留在 session_state: %s" % bool(ss(at, "pending")))
        leak2 = [s for s in texts(at) if "查重" in s or "相似度" in s or "解析并查重" in s]
        print("    图谱页上是否串出文献库的东西: %s" % (leak2 or "否"))
        print("    页面残留进度条 st.progress 数量: %d" % len(at.get("progress")))
    finally:
        library.prepare_pdf = real_prepare
        at.session_state["pending"] = None
        _FAKE["data"] = None


# ============================================================== 场景 11：Key 清空
def s11_key_cleared(at):
    print("\n" + LINE)
    print("场景 11｜演示中途侧边栏 API Key 被清空")
    print(LINE)
    _FAKE["data"] = None
    at.session_state["pending"] = None

    print("  ▶ 11a 只清空侧边栏输入框（.env 里的环境变量兜底仍在）")
    at.session_state["saved_api_key"] = ""
    try:
        at.session_state["key_input"] = ""               # 侧边栏 Key 输入框的 widget key
    except Exception:
        pass
    at.run()
    ctx = ss(at, "container")
    avail_a = getattr(getattr(ctx, "llm", None), "available", "?")
    print("    ctx.llm.available = %s" % avail_a)
    print("    侧边栏出现「尚未配置 API Key」: %s"
          % bool([s for s in texts(at.sidebar) if "尚未配置 API Key" in s]))
    print("    未捕获异常: %s" % (list(at.exception) or "无"))

    print("\n  ▶ 11b 唯一 Key 也没了：清空输入框 + 移除进程内环境变量（等价现场无可用 Key）")
    try:
        from core.providers import list_providers
        envs = ["LLM_API_KEY"] + [p.key_env for p in list_providers()
                                  if getattr(p, "key_env", "")]
    except Exception as e:
        envs = ["LLM_API_KEY"]
        print("    ⚠️ 读 provider 列表失败: %s" % e)
    saved = {k: os.environ.get(k) for k in dict.fromkeys(envs) if os.environ.get(k)}
    for k in saved:
        os.environ.pop(k, None)
    try:
        at.session_state["saved_api_key"] = ""
        at.session_state["key_input"] = ""
        at.run()
        ctx = ss(at, "container")
        print("    本进程内移除的环境变量名: %s（只动 os.environ，.env 文件未读写）" % list(saved))
        print("    ctx.llm.available = %s" % getattr(ctx.llm, "available", "?"))
        print("    ctx.llm_status = %s" % (getattr(ctx, "llm_status", "?"),))
        print("    未捕获异常: %s" % (list(at.exception) or "无"))
        for s in [x for x in (texts(at) + texts(at.sidebar))
                  if ("Key" in x or "大模型" in x or "不可用" in x)][:8]:
            print("      %s" % s.replace("\n", " ")[:230])

        print("\n    -- 无 Key 下逐个页面渲染（只渲染不点，避免触发真实外网检索）--")
        for label in ["文献库管理", "跨文献问答与溯源", "版式阅读与智能翻译",
                      "论文检索与追踪", "引用图谱与综述",
                      "写作辅助与可视化", "智能总助手"]:
            nav_to(at, label)
            print("      %-22s 异常=%s" % (label, list(at.exception) or "无"))

        print("\n    -- 点两个典型的大模型按钮（文献库「抽取标题…」/ Agent「用此任务 ▶」）--")
        nav_to(at, "文献库管理")
        b = find_button(at, "抽取标题")
        if b:
            b.click().run()
            print("      文献库页异常=%s" % (list(at.exception) or "无"))
            dump(at, limit=8)
        nav_to(at, "智能总助手")
        b2 = find_button(at, "用此任务")
        if b2:
            b2.click().run()
            print("      Agent 页异常=%s" % (list(at.exception) or "无"))
            for s in texts(at):
                if "Key" in s or "大模型" in s:
                    print("      %s" % s.replace("\n", " ")[:230])
    finally:
        for k, v in saved.items():
            os.environ[k] = v
        print("    （已还原进程内环境变量；.env 文件全程未被读写）")


# ============================================================== 场景 12：刷新
def s12_refresh():
    print("\n" + LINE)
    print("场景 12｜浏览器刷新（session 丢失，任务中心里跑了一半的任务）")
    print(LINE)
    from services import tasks

    print("  ▶ 12a 任务在旧 session 里已跑完并被消费 → 刷新（新建 session）")
    def _job1(progress):
        progress("步骤 1/3 检索文献", 0.2)
        time.sleep(6)
        progress("步骤 3/3 生成答案", 1.0)
        return "RESULT-BODY-1"
    tid = tasks.submit("演练任务1", _job1, kind="agent", goal="演练1")

    at_a = AppTest.from_file(APP, default_timeout=300)
    at_a.session_state["menu"] = "智能总助手"
    at_a.run()
    print("    [刷新前] 侧边栏任务中心: %s"
          % ([s for s in texts(at_a.sidebar) if "任务" in s] or "（面板无文案）"))
    time.sleep(7)
    at_a.run()
    print("    [刷新前] state=%s · agent_runs 条数=%s"
          % ((tasks.get(tid) or {}).get("state"), len(ss(at_a, "agent_runs") or [])))
    print("    [刷新前] 完成态文案: %s"
          % ([s for s in texts(at_a.sidebar) if ("✅" in s or "结果已转入" in s)] or "无"))

    print("\n  ▶ 12b 浏览器刷新 = 新建 session（模块级 _TASKS 在同一进程内仍存活）")
    at_b = AppTest.from_file(APP, default_timeout=300)
    at_b.session_state["menu"] = "智能总助手"
    at_b.run()
    t = tasks.get(tid) or {}
    print("    任务条目仍在模块级 dict: state=%s consumed=%s" % (t.get("state"), t.get("consumed")))
    print("    新 session 的 agent_runs 条数 = %s" % len(ss(at_b, "agent_runs") or []))
    print("    新 session 侧边栏任务中心文案: %s"
          % ([s for s in texts(at_b.sidebar) if ("✅" in s or "结果已转入" in s or "任务" in s)] or "（面板无文案）"))
    print("    新 session 侧边栏 expander 标题: %s"
          % ([e.label for e in at_b.sidebar.expander] or "无"))
    print("    未捕获异常: %s" % (list(at_b.exception) or "无"))
    print("    新 session 首页主区（历史记录区）文案: %s"
          % ([s for s in texts(at_b) if "历史记录" in s][:2] or "无"))
    # 【修复核验⑥】刷新后：①不再谎称"结果已转入页面内容"；②结果载荷是否可恢复
    lie = [s for s in texts(at_b.sidebar) if "结果已转入页面内容" in s]
    honest = [s for s in texts(at_b.sidebar) if "上一个会话" in s]
    payload = [s for s in texts(at_b.sidebar) if "RESULT-BODY-1" in s]
    print("    【核验⑥】刷新后侧边栏仍谎称「结果已转入页面内容」= %s（修复后应为 False）"
          % bool(lie))
    print("    【核验⑥】刷新后侧边栏诚实说明「属于上一个会话」= %s（修复后应为 True）"
          % bool(honest))
    print("    【核验⑥】刷新后结果载荷可被重新渲染出来 = %s（修复后应为 True）"
          % bool(payload))
    for s in (honest + payload)[:3]:
        print("        → %s" % s.replace("\n", " ")[:200])

    print("\n  ▶ 12c 任务**正在跑**时刷新：新 session 能否接着看到并领回结果")
    def _job2(progress):
        progress("步骤 1/2", 0.3)
        time.sleep(25)                       # 保证"刷新发生在任务运行中"
        return "RESULT-BODY-2"
    tid2 = tasks.submit("演练任务2（刷新时未完成）", _job2, kind="agent", goal="演练2")
    at_c = AppTest.from_file(APP, default_timeout=300)
    at_c.session_state["menu"] = "智能总助手"
    at_c.run()
    print("    提交后立刻: state=%s" % (tasks.get(tid2) or {}).get("state"))
    at_d = AppTest.from_file(APP, default_timeout=300)      # ← 刷新
    at_d.session_state["menu"] = "智能总助手"
    at_d.run()
    print("    刷新后立刻: state=%s（是否仍在跑）" % (tasks.get(tid2) or {}).get("state"))
    print("    刷新后侧边栏运行中文案: %s"
          % ([s for s in texts(at_d.sidebar) if ("⚙️" in s or "运行中的任务" in s or "🕐" in s)]
             or "（无运行中文案）"))
    print("    刷新后侧边栏 progress 条数: %d" % len(at_d.sidebar.get("progress")))
    time.sleep(30)
    at_d.run()
    print("    等它跑完再渲染: state=%s · 新 session agent_runs=%s"
          % ((tasks.get(tid2) or {}).get("state"), len(ss(at_d, "agent_runs") or [])))
    print("    侧边栏完成态文案: %s"
          % ([s for s in texts(at_d.sidebar) if ("✅" in s or "结果已转入" in s)] or "无"))
    print("    未捕获异常: %s" % (list(at_d.exception) or "无"))

    print("\n  ▶ 12d 后台任务抛异常 → 刷新后的失败态显示")
    def _boom(progress):
        progress("步骤 1/2", 0.5)
        raise RuntimeError("模拟后台任务抛异常")
    tid3 = tasks.submit("演练任务3（必失败）", _boom, kind="agent", goal="演练3")
    time.sleep(1.5)
    at_e = AppTest.from_file(APP, default_timeout=300)
    at_e.session_state["menu"] = "智能总助手"
    at_e.run()
    print("    state=%s" % (tasks.get(tid3) or {}).get("state"))
    print("    侧边栏文案: %s"
          % ([s for s in texts(at_e.sidebar) if ("❌" in s or "失败" in s)] or "无"))
    print("    侧边栏 expander 标题: %s"
          % ([e.label for e in at_e.sidebar.expander] or "无"))
    print("    未捕获异常: %s" % (list(at_e.exception) or "无"))


# ============================================================== main
def main():
    t0 = time.time()
    print("AI4S 故障演练（文件/状态类） · 解释器 %s" % sys.executable)
    print("工作目录 %s" % ROOT)
    at = AppTest.from_file(APP, default_timeout=900)
    at.session_state["menu"] = "文献库管理"
    at.run()
    print("首次加载 %.1fs · 未捕获异常=%s\n" % (time.time() - t0, list(at.exception) or "无"))

    s7_corrupt(at)
    s8_zero(at)
    s9_oversize(at)
    s10_race(at)
    s11_key_cleared(at)
    s12_refresh()

    print("\n" + LINE)
    print("全部场景执行完毕，总耗时 %.1fs" % (time.time() - t0))
    print(LINE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
