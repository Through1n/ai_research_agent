"""arXiv 追踪守护脚本（可选常驻运行，对应赛题「定期刷新追踪」）。

用法：
    python scripts/watch_daemon.py            # 每 30 分钟检查一次订阅话题
    python scripts/watch_daemon.py 10         # 每 10 分钟检查一次

说明：
- 独立进程常驻，与 Streamlit 会话解耦，实现真正的「定期」抓取；
- 复用 services/track.py 的订阅与检查点，抓到的增量新论文写入长期记忆，
  Web 端「论文自动检索与追踪」页点击「运行一次追踪」即可看到守护进程记录的
  「上次以来新增」论文；
- 抓取结果同时输出到 storage/track_daemon.log，便于演示时展示运行证据。
"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.state import Container
from services import track

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "storage", "track_daemon.log")
logging.basicConfig(filename=LOG, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


def tick(ctx):
    report = track.fetch_track(ctx, max_results=8)
    total_new = 0
    for r in report:
        if r.get("error"):
            logging.warning("话题 %s 抓取失败: %s", r["topic"], r["error"])
            continue
        for p in r["new"]:
            total_new += 1
            logging.info("新论文 [%s] %s (%s)%s", r["topic"], p["arxiv_id"],
                         p["published"], " [github]" if p["github"] else "")
    logging.info("本轮完成：新增 %d 篇", total_new)
    print(f"[daemon] {time.strftime('%H:%M:%S')} 检查完成，发现新论文 {total_new} 篇"
          f"（日志：storage/track_daemon.log）")
    return total_new


def main():
    interval_min = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ctx = Container()
    print(f"[daemon] 启动：间隔 {interval_min} 分钟｜订阅 {track.get_subscriptions(ctx)}｜Ctrl+C 退出")
    logging.info("守护进程启动，间隔 %d 分钟", interval_min)
    while True:
        try:
            tick(ctx)
        except KeyboardInterrupt:
            print("\n[daemon] 已退出")
            break
        except Exception:
            logging.exception("抓取异常")
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
