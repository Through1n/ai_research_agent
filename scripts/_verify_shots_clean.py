# -*- coding: utf-8 -*-
"""交付截图「无错误横幅」门禁。

**为什么需要它**（2026-10-26 事故）：交付文档里的 `资料/截图/p0.png` 顶部带着一条
红色错误横幅（参考文献结构化失败 · Request timed out），根因是截图脚本
`scripts/_reshoot_shots.py` 用 ``wait_text(..., must=False)`` —— **超时也不报错**，
于是把运行时报错原样拍进了交付图。本门禁把「截图里不能有错误横幅」变成
**可自动复核的硬规则**，防止同类问题再次悄悄进交付物。

检测原理（按像素颜色签名，不靠 OCR、不靠文本匹配）：
  · Streamlit 错误提示条的**底色** = ``rgb(250, 228, 228)``（浅粉红）
  · 错误提示条的**文字色** = ``rgb(189, 64, 67)``（Streamlit error 红）
两者都是主题里别处不用的颜色，实测干净页面里这两个色的占比 < 0.01%，
而带横幅的 p0.png 底色占比 1.12%、文字色 0.027%（两个量级以上的差）。

用法：
    python scripts/_verify_shots_clean.py                      # 扫 资料/截图/p*.png
    python scripts/_verify_shots_clean.py a.png b.png           # 扫指定文件
    python scripts/_verify_shots_clean.py --dir 资料/截图/_after # 换目录

退出码：0 = 全部干净；1 = 检出错误横幅（打印具体文件与占比）。
"""

import argparse
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:                                    # pragma: no cover
    np = None
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "资料" / "截图"

# Streamlit 错误提示条的颜色签名（底色 / 文字色）
ERR_BG = (250, 228, 228)
ERR_TX = (189, 64, 67)
TOL = 8                                                  # 每通道容差

# 判定阈值（实测校准）：
#   · 干净图：红色文字像素 = 0；标题树粉色节点虽用 ERR_BG 作填充，但文字是黑色，
#     所以红色文字像素同样为 0 —— 关键教训：不能只靠粉色底色判定，否则会把
#     「结构解析」的标题树节点误判成错误横幅（2026-10-26 p0 误报即此因）。
#   · 真实错误横幅：红色文字（ERR_TX）约 12000 像素（参考文献失败那条），
#     与干净图差 30 倍以上。
# 因此以「红色文字像素」为主判定信号；粉色底色仅作辅助（与少量红字共存才算横幅）。
MAX_TEXT_PX = 500              # 绝对像素数（抗不同分辨率的缩放差异）
MAX_BG_RATIO = 0.0003          # 0.03%（仅作参考，不与红字共存不判失败）


def _near_mask(arr, target, tol=TOL):
    """返回匹配目标色（每通道 ±tol）的布尔掩码。"""
    t = np.array(target, dtype=int)
    return np.all(np.abs(arr.astype(int) - t) <= tol, axis=2)


def scan_image(path):
    """扫描一张截图，返回检测结果字典。

    :return: ``{"path": str, "ok": bool, "bg_ratio": float, "text_px": int,
                "size": (w, h), "reasons": [str, ...]}``
    """
    p = Path(path)
    reasons = []
    try:
        arr = np.asarray(Image.open(p).convert("RGB"))
    except Exception as e:
        return {"path": str(p), "ok": False, "bg_ratio": 0.0, "text_px": 0,
                "size": (0, 0), "reasons": ["图片无法读取：%s" % str(e)[:80]]}
    h, w = arr.shape[0], arr.shape[1]
    total = max(1, w * h)
    bg_px = int(_near_mask(arr, ERR_BG).sum())
    tx_px = int(_near_mask(arr, ERR_TX).sum())
    bg_ratio = bg_px / float(total)
    # 主判定：红色文字像素（错误横幅的文字是 Streamlit error 红，全站别处不用此色）。
    if tx_px > MAX_TEXT_PX:
        reasons.append("错误横幅红色文字像素 %d > 阈值 %d" % (tx_px, MAX_TEXT_PX))
    # 辅助判定：粉色底色 + 少量红字共存（捕获文字较少的小横幅）；
    # 仅有粉色底色而无红字 = 标题树节点，不算横幅。
    elif bg_ratio > MAX_BG_RATIO and tx_px > 50:
        reasons.append("粉色底色占比 %.4f%% 且含 %d 红字像素，疑似错误横幅"
                       % (bg_ratio * 100, tx_px))
    return {"path": str(p), "ok": not reasons, "bg_ratio": bg_ratio,
            "text_px": tx_px, "size": (w, h), "reasons": reasons}


def scan_many(paths):
    return [scan_image(p) for p in paths]


def assert_clean(paths):
    """截图脚本内联调用：检出错误横幅立刻抛错（宁可没有图，也不要带错的图）。"""
    bad = [r for r in scan_many(paths) if not r["ok"]]
    if not bad:
        return []
    detail = "; ".join("%s（%s）" % (Path(r["path"]).name, "；".join(r["reasons"]))
                        for r in bad)
    raise SystemExit("❌ 截图检出运行时错误横幅，拒绝作为交付物：%s" % detail)


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付截图无错误横幅门禁")
    ap.add_argument("files", nargs="*", help="要检查的图片（默认 资料/截图/p*.png）")
    ap.add_argument("--dir", default=None, help="要扫描的目录（默认 资料/截图）")
    args = ap.parse_args(argv)

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        d = Path(args.dir) if args.dir else DEFAULT_DIR
        paths = sorted(d.glob("p*.png"))
    if not paths:
        print("⚠️ 没有找到要检查的图片（%s）" % (args.dir or DEFAULT_DIR))
        return 0

    rows = scan_many(paths)
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        flag = "❌" if not r["ok"] else "✅"
        print("%s %-24s %5dx%-6d 底色 %.4f%%  文字 %d px"
              % (flag, Path(r["path"]).name, r["size"][0], r["size"][1],
                 r["bg_ratio"] * 100, r["text_px"]))
        for reason in r["reasons"]:
            print("     ↳ %s" % reason)
    print("\n共 %d 张，干净 %d 张，带错误横幅 %d 张"
          % (len(rows), len(rows) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
