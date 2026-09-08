#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败原因分布统计——判断无效明细是否高度集中。

只读分析。用法：
  python fail_reasons.py --logdir <日志根目录> --keyword "失败，目标状态"
                          [--message-regex "message=([^)]{1,200})"] [--top 15]
仅依赖 Python 标准库。
"""
import argparse
import base64
import gzip
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_MSG_RE = r"message=([^,\)]{1,200})"


def decode_b64_in(text):
    """把文本中的长 base64 片段解码出来，便于阅读 SQL。"""
    def _dec(m):
        raw = m.group(0)
        try:
            pad = raw + "=" * (-len(raw) % 4)
            out = base64.b64decode(pad, validate=False).decode("utf-8", errors="replace")
            return out if sum(c.isprintable() for c in out) / max(len(out), 1) > 0.9 else raw
        except Exception:
            return raw
    return re.sub(r"[A-Za-z0-9+/=]{80,}", _dec, text)


def main():
    ap = argparse.ArgumentParser(description="失败原因分布（只读）")
    ap.add_argument("--logdir", required=True)
    ap.add_argument("--keyword", default="失败，目标状态", help="失败日志行关键词")
    ap.add_argument("--message-regex", default=DEFAULT_MSG_RE, help="原因提取正则（1 个捕获组）")
    ap.add_argument("--decode-b64", action="store_true", help="解码行内 base64 片段")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logdir = Path(args.logdir)
    if not logdir.is_dir():
        print(f"[停止] 目录不存在: {logdir}", file=sys.stderr)
        sys.exit(1)

    msg_re = re.compile(args.message_regex)
    raw_re = re.compile(r"原因：(.{1,80})")
    counter = Counter()
    total = 0

    files = sorted(logdir.glob("*.log.gz")) + sorted(p for p in logdir.glob("*.log") if p.is_file())
    seen_gz = {f.name[: -len(".gz")] for f in logdir.glob("*.log.gz")}
    for fp in files:
        if fp.is_dir() or (fp.suffix == ".log" and fp.name in seen_gz):
            continue
        opener = gzip.open if fp.suffix == ".gz" else open
        with opener(fp, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                if args.keyword not in line:
                    continue
                total += 1
                m = msg_re.search(line)
                if m:
                    reason = m.group(1)
                else:
                    m2 = raw_re.search(line)
                    reason = m2.group(1) if m2 else line[:100]
                reason = re.sub(r"\d{6,}", "<NUM>", reason)[:100]
                counter[reason] += 1

    print(f"失败行总数: {total}")
    for r, c in counter.most_common(args.top):
        print(f"  {c:6d} ({c * 100.0 / max(total, 1):5.1f}%)  {r}")


if __name__ == "__main__":
    main()
