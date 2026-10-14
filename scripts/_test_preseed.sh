#!/bin/sh
# start_app.sh 预置逻辑的隔离测试台（不动真实 HOME，不动真实缓存）
# 用法：sh /tmp/test_preseed.sh <bundle_dir>
BUNDLE="${1:-/c/Users/Administrator/ai4s_deploy_bundle}"
T=/tmp/ai4s_preseed_test
rm -rf "$T"; mkdir -p "$T"

# 抽取 start_app.sh 里的「HOME 兜底 + 预置块」（从 HOME 判断到 binding 行之前），
# 避免把 exec streamlit 也拖进来（那会在测试机上真起服务）。
sed -n '/^if \[ -z "\$HOME" \]/,/^echo "\[start\] binding/p' \
    "$BUNDLE/start_app.sh" | sed '$d' > "$T/block.sh"

echo "===== 被测代码块 ====="
cat "$T/block.sh"
echo "===== 开始测试 ====="

run_case() {
    _name="$1"; _home="$2"; _src="$3"
    printf '\n--- %s ---\n' "$_name"
    ( cd "$_src" && HOME="$_home" sh "$T/block.sh" )
}

# 场景 1：HOME 可写、无缓存、源齐全 → 预置成功
mkdir -p "$T/h1"
mkdir -p "$T/src_full/onnx_models/all-MiniLM-L6-v2/onnx"
for f in config.json model.onnx special_tokens_map.json tokenizer_config.json tokenizer.json vocab.txt; do
    echo "dummy-$f" > "$T/src_full/onnx_models/all-MiniLM-L6-v2/onnx/$f"
done
run_case "场景1 HOME可写+源齐全 → 应预置成功" "$T/h1" "$T/src_full"
[ -f "$T/h1/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx" ] \
    && echo "  ✓ 校验：model.onnx 已落盘" || echo "  ✗ 校验：model.onnx 缺失"
for f in config.json model.onnx special_tokens_map.json tokenizer_config.json tokenizer.json vocab.txt; do
    [ -f "$T/h1/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/$f" ] || echo "  ✗ 缺 $f"
done
echo "  ✓ 6 文件清点完毕"

# 场景 2：HOME 不可写 → 回退 /tmp/ai4s-home 后仍应成功
# 注：Windows/Git Bash 下 chmod 500 对属主仍可写，无法造出"不可写"，
# 故改为直接把 HOME 指向一个**不存在**的路径来触发回退分支。
rm -rf /tmp/ai4s-home
run_case "场景2 HOME不可写 → 应回退并预置成功" "$T/h_not_exist_xyz" "$T/src_full"
[ -f "/tmp/ai4s-home/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx" ] \
    && echo "  ✓ 校验：回退目录预置成功" || echo "  ✗ 校验：回退目录未预置"

# 场景 3：缓存已存在 → 跳过
run_case "场景3 缓存已存在 → 应跳过" "$T/h1" "$T/src_full"

# 场景 4：源缺一个文件 → 警告但不崩
mkdir -p "$T/src_broken/onnx_models/all-MiniLM-L6-v2/onnx"
echo "only-config" > "$T/src_broken/onnx_models/all-MiniLM-L6-v2/onnx/config.json"
mkdir -p "$T/h4"
run_case "场景4 源缺文件 → 应警告不崩" "$T/h4" "$T/src_broken"

# 场景 5：无 onnx_models 目录 → 警告
mkdir -p "$T/src_none"
run_case "场景5 无 onnx_models → 应警告" "$T/h5" "$T/src_none"

# 场景 6：退出码必须为 0（预置失败不能阻断启动）
mkdir -p "$T/h6"
( cd "$T/src_broken" && HOME="$T/h6" sh "$T/block.sh" >/dev/null 2>&1 )
_rc=$?
[ "$_rc" = "0" ] && echo "  ✓ 退出码 0（失败不阻断启动）" || echo "  ✗ 退出码 $_rc（会阻断启动！）"

rm -rf "$T" /tmp/ai4s-home
echo ""
echo "===== 测试结束 ====="
