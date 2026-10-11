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
                                citation_jump_html, selectable_text_html, detect_columns)

# ---------------- 常量（集中配置，便于调参） ----------------
JUMP_KEY = "qa_jump_page"          # 与 ui/page_qa.py 约定（只读 + 消费后清理）
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
    ok, val, err = _run_with_timeout(writing.translate_to_chinese, timeout, ctx, txt)
    if ok:
        out = str(val or "").strip()
        if not out:
            return "", "模型返回空内容"
        return out, ""
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

        # ② 消费 qa_jump_page（含 para_idx），得到默认页码
        jump = _consume_qa_jump(ctx, tps)
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
                            h, pw, ph = native_page_html(doc, int(p))
                            embed_html(h, height=min(int(ph * 1.18) + 60, 900), scrolling=True)
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

        # ---------- 4 引用双向跳转 ----------
        with tabs[3]:
            try:
                cit = st.text_input("引用标记（如 13）", key="cit")
                if st.button("🔗 生成双向跳转", key="cit_go") and str(cit).strip():
                    probe = _new_probe("引用定位")
                    probe.start()
                    body, ref, scanned = _find_citation(doc, cit, tps)
                    try:
                        embed_html(citation_jump_html(body or "未找到正文位置。",
                                                      ref or "未找到参考文献条目。"),
                                   height=560, scrolling=True)
                    except Exception as e:
                        st.warning(f"⚠️ 跳转视图渲染失败：{str(e)[:160]}")
                    st.caption(f"🔎 已回溯扫描 {scanned} 页（上限 {CITE_SCAN_CAP}）· "
                               f"峰值内存 {probe.peak_mb():.0f} MB / 耗时 {probe.elapsed():.1f} 秒")
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

        # ---------- 6 标题树 ----------
        with tabs[5]:
            try:
                toc = doc.get_toc() or []
                if not toc:
                    st.caption("未检测到内置书签目录。")
                for i, item in enumerate(toc[:400]):
                    try:
                        level, title, pg = int(item[0]), str(item[1]), int(item[2])
                    except Exception:
                        continue
                    if st.button(f"{'　' * (max(1, level) - 1)}{title} · p{pg}", key=f"toc{i}"):
                        try:
                            st.session_state["read_jump_page"] = pg
                        except Exception:
                            pass
                        st.success(f"已定位到第 {pg} 页（切换到「📄 原生渲染」标签查看）")
            except Exception as e:
                st.warning(f"⚠️ 目录解析失败：{str(e)[:160]}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
