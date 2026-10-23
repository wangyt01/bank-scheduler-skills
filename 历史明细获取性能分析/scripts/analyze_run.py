# -*- coding: utf-8 -*-
"""按执行窗口分段归因：RTT 分布、本地处理耗时、条数统计与对账。

输入 scan_events.py 产出的 events.csv，结果打印到 stdout。只读分析。
"""
import argparse
import csv
import statistics
from collections import Counter
from datetime import datetime


def pct(a, p):
    if not a:
        return 0.0
    a = sorted(a)
    return a[min(len(a) - 1, int(len(a) * p))]


def main():
    ap = argparse.ArgumentParser(description="按执行窗口分段归因")
    ap.add_argument("--events", required=True, help="scan_events.py 产出的 events.csv")
    ap.add_argument("--run", action="append", required=True,
                    help='执行窗口 "标签=起始|结束"，时间格式 YYYY-MM-DD HH:MM:SS，可重复')
    args = ap.parse_args()

    runs = []
    for r in args.run:
        label, span = r.split("=", 1)
        t0, t1 = span.split("|", 1)
        runs.append((label, datetime.strptime(t0.strip(), "%Y-%m-%d %H:%M:%S"),
                     datetime.strptime(t1.strip(), "%Y-%m-%d %H:%M:%S")))

    events = []
    with open(args.events, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row["dt"] = datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S.%f")
            events.append(row)
    events.sort(key=lambda e: e["dt"])

    def seg(dt):
        for label, t0, t1 in runs:
            if t0 <= dt <= t1:
                return label
        return None

    # REQ -> RESP 逐页配对
    pairs = []
    last_req = None
    for e in events:
        if e["type"] == "REQ":
            last_req = e
        elif e["type"] == "RESP" and last_req is not None:
            rtt = (e["dt"] - last_req["dt"]).total_seconds()
            run = seg(last_req["dt"]) or seg(e["dt"])
            pairs.append({"run": run, "acc": last_req["acc"], "date": last_req["date"],
                          "page": last_req["page"], "rtt": rtt})
            last_req = None

    for label, t0, t1 in runs:
        ps = [p for p in pairs if p["run"] == label]
        wall = (t1 - t0).total_seconds()
        if not ps:
            print("== %s: 窗口内无配对请求，跳过" % label)
            continue
        rtts = [p["rtt"] for p in ps]
        rtt_sum = sum(rtts)
        accs = set(p["acc"] for p in ps)
        combos = set((p["acc"], p["date"]) for p in ps)
        pages1 = sum(1 for p in ps if str(p["page"]) == "1")
        print("== %s: %s~%s 墙钟 %.0fs" % (label, t0.time(), t1.time(), wall))
        print("   请求 %d 页 (第1页 %d, 第2页+ %d) | RTT合计 %.0fs (占墙钟 %.1f%%)"
              % (len(ps), pages1, len(ps) - pages1, rtt_sum, rtt_sum / wall * 100))
        print("   RTT min %.2fs 中位 %.2fs P90 %.2fs P99 %.2fs max %.2fs"
              % (min(rtts), pct(rtts, 0.5), pct(rtts, 0.9), pct(rtts, 0.99), max(rtts)))
        print("   账户数 %d | 账户x日期组合 %d" % (len(accs), len(combos)))
        for t in ("SKIP", "INSERT", "REC", "BACKFILL", "DATEUPD", "DATEUPD_SKIP"):
            sub = [e for e in events if e["type"] == t and seg(e["dt"]) == label]
            if sub:
                line = "   %s: %d 行" % (t, len(sub))
                if t == "INSERT":
                    vals = [int(e["extra"]) for e in sub if str(e["extra"]).isdigit()]
                    line += ", count 合计 %d（入库对账用此值而非行数）" % sum(vals)
                print(line)
        ins = Counter(e["extra"] for e in events
                      if e["type"] == "INSERT" and seg(e["dt"]) == label)
        if ins:
            print("   INSERT count 分布: %s" % dict(ins.most_common(10)))
        evs = [e for e in events if seg(e["dt"]) == label]
        gs = [(evs[i + 1]["dt"] - evs[i]["dt"]).total_seconds()
              for i in range(len(evs) - 1) if evs[i]["type"] == "RESP"]
        if gs:
            print("   本地处理(RESP->下一事件) 中位 %.0fms P95 %.0fms 最大 %.2fs | 合计 %.0fs (%.1f%%)"
              % (statistics.median(gs) * 1000, pct(gs, 0.95) * 1000, max(gs),
                 sum(gs), sum(gs) / wall * 100))
        rq = [e["dt"] for e in evs if e["type"] == "REQ"]
        if len(rq) > 1:
            cyc = [(b - a).total_seconds() for a, b in zip(rq, rq[1:])]
            print("   页周期(REQ->REQ) 中位 %.2fs" % statistics.median(cyc))
        print()


if __name__ == "__main__":
    main()
