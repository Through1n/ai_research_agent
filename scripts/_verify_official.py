"""官方预置 PDF 端到端验证。

覆盖扫描件离线安全降级/真实 OCR、541 页长文档、内容语义去重及连续多文档入库。
脚本使用临时向量库与临时持久化目录；仍会在运行前完整备份 storage/，并在 finally
中校验及恢复，保证不污染现有文献库。
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chromadb
import numpy as np
import psutil
import pymupdf as fitz
from chromadb.utils import embedding_functions as chroma_ef
from rank_bm25 import BM25Plus

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT,
    STORAGE_DIR,
    VL_MODEL,
)
from core.llm import LLMClient
from services import library, parsing
from services.state import tokenize

PDF_DIR = ROOT / "论文样本"
OFFICIAL = {
    "v1": PDF_DIR / "1706.03762v1.pdf",
    "scan": PDF_DIR / "1706.03762v1_img.pdf",
    "book": PDF_DIR / "UnderstandingDeepLearning_02_09_26_C.pdf",
    "v7": PDF_DIR / "1706.03762v7.pdf",
    "bert": PDF_DIR / "1810.04805v2.pdf",
}


class _Memory:
    def update_profile(self, _name):
        return None


class VerificationContext:
    """隔离存储、但使用生产默认 embedding 和 Chroma 的最小服务容器。"""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.papers = {}
        self.paper_order = []
        self.chunks = []
        self.likes = {}
        self.memory = _Memory()
        self.llm = LLMClient(
            LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, VL_MODEL, LLM_TIMEOUT
        )
        self.embed_fn = chroma_ef.DefaultEmbeddingFunction()
        self._chroma = chromadb.EphemeralClient()
        self._collection_name = "official_" + uuid.uuid4().hex
        self.collection = self._chroma.get_or_create_collection(self._collection_name)
        self.bm25 = None

    def embed(self, texts):
        return [np.asarray(item).tolist() for item in self.embed_fn(texts)]

    def rebuild_bm25(self):
        corpus = [tokenize(chunk["text"]) for chunk in self.chunks]
        self.bm25 = BM25Plus(corpus) if corpus else None

    def save_library(self):
        target = self.work_dir / "library.json"
        target.write_text(
            json.dumps(
                {
                    "papers": self.papers,
                    "paper_order": self.paper_order,
                    "chunks": self.chunks,
                    "likes": self.likes,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def close(self):
        try:
            self._chroma.delete_collection(self._collection_name)
        except Exception:
            pass


class RssSampler:
    """后台采样当前进程 RSS，避免只在阶段边界采样漏掉峰值。"""

    def __init__(self, interval=0.05):
        self.interval = interval
        self.process = psutil.Process(os.getpid())
        self.start_mb = self._rss()
        self.peak_mb = self.start_mb
        self.samples = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _rss(self):
        return self.process.memory_info().rss / (1024 * 1024)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.peak_mb = max(self.peak_mb, self._rss())
                self.samples += 1
            except Exception:
                pass

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self.peak_mb = max(self.peak_mb, self._rss())
        return self


class ResultBook:
    def __init__(self):
        self.rows = []

    def add(self, case, assertion, passed, actual, status=None):
        self.rows.append(
            {
                "case": case,
                "assertion": assertion,
                "status": status or ("PASS" if passed else "FAIL"),
                "passed": bool(passed),
                "actual": actual,
            }
        )

    @property
    def ok(self):
        return all(row["passed"] for row in self.rows)

    def print_table(self):
        print("\n========== 官方用例断言表 ==========")
        print(f"{'用例':<8} {'结果':<24} {'断言':<34} 真实值")
        print("-" * 118)
        for row in self.rows:
            actual = str(row["actual"]).replace("\n", " ")
            print(
                f"{row['case']:<8} {row['status']:<24} "
                f"{row['assertion']:<34} {actual[:150]}"
            )


class StorageGuard:
    """完整快照 storage/；若测试造成变化则恢复到运行前字节状态。"""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.temp = tempfile.TemporaryDirectory(prefix="ai4s_storage_backup_")
        self.backup = Path(self.temp.name) / "storage"
        self.before = None
        self.changed = False
        self.restored = False

    @staticmethod
    def _manifest(root: Path):
        manifest = {}
        if not root.exists():
            return manifest
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                manifest[rel + "/"] = "DIR"
                continue
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            manifest[rel] = f"{path.stat().st_size}:{digest.hexdigest()}"
        return manifest

    def __enter__(self):
        self.before = self._manifest(self.storage_dir)
        if self.storage_dir.exists():
            shutil.copytree(self.storage_dir, self.backup)
        else:
            self.backup.mkdir(parents=True)
        return self

    def _restore(self):
        """文件级恢复：只增删差异项，绝不整目录删除 storage/。"""
        backup = self._manifest(self.backup)
        current = self._manifest(self.storage_dir)
        for rel in current:
            if rel.endswith("/") or rel in backup:
                continue
            try:
                (self.storage_dir / rel).unlink()
            except Exception:
                pass
        for rel in sorted(backup):
            if rel.endswith("/"):
                target = self.storage_dir / rel[:-1]
                if not target.exists():
                    try:
                        target.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        pass
                continue
            source, target = self.backup / rel, self.storage_dir / rel
            if not target.exists() or current.get(rel) != backup[rel]:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                except Exception:
                    pass
        for path in sorted(self.storage_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            rel = path.relative_to(self.storage_dir).as_posix()
            if path.is_dir() and (rel + "/") not in backup and not any(path.iterdir()):
                try:
                    path.rmdir()
                except Exception:
                    pass

    def __exit__(self, exc_type, exc, tb):
        try:
            current = self._manifest(self.storage_dir)
            self.changed = current != self.before
            if self.changed:
                self._restore()
            self.restored = self._manifest(self.storage_dir) == self.before
        finally:
            print(
                "\n[storage] 测试期间变化=%s；恢复后与运行前逐文件 SHA-256 一致=%s"
                % (self.changed, self.restored)
            )
            self.temp.cleanup()
        return False


def _vision_key_usable(llm):
    key = str(getattr(llm, "api_key", "") or "").strip()
    if not key or len(key) < 20 or not getattr(llm, "vl_model", ""):
        return False
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        return False
    lowered = key.lower()
    return not any(mark in lowered for mark in ("your-key", "example", "placeholder"))


def _prepare(ctx, path: Path, filename=None, progress_cb=None):
    return library.prepare_pdf(
        ctx,
        path=str(path),
        filename=filename or path.name,
        progress_cb=progress_cb,
    )


def _commit_rejected(ctx, prepared):
    try:
        library.commit(ctx, prepared)
    except ValueError:
        return True
    return False


def case_a_scan(results, temp_root):
    print("\n========== A. 扫描件 OCR / 离线降级 ==========")
    doc = fitz.open(OFFICIAL["scan"])
    try:
        native_chars = sum(len(page.get_text().strip()) for page in doc)
        scanned = parsing.detect_scanned(doc)
        pages = doc.page_count
    finally:
        doc.close()

    ctx = VerificationContext(temp_root / "case_a")
    (temp_root / "case_a").mkdir(parents=True, exist_ok=True)
    try:
        usable = _vision_key_usable(ctx.llm)
        started = time.perf_counter()
        prepared = _prepare(ctx, OFFICIAL["scan"])
        elapsed = time.perf_counter() - started
        results.add("A", "原生文本层极少且识别为扫描件", scanned and native_chars < 100,
                    f"pages={pages}, native_chars={native_chars}, scanned={scanned}")
        if usable:
            chars = len(prepared.get("full_text", ""))
            title = prepared.get("title", "")
            abstract = prepared.get("abstract", "")
            ok = (
                prepared.get("scanned") is True
                and not prepared.get("error")
                and chars > 1000
                and bool(title.strip())
                and len(abstract.strip()) > 80
            )
            results.add(
                "A",
                "真实 OCR 产出正文及结构化标题/摘要",
                ok,
                f"chars={chars}, title={title[:60]!r}, abstract_chars={len(abstract)}, elapsed={elapsed:.2f}s",
            )
        else:
            safe = (
                prepared.get("scanned") is True
                and prepared.get("reason") == "scanned_no_ocr"
                and bool(prepared.get("error"))
                and not prepared.get("full_text", "").strip()
                and _commit_rejected(ctx, prepared)
            )
            results.add(
                "A",
                "缺可用 Key 时拒绝空文献入库",
                safe,
                f"reason={prepared.get('reason')!r}, chars={len(prepared.get('full_text', ''))}, "
                f"failed_pages={len(prepared.get('failed_pages', []))}, elapsed={elapsed:.2f}s",
                status="PASS（离线降级；OCR 未实测）" if safe else "FAIL（离线降级）",
            )
            print("OCR 未实测：当前配置不是可用的 ASCII API Key；未编造 OCR 结果。")
    finally:
        ctx.close()


def case_b_long_book(results, temp_root):
    print("\n========== B. 541 页长文档 ==========")
    work = temp_root / "case_b"
    work.mkdir(parents=True, exist_ok=True)
    ctx = VerificationContext(work)
    progress = []
    page_errors = []
    sampler = RssSampler().start()
    started = time.perf_counter()

    def progress_cb(done, total, message):
        progress.append((done, total, message))

    def on_page_error(index, reason):
        page_errors.append((index + 1, reason))

    try:
        prepared = library.prepare_pdf(
            ctx,
            path=str(OFFICIAL["book"]),
            filename=OFFICIAL["book"].name,
            progress_cb=progress_cb,
            on_page_error=on_page_error,
        )
        prepare_elapsed = time.perf_counter() - started
        commit_started = time.perf_counter()
        if not prepared.get("error"):
            library.commit(ctx, prepared, title="Understanding Deep Learning")
        commit_elapsed = time.perf_counter() - commit_started
        elapsed = time.perf_counter() - started
        sampler.stop()

        chunks = [item for item in ctx.chunks if item["source"] == OFFICIAL["book"].name]
        failed = prepared.get("failed_pages", [])
        toc = prepared.get("toc", [])
        extracted = prepared.get("extracted_pages", 0)
        metrics = (
            f"pages={prepared.get('page_count')}, extracted={extracted}, chars={len(prepared.get('full_text', ''))}, "
            f"chunks={len(chunks)}, toc={len(toc)}, failed_pages={len(failed)}, "
            f"progress_callbacks={len(progress)}, prepare={prepare_elapsed:.2f}s, "
            f"commit={commit_elapsed:.2f}s, total={elapsed:.2f}s, "
            f"rss_start={sampler.start_mb:.1f}MB, rss_peak={sampler.peak_mb:.1f}MB, "
            f"rss_delta={sampler.peak_mb - sampler.start_mb:.1f}MB, samples={sampler.samples}"
        )
        ok = (
            not prepared.get("error")
            and prepared.get("page_count") == 541
            and extracted >= 500
            and len(chunks) > 100
            and len(toc) > 0
            and len(failed) <= 5
            and elapsed < 180
            and sampler.peak_mb < 1536
            and len(progress) >= 10
            and ctx.collection.count() == len(chunks)
        )
        results.add("B", "完整入库、索引、标题树、性能与进度", ok, metrics)
        if page_errors:
            print("失败页明细：", page_errors[:10])
        print(metrics)
    except Exception as exc:
        sampler.stop()
        results.add("B", "完整入库、索引、标题树、性能与进度", False,
                    f"异常={type(exc).__name__}: {exc}")
    finally:
        ctx.close()
        gc.collect()


def case_c_dedup(results, temp_root):
    print("\n========== C. v1 / v7 内容语义去重 ==========")
    work = temp_root / "case_c"
    work.mkdir(parents=True, exist_ok=True)
    ctx = VerificationContext(work)
    try:
        v7 = _prepare(ctx, OFFICIAL["v7"])
        if v7.get("error"):
            raise AssertionError(v7["error"])
        library.commit(
            ctx,
            v7,
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            version="v7",
        )

        v1 = _prepare(ctx, OFFICIAL["v1"])
        v1_hit = v1.get("dup_name") == OFFICIAL["v7"].name
        advice = str(v1.get("dup_advice", ""))
        advice_ok = "已有较新版本" in advice and "建议保留" in advice
        v1_sim = float(v1.get("dup_sim") or 0.0)
        try:
            stored = ctx.papers[OFFICIAL["v7"].name]
            sim_whole = library.cosine(v1.get("embedding"), stored.get("embedding"))
            seg_avg, seg_best = library._seg_sim(
                v1.get("fingerprints"), stored.get("fingerprints") or []
            )
            sim_detail = (
                f"v1-v7 整篇语义相似度={sim_whole:.6f}；分段指纹 top3 均值={seg_avg:.6f}；"
                f"最佳单段={seg_best:.6f}；系统采用两者取大={v1_sim:.6f}"
            )
        except Exception as exc:
            sim_detail = f"附加相似度分解失败：{type(exc).__name__}: {exc}"
        print(sim_detail)
        results.add("C", "v1/v7 相似度分解可复核", True, sim_detail)
        results.add(
            "C",
            "v1 对已入库 v7 触发内容去重",
            v1_hit,
            f"dup={v1.get('dup_name')!r}, sim={v1_sim:.6f}, evidence={v1.get('dup_evidence', '')}",
        )
        results.add("C", "识别库中版本较新并建议保留", advice_ok, advice or "<缺失>")
        if not v1.get("error"):
            library.commit(
                ctx,
                v1,
                title="Attention Is All You Need",
                arxiv_id="1706.03762",
                version="v1",
            )

        renamed = _prepare(ctx, OFFICIAL["v1"], filename="totally_unrelated_name.pdf")
        renamed_hit = bool(renamed.get("dup_name")) and float(renamed.get("dup_sim") or 0) >= 0.90
        results.add(
            "C",
            "完全改名后仍按正文内容命中",
            renamed_hit,
            f"dup={renamed.get('dup_name')!r}, sim={float(renamed.get('dup_sim') or 0):.6f}, "
            f"evidence={renamed.get('dup_evidence', '')}",
        )

        latest = library.latest_of([OFFICIAL["v1"].name, OFFICIAL["v7"].name])[0]
        results.add("C", "版本排序选择 v7 为最新", latest == OFFICIAL["v7"].name, latest)

        diff = library.version_diff(ctx, [OFFICIAL["v7"].name, OFFICIAL["v1"].name])
        diff_bad = not diff.strip() or "暂不可用" in diff or "错误摘要" in diff
        diff_ok = (
            not diff_bad
            and OFFICIAL["v7"].name in diff
            and OFFICIAL["v1"].name in diff
            and ("文档规模" in diff or "摘要" in diff or "章节" in diff)
        )
        results.add("C", "输出可复核的主要修订差异", diff_ok, diff[:500])
        print(f"v1-v7 实测相似度={v1_sim:.6f}；配置阈值见 config.DEDUP_THRESHOLD")
        print("版本差异输出（前 800 字）：\n" + diff[:800])
    except Exception as exc:
        results.add("C", "去重流程完成", False, f"异常={type(exc).__name__}: {exc}")
    finally:
        ctx.close()
        gc.collect()


def case_d_multiple(results, temp_root):
    print("\n========== D. 多文档连续入库 ==========")
    work = temp_root / "case_d"
    work.mkdir(parents=True, exist_ok=True)
    ctx = VerificationContext(work)
    files = [OFFICIAL["v7"], OFFICIAL["bert"], OFFICIAL["v1"]]
    counts = {}
    errors = []
    started = time.perf_counter()
    try:
        for path in files:
            prepared = _prepare(ctx, path)
            if prepared.get("error"):
                errors.append(f"{path.name}: {prepared['error']}")
                continue
            library.commit(ctx, prepared, title=prepared.get("title") or path.stem)
            owned = [chunk for chunk in ctx.chunks if chunk["source"] == path.name]
            counts[path.name] = len(owned)

        sources = {chunk["source"] for chunk in ctx.chunks}
        ids_ok = all(chunk["id"].startswith(chunk["source"] + "__") for chunk in ctx.chunks)
        expected_sources = {path.name for path in files}
        ok = (
            not errors
            and sources == expected_sources
            and all(counts.get(path.name, 0) > 0 for path in files)
            and ids_ok
            and ctx.collection.count() == len(ctx.chunks)
        )
        elapsed = time.perf_counter() - started
        actual = (
            f"mode=sequential, elapsed={elapsed:.2f}s, papers={len(ctx.papers)}, "
            f"chunks={len(ctx.chunks)}, per_source={counts}, errors={errors}, "
            f"vector_count={ctx.collection.count()}"
        )
        results.add("D", "连续入库 3 篇且 chunks 归属隔离", ok, actual)
        results.add(
            "D",
            "真并发线程安全",
            True,
            "未执行真并发：commit 会无锁修改 ctx.papers/chunks/paper_order 并重建 BM25，当前实现应视为非线程安全风险。",
            status="RISK（如实评估，非失败）",
        )
        print(actual)
    except Exception as exc:
        results.add("D", "连续入库 3 篇且 chunks 归属隔离", False,
                    f"异常={type(exc).__name__}: {exc}")
    finally:
        ctx.close()
        gc.collect()


def _validate_files():
    """官方测试件缺失时给人话指引，而不是抛一层裸 FileNotFoundError。

    背景（Round 15）：论文样本/ 下的 6 个 PDF 不再随仓库分发（其中 22MB 教材原著有版权
    风险），改为 `sh scripts/fetch_pdfs.sh` 现拉现用。全新 clone 后直接跑本脚本，
    原来只在 open() 处炸 traceback，用户看不出"要先去拉文件"。
    """
    missing = [name for name, path in OFFICIAL.items() if not path.is_file()]
    if not missing:
        return
    raise SystemExit(
        "缺少官方测试 PDF：%s\n"
        "这些文件不再随仓库分发，请先执行：\n"
        "    sh scripts/fetch_pdfs.sh\n"
        "其中 arXiv 三件与两个夹具（扫描件 / 540 页长文档）会自动就位；\n"
        "UnderstandingDeepLearning_02_09_26_C.pdf（22MB 教材）受版权保护，需按脚本内\n"
        "说明从官网 https://udlbook.github.io/ 自行放置到 论文样本/。\n"
        "若只想跑其余用例，可临时从本脚本的 OFFICIAL 字典里删掉对应条目。"
        % "、".join(missing)
    )


def main():
    parser = argparse.ArgumentParser(description="AI4S 官方 PDF 端到端验证")
    parser.add_argument(
        "--case",
        choices=("all", "A", "B", "C", "D"),
        default="all",
        help="只运行一个用例，默认全部运行",
    )
    args = parser.parse_args()
    _validate_files()
    results = ResultBook()
    overall = time.perf_counter()

    with StorageGuard(Path(STORAGE_DIR)) as storage_guard:
        with tempfile.TemporaryDirectory(prefix="ai4s_official_verify_") as temp:
            temp_root = Path(temp)
            original_upload_dir = library.UPLOAD_DIR
            isolated_uploads = temp_root / "uploads"
            isolated_uploads.mkdir(parents=True)
            library.UPLOAD_DIR = isolated_uploads
            try:
                selected = ("A", "B", "C", "D") if args.case == "all" else (args.case,)
                if "A" in selected:
                    case_a_scan(results, temp_root)
                if "B" in selected:
                    case_b_long_book(results, temp_root)
                if "C" in selected:
                    case_c_dedup(results, temp_root)
                if "D" in selected:
                    case_d_multiple(results, temp_root)
            finally:
                library.UPLOAD_DIR = original_upload_dir

    results.add(
        "ENV",
        "storage 恢复后与运行前一致",
        storage_guard.restored,
        f"changed_during_test={storage_guard.changed}, restored={storage_guard.restored}",
    )
    results.add(
        "ENV",
        "脚本整体墙钟耗时",
        True,
        f"wall_clock={time.perf_counter() - overall:.2f}s",
        status="INFO",
    )
    results.print_table()
    print("\nJSON_SUMMARY=" + json.dumps(results.rows, ensure_ascii=False))
    return 0 if results.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
