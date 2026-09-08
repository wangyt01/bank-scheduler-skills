# -*- coding: utf-8 -*-
"""通用事件分段归因：请求/响应配对算 RTT、空响应对比、INSERT 值求和、
大间隙、最慢样本、时间线分布、墙钟利用率。只读分析。

输入 scan_events.py 产出的 events.json，结果打印到 stdout。
配对类型自动选择：有 REQ/RESP 用之，否则用 SEND/RECV；可用 --pair 覆盖。
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
    print("== %s: %d 次, 合计 %.0fs | min %.2fs 中位 %.2fs P90 %.2fs P99 %.2fs max %.2fs"
          % (name, len(a), sum(a), a[0], pct(a, 0.5), pct(a, 0.9),
             pct(a, 0.99), a[-1]))


def main():
    ap = argparse.ArgumentParser(description="调度任务通用分段归因")
    ap.add_argument("--events", required=True, help="scan_events.py 产出的 events.json")
    ap.add_argument("--start", help="分析窗口起始 YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--end", help="分析窗口结束 YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--pair", help='覆盖配对类型，如 "REQ:RESP" 或 "SEND:RECV"')
    args = ap.parse_args()

    raw = json.load(open(args.events, encoding="utf-8"))
    E = [(datetime.fromisoformat(e["ts"][:26]), e) for e in raw]
    E.sort(key=lambda x: x[0])
    if args.start:
        t0 = datetime.fromisoformat(args.start)
        t1 = datetime.fromisoformat(args.end) if args.end else E[-1][0]
        E = [e for e in E if t0 <= e[0] <= t1]
    if not E:
        print("窗口内无事件")
        raise SystemExit(1)

    types = Counter(e["type"] for _, e in E)
    print("窗口内事件计数:", dict(types.most_common()))
    threads = Counter(e["thread"] for _, e in E if e["thread"])
    if threads:
        print("线程分布 TOP10:", dict(threads.most_common(10)))

    # 配对类型：在 REQ:RESP 与 SEND:RECV 中选请求数更多的那对（日志常混有其他渠道的杂散行）
    if args.pair:
        req_t, rsp_t = args.pair.split(":")
    else:
        cands = [("REQ", "RESP"), ("SEND", "RECV")]
        cands = [(a, b) for a, b in cands if types.get(a) and types.get(b)]
        if not cands:
            print("无法确定配对类型（缺少 REQ/RESP 或 SEND/RECV），用 --pair 指定。")
            raise SystemExit(1)
        req_t, rsp_t = max(cands, key=lambda ab: types[ab[0]])
    print("配对类型: %s -> %s" % (req_t, rsp_t))

    rtts = []          # (rtt, req_dt, rsp_dt)
    empty, full = [], []   # 空响应/有数据响应的 RTT
    empty_n = full_n = 0
    gaps = []
    big_gaps = []
    last_req = None
    last_end = None
    insert_sum = 0
    insert_lines = 0

    # 空响应判定方式：响应事件自带记录数（banksn/billnum）→ 直接看字段；
    # 响应不带记录数或字段覆盖率过低（报文被换行拆行时首行恒为0，见 gotchas）
    # → 看响应到下一请求之间有无下游产出事件
    rsp_total = sum(1 for _, e in E if e["type"] == rsp_t)
    rsp_with_info = sum(
        1 for _, e in E if e["type"] == rsp_t
        and (e.get("num", -1) >= 0 or (e.get("extra") or {}).get("banksn", 0) > 0))
    has_size_info = rsp_total > 0 and rsp_with_info / rsp_total >= 0.5
    productive = {"DL_OK", "REC", "DB_OK", "INSERT", "SKIP"}

    for i, (dt, e) in enumerate(E):
        t = e["type"]
        if t == req_t:
            if last_end is not None:
                g = (dt - last_end).total_seconds()
                if g > 0.05:
                    gaps.append(g)
                if g > 60:
                    big_gaps.append((g, last_end, dt))
            last_req = dt
        elif t == rsp_t and last_req is not None:
            rtt = (dt - last_req).total_seconds()
            rtts.append((rtt, last_req, dt))
            if has_size_info:
                n_num = e.get("num", -1)
                n_bsn = (e.get("extra") or {}).get("banksn", -1)
                # BillNum 优先（权威）；多行报文中 BankSN 可能被拆到后续行恒为0，仅作兜底
                if n_num >= 0:
                    is_empty = n_num == 0
                elif n_bsn >= 0:
                    is_empty = n_bsn == 0
                else:
                    is_empty = True
            else:
                is_empty = True
                for j in range(i + 1, len(E)):
                    if E[j][1]["type"] == req_t:
                        break
                    if E[j][1]["type"] in productive:
                        is_empty = False
                        break
            (empty if is_empty else full).append(rtt)
            empty_n += is_empty
            full_n += (not is_empty)
            last_end = dt
            last_req = None
        elif t == "INSERT":
            if e.get("num", -1) >= 0:
                insert_sum += e["num"]
                insert_lines += 1

    if rtts:
        r = [x[0] for x in rtts]
        stat_line("银企RTT (%s->%s)" % (req_t, rsp_t), r)
    if empty:
        method = "响应记录数=0" if has_size_info else "无下游产出事件"
        stat_line("空查询任务 (%s)" % method, empty)
        tot = empty_n + full_n
        print("   占全部响应 %.1f%%，RTT 合计 %.0fs" %
              (empty_n / tot * 100 if tot else 0, sum(empty)))
    if full:
        stat_line("有数据响应", full)

    if insert_lines:
        print("\n== INSERT 值求和: %d（%d 行；注意: 对账需按渠道/标记圈定口径，行数≠条数）"
              % (insert_sum, insert_lines))

    if big_gaps:
        big_gaps.sort(reverse=True)
        print("\nTOP10 任务间大间隙(>60s):")
        for g, a, b in big_gaps[:10]:
            print("   %8.1fs   %s -> %s" % (g, a, b))

    rtts.sort(reverse=True)
    if rtts:
        print("\nTOP10 最慢 RTT:")
        for g, a, b in rtts[:10]:
            print("   %8.1fs   %s -> %s" % (g, a, b))

    # 每 5 分钟事件桶
    bucket = Counter(dt.strftime("%Y-%m-%d %H:%M")[:16] for dt, _ in E)
    req_bucket = Counter(dt.strftime("%Y-%m-%d %H:%M")[:16]
                         for dt, e in E if e["type"] == req_t)
    if req_bucket:
        print("\n每5分钟请求数（时间线）:")
        for k in sorted(req_bucket):
            print("  %s  %d" % (k, req_bucket[k]))

    t0, t1 = E[0][0], E[-1][0]
    wall = (t1 - t0).total_seconds()
    rtt_sum = sum(x[0] for x in rtts)
    if wall > 0:
        print("\n== 窗口墙钟 %.0fs (%.2fh) | RTT合计 %.0fs (%.1f%%) | 间隙>50ms合计 %.0fs (%.1f%%)"
              % (wall, wall / 3600, rtt_sum, rtt_sum / wall * 100,
                 sum(gaps), sum(gaps) / wall * 100))
        print("   （RTT+间隙之外即本地处理与未被配对覆盖的时间）")


if __name__ == "__main__":
    main()
