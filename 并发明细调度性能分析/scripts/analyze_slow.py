#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并发明细型调度日志分析：单笔耗时分布 + 日志间隔归因 + 锚点配对统计。

只读分析。扫描 --logdir 下的 .log.gz / .log 分卷：
- --scope：按组件 logger 类名/业务标记先圈定本组件口径（防共库多任务污染）
- --worker-keyword / --start-marker：重建工作线程会话、单笔耗时分布
- --pair 锚A 锚B：同 trace 同线程配对统计（次数/P50/P90/MAX/合计 + 锚B 值分布）
- 解析率分类：堆栈续行/长报文折行不计为格式错误，良性放行；仅真格式不符占比高时停止
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
TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
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
        if not fp.is_file():
            continue
        if fp.suffix == ".log" and fp.name in seen_gz:
            continue  # gz 与同名平文件重复，只取 gz
        yield fp


def iter_lines(files):
    """逐行解析，未匹配行做良性/真格式不符启发式分类。

    良性（不计为格式错误）：缩进堆栈续行（at ...）、紧随正常行的无时间戳折行、空行。
    仅当"疑似格式不符"占比 >20% 时以退出码 2 停止。
    """
    total = parsed = benign = genuine = 0
    prev_matched = False
    for fp in files:
        opener = gzip.open if fp.suffix == ".gz" else open
        with opener(fp, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                total += 1
                s = line.rstrip("\n")
                m = LINE_RE.match(s)
                if m:
                    parsed += 1
                    prev_matched = True
                    yield m.groups()
                    continue
                st = s.strip()
                if not st:
                    benign += 1  # 空行
                elif s[:1] in (" ", "\t") and prev_matched:
                    benign += 1  # 堆栈续行（at xxx）
                elif not TS_PREFIX_RE.match(s) and prev_matched:
                    benign += 1  # 长报文折行（无时间戳前缀续行）
                else:
                    genuine += 1
                    prev_matched = False
    t = max(total, 1)
    print(f"[扫描] 总行数 {total}, 解析 {parsed} ({parsed * 100.0 / t:.1f}%), "
          f"堆栈/折行续行 {benign} ({benign * 100.0 / t:.1f}%), "
          f"疑似格式不符 {genuine} ({genuine * 100.0 / t:.1f}%)", file=sys.stderr)
    if total and genuine * 100.0 / total > 20:
        print("[停止] 疑似格式不符行占比过高，请确认日志格式", file=sys.stderr)
        sys.exit(2)


def pct_of(ds, p):
    return ds[min(len(ds) - 1, int(len(ds) * p))]


def main():
    ap = argparse.ArgumentParser(description="单笔耗时分布 + 间隔归因 + 锚点配对（只读）")
    ap.add_argument("--logdir", required=True, help="日志根目录")
    ap.add_argument("--worker-keyword", default=None,
                    help="工作线程名关键词（如 收款并发）；缺省自动探测含 thread/worker/并发 的线程名")
    ap.add_argument("--start-marker", default=None,
                    help="单笔开始标记（如 '自动入账开始['）；缺省只做间隔归因")
    ap.add_argument("--scope", default=None,
                    help="组件口径关键词（logger 类名或消息子串）：先过滤出本组件行再做全部统计，防共库多任务污染")
    ap.add_argument("--pair", nargs=2, metavar=("锚A", "锚B"), default=None,
                    help="锚点配对统计：配对 锚A 行 → 同 trace 同线程下一个 锚B 行，输出次数/P50/P90/MAX/合计与锚B值分布")
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

    scope = args.scope

    def keep(logger, thread, msg):
        # 口径优先：--scope 命中 logger 类名或消息子串即保留（线程不设限，
        # 因瓶颈可能位于平台调度主线程的前置逻辑）；无 --scope 时维持线程过滤
        if scope:
            return scope in logger or scope in msg
        if args.worker_keyword:
            return args.worker_keyword in thread
        t = thread.lower()
        return "thread" in t or "worker" in t or "并发" in thread

    trace_events = defaultdict(list)  # trace -> [(ts, thread, msg)]
    thread_counter = Counter()
    item_starts = []                  # (ts, trace, thread, item_id)
    ts_all = []
    all_lines = []                     # (ts, trace, thread, msg) 用于锚点配对

    for ts_s, trace, _mod, logger, thread, msg in iter_lines(files):
        if not keep(logger, thread, msg):
            continue
        try:
            ts = parse_ts(ts_s)
        except ValueError:
            continue
        thread_counter[thread] += 1
        ts_all.append(ts)
        trace_key = trace.rsplit(".", 1)[0]
        trace_events[trace_key].append((ts, thread, msg))
        all_lines.append((ts, trace_key, thread, msg))
        if args.start_marker and args.start_marker in msg:
            dm = re.search(r"\[([0-9a-fA-F-]{36})", msg)
            if dm:
                item_starts.append((ts, trace_key, thread, dm.group(1)))

    print("===== 工作线程分布 TOP =====")
    for th, c in thread_counter.most_common(10):
        print(f"  {th}: {c}")
    if ts_all:
        print(f"[时间窗] {min(ts_all)} ~ {max(ts_all)}")

    # 单笔耗时分布（--scope 与 --worker-keyword 叠加时，再按线程关键词收窄）
    if args.start_marker and item_starts:
        by_trace = defaultdict(list)
        for ts, trace, thread, did in item_starts:
            if scope and args.worker_keyword and args.worker_keyword not in thread:
                continue
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
            print(f"\n===== 单笔耗时分布 ({n} 笔) =====")
            print(f"均值 {sum(ds)/n:.2f}s  P50 {pct_of(ds, 0.5):.2f}  P75 {pct_of(ds, 0.75):.2f}  "
                  f"P90 {pct_of(ds, 0.9):.2f}  P95 {pct_of(ds, 0.95):.2f}  P99 {pct_of(ds, 0.99):.2f}  MAX {ds[-1]:.2f}")
            for lo, hi in [(0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 30), (30, 1e9)]:
                c = sum(1 for x in ds if lo <= x < hi)
                if c:
                    print(f"  {lo}-{hi if hi < 1e9 else '∞'}s: {c} 笔 ({c * 100.0 / n:.1f}%)")
            durations.sort(key=lambda x: -x[0])
            print("最慢 20 笔:")
            for dur, thread, did, ts in durations[:20]:
                print(f"  {ts} {thread} {dur:8.2f}s  {did[:12]}")

    # 锚点配对统计
    if args.pair:
        a_kw, b_kw = args.pair
        evs_by_tt = defaultdict(list)
        for ts, trace_key, thread, msg in all_lines:
            if a_kw in msg:
                evs_by_tt[(trace_key, thread)].append((ts, "A", msg))
            elif b_kw in msg:
                evs_by_tt[(trace_key, thread)].append((ts, "B", msg))
        pair_durs = []
        b_vals = Counter()
        for evs in evs_by_tt.values():
            evs.sort(key=lambda e: e[0])
            open_ts = None
            for ts, kind, msg in evs:
                if kind == "A":
                    open_ts = ts  # 多个锚A取最近一个
                elif open_ts is not None:
                    d = (ts - open_ts).total_seconds()
                    if 0 < d <= args.max_gap:
                        pair_durs.append(d)
                        nums = re.findall(r"[0-9][0-9,]{0,11}", msg)
                        if nums:
                            b_vals[int(nums[-1].replace(",", ""))] += 1
                    open_ts = None
        print(f"\n===== 锚点配对统计  锚A='{a_kw}' → 锚B='{b_kw}' =====")
        if not pair_durs:
            print("  无配对命中（检查锚点关键词，或是否需要 --scope 圈定组件口径）")
        else:
            ds = sorted(pair_durs)
            n = len(ds)
            print(f"配对次数 {n}  P50 {pct_of(ds, 0.5):.2f}s  P90 {pct_of(ds, 0.9):.2f}s  "
                  f"MAX {ds[-1]:.2f}s  合计 {sum(ds):.1f}s")
            if b_vals:
                print("锚B行尾数值 TOP10:")
                for v, c in b_vals.most_common(10):
                    print(f"  {v}: {c} 次")

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
