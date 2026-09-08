# -*- coding: utf-8 -*-
"""抽样发现调度日志中的业务标记（tag）与事件词汇。只读分析。

用途：面对陌生调度日志时，先跑本脚本确定 scan_events.py 的 --tag 与标记映射。
策略：按编号均匀抽取若干分卷，每卷读头部与中部若干行，统计：
  1. 候选业务标记：[组件代号 业务标记 ...] 与 BizInfo [...] 括号段
     （组件代号指日志标记前的小写组件词，因产品而异，自动识别不需指定）
  2. 默认事件关键词在各候选标记行内的命中次数
  3. content 字段前缀 TOP（用于发现词汇表未覆盖的标记）
  4. 线程名分布（并行度线索）
"""
import argparse
import os
import re
import sys
from collections import Counter

TAG_RES = [
    re.compile(r"\[([a-z][a-z0-9_]{2,15}) ([^\]]{2,70})\]"),
    re.compile(r"BizInfo \[([^\]]{2,70})\]"),
]
CONTENT_RE = re.compile(r'"content":"([^"]{0,60})')
THREAD_RE = re.compile(r"(TaskThread-\d+|Thread-\d+)")

DEFAULT_KEYWORDS = [
    ("REQ", "待发送报文"), ("SEND", "发送报文"), ("RESP", "返回报文"),
    ("RECV", "接收报文"), ("SKIP", "已存在,跳过"), ("INSERT", "insert count"),
    ("REC", "交易记录"), ("DL_OK", "下载成功"), ("UP_REQ", "上传影像"),
    ("UP_RSP", "上传影像返回"), ("DB_OK", "入库成功"), ("RM_OK", "删除成功"),
    ("EMPTY", "文件路径和文件内容都为空"), ("NO_RSP", "未返回当前明细"),
    ("LOCK_FAIL", "加锁失败"), ("BACKFILL", "触发回查"),
    ("DATEUPD", "更新历史明细查询日期"), ("THREAD_CFG", "配置的线程数"),
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


def pick_samples(vols, n):
    if len(vols) <= n:
        return vols
    step = (len(vols) - 1) / (n - 1)
    return [vols[round(i * step)] for i in range(n)]


def sample_lines(path, per_spot):
    """每卷取头部与中部各 per_spot 行。"""
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                out.append(line)
                if i + 1 >= per_spot:
                    break
        size = os.path.getsize(path)
        if size > 1 << 20:  # >1MB 才补中部采样
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(size // 2)
                f.readline()  # 丢掉半行
                for i, line in enumerate(f):
                    out.append(line)
                    if i + 1 >= per_spot:
                        break
    except OSError as e:
        print("ERR", path, e, file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="抽样发现调度日志业务标记与事件词汇")
    ap.add_argument("--logdir", required=True, help="分卷日志根目录")
    ap.add_argument("--vols", type=int, default=6, help="抽样分卷数")
    ap.add_argument("--lines", type=int, default=20000, help="每卷每位置采样行数")
    args = ap.parse_args()

    if not os.path.isdir(args.logdir):
        print("日志目录不存在:", args.logdir)
        raise SystemExit(2)

    vols = find_volumes(args.logdir)
    if not vols:
        print("未找到分卷（既无目录套同名文件也无平铺 .log）:", args.logdir)
        raise SystemExit(2)

    tag_cnt = Counter()
    content_cnt = Counter()
    thread_cnt = Counter()
    kw_in_tag = {}          # tag -> Counter(事件关键词)
    lines_seen = 0
    samples = pick_samples(vols, args.vols)
    print("抽样分卷 %d/%d:" % (len(samples), len(vols)))
    for name, _ in samples:
        print("  ", name)

    for _, path in samples:
        for line in sample_lines(path, args.lines):
            lines_seen += 1
            tags = set()
            for rex in TAG_RES:
                for m in rex.finditer(line):
                    # 双捕获组的通用模式取 group(2)（业务标记），单组模式取 group(1)
                    t = (m.group(2) if (m.lastindex or 1) >= 2 else m.group(1)).strip()
                    # 截到常见分隔，保持 tag 简洁可读
                    t = re.split(r"\s+\d{4}-|\s+\|", t)[0].strip()
                    if 2 <= len(t) <= 50:
                        tags.add(t)
                        tag_cnt[t] += 1
            mt = THREAD_RE.search(line)
            if mt:
                thread_cnt[mt.group(1)] += 1
            for t in tags:
                kw = kw_in_tag.setdefault(t, Counter())
                for ev, kw_word in DEFAULT_KEYWORDS:
                    if kw_word in line:
                        kw[ev] += 1
            mc = CONTENT_RE.search(line)
            if mc:
                content_cnt[mc.group(1)[:14]] += 1

    print("\n采样行数:", lines_seen)
    print("\n== 候选业务标记 TOP20")
    for t, n in tag_cnt.most_common(20):
        kws = kw_in_tag.get(t)
        hint = (" | 事件命中: " + ",".join("%s=%d" % (e, c) for e, c in kws.most_common(6))) if kws else ""
        print("  %8d  %s%s" % (n, t, hint))

    print("\n== content 前缀 TOP15（发现词汇表外的新标记）")
    for c, n in content_cnt.most_common(15):
        print("  %8d  %s" % (n, c))

    if thread_cnt:
        print("\n== 线程名 TOP10")
        for t, n in thread_cnt.most_common(10):
            print("  %8d  %s" % (n, t))

    top = tag_cnt.most_common(1)
    if top:
        print("\n建议: 以出现次数最高且与目标业务相关的标记作为 --tag（当前 TOP1: %s）；"
              "若目标事件关键词未命中，用 scan_events.py 的 --markers 扩展映射。" % top[0][0])


if __name__ == "__main__":
    main()
