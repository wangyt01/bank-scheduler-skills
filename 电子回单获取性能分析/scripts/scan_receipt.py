# -*- coding: utf-8 -*-
"""全量扫描电子回单获取（GetBankReceipts）调度日志，提取事件链时间线到 JSON。

事件链：SEND 发送报文 -> RECV 接收报文 -> (每笔) DL_OK 下载成功 -> UP_REQ 上传影像
        -> UP_RSP 上传影像返回 -> DB_OK 入库成功 -> RM_OK 删除成功
异常事件：EMPTY 银企直联返回文件路径和文件内容都为空
          NO_RSP 银企直联未返回当前明细的电子回单记录
          LOCK_FAIL 加锁失败

只读日志，不修改任何源文件。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

DEFAULT_EVENTS = [
    ("SEND", "发送报文"),
    ("RECV", "接收报文"),
    ("DL_OK", "下载成功"),
    ("UP_RSP", "上传影像返回"),
    ("UP_REQ", "上传影像"),
    ("DB_OK", "入库成功"),
    ("RM_OK", "删除成功"),
    ("EMPTY", "返回文件路径和文件内容都为空"),
    ("NO_RSP", "未返回当前明细的电子回单记录"),
    ("LOCK_FAIL", "加锁失败"),
]
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")


def find_volumes(logdir):
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


def main():
    ap = argparse.ArgumentParser(description="全量扫描电子回单调度日志事件链")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--tag", default="GetBankReceipts 电子回单下载", help="业务日志标记")
    ap.add_argument("--out", required=True, help="事件 JSON 输出路径")
    args = ap.parse_args()

    if not os.path.isdir(args.logdir):
        print("日志目录不存在:", args.logdir)
        raise SystemExit(2)

    vols = find_volumes(args.logdir)
    if not vols:
        print("未找到分卷（既无目录套同名文件也无平铺 .log）:", args.logdir)
        raise SystemExit(2)

    events = []
    counts = Counter()
    threads = Counter()

    for vname, path in vols:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if args.tag not in line:
                        continue
                    m = LINE_RE.match(line)
                    if not m:
                        continue
                    ts = m.group(1)
                    ev = None
                    for name, kw in DEFAULT_EVENTS:
                        if kw in line:
                            ev = name
                            break
                    if ev is None:
                        continue
                    counts[ev] += 1
                    mt = re.search(r"(TaskThread-\d+|Thread-\d+)", line)
                    if mt:
                        threads[mt.group(1)] += 1
                    ma = re.search(r"AccNo>(\d+)<", line) or re.search(r"账户\[(\d+)\]", line)
                    acc = ma.group(1) if ma else ""
                    events.append((ts, ev, acc))
        except OSError as e:
            print("ERR", vname, e, file=sys.stderr)

    events.sort(key=lambda e: e[0])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False)

    print("事件总数:", len(events))
    print("事件分类计数:")
    for k, v in counts.most_common():
        print("  %-8s %d" % (k, v))
    if threads:
        print("线程分布:", dict(threads.most_common(10)))
    if events:
        print("时间范围:", events[0][0], "->", events[-1][0])
    if counts.get("SEND", 0) == 0 and counts.get("RECV", 0) == 0:
        print("警告: 未匹配到任何 SEND/RECV 事件，tag 可能不匹配，请 grep 实际业务标记。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
