#!/bin/sh
# start_app.sh 预置逻辑的隔离测试台（不动真实 HOME，不动真实缓存）
#
# ⚠️ 本测试的第一版栽过一个跟头：场景 1 用**合成**的 onnx_models 目录当源，
#    而合成目录恰好符合 start_app.sh 的期望，于是测试全绿——但**真实部署包**的
#    文件少了一层 onnx/ 子目录，预置在线上会静默失败。故本版把「用真实部署包
#    当源」提为场景 1，并用 chroma 自己的判据反向校验布局。
#
# 用法：sh scripts/_test_preseed.sh [bundle_dir]
BUNDLE="${1:-/c/Users/Administrator/ai4s_deploy_bundle}"
T=/tmp/ai4s_preseed_test
rm -rf "$T"; mkdir -p "$T"

# 抽取 start_app.sh 里的「HOME 兜底 + 预置块」（从 HOME 判断到 binding 行之前），
# 避免把 exec streamlit 也拖进来（那会在测试机上真起服务）。
sed -n '/^if \[ -z "\$HOME" \]/,/^echo "\[start\] binding/p' \
    "$BUNDLE/start_app.sh" | sed '$d' > "$T/block.sh"

echo "===== 被测代码块（$(wc -l < "$T/block.sh") 行）====="
cat "$T/block.sh"
echo "===== 开始测试 ====="

run_case() {
    _name="$1"; _home="$2"; _src="$3"
    printf '\n--- %s ---\n' "$_name"
    ( cd "$_src" && HOME="$_home" sh "$T/block.sh" )
}

FAIL=0
chk() { if [ "$1" = "0" ]; then echo "  ✓ $2"; else echo "  ✗ $2"; FAIL=1; fi; }

# ---------------------------------------------------------------- 场景 1（关键）
# **用真实部署包当源**：这一条直接对着线上会发生的路径，布局漂移立刻暴露。
mkdir -p "$T/h1"
run_case "场景1 真实部署包当源 → 必须预置成功" "$T/h1" "$BUNDLE"
[ -f "$T/h1/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx" ]; chk $? "model.onnx 已落盘"
for f in config.json model.onnx special_tokens_map.json tokenizer_config.json tokenizer.json vocab.txt; do
    [ -f "$T/h1/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/$f" ] || { echo "  ✗ 缺 $f"; FAIL=1; }
done
[ "$FAIL" = "0" ] && echo "  ✓ chroma 需要的 6 个文件全部就位"

# 场景 1b：反向校验真实部署包自身的布局（不经过 start_app.sh）
printf '\n--- 场景1b 真实部署包布局 vs chroma 判据 ---\n'
BONNX="$BUNDLE/onnx_models/all-MiniLM-L6-v2/onnx"
for f in config.json model.onnx special_tokens_map.json tokenizer_config.json tokenizer.json vocab.txt; do
    if [ -f "$BONNX/$f" ]; then echo "  ✓ 部署包内有 $f"; else echo "  ✗ 部署包缺 $f（预置必失败！）"; FAIL=1; fi
done

# ---------------------------------------------------------------- 场景 2
# HOME 指向不存在的路径 → 触发回退分支
rm -rf /tmp/ai4s-home
run_case "场景2 HOME不可用 → 应回退并预置成功" "$T/h_not_exist_xyz" "$BUNDLE"
[ -f "/tmp/ai4s-home/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx" ]; chk $? "回退目录预置成功"

# ---------------------------------------------------------------- 场景 3
run_case "场景3 缓存已存在 → 应跳过" "$T/h1" "$BUNDLE"

# ---------------------------------------------------------------- 场景 4
# 源缺一个文件 → 警告但不崩（退出码必须 0，否则 set -e 会杀掉启动壳）
mkdir -p "$T/src_broken/onnx_models/all-MiniLM-L6-v2/onnx"
echo "only-config" > "$T/src_broken/onnx_models/all-MiniLM-L6-v2/onnx/config.json"
mkdir -p "$T/h4"
( cd "$T/src_broken" && HOME="$T/h4" sh "$T/block.sh" ); _rc=$?
[ "$_rc" = "0" ]; chk $? "源缺文件时退出码 0（失败不阻断启动）"

# ---------------------------------------------------------------- 场景 5
mkdir -p "$T/src_none"
run_case "场景5 无 onnx_models → 应警告" "$T/h5" "$T/src_none"

rm -rf "$T" /tmp/ai4s-home
echo ""
if [ "$FAIL" = "0" ]; then echo "===== ✅ 全部通过 ====="; else echo "===== ❌ 存在失败项 ====="; fi
exit $FAIL
