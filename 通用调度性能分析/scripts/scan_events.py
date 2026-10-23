# -*- coding: utf-8 -*-
"""全量扫描调度日志，按标记映射提取通用事件时间线到 JSON。只读分析。

事件由「类型=关键词」映射识别，默认词汇表覆盖银企直联常见调度任务
（明细查询/回单下载等）。记录字段：ts、type、acc、page、num、thread、extra。

注意顺序：待发送报文 先于 发送报文 匹配（前者包含后者子串）。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
THREAD_RE = re.compile(r"(TaskThread-\d+|Thread-\d+)")
REQ_RE = re.compile(
    r"<AccNo>(\w+)</AccNo>.*?<Page>(\d+)</Page>"
    r"(?:.*?<StartDate>([\d-]+)</StartDate>)?")
INSERT_RE = re.compile(r"insert count\s*=\s*(\d+)")
SKIP_RE = re.compile(r"\[([0-9A-Za-z@.\-]{6,120})\]已存在,跳过")
BILLNUM_RE = re.compile(r"<BillNum>(\d+)</BillNum>")

DEFAULT_MARKERS = [
    ("REQ", "待发送报文"),
    ("SEND", "发送报文"),
    ("RESP", "返回报文"),
    ("RECV", "接收报文"),
    ("SKIP", "已存在,跳过"),
    ("DL_OK", "下载成功"),
    ("UP_RSP", "上传影像返回"),
    ("UP_REQ", "上传影像"),
    ("DB_OK", "入库成功"),
    ("RM_OK", "删除成功"),
    ("EMPTY", "文件路径和文件内容都为空"),
    ("NO_RSP", "未返回当前明细"),
    ("LOCK_FAIL", "加锁失败"),
    ("INSERT", "insert count"),
    ("REC", "交易记录"),
    ("BACKFILL", "触发回查"),
    ("DATEUPD", "更新历史明细查询日期"),
    ("THREAD_CFG", "配置的线程数"),
]


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


def parse_markers(spec):
    """解析 --markers "TYPE=关键词;TYPE=关键词"，追加到默认映射之后（优先级低于默认，避免覆盖 REQ/SEND 顺序）。"""
    markers = list(DEFAULT_MARKERS)
    if not spec:
        return markers
    for pair in spec.split(";"):
        if "=" not in pair:
            continue
        t, kw = pair.split("=", 1)
        t, kw = t.strip(), kw.strip()
        if t and kw:
            markers.append((t, kw))
    return markers


def classify(line, markers):
    for t, kw in markers:
        if kw in line:
            return t
    return None


def main():
    ap = argparse.ArgumentParser(description="全量扫描调度日志通用事件")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--tag", required=True, help="业务日志标记（discover_events.py 发现）")
    ap.add_argument("--out", required=True, help="事件 JSON 输出路径")
    ap.add_argument("--markers", help='扩展标记映射，如 "CHECK=校验完成;SYNC=同步完成"')
    ap.add_argument("--start", help="仅保留该时间之后的事件 YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--end", help="仅保留该时间之前的事件")
    args = ap.parse_args()

    if not os.path.isdir(args.logdir):
        print("日志目录不存在:", args.logdir)
        raise SystemExit(2)

    vols = find_volumes(args.logdir)
    if not vols:
        print("未找到分卷（既无目录套同名文件也无平铺 .log）:", args.logdir)
        raise SystemExit(2)

    markers = parse_markers(args.markers)
    events = []
    counts = Counter()
    threads = Counter()
    lines = 0
    tag_lines = 0

    for vname, path in vols:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    lines += 1
                    if args.tag not in line:
                        continue
                    tag_lines += 1
                    m = LINE_RE.match(line)
                    if not m:
                        continue
                    ts = m.group(1)
                    if args.start and ts[:19] < args.start:
                        continue
                    if args.end and ts[:19] > args.end:
                        continue
                    ev = classify(line, markers)
                    if ev is None:
                        continue
                    counts[ev] += 1
                    mt = THREAD_RE.search(line)
                    th = mt.group(1) if mt else ""
                    if th:
                        threads[th] += 1
                    acc = page = ""
                    num = -1
                    extra = {}
                    if ev in ("REQ", "SEND"):
                        ma = REQ_RE.search(line)
                        if ma:
                            acc = ma.group(1)
                            page = ma.group(2)
                    elif ev in ("RESP", "RECV"):
                        extra["banksn"] = line.count("<BankSN>")
                        mb = BILLNUM_RE.search(line)
                        if mb:
                            num = int(mb.group(1))
                        extra["len"] = len(line)
                    elif ev == "INSERT":
                        mi = INSERT_RE.search(line)
                        num = int(mi.group(1)) if mi else -1
                    elif ev == "SKIP":
                        ms = SKIP_RE.search(line)
                        if ms:
                            extra["sn"] = ms.group(1)[:80]
                    events.append({"ts": ts, "type": ev, "acc": acc, "page": page,
                                   "num": num, "thread": th, "extra": extra})
        except OSError as e:
            print("ERR", vname, e, file=sys.stderr)

    events.sort(key=lambda e: e["ts"])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False)

    print("scanned lines:", lines, "| tag lines:", tag_lines, "| events:", len(events))
    print("事件分类计数:")
    for k, v in counts.most_common():
        print("  %-10s %d" % (k, v))
    if threads:
        print("线程分布 TOP10:", dict(threads.most_common(10)))
    if events:
        print("时间范围:", events[0]["ts"], "->", events[-1]["ts"])
    if counts.get("REQ", 0) + counts.get("SEND", 0) == 0:
        print("警告: 未匹配到任何请求事件，tag 可能不匹配，请用 discover_events.py 重新发现。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
