# Gotchas：线程快照分析陷阱（来自 2026-08/09 山东港财司两次真实卡死实战，违反会得出错误结论）

## 解析层

- **线程名含中文**（如 `bebc Details 当日明细查询`）。统一 `open(..., encoding="utf-8", errors="replace")` 读；GBK 环境导出的快照个别线程名会乱码，**同名线程在一致性表里会分裂成两条**——按名称前缀聚合后再判读。
- **块切分按"行首引号"**：`re.split(r'^(?=")', text, re.M)`。栈尾的 `- locked <0x...>` 行属于该线程块，别丢。
- **栈帧行含空格**：`(Native Method)` 带空格，帧正则用 `^\s+at\s+(.+?)\s*$`，用 `(\S+)` 会截断。
- **不要用 `grep -B N` 定位线程名**：上下文窗口会串块，把别的线程的帧算进来。用块解析脚本。
- **Windows 下给 Python 传 `D:/...` 路径**：Git Bash 的 `/d/...` 映射 Windows Python 不识别，会报"文件不存在"。

## 采样与判定层

- **单次快照不能下"静态卡死"结论**。至少 3 次、间隔 10~30s。判定标准三条同时满足：同名同 tid、栈帧逐帧一致、`parking to wait for` 对象地址不变。栈有变化 = 慢执行/重试中。
- **parking 对象类型是关键证据**：`CountDownLatch$Sync` = 主线程等子线程退出；`CompletableFuture$Signaller` = 等 Redisson 命令 future（Redis 响应）；`AQS` 独占 = 等 JVM 内锁。
- **快照头信息必读**：首部有 JVM 运行秒数（`347112.953 s` ≈ 4 天）、GC flags、`PSYoungGen`/`ParOldGen` used/total。老年代 >70% 才考虑 GC 因素；两次事件对照（77% vs 23%）可干净排除内存变量。
- **Worker 占用口径**：只统计卡在业务代码（如 `CountDownLatch.await`）的 Worker；`Object.wait` 在 `SimpleThreadPool:568` 的是空闲 Worker，不算。Quartz 池默认 10 个，卡满即全局调度停滞。
- **一次可能同时卡多个调度**：实测当日明细、历史明细、回单下载、待签收结果查询的主线程同时挂起，别只盯着用户报的那个任务。

## 判读层（最容易误判的点）

- **看到 `RedissonBaseLock.unlock` 卡住 ≠ 解锁逻辑缺陷**：调用链是 `TaskSchedulerLock.lock` → `DistributedLockService.internalAddLock`（加锁流程**先 close 旧锁再加新锁**）→ `close` → `unlock`。本质仍是"加锁入口卡在 Redis 通信"。
- **`RedissonLock.lock` 无限等锁与 unlock 无响应互相印证**：两者都在 `CommandAsyncService.get` 等 CompletableFuture，统一指向"Redis 命令无响应/响应丢失"，而非单一锁被占。
- **`redisson-netty-*` 线程 RUNNABLE + `epollWait` 是空闲**，不是忙：96 个 IO 线程全部 epollWait 且无业务线程消耗 CPU = 静止阻塞等网络响应，排除锁自旋。
- **`MasterListener-<masterName>-[...]`、`cache-sub-*` 的 `socketRead0` 属正常**：哨兵架构（sentinel）的订阅长连接阻塞读，勿计为故障卡点。看到 MasterListener 即可判定 Redis 为哨兵架构，为取证方向（`+switch-master` 日志）提供线索。
- **银企 Socket 卡点（`TcpClient`/`IOUtil.copy`/`PubTools.TransferStreamToByte`）是次要点**：即使本次主因是锁，也照样存在，报告里单列，不要和锁混在一个结论里。

## 结论层

- **现象层/根因层分层表述**：主线程 `CountDownLatch.await()` 无超时是被动的受害者，必须继续找到它等待的子线程卡点，否则会得出"把 await 加个超时"就完事的浅结论（那是止损项 P0，不是根因）。
- **同源复发判定要逐帧比对历史栈**：类名+行号+调用链一致即同源（包名前缀或行号轻微漂移可忽略）；同时对照"上次给的 P0/P1 改造是否落地"——未落地而复发，直接写明因果。
- **报告必须给"下一步取证动作"**：归因到卡点签名为止，Redis 侧终审靠 `redis-cli --latency`、`slowlog get`、sentinel `+switch-master`/`+failover` 日志、redisson `timeout/retryAttempts` 配置核对、`--scan --pattern "*lock*"` 残留锁检查——这些应用侧拿不到，列为运维动作清单。
