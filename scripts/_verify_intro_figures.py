# -*- coding: utf-8 -*-
"""交付文档配图门禁（专门防「错误横幅第二次」）。

扫描两处配图源：
  · 资料/截图/p*.png               —— 干净源（重拍后此处先过 _verify_shots_clean）
  · output/*/stage2/images/*.png   —— docx 实际内嵌的副本。
    2026-10-26 事故根因：流水线在 17 点拷贝了这批副本，但 p0 在 20:26 才重拍干净，
    副本从未同步，于是 docx 内嵌的 fig02_p0.png 仍带红色错误横幅，门禁只扫 资料/截图/
    完全漏掉它，错误横幅第二次进了交付物。

任一张检出错误横幅即退出 1，阻断交付。convert_doc.py 之前必须先跑本门禁。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from _verify_shots_clean import scan_image


def main():
    shot_dir = ROOT / "资料" / "截图"
    stage_dirs = sorted((ROOT / "output").glob("*/stage2/images"))
    paths = []
    if shot_dir.is_dir():
        paths += sorted(shot_dir.glob("p*.png"))
    for d in stage_dirs:
        paths += sorted(d.glob("*.png"))

    if not paths:
        print("⚠️ 没有找到要检查的配图")
        return 0

    bad = []
    for p in paths:
        r = scan_image(p)
        flag = "✅" if r["ok"] else "❌"
        tag = str(p.relative_to(ROOT))
        print("%s %-42s 文字 %d px  底色 %.4f%%"
              % (flag, tag, r["text_px"], r["bg_ratio"] * 100))
        if not r["ok"]:
            bad.append(p)
    print("\n共 %d 张，带错误横幅 %d 张" % (len(paths), len(bad)))
    if bad:
        print("❌ 以下配图仍带错误横幅，禁止进入交付物：")
        for p in bad:
            print("   - %s" % p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
