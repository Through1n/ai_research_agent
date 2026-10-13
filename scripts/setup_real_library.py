"""用真实 PDF 重建文献库（R9 官方重建入口，任何人可复现）。

背景（R9 头号缺陷）：仓库里的 storage/library.json 曾是测试夹具——3 篇文献只有
4 个 chunk 且全部来自第 1 页，用户在问答/图谱/写作/对比页只能检索到首页片段。
本脚本用 pdf/ 下的真实 PDF 走完整入库路径（分 20 页批 + 单页 30s 超时 +
扫描件 OCR）重建文献库，并打印实测表。

用法（必须用 ai_research 环境的 Python，base 环境会误判缺模块）：
    python scripts/setup_real_library.py   # 须用 ai_research 环境的 Python（base 环境会误判缺模块）

可选参数：
    --pdf-dir <目录>     指定 PDF 目录（默认 <项目根>/pdf）
    --files a.pdf,b.pdf  只重建指定文件（默认官方 5 件，见下）
    --no-reset           不清空全库，只覆盖同名官方文献
                         （默认全新重建：先自动备份 storage/ 再清空）

入库的官方测试件（pdf/ 下，共 5 件）：
    1706.03762v7.pdf                     15 页 文本层  Attention Is All You Need v7
    1706.03762v1.pdf                     15 页 文本层  v1（复现 v1/v7 语义查重）
    1810.04805v2.pdf                     16 页 文本层  BERT
    1706.03762v1_img.pdf                 15 页 扫描件  无文本层，走多模态 OCR（需有效 Key）
    UnderstandingDeepLearning_02_09_26_C.pdf   541 页 / 22MB 官方教材（重点用例）

耗时预期（2026-10-12 本机实测，供参考，机器不同会有差异）：
    4 篇文本层 PDF 合计约 5-8s；扫描件 15 页 OCR 约 1-3min（取决于视觉模型速度）；
    541 页教材约 40-90s（解析 + 本地 embedding 入库）。全程约 2-5 分钟。

失败如何处理（都不中断整体重建，逐篇记录，最终汇总）：
    - 单页解析超过 30s 或抛错 → 记入 failed_pages，跳过该页继续后续页；
    - 整篇失败（损坏/加密/0 页/扫描件但无可用 OCR Key）→ 该篇标记 FAIL 并继续下一篇；
    - 成功提取页 < 声称页数 70% → 判为截断，该篇不入库（防止静默丢正文）；
    - 扫描件无有效 Key：v1_img 会 FAIL（reason=scanned_no_ocr），其余 4 篇不受影响；
    - reset 模式清空前自动把 storage/library.json 与 storage/chroma/ 备份到
      storage/backup_YYYYMMDD_HHMMSS/（chroma 被进程占用时跳过并告警，不影响重建）；
    - 退出码：全部成功 0；有任一篇失败 1。

重建后核对（任选其一）：
    python -c "import json;lib=json.load(open('storage/library.json',encoding='utf-8'));\
print('chunks',len(lib['chunks']),'papers',len(lib['papers']))"
    或打开应用「📁 文献库管理」页查看「🩺 文献库健康检查」与「当前文献库」。
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.state import Container          # noqa: E402
from services import library                  # noqa: E402


def _fmt_pages(pages):
    if not pages:
        return "—"
    return f"{pages[0]}–{pages[-1]}（{len(pages)} 页）"


def main():
    parser = argparse.ArgumentParser(description="用真实 PDF 重建文献库")
    parser.add_argument("--pdf-dir", default=str(ROOT / "pdf"), help="PDF 目录")
    parser.add_argument("--files", default="", help="逗号分隔的文件名列表（默认官方 5 件）")
    parser.add_argument("--no-reset", action="store_true", help="不清空全库，只覆盖同名文献")
    args = parser.parse_args()

    filenames = [s.strip() for s in args.files.split(",") if s.strip()] or None

    print("=" * 96)
    print("AI4S 文献库重建（真实 PDF）")
    print("=" * 96)
    print(f"Python      : {sys.executable}")
    print(f"PDF 目录    : {args.pdf_dir}")
    print(f"重建清单    : {', '.join(filenames or library.OFFICIAL_LIBRARY_PDFS)}")
    print(f"模式        : {'覆盖同名文献（不清库）' if args.no_reset else '全新重建（先备份+清库）'}")

    ctx = Container()
    llm = getattr(ctx, "llm", None)
    available = bool(getattr(llm, "available", False))
    ocr_ok = False
    try:
        from services import parsing
        ocr_ok = bool(available and parsing.ocr_supported(llm))
    except Exception:
        pass
    # 只打印可用性与模型名，绝不打印 Key 内容
    print(f"LLM 可用    : {available}（模型 {getattr(llm, 'model', '?')} / "
          f"视觉 {getattr(llm, 'vl_model', '?')}）")
    print(f"OCR 可用    : {ocr_ok}"
          + ("" if ocr_ok else "（扫描件 1706.03762v1_img.pdf 将失败，其余不受影响）"))

    failed_pages_log = []
    t_start = time.time()

    def _progress(done, total, msg):
        print(f"  … {done}/{total} 页（{done / max(total, 1):.0%}）{msg}"
              f"｜总已用 {time.time() - t_start:.0f}s")

    def _page_error(page_index, reason):
        failed_pages_log.append((page_index + 1, str(reason)[:160]))

    report = library.rebuild_from_pdf_dir(
        ctx, pdf_dir=args.pdf_dir, filenames=filenames,
        reset_first=not args.no_reset, progress_cb=_progress, page_error_cb=_page_error,
    )

    # ---------------- 实测表 ----------------
    print("\n" + "=" * 96)
    print("重建实测表")
    print("=" * 96)
    header = (f"{'文献':<38}{'PDF页':>5}{'扫描':>5}{'chunk':>7}"
              f"{'覆盖页':>16}{'失败页':>6}{'耗时s':>8}  结果")
    print(header)
    print("-" * 96)
    for r in report["results"]:
        status = "✅ 已入库" if r["ok"] else f"❌ {str(r.get('error', ''))[:40]}"
        print(f"{r['filename']:<38}{r['page_count']:>5}"
              f"{('是' if r['scanned'] else '否'):>5}{r['chunks']:>7}"
              f"{_fmt_pages(r['pages']):>16}{len(r['failed_pages']):>6}"
              f"{r['elapsed']:>8.1f}  {status}")
        if r.get("dup_name"):
            print(f"{'':<38}└ 查重命中《{r['dup_name']}》相似度 {r['dup_sim']:.1%}"
                  f"（按“保留两个版本”处理，用于版本对比演示）")
    for s in report["skipped"]:
        print(f"{s['filename']:<38}⏭️ 跳过：{s['error']}")

    print("-" * 96)
    print(f"文献库现状：{len(ctx.files)} 篇 · chunks 总数 {report['total_chunks']} · "
          f"总耗时 {report['elapsed']:.1f}s")
    try:
        vector_ids = ctx.collection.get().get("ids", [])
        print(f"向量库核对：chroma 向量 {len(vector_ids)} 条（应等于 chunks 总数）")
    except Exception as exc:
        print(f"向量库核对失败：{exc}")

    if failed_pages_log:
        print(f"\n⚠️ 失败页合计 {len(failed_pages_log)} 页：")
        for page, reason in failed_pages_log[:40]:
            print(f"  - 第 {page} 页：{reason}")
    else:
        print("\n✅ 无失败页（全部页面解析成功）")

    if report["backup"]:
        print(f"\n🗂️ 重建前已备份：{report['backup']}"
              f"（{', '.join(report['backup_files']) or '无'}）")
    for w in report["backup_warnings"]:
        print(f"⚠️ {w}")

    ok = bool(report["ok"]) and not report["skipped"]
    print("\n" + ("✅ 重建完成：全部官方文献入库成功" if ok else "❌ 重建存在失败项（见上表）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
