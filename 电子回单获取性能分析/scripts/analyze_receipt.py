# -*- coding: utf-8 -*-
"""按事件链做分段耗时归因与空查询识别。

输入 scan_receipt.py 产出的 receipt_events.json，结果打印到 stdout。只读分析。

输出内容：
  1. 任务级银企 RTT（SEND->RECV）整体分布
  2. 空查询任务 vs 有回单任务的 RTT 对比（空查询=RECV 后到下一 SEND 前无 DL_OK）
  3. 回单级分段耗时：下载(SFTP)/上传影像/入库/删除
  4. 任务间隙（RECV->下一 SEND）与 TOP10 大间隙
  5. TOP10 最慢 RTT（定位银行侧长尾样本）
  6. 总墙钟时间利用率
"""
import argparse
import json
import statistics
from collections import Counter
from datetime import datetime


def pct(a, p):
    if not a:
        return 0.0
    a = sorted(a)
    return a[min(len(a) - 1, int(len(a) * p))]


def stat_line(name, a):
    a = sorted(a)
    print("== %s: %d 笔, 合计 %.0fs | min %.2fs 中位 %.2fs P90 %.2fs P99 %.2fs max %.2fs"
          % (name, len(a), sum(a), a[0], pct(a, 0.5), pct(a, 0.9), pct(a, 0.99), a[-1]))


def main():
    ap = argparse.ArgumentParser(description="电子回单事件链分段归因")
    ap.add_argument("--events", required=True, help="scan_receipt.py 产出的 receipt_events.json")
    ap.add_argument("--start", help="分析窗口起始 YYYY-MM-DD HH:MM:SS（可选）")
    ap.add_argument("--end", help="分析窗口结束 YYYY-MM-DD HH:MM:SS（可选）")
    args = ap.parse_args()

    E = [(datetime.fromisoformat(d), ev, acc)
         for d, ev, acc in json.load(open(args.events, encoding="utf-8"))]
    E.sort(key=lambda x: x[0])
    if args.start:
        t0 = datetime.fromisoformat(args.start)
        t1 = datetime.fromisoformat(args.end) if args.end else E[-1][0]
        E = [e for e in E if t0 <= e[0] <= t1]
    if not E:
        print("窗口内无事件")
        raise SystemExit(1)

    counts = Counter(e[1] for e in E)
    print("窗口内事件计数:", dict(counts.most_common()))

    rtts_all = []   # (rtt, send_dt, recv_dt)
    empty = []      # 空查询任务 RTT
    full = []       # 有回单任务 RTT
    seg_dl, seg_up, seg_db, seg_rm = [], [], [], []
    gaps = []
    big_gaps = []   # (gap, from_dt, to_dt)
    last_send_dt = None
    last_end = None
    st = {}

    for i, (dt, ev, acc) in enumerate(E):
        if ev == "SEND":
            if last_end is not None:
                g = (dt - last_end).total_seconds()
                if g > 0.05:
                    gaps.append(g)
                if g > 60:
                    big_gaps.append((g, last_end, dt))
            last_send_dt = dt
            st = {}
        elif ev == "RECV" and last_send_dt is not None:
            rtt = (dt - last_send_dt).total_seconds()
            rtts_all.append((rtt, last_send_dt, dt))
            has_dl = False
            for j in range(i + 1, len(E)):
                if E[j][1] == "SEND":
                    break
                if E[j][1] == "DL_OK":
                    has_dl = True
                    break
            (empty if not has_dl else full).append(rtt)
            last_end = dt
            st = {"t": dt}
        elif ev == "DL_OK" and st.get("t") is not None:
            seg_dl.append((dt - st["t"]).total_seconds())
            st["dl"] = dt
        elif ev == "UP_RSP" and st.get("dl") is not None:
            seg_up.append((dt - st["dl"]).total_seconds())
            st["up"] = dt
        elif ev == "DB_OK" and st.get("up") is not None:
            seg_db.append((dt - st["up"]).total_seconds())
            st["db"] = dt
        elif ev == "RM_OK" and st.get("db") is not None:
            seg_rm.append((dt - st["db"]).total_seconds())
            st = {}

    # 1. RTT 整体
    if rtts_all:
        r = [x[0] for x in rtts_all]
        stat_line("任务级 银企RTT (SEND->RECV)", r)

    # 2. 空查询 vs 有回单
    if empty:
        stat_line("空查询任务 (RECV 后无 DL_OK)", empty)
        print("   占全部任务 %.1f%%，RTT 合计 %.0fs" %
              (len(empty) / (len(empty) + len(full)) * 100 if (len(empty) + len(full)) else 0,
               sum(empty)))
    if full:
        stat_line("有回单任务", full)

    # 3. 回单级分段
    for name, a in (("下载回单文件(SFTP)", seg_dl), ("上传影像", seg_up),
                    ("入库 receipt2Db", seg_db), ("删除SFTP文件", seg_rm)):
        if a:
            stat_line(name, a)
        else:
            print("== %s: 无事件" % name)

    # 4. 任务间隙
    if gaps:
        stat_line("任务间隙 (RECV->下一SEND, >50ms)", gaps)
    big_gaps.sort(reverse=True)
    if big_gaps:
        print("\nTOP10 任务间大间隙(>60s):")
        for g, a, b in big_gaps[:10]:
            print("   %8.1fs   %s -> %s" % (g, a, b))

    # 5. 最慢 RTT
    rtts_all.sort(reverse=True)
    if rtts_all:
        print("\nTOP10 最慢 RTT:")
        for g, a, b in rtts_all[:10]:
            print("   %8.1fs   %s -> %s" % (g, a, b))

    # 6. 墙钟利用率
    t0, t1 = E[0][0], E[-1][0]
    wall = (t1 - t0).total_seconds()
    rtt_sum = sum(x[0] for x in rtts_all)
    print("\n== 总墙钟 %.0fs (%.2fh) | RTT合计 %.0fs (%.1f%%) | 下载 %.0fs | 上传 %.0fs | 入库 %.0fs | 删除 %.0fs | 间隙 %.0fs"
          % (wall, wall / 3600, rtt_sum, rtt_sum / wall * 100 if wall else 0,
             sum(seg_dl), sum(seg_up), sum(seg_db), sum(seg_rm), sum(gaps)))


if __name__ == "__main__":
    main()
