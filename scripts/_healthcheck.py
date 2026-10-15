"""部署后健康自检：HTTP 探活 + 存储完整性 + 静态资源在位性（部署辅助脚本）。

定位
----
`docker run` / 沙箱平台把服务拉起来之后，**怎么确认它真的能用**。
HTTP 200 不等于可用——本项目历史上出过「/_stcore/health 返回 200 但页面全白」
的事故（根因是 embedding 模型下载挂死 + WebSocket Origin，均已修），所以本脚本
把「探活」拆成可深查的多个维度，每个失败项都带 🩺 处置建议。

设计约束（为什么这脚本很重要且必须零依赖）
-----------------------------------------
· 纯标准库：应用依赖坏了（缺包/版本不符）时它照样能跑，才能用来诊断
  「应用起不来」这一类故障——依赖 urllib 之外任何第三方都会把诊断工具本身
  变成新的故障点。
· 只访问 127.0.0.1（或 --host 指定）：不需要外网、不需要 API Key，
  CI / 离线环境 / 评委现场都能跑。
· 从任何 cwd 调用都能正确定位项目根（按本文件的上级目录推）。

用法
----
    python scripts/_healthcheck.py                    # 只探活（= Dockerfile HEALTHCHECK 同款）
    python scripts/_healthcheck.py --deep             # 追加存储/资源完整性检查
    python scripts/_healthcheck.py --port 8501 --host 127.0.0.1 --timeout 5

退出码
------
    0  硬检查全部通过（探活必过；--deep 的硬检查见下）；
    1  任一硬检查失败。
    ⚠ 级（降级但不致命）不影响退出码，只提示——它们对应的正是「功能静默丢失」
    类故障（.dockerignore 里逐个论证过的那五个目录），提示出来供人决定。

检查项与严重级（--deep）
------------------------
    硬（不过就 exit 1，应用起不来或数据会坏）：
      · /_stcore/health 返回 200 且 body 为 ok
      · storage/ 及 uploads/chroma 子目录可写（config.py import 期 _mkdirs
        自动建这三个；不可写 = import 期 PermissionError，被 app.py 捕获后
        显示成误导性文案「依赖未安装或版本不匹配」）
      · 桌面态追加 storage/pdf、logs、config（config.py 桌面态 _mkdirs 同样建）
      · library.json 是 dict（写成 [] 会让 services/state.py 的
        lib.get("papers", {}) 抛 AttributeError，容器直接起不来）
      · memory.json 是带 notes/kv 两个键的 dict（缺键 → KeyError）
      · user_profile.json 是 dict（写成 [] → setdefault AttributeError）
    ⚠（不过只提示，功能降级但不崩）：
      · ONNX 模型缓存 6 文件齐全（缺 → 首次 embed 现下载，境内可能很慢；
        应用已有后台预热 + 有界等待 + 人话降级，不会白屏）
      · .streamlit/config.toml 存在（缺 → 学术风主题/静态服务设置丢失）
      · static/mermaid.min.js 存在（缺 → 写作页退化到 CDN，离线失效）
      · lib/vis-9.1.2/vis-network.min.js 存在（缺 → 引用图谱可视化空白，
        只剩表格指标——最难当场定位的一类故障）
      · demo_data/ 存在（缺 → 演示语料加载按钮无数据可写）
      · logs/、storage/cache/、storage/pdf/（服务态）、pdf/：均为**懒创建**
        目录（app.py 首个错误才建 logs；citation_quality.py:169 才建 cache；
        config.py 桌面态才建 storage/pdf；fetch_pdfs.sh 才建 pdf/），
        缺失属正常初态，提示而不判失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("AI4S_ROOT") or Path(__file__).resolve().parent.parent)

ONNX_REQUIRED = ("config.json", "model.onnx", "special_tokens_map.json",
                 "tokenizer_config.json", "tokenizer.json", "vocab.txt")

_fails = 0
_warns = 0


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str, hint: str = "") -> None:
    global _fails
    _fails += 1
    print(f"  ✗ {msg}")
    if hint:
        print(f"    🩺 {hint}")


def _warn(msg: str, hint: str = "") -> None:
    global _warns
    _warns += 1
    print(f"  ⚠ {msg}")
    if hint:
        print(f"    🩺 {hint}")


def _rel(d: Path) -> str:
    """相对项目根的显示路径（跨根时退化为绝对路径）。"""
    try:
        return str(d.relative_to(ROOT))
    except ValueError:
        return str(d)


def _dir_writable(d: Path) -> bool:
    """真实写探测（touch 临时文件），不只是 os.access——挂载卷属主问题
    os.access 可能误判为可写。"""
    if not d.is_dir():
        return False
    try:
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".hc_")
        os.close(fd)
        os.unlink(tmp)
        return True
    except Exception:
        return False


def check_health(host: str, port: int, timeout: float) -> None:
    print(f"[1] HTTP 探活：http://{host}:{port}/_stcore/health")
    url = f"http://{host}:{port}/_stcore/health"
    # 显式绕过代理：本脚本探的是本机服务，而开发机/沙箱常设 http_proxy，
    # urllib 默认会把 127.0.0.1 的请求也送去代理 → 502 假失败（实测踩到过）。
    # 健康检查永远是「直连本机端口」，没有走代理的道理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
            code = resp.status
    except Exception as e:
        _fail(f"探活失败：{e}",
              "服务未起来或端口不对。先看服务日志（容器：docker logs；"
              "桌面：logs/streamlit.log）。⚠️ 注意：本项通过也不代表页面可用"
              "（历史上有过 health 200 但全白的事故），关键功能请加 --deep。")
        return
    if code == 200 and body == "ok":
        _ok(f"health 返回 200 ok（runtime 就绪）")
    elif code == 200:
        # Streamlit 个别版本就绪时返回 200 但 body 不是 "ok"——按 200 放行，仅提示
        _ok(f"health 返回 200（body={body[:40]!r}，非 'ok' 但 HTTP 层就绪）")
    else:
        _fail(f"health 返回 {code}（runtime 未就绪）",
              "等 start-period 后再查；持续 503 说明脚本仍在初始化。")


def _storage_root() -> Path:
    """状态文件根：桌面态在 %APPDATA%\\AI4SResearchAgent\\storage（与 config.py 的
    DESKTOP_MODE 分支一致），服务态/开发态在项目根 storage/。"""
    if os.environ.get("AI4S_DESKTOP") == "1":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "AI4SResearchAgent" / "storage"
    return ROOT / "storage"


def check_storage() -> None:
    print("[2] 存储目录可写性")
    desktop = os.environ.get("AI4S_DESKTOP") == "1"
    st = _storage_root()
    # 硬检查 = config.py import 期 _mkdirs 保证会建的目录（config.py:39-44）：
    # 两种模式都建 storage/uploads、storage/chroma；桌面态追加 storage/pdf、logs、config
    hard = [st, st / "uploads", st / "chroma"]
    if desktop:
        hard += [st / "pdf", ROOT / "logs", ROOT / "config"]
    for d in hard:
        rel = _rel(d)
        if _dir_writable(d):
            _ok(f"{rel} 可写")
        elif not d.exists():
            _fail(f"{rel} 不存在",
                  "config.py import 期应自动创建；不存在说明运行目录不是项目根，"
                  "或该目录被挂载覆盖。可手动 mkdir -p 后重启。")
        else:
            _fail(f"{rel} 不可写",
                  "容器场景九成是挂载卷属主为 root 而服务以 uid 10001 运行："
                  "宿主机执行 chown -R 10001:10001 ./storage ./logs 后重启。")
    # 懒创建目录：缺失属正常初态，只提示
    lazy = [st / "cache", ROOT / "logs" if not desktop else None,
            ROOT / "pdf" if not desktop else None]
    for d in lazy:
        if d is None:
            continue
        if not d.exists():
            _warn(f"{_rel(d)} 尚不存在（懒创建目录，属正常初态）",
                  "logs/ 由 app.py 首个错误日志时建；storage/cache/ 由 "
                  "services/citation_quality.py:169 首次写缓存时建；"
                  "服务态 PDF 目录 pdf/ 由 scripts/fetch_pdfs.sh 拉取时建。")
        elif not _dir_writable(d):
            _warn(f"{_rel(d)} 不可写", "懒创建目录不可写只会让对应功能降级（日志/引用缓存/PDF 拉取）。")


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def check_state_files() -> None:
    print("[3] 状态文件类型完整性（硬检查，防「合法但类型不对」的 JSON）")
    st = _storage_root()

    lib = _load_json(st / "library.json")
    if lib is None:
        _fail("library.json 缺失或不是合法 JSON",
              "删掉该文件重启即可自动重建（services/state.py 对缺失/损坏有兜底）。")
    elif not isinstance(lib, dict):
        _fail(f"library.json 是 {type(lib).__name__}，必须是 dict",
              "写成 [] 会让 lib.get(\"papers\", {}) 抛 AttributeError 且不在 "
              "try 内——Container 构造失败，应用直接起不来。"
              "修法：写回 {\"papers\": {}, \"paper_order\": [], \"chunks\": [], \"likes\": {}}")
    else:
        _ok(f"library.json 是 dict（papers {len(lib.get('papers', {})) if isinstance(lib.get('papers'), dict) else '?'} 篇）")

    mem = _load_json(st / "memory.json")
    if mem is None:
        _fail("memory.json 缺失或不是合法 JSON", "删掉重启自动重建。")
    elif not isinstance(mem, dict) or "notes" not in mem or "kv" not in mem:
        _fail("memory.json 必须是带 notes/kv 两个键的 dict",
              "缺键时 core/memory.py 的 self.data[\"kv\"][key] 抛 KeyError"
              "（track.py / query_expansion.py 的 remember 无兜底）。"
              "修法：写回 {\"notes\": [], \"kv\": {}}")
    else:
        _ok("memory.json 是带 notes/kv 的 dict")

    prof = _load_json(st / "user_profile.json")
    if prof is None:
        _warn("user_profile.json 缺失或不是合法 JSON",
              "不影响启动（ profiles.setdefault 前有 isfile/isinstance 判断），"
              "删掉重启自动重建。")
    elif not isinstance(prof, dict):
        _fail("user_profile.json 必须是 dict",
              "写成 [] 会让 profiles.setdefault 抛 AttributeError。")
    else:
        _ok("user_profile.json 是 dict")


def check_onnx_cache() -> None:
    print("[4] ONNX embedding 模型缓存（⚠ 级：缺则慢/降级，不崩）")
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )
        d = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx"
    except Exception as e:
        _warn(f"无法定位 ONNX 缓存目录：{e}", "chromadb 未安装或版本变化。")
        return
    miss = [f for f in ONNX_REQUIRED if not (d / f).is_file()]
    if not miss:
        _ok(f"6 个模型文件齐全：{d}")
    else:
        _warn(f"模型缓存不齐（缺 {', '.join(miss)}）：{d}",
              "首次 embed 会现从 AWS S3 下载（境内可能挂死）。应用有后台预热 + "
              "有界等待不会白屏，但向量功能会慢。处置：跑 "
              "python scripts/_prewarm.py 预热，或按 start_app.sh 的方式 "
              "预置 onnx_models/。")


def check_static_assets() -> None:
    print("[5] 静态资源在位性（⚠ 级：缺则功能静默降级）")
    checks = [
        (ROOT / ".streamlit" / "config.toml",
         ".streamlit/config.toml 缺失",
         "学术风主题与 [server] enableStaticServing 设置丢失，写作页 mermaid "
         "会退化到 CDN。从仓库恢复该文件（.dockerignore 已保证它进镜像）。"),
        (ROOT / "static" / "mermaid.min.js",
         "static/mermaid.min.js 缺失",
         "写作页的 mermaid 图会退化到 CDN 加载，离线/内网环境下直接失效。"),
        (ROOT / "lib" / "vis-9.1.2" / "vis-network.min.js",
         "lib/vis-9.1.2/vis-network.min.js 缺失",
         "引用图谱可视化会变空白、只剩表格指标（page_graph.py 有兜底不会崩）。"
         "这类「不报错、只丢功能」的故障最难当场定位，看到本项警告请优先恢复。"),
        (ROOT / "demo_data",
         "demo_data/ 缺失",
         "「一键加载演示数据」无语料可写（DEMO_PAPERS 文本在 config.py，"
         "但写作页还读 demo_data/data.csv）。"),
    ]
    for path, msg, hint in checks:
        if path.exists():
            _ok(f"{path.relative_to(ROOT)} 在位")
        else:
            _warn(msg, hint)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI4S 部署后健康自检（纯标准库，无 Key 无外网）")
    ap.add_argument("--host", default="127.0.0.1", help="服务地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8501, help="服务端口（默认 8501）")
    ap.add_argument("--timeout", type=float, default=5.0, help="HTTP 超时秒数（默认 5）")
    ap.add_argument("--deep", action="store_true",
                    help="追加存储完整性 / 模型缓存 / 静态资源检查")
    args = ap.parse_args()

    print("=" * 62)
    print("  AI4S 科研助手 · 部署后健康自检" + ("（深度模式）" if args.deep else "（仅探活）"))
    print(f"  项目根：{ROOT}")
    print("=" * 62)

    check_health(args.host, args.port, args.timeout)
    if args.deep:
        check_storage()
        check_state_files()
        check_onnx_cache()
        check_static_assets()

    print("-" * 62)
    if _fails:
        print(f"结果：{_fails} 项硬检查失败，{_warns} 项警告 —— 服务不可用或数据有损，按上面 🩺 处置。")
        return 1
    if _warns:
        print(f"结果：硬检查全部通过，{_warns} 项警告（功能可能降级，见上）。")
    else:
        print("结果：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
