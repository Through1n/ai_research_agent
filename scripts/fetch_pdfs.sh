#!/bin/sh
# 拉取官方测试 PDF（Round 15：pdf/ 不再随仓库分发，改为现拉现用）
#
# 背景：pdf/ 原跟踪 6 个文件共 31 MB，其中 UnderstandingDeepLearning_02_09_26_C.pdf
# 是 22 MB 的受版权保护教材原著。随公开仓库分发它有法律风险，而它又是 541 页长文档
# 压测用例的核心素材。取舍：仓库只保留生成/下载逻辑，PDF 本身不入库。
#
# 六件分三类，处理方式不同：
#   ① arXiv 论文 3 件  → 直接下载（公开预印本，允许再分发）
#   ② 本地夹具 2 件    → 由 scripts/make_scanned.py / make_longbook.py 生成，零外部依赖
#   ③ 官方教材 1 件    → 受版权保护，**本脚本不代下载**，只打印获取方式
#
# 用法：
#   sh scripts/fetch_pdfs.sh            # 拉齐全部可自动获取的 5 件
#   sh scripts/fetch_pdfs.sh --check    # 只检查现状，不下载
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PDF_DIR="$ROOT/pdf"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    # 优先 ai_research 环境（make_* 脚本依赖 pymupdf）；base 环境会误判缺模块
    for c in /c/Users/Administrator/anaconda3/envs/ai_research/python.exe \
             "$(command -v python3 2>/dev/null)" "$(command -v python 2>/dev/null)"; do
        [ -n "$c" ] && [ -x "$c" ] && PY="$c" && break
    done
fi
[ -n "$PY" ] || { echo "✗ 找不到 Python 解释器，请设置 PYTHON=<路径>"; exit 2; }

mkdir -p "$PDF_DIR"

# arXiv 公开预印本：编号 → 目标文件名
ARXIV="1706.03762v1:1706.03762v1.pdf
1706.03762v7:1706.03762v7.pdf
1810.04805v2:1810.04805v2.pdf"

MISSING=0
have() { [ -f "$PDF_DIR/$1" ]; }

echo "=== pdf/ 现状 ==="
for f in 1706.03762v1.pdf 1706.03762v1_img.pdf 1706.03762v7.pdf \
         1810.04805v2.pdf LongBook_540p_fixture.pdf \
         UnderstandingDeepLearning_02_09_26_C.pdf; do
    if have "$f"; then
        echo "  ✓ $f  ($(du -h "$PDF_DIR/$f" | cut -f1))"
    else
        echo "  ✗ $f  缺失"
        MISSING=$((MISSING + 1))
    fi
done

if [ "${1:-}" = "--check" ]; then
    echo ""
    [ "$MISSING" = "0" ] && echo "✅ 6 件齐全" || echo "⚠️ 缺 $MISSING 件（去掉 --check 可自动拉齐其中 5 件）"
    exit 0
fi

echo ""
echo "=== ① 下载 arXiv 论文 ==="
echo "$ARXIV" | while IFS=: read -r id fn; do
    [ -n "$id" ] || continue
    if have "$fn"; then
        echo "  ✓ $fn 已存在，跳过"
        continue
    fi
    echo "  ↓ $fn ← https://arxiv.org/pdf/$id"
    # --retry 3： arXiv 偶发 502；-L 跟随重定向到最终 PDF
    if curl -sSL --retry 3 --max-time 180 -o "$PDF_DIR/$fn" "https://arxiv.org/pdf/$id"; then
        # 简单健全性校验：PDF 头 + 非空
        if [ -s "$PDF_DIR/$fn" ] && head -c 5 "$PDF_DIR/$fn" | grep -q "%PDF-"; then
            echo "    ✓ 完成 ($(du -h "$PDF_DIR/$fn" | cut -f1))"
        else
            echo "    ✗ 下载内容不像 PDF，已删除"; rm -f "$PDF_DIR/$fn"
        fi
    else
        echo "    ✗ 下载失败（网络问题？可稍后重跑本脚本）"
    fi
done

echo ""
echo "=== ② 生成本地夹具 ==="
if have "1706.03762v1_img.pdf"; then
    echo "  ✓ 扫描件已存在，跳过"
elif have "1706.03762v1.pdf"; then
    echo "  ⚙ 生成 1706.03762v1_img.pdf（scripts/make_scanned.py）"
    "$PY" "$ROOT/scripts/make_scanned.py" || echo "    ✗ 生成失败"
else
    echo "  ⚠ 跳过：make_scanned.py 需要先有 1706.03762v1.pdf"
fi

if have "LongBook_540p_fixture.pdf"; then
    echo "  ✓ 长文档夹具已存在，跳过"
else
    echo "  ⚙ 生成 LongBook_540p_fixture.pdf（scripts/make_longbook.py）"
    "$PY" "$ROOT/scripts/make_longbook.py" || echo "    ✗ 生成失败"
fi

echo ""
echo "=== ③ 官方教材（受版权保护，不代下载）==="
if have "UnderstandingDeepLearning_02_09_26_C.pdf"; then
    echo "  ✓ 已存在"
else
    cat <<'EOF'
  ⚠ UnderstandingDeepLearning_02_09_26_C.pdf 未获取。

  它是《Understanding Deep Learning》官方免费教材（作者 Simon J.D. Prince 授权
  官网免费阅读/下载），但**不允许随第三方仓库再分发**，故本脚本不代下载。

  获取方式（二选一）：
    1. 官网：https://udlbook.github.io/  → 页面内 PDF 链接，下载后改名存到
       pdf/UnderstandingDeepLearning_02_09_26_C.pdf
    2. 若只需验证长文档链路，可跳过它：scripts/setup_real_library.py --files
       只列其余 4 件即可，541 页压测用例会缺一本。

  未放置该文件时，以下能力会缺样本（其余功能不受影响）：
    - scripts/verify_official.py 的 541 页长文档基准测试
    - 「🔧 一键重建文献库」里的教材分块/内存探针演示
EOF
fi

echo ""
echo "=== 汇总 ==="
STILL=0
for f in 1706.03762v1.pdf 1706.03762v1_img.pdf 1706.03762v7.pdf \
         1810.04805v2.pdf LongBook_540p_fixture.pdf \
         UnderstandingDeepLearning_02_09_26_C.pdf; do
    have "$f" || STILL=$((STILL + 1))
done
if [ "$STILL" = "0" ]; then
    echo "✅ 6 件齐全，可跑 scripts/setup_real_library.py 重建官方文献库"
else
    echo "⚠️ 仍缺 $STILL 件。缺 arXiv 三件请检查网络后重跑；缺教材见上面第 ③ 节。"
fi
exit 0
