# -*- coding: utf-8 -*-
"""全量扫描财司历史明细（HisDetails）调度日志，提取事件时间线到 CSV。

事件类型：
  REQ          待发送报文（提取 AccNo/Page/StartDate/EndDate）
  RESP         返回报文（记录行长与 BankSN 出现次数）
  SKIP         已存在,跳过（一条记录一行，可当记录数）
  INSERT       insert count = N（线程标记可能是 [N/A]，不做线程过滤）
  REC          交易记录
  BACKFILL     触发回查[N]天
  DATEUPD      更新历史明细查询日期
  DATEUPD_SKIP 欲更新但被跳过
  BOUND:*      调度边界（开始处理/处理结束/银行线程开始/退出/入口）

只读日志，不修改任何源文件。
"""
import argparse
import csv
import os
import re
import sys

REQ_RE = re.compile(
    r"<AccNo>(\w+)</AccNo>.*?<Page>(\d+)</Page>"
    r"<StartDate>([\d-]+)</StartDate><EndDate>([\d-]+)</EndDate>")
INSERT_RE = re.compile(r"insert count\s*=\s*(\d+)")
BACKFILL_RE = re.compile(r"触发回查\[(\d+)\]天")
BOUND_MARKERS = ("开始处理,共", "处理结束", "银行线程开始", "银行线程退出")


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


def classify(line):
    if "待发送报文" in line:
        return "REQ"
    if "返回报文" in line:
        return "RESP"
    if "已存在,跳过" in line:
        return "SKIP"
    if "insert count" in line:
        return "INSERT"
    if "交易记录" in line:
        return "REC"
    if "触发回查" in line:
        return "BACKFILL"
    if "更新历史明细查询日期" in line:
        return "DATEUPD_SKIP" if "欲更新" in line else "DATEUPD"
    return None


def main():
    ap = argparse.ArgumentParser(description="全量扫描 HisDetails 调度日志事件")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--tag", default="HisDetails 历史明细查询", help="业务日志标记")
    ap.add_argument("--out", required=True, help="事件 CSV 输出路径")
    ap.add_argument("--boundary-class", default="",
                    help="调度边界行过滤的类名（可选，因部署环境而异）")
    args = ap.parse_args()

    if not os.path.isdir(args.logdir):
        print("日志目录不存在:", args.logdir)
        raise SystemExit(2)

    vols = find_volumes(args.logdir)
    if not vols:
        print("未找到分卷（既无目录套同名文件也无平铺 .log）:", args.logdir)
        raise SystemExit(2)

    events = []
    stats = {"lines": 0, "tag_lines": 0, "req": 0, "resp": 0}

    for vname, path in vols:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stats["lines"] += 1
                    if args.tag not in line:
                        continue
                    stats["tag_lines"] += 1
                    ts = line[:23]
                    et = classify(line)
                    if et == "REQ":
                        m = REQ_RE.search(line)
                        if m:
                            events.append((ts, vname, "REQ", m.group(1), m.group(3),
                                           int(m.group(2)), ""))
                            stats["req"] += 1
                        else:
                            events.append((ts, vname, "REQ", "?", "?", -1,
                                           line[24:120]))
                            stats["req"] += 1
                    elif et == "RESP":
                        n = line.count("<BankSN>")
                        events.append((ts, vname, "RESP", "", "", -1,
                                       "len=%d;banksn=%d" % (len(line), n)))
                        stats["resp"] += 1
                    elif et == "INSERT":
                        m = INSERT_RE.search(line)
                        events.append((ts, vname, "INSERT", "", "", -1,
                                       m.group(1) if m else "?"))
                    elif et == "BACKFILL":
                        m = BACKFILL_RE.search(line)
                        events.append((ts, vname, "BACKFILL", "", "", -1,
                                       m.group(1) if m else "?"))
                    elif et:
                        events.append((ts, vname, et, "", "", -1, ""))
        except OSError as e:
            print("ERR", vname, e, file=sys.stderr)

    # 调度边界（非业务标记行）；--boundary-class 可指定调度类名以缩小范围（因部署环境而异）
    cls_filter = args.boundary_class or ""
    for vname, path in vols:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if cls_filter and cls_filter not in line:
                    continue
                for b in BOUND_MARKERS:
                    if b in line:
                        events.append((line[:23], vname, "BOUND:" + b, "", "", -1,
                                       line[24:160].strip()[:120]))
                        break

    events.sort(key=lambda e: e[0])
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "vol", "type", "acc", "date", "page", "extra"])
        w.writerows(events)

    print("scanned lines:", stats["lines"], "| tag lines:", stats["tag_lines"])
    print("REQ:", stats["req"], "RESP:", stats["resp"], "| events total:", len(events))
    print("saved:", args.out)
    if stats["req"] == 0 and stats["resp"] == 0:
        print("警告: 未匹配到任何 REQ/RESP 事件，tag 可能不匹配，请 grep 实际业务标记。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
