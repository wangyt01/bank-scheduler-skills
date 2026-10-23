#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调度实参核查——调度进入参数、队列条数、进入/结束时间线。

只读分析。用法：
  python sched_args.py --logdir <日志根目录> [--component 组件类名片段]
                        [--patterns "进入构件,条数：,构件执行结束,加锁"]
仅依赖 Python 标准库。
"""
import argparse
import gzip
import re
import sys
from pathlib import Path

LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\S+\s+\w+\s+"
    r"\[([^\]]*)\]\s+\[([^\]]*)\]\s+(\S+)\s+\[([^\]]*)\]\s(.*)$"
)
DEFAULT_PATTERNS = "进入构件,进入调度,条数：,构件执行结束,构件加锁"


def main():
    ap = argparse.ArgumentParser(description="调度实参与时间线核查（只读）")
    ap.add_argument("--logdir", required=True)
    ap.add_argument("--component", default="BankTransDetailAutoProcessComponent",
                    help="调度组件类名片段（缺省为收款自动入账组件）")
    ap.add_argument("--patterns", default=DEFAULT_PATTERNS, help="逗号分隔的事件关键词")
    ap.add_argument("--limit", type=int, default=40, help="最多输出事件条数")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logdir = Path(args.logdir)
    if not logdir.is_dir():
        print(f"[停止] 目录不存在: {logdir}", file=sys.stderr)
        sys.exit(1)

    patterns = [p for p in args.patterns.split(",") if p]
    events = []

    files = sorted(logdir.glob("*.log.gz")) + sorted(p for p in logdir.glob("*.log") if p.is_file())
    seen_gz = {f.name[: -len(".gz")] for f in logdir.glob("*.log.gz")}
    for fp in files:
        if fp.is_dir() or (fp.suffix == ".log" and fp.name in seen_gz):
            continue
        opener = gzip.open if fp.suffix == ".gz" else open
        with opener(fp, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                if args.component not in line:
                    continue
                m = LINE_RE.match(line.rstrip("\n"))
                if not m:
                    continue
                ts_s, _trace, _mod, _logger, thread, msg = m.groups()
                for p in patterns:
                    if p in msg:
                        clean = re.sub(r"\s+", " ", msg)[:160]
                        events.append((ts_s, thread, clean))
                        break

    if not events:
        print(f"[无结果] 未命中组件 {args.component} 的调度事件（可能窗口不含调度边界）", file=sys.stderr)
        sys.exit(3)

    print(f"===== 调度事件时间线（组件 {args.component}） =====")
    for ts, th, msg in events[: args.limit]:
        print(f"  {ts} [{th}] {msg}")
    if len(events) > args.limit:
        print(f"  ...共 {len(events)} 条，仅显示前 {args.limit} 条")

    # 提取实参 JSON 中的关键参数
    print("\n===== 调度实参关键值 =====")
    found = False
    for _ts, _th, msg in events:
        for key in ("threadCount", "backDays", "isUseDetailsUnit"):
            m = re.search(rf'"{key}"\s*:\s*"?([^,"}}\]]+)', msg)
            if m:
                print(f"  {key} = {m.group(1)}   <- 来自: {msg[:80]}")
                found = True
    if not found:
        print("  [未找到] 窗口内无调度进入实参日志——换更早的日志窗口重试，实参必须以日志为准")


if __name__ == "__main__":
    main()
