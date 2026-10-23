# -*- coding: utf-8 -*-
"""建立调度日志分卷时间索引。

支持两种布局：logging-YYYY-MM-DD.N.log 目录套同名文件、或平铺 .log 文件。
提取每卷首尾时间戳，输出 JSON 索引。只读日志，不修改任何源文件。
"""
import argparse
import json
import os
import re

TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


def find_volumes(logdir):
    """返回 [(卷名, 文件路径)]，按卷号数字排序近似时间序。"""
    vols = []
    for name in sorted(os.listdir(logdir)):
        d = os.path.join(logdir, name)
        if os.path.isdir(d):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                vols.append((name, f))
        elif name.endswith(".log"):
            vols.append((name, d))

    def vol_num(n):
        m = re.search(r"\.(\d+)\.log$", n)
        return int(m.group(1)) if m else -1

    vols.sort(key=lambda x: vol_num(x[0]))
    return vols


def head_tail_ts(path, window=3000):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(window).decode("utf-8", "replace")
        fh.seek(max(0, size - window))
        tail = fh.read(window).decode("utf-8", "replace")
    m1 = TS_RE.search(head)
    m2 = None
    for m in TS_RE.finditer(tail):
        m2 = m
    return (m1.group(0) if m1 else "?"), (m2.group(0) if m2 else "?")


def main():
    ap = argparse.ArgumentParser(description="建立日志分卷时间索引")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--out", required=True, help="索引 JSON 输出路径")
    args = ap.parse_args()

    if not os.path.isdir(args.logdir):
        print("日志目录不存在:", args.logdir)
        raise SystemExit(2)

    index = []
    for name, path in find_volumes(args.logdir):
        first, last = head_tail_ts(path)
        index.append({"file": name, "path": path, "size": os.path.getsize(path),
                      "first": first, "last": last})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print("总卷数:", len(index))
    for r in index:
        print("%-42s %9.1fMB  %s ~ %s" % (r["file"], r["size"] / 1e6, r["first"], r["last"]))


if __name__ == "__main__":
    main()
