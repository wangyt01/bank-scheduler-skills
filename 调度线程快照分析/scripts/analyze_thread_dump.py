# -*- coding: utf-8 -*-
"""线程快照（jstack/trace dump）卡死归因分析。只读分析，仅用 Python 标准库（>=3.10）。

用法：
    python analyze_thread_dump.py <dump1> [<dump2> ...] [--out report.md]
                                  [--worker-pattern RTF_CLUSTERED_JOB_SCHEDULER_Worker]
                                  [--max-stack 25]

输入为 jstack / jcad / arthas 导出的线程快照 txt（多个文件=多次采样，可做一致性判定）。
输出 Markdown 报告：内存/GC 头信息、线程分类统计、卡死 Worker 清单、
核心卡点线程明细、跨采样一致性矩阵。

分类口径（按顺序首命中即停，顺序不能乱）：
  unlock 在 lock 之前（close 旧锁的加锁流程会被误判成解锁）；
  银企 Socket 在 HTTP 之前；正常订阅监听在通用 socketRead0 之前。
"""
import argparse
import io
import os
import re
import sys
from collections import Counter

BLOCK_SPLIT_RE = re.compile(r'^(?=")', re.M)
NAME_RE = re.compile(r'^"(.+?)"')
STATE_RE = re.compile(r"java\.lang\.Thread\.State:\s*(\S+[^\n]*)")
FRAME_RE = re.compile(r"^\s+at\s+(.+?)\s*$", re.M)
PARK_RE = re.compile(r"- parking to wait for\s+<([^>]+)>\s+\(([^)]+)\)")
LOCKED_RE = re.compile(r"^\s+- locked\s+<([^>]+)>", re.M)
UPTIME_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*s\s*$", re.M)
OLDGEN_RE = re.compile(r"ParOldGen\s+total (\d+)K, used (\d+)K")
YOUNG_RE = re.compile(r"PSYoungGen\s+total (\d+)K, used (\d+)K")

# 分类签名：(标签, 必须全部包含的子串, 任一包含的子串)。顺序即优先级，勿随意调整。
SIGNATURES = [
    ("调度主线程等待子线程(CountDownLatch.await)",
     ["CountDownLatch.await"], []),
    ("等Redis解锁响应(RedissonBaseLock.unlock)",
     ["RedissonBaseLock.unlock"], []),
    ("无限等分布式锁(RedissonLock.lock)",
     ["RedissonLock.lock("], []),
    ("等Redisson命令结果(CommandAsyncService.get)",
     ["CommandAsyncService.get"], []),
    ("银企Socket读(无超时嫌疑)",
     ["socketRead0"], ["TcpClient", "IOUtil", "PubTools", "TransferStreamToByte", "SocketClientConnection"]),
    ("Redis订阅或哨兵监听(正常长连接)",
     ["socketRead0"], ["MasterListener", "cache-sub", "SentinelConnect", "LeaderSelector"]),
    ("HTTP响应等待",
     ["socketRead0"], ["HttpClient", "SessionInputBufferImpl", "http"]),
    ("Netty IO空闲(epollWait)",
     ["epollWait"], []),
    ("DB查询",
     [], ["executeSelect", "DBUtil", "BQL", "PreparedStatement", "SocketInputStream.socketRead0"]),
    ("Quartz空闲Worker",
     ["SimpleThreadPool$WorkerThread.run(SimpleThreadPool.java:568)"], []),
]

CORE_LABELS = {
    "调度主线程等待子线程(CountDownLatch.await)",
    "等Redis解锁响应(RedissonBaseLock.unlock)",
    "无限等分布式锁(RedissonLock.lock)",
    "等Redisson命令结果(CommandAsyncService.get)",
    "银企Socket读(无超时嫌疑)",
}


def read_text(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def parse_blocks(text):
    blocks = []
    for b in BLOCK_SPLIT_RE.split(text):
        if not b.startswith('"'):
            continue
        m = NAME_RE.match(b)
        if not m:
            continue
        frames = FRAME_RE.findall(b)
        park = PARK_RE.search(b)
        state_m = STATE_RE.search(b)
        blocks.append({
            "name": m.group(1),
            "state": state_m.group(1).strip() if state_m else "?",
            "frames": frames,
            "park_obj": (park.group(2) if park else ""),
            "park_addr": (park.group(1) if park else ""),
            "text": b,
        })
    return blocks


def classify(block):
    text = block["text"]
    for label, all_subs, any_subs in SIGNATURES:
        if all(s in text for s in all_subs) and (not any_subs or any(s in text for s in any_subs)):
            return label
    if "socketRead0" in text:
        return "其他Socket读(待归类)"
    if block["state"].startswith("RUNNABLE"):
        return "其他RUNNABLE"
    return "其他WAITING/TIMED"


def short_name(name, worker_pattern):
    return re.sub(r"\d+", "N", name)[:60]


def biz_frames(block, limit=10):
    """业务相关帧（过滤 JDK/基础库前缀），用于一致性与展示。"""
    skip = ("java.", "sun.", "org.quartz", "io.iec.edp.caf.rpc", "org.redisson",
            "io.netty", "java.util.concurrent", "sun.nio", "org.apache")
    biz = [f for f in block["frames"] if not f.startswith(skip)]
    return biz[:limit] if biz else block["frames"][:limit]


def parse_header(text):
    out = {}
    m = UPTIME_RE.search(text)
    if m:
        try:
            secs = float(m.group(1).replace(",", ""))
            out["uptime_s"] = secs
            out["uptime_human"] = "%.1f 天" % (secs / 86400.0) if secs > 86400 else "%.1f 小时" % (secs / 3600.0)
        except ValueError:
            pass
    m = OLDGEN_RE.search(text)
    if m:
        total, used = int(m.group(1)), int(m.group(2))
        out["oldgen"] = "%.1fG/%.1fG (%.0f%%)" % (used / 1048576.0, total / 1048576.0, used * 100.0 / total)
    m = YOUNG_RE.search(text)
    if m:
        total, used = int(m.group(1)), int(m.group(2))
        out["younggen"] = "%.1fG/%.1fG (%.0f%%)" % (used / 1048576.0, total / 1048576.0, used * 100.0 / total)
    return out


def analyze_file(path, worker_pattern, max_stack):
    text = read_text(path)
    blocks = parse_blocks(text)
    rows = []
    for b in blocks:
        b["label"] = classify(b)
        rows.append(b)
    workers_stuck = [b for b in rows
                     if re.search(worker_pattern, b["name"]) and b["label"].startswith("调度主线程")]
    counters = Counter(b["label"] for b in rows)
    return {
        "path": path,
        "header": parse_header(text),
        "total": len(rows),
        "counters": counters,
        "blocks": rows,
        "workers_stuck": workers_stuck,
    }


def consistency(results, max_stack):
    """跨采样一致性：同名线程（核心卡点类）栈帧是否逐帧一致。"""
    by_name = {}
    for r in results:
        for b in r["blocks"]:
            if b["label"] in CORE_LABELS:
                by_name.setdefault(b["name"], []).append((os.path.basename(r["path"]), b))
    lines = ["| 线程 | 出现次数 | 栈一致 | 卡点 | parking对象 |", "|---|---|---|---|---|"]
    for name, items in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
        sigs = {tuple(b["frames"][:max_stack]) for _, b in items}
        labels = {b["label"] for _, b in items}
        parks = {b["park_obj"] for _, b in items}
        lines.append("| %s | %d | %s | %s | %s |" % (
            name[:55], len(items),
            "✅ 静态卡死" if len(sigs) == 1 else "⚠️ 栈有变化",
            " / ".join(sorted(labels))[:40],
            " / ".join(sorted(parks))[:45] or "-"))
    return "\n".join(lines)


def detail_section(results, worker_pattern, max_stack):
    """核心卡点线程完整栈（取第一个文件中的代表样本）。"""
    shown = set()
    out = []
    for r in results:
        for b in r["blocks"]:
            if b["label"] not in CORE_LABELS:
                continue
            key = (b["name"], b["label"])
            if key in shown:
                continue
            shown.add(key)
            out.append("### %s\n\n- **分类**：%s\n- **状态**：%s%s\n- **采样文件**：`%s`\n" % (
                b["name"], b["label"], b["state"],
                ("，parking → %s" % b["park_obj"]) if b["park_obj"] else "",
                os.path.basename(r["path"])))
            out.append("```")
            for f in b["frames"][:max_stack]:
                out.append("at " + f)
            out.append("```\n")
    return "\n".join(out) if out else "（未发现核心卡点线程）\n"


def report(results, worker_pattern, max_stack):
    md = ["# 线程快照卡死归因分析报告", ""]
    md.append("> 由 scheduler-thread-dump-analysis 生成；只读分析，结论需结合业务复核。\n")
    md.append("## 一、采样与内存概览\n")
    md.append("| 快照文件 | JVM运行时长 | 老年代 | 年轻代 | 线程总数 |")
    md.append("|---|---|---|---|---|")
    for r in results:
        h = r["header"]
        md.append("| `%s` | %s | %s | %s | %d |" % (
            os.path.basename(r["path"]),
            h.get("uptime_human", "-") + ("(%s s)" % h["uptime_s"] if "uptime_s" in h else ""),
            h.get("oldgen", "-"), h.get("younggen", "-"), r["total"]))
    md.append("")
    md.append("> 内存判读：老年代 >70% 视为有压力，需并行排查 GC；<50% 时可基本排除内存因素。\n")

    md.append("## 二、卡死 Worker（%s）\n" % worker_pattern)
    any_worker = False
    for r in results:
        if r["workers_stuck"]:
            any_worker = True
            names = ", ".join("`%s`" % b["name"] for b in r["workers_stuck"])
            md.append("- `%s`：%d 个 → %s" % (os.path.basename(r["path"]), len(r["workers_stuck"]), names))
    if not any_worker:
        md.append("未发现卡在 CountDownLatch.await 的调度 Worker。")
    md.append("")

    md.append("## 三、线程分类统计（各采样文件）\n")
    all_labels = sorted({l for r in results for l in r["counters"]})
    md.append("| 分类 | " + " | ".join(os.path.basename(r["path"]) for r in results) + " |")
    md.append("|---" * (len(results) + 1) + "|")
    for l in all_labels:
        md.append("| %s | %s |" % (l, " | ".join(str(r["counters"].get(l, 0)) for r in results)))
    md.append("")

    md.append("## 四、跨采样一致性（核心卡点线程）\n")
    md.append(consistency(results, max_stack))
    md.append("")
    md.append("> 判读：栈一致 + parking 对象地址不变 = 静态卡死（非慢执行）；栈变化 = 慢执行或重试中。\n")

    md.append("## 五、核心卡点线程完整栈\n")
    md.append(detail_section(results, worker_pattern, max_stack))

    md.append("## 六、归因提示（供复核，不替代人工判断）\n")
    md.append("""
- 主线程卡 `CountDownLatch.await` 是**现象层**：继续找它等待的同名银行子线程。
- `RedissonBaseLock.unlock` 卡住多为**加锁流程里先 close 旧锁**（`internalAddLock`），不等价于"解锁逻辑缺陷"。
- `RedissonLock.lock` 无限等锁与 unlock 无响应可**互相印证**为 Redis 命令无响应/响应丢失。
- `redisson-netty-*` 全部空闲 `epollWait`（RUNNABLE 但无 CPU）= 静止等网络响应，非锁自旋。
- `MasterListener`/`cache-sub` 的 `socketRead0` 属哨兵/订阅正常长连接读，勿误判为故障。
- 老年代正常（<50%）时优先沿锁→Redis→sentinel 链路取证（slowlog、`+switch-master`、redisson timeout 配置）。
- 银企 Socket 卡点（TcpClient/IOUtil/PubTools）为次要点，但同样需要 setSoTimeout 兜底。
""".strip())
    return "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser(description="线程快照卡死归因分析（只读）")
    ap.add_argument("files", nargs="+", help="线程快照 txt 文件（>=2 个可做一致性判定）")
    ap.add_argument("--out", default=None, help="输出 Markdown 路径（缺省打印到 stdout）")
    ap.add_argument("--worker-pattern", default=r"RTF_CLUSTERED_JOB_SCHEDULER_Worker",
                    help="调度 Worker 线程名正则（默认 RTF_CLUSTERED_JOB_SCHEDULER_Worker）")
    ap.add_argument("--max-stack", type=int, default=25, help="展示的最大栈帧数")
    args = ap.parse_args(argv)

    results = []
    for p in args.files:
        if not os.path.isfile(p):
            print("文件不存在，跳过：%s" % p, file=sys.stderr)
            continue
        results.append(analyze_file(p, args.worker_pattern, args.max_stack))
    if not results:
        print("没有可分析的快照文件", file=sys.stderr)
        return 2

    md = report(results, args.worker_pattern, args.max_stack)
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(md)
        print("报告已写入：%s" % args.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
