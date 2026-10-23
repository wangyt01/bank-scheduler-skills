# -*- coding: utf-8 -*-
"""统计银行返回条数（BillNum/BillNumTotal/PageTotal）。

大报文 XML 可能被真实换行拆成几十万条短行，<BillNum> 在尾行且没有业务日志
标签，因此必须用「返回报文起点 + 后续行」跨行关联，只按标签行过滤会大量漏计。
只读日志，不修改任何源文件。
"""
import argparse
import json
import os
import re
from datetime import datetime

BILLNUM_RE = re.compile(r"<BillNum>(\d+)</BillNum>")
TOTAL_RE = re.compile(r"<BillNumTotal>(\d+)</BillNumTotal>")
PAGETOTAL_RE = re.compile(r"<PageTotal>(\d+)</PageTotal>")
CLOSE_MARKERS = ("待发送报文", "交易记录", "已存在", "insert count")


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
    ap = argparse.ArgumentParser(description="按执行窗口统计银行返回条数")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--tag", default="HisDetails 历史明细查询", help="业务日志标记")
    ap.add_argument("--index", help="build_index.py 生成的索引 JSON（可选）")
    ap.add_argument("--run", action="append",
                    help='执行窗口 "标签=起始|结束"，时间格式 YYYY-MM-DD HH:MM:SS，可重复；缺省全量')
    args = ap.parse_args()

    runs = []
    for r in (args.run or []):
        label, span = r.split("=", 1)
        t0, t1 = span.split("|", 1)
        runs.append((label, datetime.strptime(t0.strip(), "%Y-%m-%d %H:%M:%S"),
                     datetime.strptime(t1.strip(), "%Y-%m-%d %H:%M:%S")))

    def seg(ts_line):
        if not runs:
            return "ALL"
        dt = datetime.strptime(ts_line[:19], "%Y-%m-%d %H:%M:%S")
        for label, t0, t1 in runs:
            if t0 <= dt <= t1:
                return label
        return "OUT"

    if args.index:
        recs = json.load(open(args.index, encoding="utf-8"))
        vols = [(r["file"], r.get("path") or os.path.join(args.logdir, r["file"], r["file"]))
                for r in recs]

        def vol_num(n):
            m = re.search(r"\.(\d+)\.log$", n)
            return int(m.group(1)) if m else -1
        vols.sort(key=lambda x: vol_num(x[0]))
    else:
        vols = find_volumes(args.logdir)

    if not vols:
        print("未找到分卷:", args.logdir)
        raise SystemExit(2)

    empty = {r: {"pages": 0, "billnum": 0, "page1_pages": 0, "page1_billnum": 0,
                 "maxpage": 0, "bytes": 0} for r in ([l for l, _, _ in runs] if runs else ["ALL"])}
    cur = None  # 当前未闭合返回报文：{"run", "billnum", "page1", "bytes"}

    for vname, path in vols:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if args.tag in line:
                    if "返回报文" in line:
                        run = seg(line[:23])
                        cur = {"run": run, "billnum": 0, "page1": 0, "bytes": len(line)}
                        st = empty.setdefault(run, {"pages": 0, "billnum": 0,
                                                    "page1_pages": 0, "page1_billnum": 0,
                                                    "maxpage": 0, "bytes": 0})
                        st["pages"] += 1
                        st["bytes"] += len(line)
                        m = BILLNUM_RE.search(line)
                        if m:
                            bn = int(m.group(1))
                            cur["billnum"] += bn
                            st["billnum"] += bn
                            if "<Page>1</Page>" in line:
                                st["page1_pages"] += 1
                                st["page1_billnum"] += bn
                        pt = PAGETOTAL_RE.search(line)
                        if pt:
                            st["maxpage"] = max(st["maxpage"], int(pt.group(1)))
                    elif any(k in line for k in CLOSE_MARKERS):
                        cur = None
                    continue
                if cur is None:
                    continue
                # 当前返回报文的后续行（可能无标签，BillNum 在尾行）
                st = empty.get(cur["run"])
                if st is None:
                    continue
                cur["bytes"] += len(line)
                st["bytes"] += len(line)
                m = BILLNUM_RE.search(line)
                if m:
                    bn = int(m.group(1))
                    cur["billnum"] += bn
                    st["billnum"] += bn
                    if "<Page>1</Page>" in line:
                        st["page1_pages"] += 1
                        st["page1_billnum"] += bn
                pt = PAGETOTAL_RE.search(line)
                if pt:
                    st["maxpage"] = max(st["maxpage"], int(pt.group(1)))

    for run, st in empty.items():
        zero = st["pages"] - st["page1_pages"] if st["pages"] else 0
        print(run, "| 返回报文页数:", st["pages"],
              "| BillNum 合计:", st["billnum"],
              "| 第1页页数:", st["page1_pages"],
              "| 第1页 BillNum 合计:", st["page1_billnum"],
              "| 最大 PageTotal:", st["maxpage"],
              "| 报文总量MB: %.1f" % (st["bytes"] / 1e6))
    if not empty:
        print("未匹配到任何返回报文，tag 可能不匹配。")


if __name__ == "__main__":
    main()
