#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并发明细型调度日志分析：单笔耗时分布 + 日志间隔归因。

只读分析。扫描 --logdir 下的 .log.gz / .log 分卷，按 traceId 重建工作线程会话，
输出单笔耗时分布、步骤间隔归因 TOP、单次最大间隔 TOP。
仅依赖 Python 标准库。
"""
import argparse
import gzip
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\S+\s+\w+\s+"
    r"\[([^\]]*)\]\s+\[([^\]]*)\]\s+(\S+)\s+\[([^\]]*)\]\s(.*)$"
)
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}")
B64_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")


def normalize(msg):
    if "sql" in msg[:30] and B64_RE.search(msg):
        msg = B64_RE.sub("<B64SQL>", msg)
    if "{" in msg and len(msg) > 150:
        msg = msg[: msg.index("{")] + "<JSON>"
    msg = UUID_RE.sub("<UUID>", msg)
    msg = re.sub(r"\d{10,}", "<NUM>", msg)
    return msg[:110]


def iter_log_files(logdir: Path):
    files = sorted(logdir.glob("*.log.gz")) + sorted(logdir.glob("*.log"))
    seen_gz = {f.name[: -len(".gz")] for f in logdir.glob("*.log.gz")}
    for fp in files:
        if fp.is_dir():
            continue
        if fp.suffix == ".log" and fp.name in seen_gz:
            continue  # gz 与同名平文件重复，只取 gz
        yield fp


def iter_lines(files):
    total = parsed = 0
    for fp in files:
        opener = gzip.open if fp.suffix == ".gz" else open
        with opener(fp, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                total += 1
                m = LINE_RE.match(line.rstrip("\n"))
                if m:
                    parsed += 1
                    yield m.groups()
    print(f"[扫描] 总行数 {total}, 解析行数 {parsed} ({parsed * 100.0 / max(total, 1):.1f}%)", file=sys.stderr)
    if total and parsed < total * 0.8:
        print("[停止] 日志解析率 <80%，格式不符，请确认日志格式", file=sys.stderr)
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="单笔耗时分布 + 间隔归因（只读）")
    ap.add_argument("--logdir", required=True, help="日志根目录")
    ap.add_argument("--worker-keyword", default=None,
                    help="工作线程名关键词（如 收款并发）；缺省自动探测含 thread 的线程名")
    ap.add_argument("--start-marker", default=None,
                    help="单笔开始标记（如 '自动入账开始['）；缺省只做间隔归因")
    ap.add_argument("--max-gap", type=float, default=120.0,
                    help="单次间隔超过该秒数视为跨批次，丢弃（默认 120）")
    ap.add_argument("--top", type=int, default=35, help="归因表行数（默认 35）")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logdir = Path(args.logdir)
    if not logdir.is_dir():
        print(f"[停止] 目录不存在: {logdir}", file=sys.stderr)
        sys.exit(1)

    files = list(iter_log_files(logdir))
    if not files:
        print("[停止] 目录下无 .log.gz / .log 文件", file=sys.stderr)
        sys.exit(1)

    trace_events = defaultdict(list)  # trace -> [(ts, thread, msg)]
    thread_counter = Counter()
    item_starts = []                  # (ts, trace, thread, item_id)
    ts_all = []

    for ts_s, trace, _mod, _logger, thread, msg in iter_lines(files):
        if args.worker_keyword and args.worker_keyword not in thread:
            continue
        if not args.worker_keyword and "thread" not in thread.lower():
            continue
        thread_counter[thread] += 1
        try:
            ts = parse_ts(ts_s)
        except ValueError:
            continue
        ts_all.append(ts)
        trace_events[trace.rsplit(".", 1)[0]].append((ts, thread, msg))
        if args.start_marker and args.start_marker in msg:
            dm = re.search(r"\[([0-9a-fA-F-]{36})", msg)
            if dm:
                item_starts.append((ts, trace.rsplit(".", 1)[0], thread, dm.group(1)))

    print("===== 工作线程分布 TOP =====")
    for th, c in thread_counter.most_common(10):
        print(f"  {th}: {c}")
    if ts_all:
        print(f"[时间窗] {min(ts_all)} ~ {max(ts_all)}")

    # 单笔耗时分布
    if args.start_marker and item_starts:
        by_trace = defaultdict(list)
        for ts, trace, thread, did in item_starts:
            by_trace[trace].append((ts, thread, did))
        durations = []
        for trace, starts in by_trace.items():
            starts.sort()
            last_ts = max(e[0] for e in trace_events.get(trace, [])) if trace_events.get(trace) else None
            for i, (ts, thread, did) in enumerate(starts):
                if i + 1 < len(starts):
                    dur = (starts[i + 1][0] - ts).total_seconds()
                else:
                    dur = (last_ts - ts).total_seconds() if last_ts else None
                if dur is not None and 0 <= dur <= args.max_gap:
                    durations.append((dur, thread, did, ts))
        if durations:
            ds = sorted(d[0] for d in durations)
            n = len(ds)

            def pct(p):
                return ds[min(n - 1, int(n * p))]
            print(f"\n===== 单笔耗时分布 ({n} 笔) =====")
            print(f"均值 {sum(ds)/n:.2f}s  P50 {pct(0.5):.2f}  P75 {pct(0.75):.2f}  "
                  f"P90 {pct(0.9):.2f}  P95 {pct(0.95):.2f}  P99 {pct(0.99):.2f}  MAX {ds[-1]:.2f}")
            for lo, hi in [(0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 30), (30, 1e9)]:
                c = sum(1 for x in ds if lo <= x < hi)
                if c:
                    print(f"  {lo}-{hi if hi < 1e9 else '∞'}s: {c} 笔 ({c * 100.0 / n:.1f}%)")
            durations.sort(key=lambda x: -x[0])
            print("最慢 20 笔:")
            for dur, thread, did, ts in durations[:20]:
                print(f"  {ts} {thread} {dur:8.2f}s  {did[:12]}")

    # 间隔归因
    gap_by_pattern = defaultdict(list)
    top_gaps = []
    for trace, events in trace_events.items():
        events.sort(key=lambda e: e[0])
        for i in range(1, len(events)):
            p_ts, _p_th, p_msg = events[i - 1]
            c_ts, c_th, _c_msg = events[i]
            gap = (c_ts - p_ts).total_seconds()
            if gap <= 0 or gap > args.max_gap:
                continue
            pat = normalize(p_msg)
            gap_by_pattern[pat].append(gap)
            top_gaps.append((gap, c_ts, c_th, pat))
    if gap_by_pattern:
        print(f"\n===== 步骤间隔归因 TOP{args.top}（间隔归属前一行日志） =====")
        stats = [(sum(g), len(g), sum(g) / len(g), max(g), p) for p, g in gap_by_pattern.items()]
        stats.sort(key=lambda x: -x[0])
        print(f"{'总耗时(s)':>10} {'次数':>7} {'均值(s)':>8} {'最大(s)':>8}  模式")
        for tot, cnt, avg, mx, pat in stats[: args.top]:
            print(f"{tot:10.1f} {cnt:7d} {avg:8.3f} {mx:8.2f}  {pat}")
        print(f"所有间隔合计: {sum(s[0] for s in stats):.1f}s")
        top_gaps.sort(key=lambda x: -x[0])
        print("\n===== 单次最大间隔 TOP25 =====")
        for gap, ts, th, pat in top_gaps[:25]:
            print(f"  {ts} {th} gap={gap:8.2f}s  prev={pat[:80]}")


if __name__ == "__main__":
    main()
