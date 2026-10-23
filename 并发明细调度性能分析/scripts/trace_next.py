#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锚点日志行之后，同一 trace 的下一行分布与间隔——锁定无打点区间的归属。

只读分析。用法：
  python trace_next.py --logdir <日志根目录> --anchor "认领规则出参" [--worker-keyword 收款并发]
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


def head(msg, n=60):
    if B64_RE.search(msg):
        msg = B64_RE.sub("<B64>", msg)
    if "{" in msg and len(msg) > 120:
        msg = msg[: msg.index("{")] + "<JSON>"
    return UUID_RE.sub("<UUID>", msg)[:n]


def main():
    ap = argparse.ArgumentParser(description="锚点行之后的下一行分布（只读）")
    ap.add_argument("--logdir", required=True)
    ap.add_argument("--anchor", required=True, help="锚点日志片段（在该行之后观察下一行）")
    ap.add_argument("--worker-keyword", default=None, help="线程名过滤关键词")
    ap.add_argument("--files-sample", type=int, default=6, help="抽样文件数（默认 6 个）")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logdir = Path(args.logdir)
    if not logdir.is_dir():
        print(f"[停止] 目录不存在: {logdir}", file=sys.stderr)
        sys.exit(1)

    files = sorted(logdir.glob("*.log.gz"))[:: max(1, len(list(logdir.glob('*.log.gz'))) // args.files_sample or 1)]
    if not files:
        files = sorted(p for p in logdir.glob("*.log") if p.is_file())[: args.files_sample]
    if not files:
        print("[停止] 无日志文件", file=sys.stderr)
        sys.exit(1)

    trace_events = defaultdict(list)
    for fp in files:
        opener = gzip.open if fp.suffix == ".gz" else open
        with opener(fp, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE_RE.match(line.rstrip("\n"))
                if not m:
                    continue
                ts_s, _trace, _mod, _logger, thread, msg = m.groups()
                if args.worker_keyword and args.worker_keyword not in thread:
                    continue
                try:
                    ts = datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    continue
                trace_events[_trace.rsplit(".", 1)[0]].append((ts, msg))

    pairs = Counter()
    gap_sum, gap_cnt, gap_max = Counter(), Counter(), Counter()
    for _trace, evs in trace_events.items():
        evs.sort(key=lambda e: e[0])
        for i in range(1, len(evs)):
            p_ts, p_msg = evs[i - 1]
            c_ts, c_msg = evs[i]
            if args.anchor in p_msg:
                gap = (c_ts - p_ts).total_seconds()
                key = head(c_msg)
                pairs[key] += 1
                gap_sum[key] += gap
                gap_cnt[key] += 1
                gap_max[key] = max(gap_max[key], gap)

    if not pairs:
        print(f"[无结果] 抽样中未命中锚点: {args.anchor}", file=sys.stderr)
        sys.exit(3)
    print(f"锚点「{args.anchor}」之后的下一行日志分布（抽样 {len(files)} 个文件）:")
    for key, cnt in pairs.most_common(12):
        print(f"  n={cnt:5d} avg={gap_sum[key] / gap_cnt[key]:6.3f}s max={gap_max[key]:6.2f}s  next= {key}")


if __name__ == "__main__":
    main()
