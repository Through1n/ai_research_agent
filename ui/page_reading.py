"""页面 3：版式感知阅读与双向联动翻译。

本轮（考生6）专项重做：
  A1 原生渲染双栏 PDF 文字块坐标重叠 → services/translate.detect_columns 分栏容器渲染
  A2 同步滚动左右不同步             → bilingual_html 按比例同步 + isSyncing + rAF
  A3 划词翻译后选区未清空            → SELECTION_JS（捕获即清空 + hash 去重 + 长度<2 忽略）
  A4 原位 PDF 中文乱码               → pymupdf 内置字体 china-s + insert_textbox + 负值回退
  B1 内存监控                        → scripts/_memory_probe.MemoryProbe（psutil→tracemalloc→无）
  B2 长文档加固                      → 逐页/分批、每 50 页进度、单页 30s 跳过、失败汇总、临时目录清理
  B3 LLM 并发与超时                  → 并发 ≤ MAX_TRANSLATE_WORKERS、单次 ≤ LLM_CALL_TIMEOUT、失败中文提示
  跨页对接                           → 消费 ui/page_qa.py 写入的 st.session_state["qa_jump_page"]

本轮（考生7）新增（模块三：正文引用标记 ↔ References 双向无损跳转）：
  T1 引用双向跳转   → build_reference_index() 真实抽取 References + citation_links_html()
                      （正文 [n] → #ref-n；参考文献「↩ 回到正文」循环第 k/m 处）
  T2 标题树定位     → 无内置书签时接 services.parsing.infer_headings_from_text 推断回退，
                      点击后原生渲染定位到该页并高亮该标题（native_page_html(highlight=)）
  T3 公式保留       → 翻译前 protect_math() 占位、翻译后 restore_math() 还原

硬性约束：所有新代码块 try/except 兜底，异常不冒泡；session_state key 先初始化再读。
"""
import gc
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from ui.common import embed_html, page_header

from config import UPLOAD_DIR, PDF_DIR
from services import parsing, writing
from services.translate import (native_page_html, bilingual_html, in_place_pdf,
                                citation_jump_html, selectable_text_html, detect_columns,
                                build_reference_index, find_reference_start_page,
                                citation_links_html, protect_math, restore_math)

# ---------------- 常量（集中配置，便于调参） ----------------
JUMP_KEY = "qa_jump_page"          # 与 ui/page_qa.py 约定（只读 + 消费后清理）
TOC_JUMP_KEY = "read_toc_jump"     # 考生7：标题树点击 → 原生渲染定位（消费后清理）
CITE_BODY_CAP = 40                 # T1：正文窗口最大页数（长文档防卡）
TOC_SCAN_CAP = 120                 # T2：无书签时推断标题最多扫描的页数
TOC_RENDER_CAP = 220               # T2：标题树首屏最多渲染的按钮数（长文档防卡）
MAX_TRANSLATE_WORKERS = 3          # B3：翻译并发上限（≤3，避免打爆限流）
LLM_CALL_TIMEOUT = 60.0            # B3：单次 LLM 调用超时（秒）
PAGE_TIMEOUT = 30.0                # B2：单页处理超时（秒），超时跳过并记录页码
PROGRESS_EVERY = 50                # B2：每 N 页刷新一次进度条
BATCH_FLUSH = 50                   # B2：每 N 页释放一次引用 + gc
FIG_TTL_HOURS = 24                 # B2：fig_* 临时目录超过该时长视为陈旧
CITE_SCAN_CAP = 260                # 引用跳转：正文/文献区回溯扫描页数上限

# ---------------- MemoryProbe 导入（缺失时降级为占位探针，绝不崩） ----------------
try:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from scripts._memory_probe import MemoryProbe, friendly_note as _mem_note
except Exception:  # pragma: no cover
    MemoryProbe = None
    _mem_note = None


class _NullProbe:
    """探针不可用时的占位实现：接口相同，数值恒 0，保证页面不崩。"""

    def __init__(self, label=""):
        self.label = str(label or "")
        self._t0 = time.time()

    def start(self):
        self._t0 = time.time()
        return self

    def sample(self, note=""):
        return 0.0

    def peak_mb(self):
        return 0.0

    def start_mb(self):
        return 0.0

    def delta_mb(self):
        return 0.0

    def elapsed(self):
        return max(0.0, time.time() - self._t0)

    def report(self):
        return {"label": self.label, "backend": "none", "start_mb": 0.0,
                "peak_mb": 0.0, "delta_mb": 0.0, "elapsed_s": round(self.elapsed(), 2),
                "n_samples": 0}

    def text(self):
        return f"「{self.label}」 峰值内存 — MB / 耗时 {self.elapsed():.1f} 秒（内存探针不可用）"

    def stop(self):
        return self.report()


def _new_probe(label):
    try:
        if MemoryProbe is not None:
            return MemoryProbe(label)
    except Exception:
        pass
    return _NullProbe(label)


def _mem_warning():
    try:
        return _mem_note() if _mem_note else ""
    except Exception:
        return ""


# ============================ 通用工具 ============================
def _ensure_state(pairs):
    """session_state 先初始化再读，杜绝 KeyError。"""
    for k, v in (pairs or {}).items():
        try:
            if k not in st.session_state:
                st.session_state[k] = v
        except Exception:
            pass


def _drop_state(keys):
    """删除 widget key（用于跳转后让页码控件重新取新默认值）。"""
    for k in (keys or ()):
        try:
            if k in st.session_state:
                del st.session_state[k]
        except Exception:
            try:
                st.session_state.pop(k, None)
            except Exception:
                pass


def _progress(pb, value, text=""):
    """进度条更新：兼容不支持 text= 的旧版 Streamlit。"""
    if pb is None:
        return
    try:
        v = min(1.0, max(0.0, float(value)))
    except Exception:
        return
    try:
        pb.progress(v, text=str(text or ""))
    except TypeError:
        try:
            pb.progress(v)
        except Exception:
            pass
    except Exception:
        try:
            pb.progress(v)
        except Exception:
            pass


def _run_with_timeout(fn, timeout, *args, **kwargs):
    """在单线程池中执行 fn，超时/异常都不抛：返回 (ok, value, err)。

    说明：Python 无法强行杀掉阻塞中的工作线程，超时后主线程不再等待并继续处理后续页，
    工作线程在后台跑完后自行退出（PyMuPDF 的 C 调用会释放 GIL，不会卡死主线程）。
    """
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(fn, *args, **kwargs)
        try:
            return True, fut.result(timeout=float(timeout)), None
        except Exception as e:
            name = type(e).__name__
            if "Timeout" in name:
                return False, None, f"timeout>{float(timeout):g}s"
            return False, None, f"{name}: {str(e)[:160]}"
    except Exception as e:
        return False, None, f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                try:
                    pool.shutdown(wait=False)
                except Exception:
                    pass
            except Exception:
                pass


def cleanup_stale_figure_dirs(max_age_hours=FIG_TTL_HOURS, roots=None):
    """清理超过 max_age_hours 小时的 fig_* 临时目录，返回清理个数。永不抛异常。"""
    removed = 0
    now = time.time()
    for root in (roots or [tempfile.gettempdir()]):
        try:
            names = os.listdir(root)
        except Exception:
            continue
        for name in names:
            if not str(name).startswith("fig_"):
                continue
            p = os.path.join(root, name)
            try:
                if not os.path.isdir(p):
                    continue
                age = now - os.path.getmtime(p)
            except OSError:
                continue
            except Exception:
                continue
            if age > float(max_age_hours) * 3600.0:
                try:
                    shutil.rmtree(p, ignore_errors=True)
                    removed += 1
                except Exception:
                    pass
    return removed


def _extract_page_images(doc, page_no, out_dir, limit=3):
    """抽取指定页图片到 out_dir，返回写出文件数（失败返回 0）。"""
    n = 0
    try:
        page = doc[page_no - 1]
        for info in (page.get_images(full=True) or [])[:limit]:
            try:
                xref = info[0]
                data = doc.extract_image(xref)
                if not data:
                    continue
                ext = str(data.get("ext", "png"))
                fp = os.path.join(out_dir, "p%04d_%d.%s" % (int(page_no), int(xref), ext))
                with open(fp, "wb") as fh:
                    fh.write(data.get("image", b""))
                n += 1
            except Exception:
                continue
    except Exception:
        return n
    return n


def _scan_one_page(doc, page_no):
    """单页扫描：返回 (字符数, 栏数)。逐页调用，绝不一次性 get_text 全库。"""
    txt = ""
    try:
        txt = doc[page_no - 1].get_text() or ""
    except Exception:
        raise
    ncol = 1
    try:
        ncol = int(detect_columns(doc[page_no - 1]).get("n_cols", 1) or 1)
    except Exception:
        ncol = 1
    return len(txt), ncol


def _long_doc_scan(doc, start_page, end_page, with_figures=False):
    """B1+B2：长文档批量扫描（逐页 + 分批 + 超时跳过 + 失败汇总 + 临时目录 finally 清理）。"""
    try:
        s, e = int(start_page), int(end_page)
    except Exception:
        st.warning("⚠️ 页码范围非法，已取消扫描。")
        return
    if e < s:
        s, e = e, s
    total = e - s + 1
    if total <= 0:
        st.warning("⚠️ 页码范围为空，已取消扫描。")
        return

    probe = _new_probe(f"长文档扫描 {total} 页")
    probe.start()
    pb = st.progress(0.0, text="准备中…")
    status = st.empty()
    failed, timed_out, two_col, empty_pages = [], [], [], []
    chars = 0
    figs = 0
    fig_dir = None
    try:
        if with_figures:
            try:
                fig_dir = tempfile.mkdtemp(prefix="fig_")
            except Exception:
                fig_dir = None
        t0 = time.time()
        for i, pno in enumerate(range(s, e + 1), 1):
            ok, payload, err = _run_with_timeout(_scan_one_page, PAGE_TIMEOUT, doc, pno)
            if ok and payload:
                n_chars, ncol = payload
                chars += int(n_chars or 0)
                if ncol == 2:
                    two_col.append(pno)
                if not n_chars:
                    empty_pages.append(pno)
                if fig_dir:
                    figs += _extract_page_images(doc, pno, fig_dir)
            else:
                if err and err.startswith("timeout"):
                    timed_out.append(pno)
                else:
                    failed.append(pno)
                try:
                    status.caption(f"⏭️ 第 {pno} 页跳过：{err}")
                except Exception:
                    pass
            # 每 BATCH_FLUSH 页释放引用；每 PROGRESS_EVERY 页刷新进度
            if i % BATCH_FLUSH == 0:
                try:
                    gc.collect()
                except Exception:
                    pass
                probe.sample()
            if i % PROGRESS_EVERY == 0 or i == total:
                _progress(pb, i / total, f"已处理 {i}/{total} 页（{chars} 字符）…")
    except Exception as e:
        st.warning(f"⚠️ 批量扫描中断（已完成部分页）：{str(e)[:160]}")
    finally:
        # 图片临时目录：try/finally 保证一定清理
        if fig_dir:
            try:
                shutil.rmtree(fig_dir, ignore_errors=True)
            except Exception:
                pass
        try:
            gc.collect()
        except Exception:
            pass
        try:
            pb.empty()
        except Exception:
            pass
        stale = cleanup_stale_figure_dirs(FIG_TTL_HOURS)

    elapsed = probe.elapsed()
    peak = probe.peak_mb()
    st.info(f"✅ 扫描完成：{total} 页 / {chars:,} 字符 / 抽取图片 {figs} 个 · "
            f"峰值内存 {peak:.0f} MB / 耗时 {elapsed:.1f} 秒")
    note = _mem_warning()
    if note:
        st.caption(note)
    if timed_out:
        st.warning(f"⏱️ {len(timed_out)} 页处理超时（> {PAGE_TIMEOUT:.0f}s）已跳过："
                   f"{timed_out[:20]}{' …' if len(timed_out) > 20 else ''}")
    if failed:
        st.warning(f"⚠️ {len(failed)} 页解析失败已跳过："
                   f"{failed[:20]}{' …' if len(failed) > 20 else ''}")
    if empty_pages:
        st.caption(f"📭 空白页（无文本层，可能是扫描件）{len(empty_pages)} 个："
                   f"{empty_pages[:12]}{' …' if len(empty_pages) > 12 else ''}")
    st.caption(f"📑 双栏页 {len(two_col)} 个：{two_col[:12]}"
               f"{' …' if len(two_col) > 12 else ''}；🧹 清理陈旧 fig_* 临时目录 {stale} 个（> {FIG_TTL_HOURS}h）")


# ============================ B3：LLM 调用统一超时/并发/兜底 ============================
def _safe_translate(ctx, text, timeout=LLM_CALL_TIMEOUT):
    """单次翻译：带超时、带异常兜底。返回 (译文, 错误信息)；失败时译文为 ''。"""
    txt = str(text or "")
    if not txt.strip():
        return "", "内容为空"
    if not getattr(getattr(ctx, "llm", None), "available", False):
        return "", "未配置 API Key"
    # T3：翻译前把公式/LaTeX/数值表达式占位，翻译后原样还原（模块三要求公式保留英文）
    masked, store = protect_math(txt)
    ok, val, err = _run_with_timeout(writing.translate_to_chinese, timeout, ctx, masked)
    if ok:
        out = str(val or "").strip()
        if not out:
            return "", "模型返回空内容"
        return restore_math(out, store), ""
    if err and err.startswith("timeout"):
        return "", f"调用超时（> {timeout:.0f}s）"
    return "", (err or "调用失败")


def _translate_pairs(ctx, paras, label="段落翻译"):
    """并发翻译（≤ MAX_TRANSLATE_WORKERS）+ 顺序还原 + 缓存命中跳过。

    返回 (pairs, errors)；pairs = [(原文, 译文), ...]，errors = [(段号, 原因), ...]
    """
    pairs = [(p, "") for p in paras]
    errors = []
    n = len(paras)
    if n == 0:
        return pairs, errors
    cache = st.session_state.get("read_tr_cache")
    if not isinstance(cache, dict):
        cache = {}
        st.session_state["read_tr_cache"] = cache

    todo = []
    for i, p in enumerate(paras):
        key = _cache_key(p)
        hit = cache.get(key)
        if isinstance(hit, str) and hit.strip():
            pairs[i] = (p, hit)
        else:
            todo.append(i)

    if not todo:
        st.caption(f"♻️ {label}：全部 {n} 段命中本地缓存，未重复调用模型。")
        return pairs, errors

    workers = max(1, min(MAX_TRANSLATE_WORKERS, len(todo)))
    pb = st.progress(0.0, text=f"{label} 0/{len(todo)} …")
    done = 0
    probe = _new_probe(label)
    probe.start()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_safe_translate, ctx, str(paras[i])[:1500], LLM_CALL_TIMEOUT): i
                    for i in todo}
            for fut in futs:
                i = futs[fut]
                try:
                    zh, err = fut.result(timeout=LLM_CALL_TIMEOUT + 15.0)
                except Exception as e:
                    zh, err = "", f"{type(e).__name__}: {str(e)[:100]}"
                if zh:
                    pairs[i] = (paras[i], zh)
                    try:
                        cache[_cache_key(paras[i])] = zh
                    except Exception:
                        pass
                else:
                    errors.append((i + 1, err or "调用失败"))
                done += 1
                if done % 5 == 0 or done == len(todo):
                    _progress(pb, done / len(todo), f"{label} {done}/{len(todo)} …")
                    probe.sample()
    except Exception as e:
        st.warning(f"⚠️ {label}并发执行异常：{str(e)[:160]}")
    finally:
        try:
            pb.empty()
        except Exception:
            pass
    try:
        st.info(f"🧠 {label}：{len(todo)} 段调用（并发 ≤ {workers}，单次 ≤ {LLM_CALL_TIMEOUT:.0f}s）· "
                f"峰值内存 {probe.peak_mb():.0f} MB / 耗时 {probe.elapsed():.1f} 秒")
    except Exception:
        pass
    if errors:
        st.warning("⚠️ 以下段落翻译失败（已保留原文，未影响其余段落）："
                   + "；".join(f"第 {i} 段（{r}）" for i, r in errors[:8])
                   + ("…" if len(errors) > 8 else ""))
        st.caption("🛠️ 怎么办：检查 API Key / 网络后重试失败的段落；其余功能不受影响。")
    return pairs, errors


def _safe_int_pos(v, default=0):
    """转正整数：解析失败返回 default（页面内所有编号比较都走这里，杜绝 ValueError 冒泡）。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _cache_key(text):
    try:
        import hashlib
        return hashlib.md5(str(text).encode("utf-8", "ignore")).hexdigest()[:16]
    except Exception:
        return str(text)[:64]


# ============================ 跨页跳转：消费 qa_jump_page ============================
def _consume_qa_jump(ctx, tps):
    """消费 ui/page_qa.py 写入的 st.session_state["qa_jump_page"]。

    返回 dict(source, page, para_idx) 或 None；消费后立即清空 key，避免重复跳转。
    """
    payload = None
    try:
        payload = st.session_state.get(JUMP_KEY)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or not payload.get("source"):
        return None
    src = str(payload.get("source") or "")
    try:
        page = int(payload.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), max(1, int(tps or 1)))
    para = None
    try:
        para = int(payload["para_idx"]) if payload.get("para_idx") else None
    except (TypeError, ValueError, KeyError):
        para = None
    # 让页码控件重新取默认值（否则 widget 已有状态会把新跳转吞掉）
    _drop_state(("np", "sp", "pp", "hp"))
    try:
        st.session_state["read_jump_page"] = page
        st.session_state["read_jump_para"] = para
    except Exception:
        pass
    label = src[:-4] if src.lower().endswith(".pdf") else src
    tail = f"（第 {para} 段）" if para else ""
    try:
        st.success(f"🎯 已从问答页跳转：《{label}》第 {page} 页{tail}")
    except Exception:
        pass
    try:
        st.toast(f"已从问答页跳转 → 第 {page} 页", icon="🎯")
    except Exception:
        try:
            st.toast("已从问答页跳转")
        except Exception:
            pass
    # 清理：避免下次进页面重复跳转
    try:
        st.session_state[JUMP_KEY] = None
    except Exception:
        pass
    try:
        qp = st.query_params
        for k in ("jump_page", "paper"):
            if k in qp:
                del qp[k]
    except Exception:
        pass
    return {"source": src, "page": page, "para_idx": para}


# ============================ T2：标题树跳转消费 ============================
def _consume_toc_jump(tps):
    """消费标题树点击写入的 st.session_state["read_toc_jump"]。

    返回 dict(page, title) 或 None；消费后清空，避免下次进页面重复跳转。
    """
    payload = None
    try:
        payload = st.session_state.get(TOC_JUMP_KEY)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return None
    try:
        page = int(payload.get("page", 1) or 1)
    except (TypeError, ValueError):
        return None
    page = min(max(1, page), max(1, int(tps or 1)))
    title = str(payload.get("title") or "").strip()
    _drop_state(("np", "sp", "pp", "hp"))
    try:
        st.session_state["read_jump_page"] = page
        st.session_state["read_hl"] = {"page": page, "title": title}
        st.session_state[TOC_JUMP_KEY] = None
    except Exception:
        pass
    try:
        st.success(f"🎯 标题树定位：第 {page} 页「{title[:40]}」"
                   f"（已切到「📄 原生渲染」标签并高亮该标题）")
    except Exception:
        pass
    return {"page": page, "title": title, "source": "toc"}


# ---- T2：无内置书签时用 parsing.infer_headings_from_text 推断标题树 ----
_SECTION_WORDS = frozenset((
    "abstract", "摘要", "introduction", "引言", "conclusion", "conclusions", "结论",
    "references", "bibliography", "参考文献", "acknowledgment", "acknowledgments",
    "acknowledgement", "acknowledgements", "appendix", "related work", "background",
    "method", "methods", "experiment", "experiments", "results", "discussion",
))


def _tidy_headings(items, tps=None):
    """整理推断结果：合并「编号行 + 标题行」、剔除首页作者行、去重、夹取页码。"""
    out = []
    seen = set()
    pending = None            # (编号, 页, level)
    p1_root_taken = False
    for item in items or []:
        try:
            level, title, pg = int(item[0]), str(item[1]).strip(), int(item[2])
        except Exception:
            continue
        if pg < 1 or (tps and pg > int(tps)) or not title:
            continue
        # ① 纯编号行（"1" / "3.2.1"）与同一页的下一行标题合并为 "3.2.1 Scaled Dot-Product ..."
        if re.match(r"^\d+(?:\.\d+)*\.?$", title):
            pending = (title.rstrip("."), pg, level)
            continue
        if pending and pending[1] == pg:
            title = "%s %s" % (pending[0], title)
            pending = None
        # ② 首页非编号条目多半是作者/机构：只保留首个（文章标题）与常见章节名
        if pg == 1 and not re.match(r"^\d", title):
            if title.lower().strip(" .:") in _SECTION_WORDS:
                pass
            elif p1_root_taken:
                continue
            else:
                p1_root_taken = True
        if len(title) < 2:
            continue
        # ③ 正文碎片（"2014) methods." / "or paragraph embeddings (Le and..."）不是标题
        if re.match(r"^\d{4}\b", title):                       # 以年份开头的句子残片
            continue
        if title[:1].isalpha() and title[:1].islower():        # 小写开头 = 句中片段
            continue
        key = (pg, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append((min(6, max(1, level)), title[:90], pg))
    return out


_CHAPTER_RE = re.compile(r"^\s*(?:chapter|part|section)\s+(\d+)\s*[:.\-\u2013]?\s*(\S.*)$", re.I)
_CN_CHAPTER_RE = re.compile(r"^\s*\u7b2c\s*([0-9\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)"
                            r"\s*[\u7ae0\u8282\u7bc7\u90e8]\s*(\S.*)$")


def _chapter_headings(doc, tps, cap=TOC_SCAN_CAP):
    """兜底：按「Chapter N xxx」/「第 N 章 xxx」识别章节（无字号差异的合成长文档常用）。"""
    out, seen = [], set()
    try:
        n = min(int(tps or 0), int(cap))
        for i in range(n):
            try:
                txt = doc[i].get_text() or ""
            except Exception:
                continue
            for line in str(txt).splitlines():
                line = re.sub(r"\s+", " ", line).strip()
                if not line or len(line) > 90:
                    continue
                m = _CHAPTER_RE.match(line) or _CN_CHAPTER_RE.match(line)
                if not m:
                    continue
                title = ("%s %s" % (m.group(1), m.group(2))).strip()[:90]
                key = title.casefold()
                if key in seen:
                    continue          # 页眉重复：只保留首次出现的页
                seen.add(key)
                out.append((1, title, i + 1))
    except Exception:
        return []
    return out


def _infer_headings(doc, tps, cap=TOC_SCAN_CAP):
    """无内置书签时的推断回退：接 services.parsing.infer_headings_from_text。"""
    try:
        n = min(int(tps or 0), int(cap))
        if n <= 0:
            return []
        pages = []
        for i in range(n):
            try:
                blocks = parsing.extract_page_blocks_column_aware(doc[i])
                pages.append(parsing.heading_page_data(blocks))
            except Exception:
                pages.append({"lines": [], "text": ""})
        raw = parsing.infer_headings_from_text(pages) or []
        tidy = _tidy_headings(raw, tps)
        if not tidy:
            tidy = _chapter_headings(doc, tps, cap)   # 字号无差异时的章节号兜底
        return tidy
    except Exception:
        return []


def _ensure_headings(doc, doc_id, tps):
    """标题树缓存（内置书签优先，无则推断回退），避免每次 rerun 重复扫描。"""
    cache = st.session_state.get("read_toc_cache")
    if not isinstance(cache, dict):
        cache = {}
        try:
            st.session_state["read_toc_cache"] = cache
        except Exception:
            pass
    if cache.get("id") == doc_id and isinstance(cache.get("toc"), list):
        return list(cache.get("toc") or []), bool(cache.get("inferred"))
    toc, inferred = [], False
    try:
        toc = list(doc.get_toc() or [])
    except Exception:
        toc = []
    if not toc:
        toc = _infer_headings(doc, tps)
        inferred = bool(toc)
    try:
        st.session_state["read_toc_cache"] = {"id": doc_id, "toc": toc, "inferred": inferred}
    except Exception:
        pass
    return toc, inferred


# ============================ T1：References 索引（带缓存） ============================
def _ensure_ref_index(doc, doc_id, force=False):
    """抽取 References 编号索引并缓存到 session_state（key 先初始化再读）。"""
    cache = st.session_state.get("read_ref_index")
    if not isinstance(cache, dict):
        cache = {}
        try:
            st.session_state["read_ref_index"] = cache
        except Exception:
            pass
    if (not force) and cache.get("id") == doc_id and isinstance(cache.get("refs"), list):
        return list(cache.get("refs") or []), dict(cache.get("meta") or {})
    refs, meta = [], {"source": "", "status": "empty", "message": "未抽取到参考文献条目"}
    try:
        refs, meta = build_reference_index(doc)
    except Exception as e:
        meta = {"source": "", "status": "empty",
                "message": "%s: %s" % (type(e).__name__, str(e)[:120])}
    try:
        st.session_state["read_ref_index"] = {"id": doc_id, "refs": refs, "meta": meta}
    except Exception:
        pass
    return refs, meta


# ============================ 文档/文本 ============================
def _resolve_doc(ctx, prefer=None):
    """解析 PDF 路径：临时上传 > 文献库选择（prefer 为跨页跳转指定的文件名）。"""
    path = None
    try:
        up = st.file_uploader("或临时上传 PDF", type=["pdf"], key="read_up")
        if up:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            try:
                tmp.write(up.read())
                path = tmp.name
            finally:
                try:
                    tmp.close()
                except Exception:
                    pass
    except Exception as e:
        st.caption(f"（上传组件异常：{str(e)[:80]}）")

    files = list(getattr(ctx, "files", None) or [])
    sel_opts = ([""] + files) if files else [""]
    idx = 1 if files else 0
    if prefer and prefer in sel_opts:
        idx = sel_opts.index(prefer)
    try:
        sel = st.selectbox("从文献库选择", sel_opts, index=idx, key="read_sel")
    except Exception:
        sel = prefer or ""
    if not path and sel:
        for base in (UPLOAD_DIR, PDF_DIR):
            try:
                candidate = str(base / sel)
                if os.path.exists(candidate):
                    path = candidate
                    break
            except Exception:
                continue
        if not path:
            st.warning("该论文为内置演示语料（纯文本入库），无原始 PDF 文件，不支持版式渲染。"
                       "如需体验阅读与翻译，请改选带 PDF 的论文或临时上传。")
    return path


def _page_paragraphs(doc, page):
    """提取页面段落；异常返回空列表（不冒泡）。"""
    try:
        raw = doc[page - 1].get_text() or ""
    except Exception:
        return []
    blocks = [b.strip() for b in str(raw).split("\n") if b.strip()]
    return [b for b in blocks if len(b) > 15 and not re.match(r"^[\W\d_]+$", b)]


def _find_citation(doc, cit, tps):
    """逐页扫描定位引用（B2：不一次性 join 全库）。返回 (正文片段, 参考文献条目, 扫描页数)。"""
    key = re.escape(str(cit).strip("[]"))
    bp = re.compile(r".{0,180}\[%s\].{0,180}" % key, re.DOTALL)
    rp = re.compile(r"\[%s\]\s*(.*?)(?=\n\s*\[|\Z)" % key, re.DOTALL)
    body, ref = "", ""
    scanned = 0
    ref_buf = []
    ref_chars = 0
    try:
        for pno in range(int(tps), 0, -1):     # 从后往前扫，先命中参考文献区
            if scanned >= CITE_SCAN_CAP and (body or ref):
                break
            try:
                txt = doc[pno - 1].get_text() or ""
            except Exception:
                scanned += 1
                continue
            scanned += 1
            if not ref:
                m = rp.search(txt)
                if m and 20 < len(m.group(1).strip()) < 800:
                    ref = re.sub(r"\s+", " ", m.group(1).strip())
            if not body:
                m2 = bp.search(txt)
                if m2 and len(m2.group(0).strip()) > 30:
                    body = re.sub(r"\s+", " ", m2.group(0).strip())
            if not ref:                        # 累积尾部文本供 ref_section 兜底
                ref_buf.append(txt)
                ref_chars += len(txt)
                if ref_chars > 60000:
                    ref_buf = ref_buf[-12:]
                    ref_chars = sum(len(x) for x in ref_buf)
            if body and ref:
                break
            del txt
        if not ref and ref_buf:
            try:
                from services.graph import ref_section
                tail = "".join(reversed(ref_buf))
                seg = ref_section(tail)
                m = rp.search(seg)
                if m and 20 < len(m.group(1).strip()) < 800:
                    ref = re.sub(r"\s+", " ", m.group(1).strip())
            except Exception:
                pass
    except Exception:
        pass
    return body, ref, scanned


# ============================ 主渲染 ============================
def render(ctx):
    page_header("📖 版式阅读与智能翻译",
                "原生坐标渲染（双栏分栏）、中英同步滚动、原位 PDF 叠加中文、引用双向跳转、划词翻译、标题树。")
    # 注意：只初始化「非 widget」key；widget key（np/sp/pp/hp/cit/hl_frag/ld_*）
    # 若在这里预设会触发 Streamlit 的 "default value + session_state" 警告。
    _ensure_state({"read_tr_cache": {}, "read_jump_page": 1, "read_jump_para": None, "hl": ""})

    doc = None
    try:
        path = None
        jump = None
        # ① 先读跨页跳转约定（session_state 优先，query_params 兜底）
        prefer = None
        try:
            payload = st.session_state.get(JUMP_KEY)
            if isinstance(payload, dict) and payload.get("source"):
                prefer = str(payload["source"])
        except Exception:
            prefer = None
        qp = {}
        try:
            qp = dict(st.query_params)
        except Exception:
            qp = {}
        if not prefer and qp.get("paper") and (getattr(ctx, "files", None) or []):
            cand = str(qp.get("paper"))
            if cand in ctx.files:
                prefer = cand

        path = _resolve_doc(ctx, prefer)
        if not path:
            st.info("请先在文献库导入论文，或临时上传。")
            return
        try:
            doc = parsing.open_doc(path)
        except Exception as e:
            st.error(f"❌ PDF 打开失败：{str(e)[:160]}")
            st.caption("🛠️ 怎么办：确认文件未损坏；或改用「📁 文献库管理」重新上传。")
            return
        tps = int(getattr(doc, "page_count", 0) or 0)
        if tps <= 0:
            st.warning("⚠️ 该 PDF 没有可读取的页面。")
            return
        st.caption(f"📄 {os.path.basename(path)}（{tps} 页）")
        # T1/T2 缓存键：文件名 + 页数（切换 PDF 时自动失效）
        doc_id = "%s|%d" % (os.path.basename(path), int(tps))

        # ② 消费 qa_jump_page（含 para_idx），得到默认页码；无则消费标题树跳转
        jump = _consume_qa_jump(ctx, tps)
        if not jump:
            jump = _consume_toc_jump(tps)
        default_page = 1
        if jump:
            default_page = int(jump.get("page", 1) or 1)
        elif qp.get("jump_page"):
            try:
                default_page = min(max(1, int(qp["jump_page"])), tps)
                st.caption(f"🎯 已从问答页跳转到第 {default_page} 页（切换上方标签查看）")
            except (TypeError, ValueError):
                default_page = 1
        default_page = min(max(1, int(default_page)), tps)

        tabs = st.tabs(["📄 原生渲染", "🌐 中英同步滚动", "📑 原位 PDF",
                        "🔗 引用跳转", "🖱️ 划词翻译", "📚 标题树"])

        # ---------- 1 原生渲染（A1） ----------
        with tabs[0]:
            try:
                p = st.number_input("页码", 1, tps, value=default_page, key="np")
                c_a, c_b = st.columns([1, 1])
                with c_a:
                    if st.button("🎨 渲染该页", key="np_go"):
                        try:
                            # T2：标题树跳转命中的那页，把标题文字高亮出来
                            hl_word = ""
                            try:
                                hlv = st.session_state.get("read_hl")
                                if isinstance(hlv, dict) and int(hlv.get("page", 0)) == int(p):
                                    hl_word = str(hlv.get("title") or "")
                            except Exception:
                                hl_word = ""
                            h, pw, ph = native_page_html(doc, int(p), highlight=hl_word or None)
                            embed_html(h, height=min(int(ph * 1.18) + 60, 900), scrolling=True)
                            if hl_word:
                                st.caption(f"🔎 已高亮标题：{hl_word[:60]}")
                        except Exception as e:
                            st.warning(f"⚠️ 该页渲染失败：{str(e)[:160]}")
                with c_b:
                    if st.button("🔍 检测本页栏数", key="np_col"):
                        try:
                            info = detect_columns(doc[int(p) - 1])
                            if info.get("n_cols") == 2:
                                g = info.get("gap") or (0, 0)
                                st.success(f"📑 双栏：左 {info.get('left')} 块 / 右 {info.get('right')} 块，"
                                           f"中缝 {g[0]:.0f}–{g[1]:.0f}pt")
                            else:
                                st.info(f"📄 单栏（文本块 {info.get('n_blocks')} 个，未发现 ≥ {12:g}pt 中缝）")
                        except Exception as e:
                            st.warning(f"⚠️ 栏数检测失败：{str(e)[:160]}")
            except Exception as e:
                st.warning(f"⚠️ 原生渲染区异常：{str(e)[:160]}")

            with st.expander("📚 长文档批量扫描（性能 / 内存加固）", expanded=False):
                try:
                    c1, c2, c3 = st.columns([1, 1, 1])
                    with c1:
                        s_pg = st.number_input("起始页", 1, tps, value=1, key="ld_start")
                    with c2:
                        e_pg = st.number_input("结束页", 1, tps, value=min(tps, 120), key="ld_end")
                    with c3:
                        do_fig = st.checkbox("抽取页面图片", value=False, key="ld_fig")
                    st.caption(f"单页处理超过 {PAGE_TIMEOUT:.0f}s 自动跳过并记录；每 {PROGRESS_EVERY} 页刷新进度；"
                               f"图片临时目录 try/finally 清理 + 清理 > {FIG_TTL_HOURS}h 的陈旧 fig_* 目录。")
                    if st.button("🚀 开始批量扫描", key="ld_go"):
                        _long_doc_scan(doc, int(s_pg), int(e_pg), bool(do_fig))
                    if st.button("🧹 立即清理陈旧图片临时目录", key="ld_clean"):
                        try:
                            n = cleanup_stale_figure_dirs(FIG_TTL_HOURS)
                            st.success(f"✅ 已清理 {n} 个陈旧 fig_* 临时目录（> {FIG_TTL_HOURS}h）。")
                        except Exception as e:
                            st.warning(f"⚠️ 清理失败：{str(e)[:120]}")
                except Exception as e:
                    st.warning(f"⚠️ 长文档扫描区异常：{str(e)[:160]}")

        # ---------- 2 中英同步滚动（A2 + B3） ----------
        with tabs[1]:
            try:
                p2 = st.number_input("页码", 1, tps, value=default_page, key="sp")
                if st.button("📥 加载并逐段翻译（段落对齐）", key="sp_go"):
                    if not getattr(getattr(ctx, "llm", None), "available", False):
                        st.warning("⚠️ 未检测到可用 API Key，无法调用翻译模型。")
                        st.caption("🛠️ 怎么办：在左侧「接入方式」填写 Key 后重试。")
                    else:
                        paras = _page_paragraphs(doc, int(p2))
                        if not paras:
                            st.error("该页文本过少，可能是扫描件（无文本层）。")
                        else:
                            pairs, _errs = _translate_pairs(ctx, paras, label="逐段翻译")
                            try:
                                embed_html(bilingual_html(pairs), height=760, scrolling=False)
                            except Exception as e:
                                st.warning(f"⚠️ 双语视图渲染失败：{str(e)[:160]}")
            except Exception as e:
                st.warning(f"⚠️ 同步滚动区异常：{str(e)[:160]}")

        # ---------- 3 原位 PDF（A4） ----------
        with tabs[2]:
            try:
                p3 = st.number_input("页码", 1, tps, value=default_page, key="pp")
                if st.button("⚡ 翻译并生成原位 PDF", key="pp_go"):
                    if not getattr(getattr(ctx, "llm", None), "available", False):
                        st.warning("⚠️ 未检测到可用 API Key，无法调用翻译模型。")
                    else:
                        p3 = min(max(1, int(p3)), tps)
                        try:
                            raw = doc[p3 - 1].get_text()[:3000]
                        except Exception as e:
                            st.warning(f"⚠️ 读取第 {p3} 页失败：{str(e)[:120]}")
                            raw = ""
                        if not raw.strip():
                            st.error("该页无可提取文本，可能是扫描件。")
                        else:
                            zh, err = _safe_translate(ctx, raw)
                            if not zh:
                                st.warning(f"⚠️ 翻译失败（{err}），已中止原位 PDF 生成。")
                                st.caption("🛠️ 怎么办：检查 API Key / 网络后重试；或改用「🖱️ 划词翻译」手动翻译少量文本。")
                            else:
                                tmp_dir = None
                                out = None
                                probe = _new_probe("原位 PDF 生成")
                                probe.start()
                                try:
                                    with st.spinner("⚡ 正在生成原位 PDF…"):
                                        tmp_dir = tempfile.mkdtemp(prefix="fig_inplace_")
                                        out = os.path.join(tmp_dir, f"translated_p{p3}.pdf")
                                        in_place_pdf(doc, p3, zh, out)
                                    probe.sample()
                                    if not out or not os.path.exists(out):
                                        st.error("❌ PDF 生成失败：输出文件不存在。")
                                    else:
                                        with open(out, "rb") as fh:
                                            data = fh.read()
                                        if not data:
                                            st.error("❌ PDF 生成失败：输出为空文件。")
                                        else:
                                            st.success(f"✅ 已生成（{len(data) / 1024:.0f} KB）· "
                                                       f"峰值内存 {probe.peak_mb():.0f} MB / "
                                                       f"耗时 {probe.elapsed():.1f} 秒")
                                            st.download_button("📥 下载", data,
                                                               file_name=f"translated_p{p3}.pdf",
                                                               mime="application/pdf",
                                                               key="pp_dl")
                                except Exception as e:
                                    st.warning(f"⚠️ 原位 PDF 生成失败：{str(e)[:160]}")
                                finally:
                                    # 临时目录 try/finally 清理
                                    if tmp_dir:
                                        try:
                                            shutil.rmtree(tmp_dir, ignore_errors=True)
                                        except Exception:
                                            pass
            except Exception as e:
                st.warning(f"⚠️ 原位 PDF 区异常：{str(e)[:160]}")

        # ---------- 4 引用双向跳转（T1：正文 [n] ↔ References 条目） ----------
        with tabs[3]:
            try:
                ref_page = None
                try:
                    ref_page = find_reference_start_page(doc)
                except Exception:
                    ref_page = None
                refs, meta = _ensure_ref_index(doc, doc_id)

                if not refs:
                    st.warning("⚠️ 未能从该 PDF 抽取到 References 列表"
                               f"（{meta.get('message') or '未定位到 References 标题'}）。"
                               "正文引用标记仍会显示，但不提供跳转。")
                    st.caption("🛠️ 怎么办：该 PDF 可能是扫描件（无文本层）或参考文献标题非标准；"
                               "可改选带文本层的 PDF，或临时上传。")
                else:
                    st.success(f"✅ References 抽取成功：{len(refs)} 条"
                               f"（来源：{meta.get('source') or '-'}"
                               f"{'，标题页 p%d' % ref_page if ref_page else ''}）")

                c1, c2 = st.columns([1, 1])
                with c1:
                    bs = st.number_input("正文起始页", 1, max(1, tps), value=1, key="cj_s")
                with c2:
                    be_def = (ref_page - 1) if (ref_page and ref_page > 1) else min(tps, 12)
                    be_def = min(max(1, int(be_def)), max(1, tps))
                    be = st.number_input("正文结束页", 1, max(1, tps), value=be_def, key="cj_e")
                cit = st.text_input("按编号快速定位单条（如 11，可留空）", key="cit")

                if st.button("🔗 生成双向跳转视图", key="cit_go"):
                    probe = _new_probe("引用双向跳转")
                    probe.start()
                    try:
                        if not refs:
                            st.info("ℹ️ 当前 PDF 无 References 索引，已转为单条回溯定位模式。")
                            body, ref, scanned = _find_citation(doc, cit or "1", tps)
                            try:
                                embed_html(citation_jump_html(body or "未找到正文位置。",
                                                              ref or "未找到参考文献条目。"),
                                           height=560, scrolling=True)
                            except Exception as e:
                                st.warning(f"⚠️ 跳转视图渲染失败：{str(e)[:160]}")
                            st.caption(f"🔎 已回溯扫描 {scanned} 页（上限 {CITE_SCAN_CAP}）")
                        else:
                            s_i, e_i = int(bs), int(be)
                            if e_i < s_i:
                                s_i, e_i = e_i, s_i
                            if (e_i - s_i + 1) > CITE_BODY_CAP:
                                e_i = s_i + CITE_BODY_CAP - 1
                                st.caption(f"ℹ️ 正文窗口已限制为 {CITE_BODY_CAP} 页，避免长文档卡顿。")
                            body_pages = []
                            for pno in range(s_i, e_i + 1):
                                try:
                                    body_pages.append((pno, doc[pno - 1].get_text() or ""))
                                except Exception:
                                    body_pages.append((pno, ""))
                            html, cst = citation_links_html(body_pages, refs)
                            try:
                                embed_html(html, height=780, scrolling=True)
                            except Exception as e:
                                st.warning(f"⚠️ 跳转视图渲染失败：{str(e)[:160]}")
                            st.success(
                                f"🔗 双向跳转已生成：正文 {cst.get('pages', 0)} 页 / "
                                f"引用标记 {cst.get('markers', 0)} 个"
                                f"（可跳转 {cst.get('linked', 0)}、未匹配 {cst.get('unlinked', 0)}）/ "
                                f"引用出现 {cst.get('occurrences', 0)} 处 / "
                                f"References {cst.get('refs', 0)} 条"
                                f"（其中 {cst.get('refs_in_body', 0)} 条在正文中被引用）")
                            st.caption("· 正向：点击正文里蓝色 [n] → 右侧定位并高亮第 n 条；"
                                       "悬停显示该条标题前 60 字。"
                                       "· 反向：点击条目下方「↩ 回到正文」→ 循环定位第 1/m、2/m… 处引用。"
                                       "全程不刷新页面、不重置翻译状态、不改变当前页码。")
                    except Exception as e:
                        st.warning(f"⚠️ 引用跳转视图生成失败：{str(e)[:160]}")
                    finally:
                        try:
                            st.caption(f"🔎 峰值内存 {probe.peak_mb():.0f} MB / "
                                       f"耗时 {probe.elapsed():.1f} 秒")
                        except Exception:
                            pass

                # 按编号快速定位单条（无需生成整窗视图）
                try:
                    n_raw = re.sub(r"\D", "", str(cit or ""))
                    n = int(n_raw) if n_raw else 0
                except (TypeError, ValueError):
                    n = 0
                if n and refs:
                    hit = [r for r in refs if _safe_int_pos(r[0]) == n]
                    if hit:
                        st.markdown(f"**[{n}]** {str(hit[0][1])[:400]}")
                    else:
                        st.caption(f"References 中没有编号 {n} 的条目（共 {len(refs)} 条）。")
            except Exception as e:
                st.warning(f"⚠️ 引用跳转区异常：{str(e)[:160]}")

        # ---------- 5 划词翻译（A3） ----------
        with tabs[4]:
            try:
                p5 = st.number_input("页码", 1, tps, value=default_page, key="hp")
                if st.button("📥 加载该页", key="hp_go"):
                    try:
                        st.session_state["hl"] = (doc[int(p5) - 1].get_text() or "")[:4500]
                    except Exception as e:
                        st.session_state["hl"] = ""
                        st.warning(f"⚠️ 第 {p5} 页读取失败：{str(e)[:120]}")
                hl = str(st.session_state.get("hl") or "")
                if hl:
                    st.caption("在下方用鼠标划选要翻译的词句：选中后自动清空选区并复制到剪贴板，"
                               "再到输入框 Ctrl+V 粘贴后翻译（同一段文本不会重复触发）。")
                    try:
                        embed_html(selectable_text_html(
                            hl, "划选词句 → 自动清空选区 + 复制，再到下方输入框 Ctrl+V"),
                            height=430, scrolling=True)
                    except Exception as e:
                        st.warning(f"⚠️ 可选中文本渲染失败：{str(e)[:120]}")
                elif not getattr(getattr(ctx, "llm", None), "available", False):
                    st.caption("请先在左侧填入 API Key。")
                else:
                    st.caption("点击「📥 加载该页」后即可划选翻译。")

                frag = st.text_area("选中/粘贴原文片段", height=110, key="hl_frag")
                if st.button("🌐 翻译该片段", key="hl_tr") and str(frag or "").strip():
                    if not getattr(getattr(ctx, "llm", None), "available", False):
                        st.warning("⚠️ 未检测到可用 API Key，无法调用翻译模型。")
                    else:
                        zh, err = _safe_translate(ctx, str(frag).strip(), timeout=LLM_CALL_TIMEOUT)
                        if zh:
                            st.markdown("**🇨🇳 译文：**" + zh)
                        else:
                            st.warning(f"⚠️ 翻译失败：{err}")
                            st.caption("🛠️ 怎么办：稍后重试或缩短片段；接口限流时建议分段翻译。")
            except Exception as e:
                st.warning(f"⚠️ 划词翻译区异常：{str(e)[:160]}")

        # ---------- 6 标题树（T2：内置书签 + 推断回退 + 点击定位） ----------
        with tabs[5]:
            try:
                toc, inferred = _ensure_headings(doc, doc_id, tps)
                if inferred:
                    st.caption(f"未检测到内置书签目录，已按字号/粗体/章节编号自动推断 "
                               f"{len(toc)} 条标题（扫描前 {min(tps, TOC_SCAN_CAP)} 页）。"
                               "点击任一标题即可跳转并高亮。")
                elif toc:
                    st.caption(f"内置书签 {len(toc)} 条，点击可跳转并高亮。")
                else:
                    st.caption("未检测到内置书签目录，且未能从正文推断出标题"
                               "（可能是扫描件，或全文无章节编号/字号差异）。")
                shown = toc[:TOC_RENDER_CAP]
                for i, item in enumerate(shown):
                    try:
                        level, title, pg = int(item[0]), str(item[1]), int(item[2])
                    except Exception:
                        continue
                    pg = min(max(1, pg), max(1, int(tps)))
                    if st.button(f"{'　' * (max(1, level) - 1)}{title} · p{pg}", key=f"toc{i}"):
                        try:
                            st.session_state[TOC_JUMP_KEY] = {"page": pg, "title": title}
                        except Exception:
                            pass
                        st.success(f"已定位到第 {pg} 页「{title[:40]}」"
                                   f"（切换到「📄 原生渲染」标签查看并高亮）")
                if len(toc) > len(shown):
                    st.caption(f"（目录共 {len(toc)} 条，首屏渲染前 {len(shown)} 条，避免长文档卡顿）")
            except Exception as e:
                st.warning(f"⚠️ 目录解析失败：{str(e)[:160]}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
