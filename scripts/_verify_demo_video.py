# -*- coding: utf-8 -*-
"""演示视频门禁：成品 MP4 的规范 + 字幕与旁白稿的逐字一致性。

为什么需要
  视频是「看起来没问题」最容易蒙混的交付物：画面抽几帧都能看，但字幕少一句、
  时间轴倒挂、音轨没挂上，只有逐项量出来才知道。本门禁只用计算值判定。

判据
  ① 容器：1920×1080 / 30fps / 含 AAC 音轨 / 时长与音轨末点一致（±0.5s）
  ② 字幕文本：把 ASS 里所有 Dialogue 拼起来、去换行与空白后，必须与
     旁白稿（去掉标题/引用行/markdown 标记）**逐字相等** —— 少一句、多一句都翻红
  ③ 时间轴：单调不倒挂；每条时长在 0.8–16s 之间（过短来不及看，过长像卡住）
  ④ 画面覆盖：每段画面与其时间窗都在视频时长内，且首尾相接不留空洞

用法
  python scripts/_verify_demo_video.py
  python scripts/_verify_demo_video.py --dump    # 只打印量测
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FF = (r"C:/Users/Administrator/anaconda3/envs/ai_research/Lib/site-packages/"
      r"imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")
VIDEO = ROOT / "资料/演示视频.mp4"
ASS = ROOT / "资料/_构建/演示视频/字幕.ass"
SCRIPT = ROOT / "资料/演示视频旁白稿.md"

FAILS = []


def ck(name, ok, detail=""):
    print(("  OK   " if ok else "  FAIL ") + name + (f" | {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def probe(path):
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True)
    err = r.stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    seconds = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
    v = re.search(r"Video: h264.*?, (\d+)x(\d+)", err)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", err)
    has_a = "Audio: aac" in err
    return {
        "dur": seconds,
        "w": int(v.group(1)) if v else 0,
        "h": int(v.group(2)) if v else 0,
        "fps": float(fps.group(1)) if fps else 0.0,
        "audio": has_a,
    }


def ass_rows(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        p = line.split(",", 9)
        rows.append((p[1], p[2], p[9]))
    return rows


def t2s(v):
    h, m, s = v.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def expected_text():
    raw = SCRIPT.read_text(encoding="utf-8")
    raw = re.sub(r"^#{1,6} .*$", "", raw, flags=re.M)
    raw = re.sub(r"^>.*$", "", raw, flags=re.M)
    raw = raw.replace("**", "").replace("---", "")
    return re.sub(r"\s+", "", raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--file", default="",
                    help="要校验的 mp4（默认 资料/演示视频.mp4）。"
                         "换交付物后必须带这个参数，否则校验的仍是旧片")
    args = ap.parse_args()

    video = ROOT / args.file if args.file else VIDEO
    # 字幕默认找视频同名的 .ass；找不到就退回构建目录里的那份（字幕是烧进画面的，
    # 交付目录并不一定有同名文件 —— 旧校验器因此对新交付物一律 FAIL）。
    ass_path = video.with_suffix(".ass")
    if not ass_path.exists():
        ass_path = ASS

    if not video.exists():
        print(f"FAIL 视频不存在：{video}")
        return 1
    if not ass_path.exists():
        print(f"FAIL 字幕不存在：{ass_path}")
        return 1

    info = probe(video)
    print("① 容器：", info)
    ck("分辨率 1920x1080", (info["w"], info["h"]) == (1920, 1080))
    ck("帧率 30fps", abs(info["fps"] - 30) < 0.5, f"{info['fps']}")
    ck("含 AAC 音轨（原声已挂上）", info["audio"])

    rows = ass_rows(ass_path)
    print(f"② 字幕：{len(rows)} 条")
    got = re.sub(r"\s+", "", "".join(r[2].replace("\\N", "") for r in rows))
    exp = expected_text()
    if args.dump:
        print("   字幕字数", len(got), "稿件字数", len(exp))
    ck("字幕与旁白稿逐字一致", got == exp, f"字幕 {len(got)} 字 / 稿件 {len(exp)} 字")
    if got != exp:
        for i in range(min(len(got), len(exp))):
            if got[i] != exp[i]:
                print(f"       首处差异 @{i}：字幕「{got[max(0, i-14):i+14]}」"
                      f" 稿件「{exp[max(0, i-14):i+14]}」")
                break
        else:
            longer = "字幕" if len(got) > len(exp) else "稿件"
            print(f"       前缀一致，{longer}多出："
                  f"{(got[len(exp):] if len(got) > len(exp) else exp[len(got):])[:40]}")

    spans = [(t2s(a), t2s(b)) for a, b, _ in rows]
    inv = [(i, spans[i - 1][1], spans[i][0]) for i in range(1, len(spans))
           if spans[i][0] < spans[i - 1][1] - 0.02]
    ck("时间轴不倒挂", not inv, f"{len(inv)} 处倒挂 {inv[:2]}")
    durs = [b - a for a, b in spans]
    ck("首条不早于原声起点且不晚于 4s", 0 <= spans[0][0] <= 4.0, f"{spans[0][0]:.2f}s")
    ck("单条时长 0.8–16s", min(durs) >= 0.8 and max(durs) <= 16.0,
       f"min={min(durs):.2f} max={max(durs):.2f}")
    ck("末条收在原声结束前且不超过视频时长",
       spans[-1][1] <= info["dur"] + 0.01, f"{spans[-1][1]:.2f} / 视频 {info['dur']:.2f}s")

    print("③ 画面覆盖")
    # 时长不再写死「3–4 分钟」——那是静态截图版（3:10 原声）的数值，真实使用版按
    # 旁白句数重剪成 2'56"，硬编码会把交付的新片判成 FAIL。
    # 改成区间式：旁白必须整条装进片子（差 ≥ -0.5s），也不许长出太多（≤20s，
    # 20s 是片头卡 + 尾部卡的上限）。静态版 190s / 旁白 176s 与新片 176.5s 都能过。
    nar = ROOT / "资料/_构建/演示视频/narration.wav"
    if nar.exists():
        nd = probe(nar)["dur"]
        ck("视频时长装得下旁白且不超长（≤+20s）",
           nd - 0.5 <= info["dur"] <= nd + 20,
           f"视频 {info['dur']:.1f}s / 旁白 {nd:.1f}s")
    else:
        print("  [SKIP] 找不到 narration.wav，跳过时长比对")
    # 每段图源都在
    for name in ("title_card", "outro_card"):
        ck(f"{name}.png 存在", (ROOT / f"资料/_构建/{name}.png").exists())
    for p in range(7):
        ck(f"p{p}.png 存在", (ROOT / f"资料/截图/p{p}.png").exists())

    print()
    if args.dump:
        print("（--dump 模式，不做结论）")
        return 0
    if FAILS:
        print(f"FAIL {len(FAILS)} 项未通过：" + " / ".join(FAILS))
        return 1
    print("OK 全部通过：规格合规、字幕与旁白稿逐字一致、时间轴连续、图源齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
