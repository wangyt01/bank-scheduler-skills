---
name: scheduler-thread-dump-analysis
slug: scheduler-thread-dump-analysis
displayName: 调度卡死线程快照分析
summary: 多次线程快照签名分类与跨采样一致性判定，追溯主线程 await 到根因子卡点，支持历史复卡同源判定，产出归因报告与取证清单。
description: 调度卡死线程快照分析（jstack/trace dump 归因）。当用户说"调度卡死了/卡主了""线程快照分析""jstack分析""线程dump分析""Worker线程占满""CountDownLatch等待不退出""又卡死了，和上次一样吗"时使用。给定 >=2 次线程快照文件（jstack/jcad/arthas 导出 txt，>=3 次更佳、间隔 10~30s），自动解析线程块并按签名分类（CountDownLatch 主线程等待 / Redisson 解锁与等锁 / 银企 Socket / HTTP / DB / netty 空闲 / 哨兵正常长连接），统计卡死 Worker 清单，做跨采样一致性判定（静态卡死 vs 慢执行），提取 JVM 运行时长与堆内存排除 GC 因素，追溯"主线程 await → 根因子线程卡点"，可对照历史卡死分析文档做同源判定，产出 Markdown 归因报告与 P0/P1 建议、Redis/sentinel 取证动作。仅只读分析：不修改快照与源码，仅用 Python 标准库（>=3.10）。
version: 1.0.1
category: devtools
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# 调度卡死线程快照分析（jstack 归因）

面向银企直联调度"卡死/卡主"场景：调度 Worker 被占满、交易全部排队的定位与归因。
核心方法：多快照块解析 → 签名分类 → 卡死 Worker 清单 → 跨采样一致性判定 → 现象层（主线程 await）追溯根因层（子线程卡点）→ 对照历史文档同源判定 → 归因报告。

开始前先加载 @references/gotchas.md。快照解析与判读陷阱都在其中，违反会得出错误结论（如把"加锁流程 close 旧锁"误判成解锁缺陷、把 epollWait 空闲当成忙、单次快照下静态卡死结论）。

## 输入

| 项 | 必需 | 缺省行为 |
|---|---|---|
| 线程快照文件（jstack/jcad/arthas 导出 txt，**>=2 次**可跑，**>=3 次**、间隔 10~30s 最佳） | 是 | 只有 1 次时只能分类不能判"静态卡死"，报告须标注 |
| 关注线程名（如 `RTF_CLUSTERED_JOB_SCHEDULER_Worker-44`） | 否 | 脚本按 `--worker-pattern` 自动发现所有卡死 Worker |
| 历史卡死分析文档（路径） | 否 | 给了则做逐帧同源比对；没给则输出首次归因 |
| 工程源码路径 | 否 | 给了则把卡点落到具体类与行，核对锁/超时逻辑 |

## 提问模板（用户侧）

一条消息给齐要素即可一次出完整报告：

```
读取一下 <快照目录> 日志，最新的 N 次线程快照。
上次卡死过，分析结论文档在：<应用侧分析文档路径>，工程路径和报错在：<txt 路径>。
本次卡死线程是 <线程名>。请对照上次结论做卡死归因分析，输出报告。
```

只给快照目录也能跑（线程自动发现 + 分类），但"同源复发"判定会缺失历史对照。

## 步骤

1. 环境检查：`python --version` >= 3.10 即可（脚本仅用标准库）。注意 Windows 下传 `D:/...` 路径，Git Bash 的 `/d/...` Python 不识别。
2. 跑初筛：
   ```bash
   python scripts/analyze_thread_dump.py <dump1> <dump2> <dump3> \
       --out <报告目录>/thread-dump-analysis.md \
       [--worker-pattern RTF_CLUSTERED_JOB_SCHEDULER_Worker] [--max-stack 25]
   ```
3. 复核报告五个部分：内存概览（排除 GC）→ 卡死 Worker 清单（占池比例）→ 分类统计 → 一致性矩阵（静态卡死判定）→ 核心卡点完整栈。
4. 现象层→根因层追溯：主线程卡 `CountDownLatch.await(TransProcessor.process)` 只是现象；找到它等待的同名业务子线程，看子线程栈顶落在哪个签名分类（等 Redis 解锁响应 / 无限等锁 / 银企 Socket / HTTP / DB）。
5. 判读（详见 gotchas）：`RedissonBaseLock.unlock` 多为加锁流程 `internalAddLock` 先 close 旧锁；`redisson-netty-*` 空闲 `epollWait` = 命令发出无响应；`MasterListener`/`cache-sub` 的 `socketRead0` 属正常。
6. 同源判定：与历史文档的核心栈逐帧比对（类名 + 行号 + 调用链），记录差异点（包名前缀/行号漂移可忽略，卡点帧一致即同源）。
7. 产出报告（结构 = 脚本生成模板 + 人工补充）：
   - 结论速览表（卡死线程 / 卡点 / 根因 / 内存判定 / 同源判定）
   - 现象层栈、根因层栈原文
   - 与上次对比表（维度：主线程、根因子线程、内存、IO 线程、改造落地情况）
   - 建议：P0 主线程 await 兜底超时 / P1 锁改 tryLock+leaseTime / P1 银企 setSoTimeout
   - 取证动作：Redis slowlog 与 `--latency`、sentinel `+switch-master` 日志、redisson `timeout/retryAttempts` 配置、`--scan --pattern "*lock*"` 残留锁
8. 汇报：简短中文结论（卡在哪 / 为什么 / 与上次关系 / 下一步），报告文件用 present_files 交付。

## 边界

- 只读：不修改快照与源码，不执行 Redis/数据库操作。
- 归因到"卡点签名 + 可复核栈帧"为止；Redis 侧最终取证（slowlog/sentinel 日志）需运维配合，报告列动作清单。
- 快照含生产线程名与内部类名，对外分享前注意脱敏。
