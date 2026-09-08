# bank-scheduler-skills

WorkBuddy 调度任务性能分析 Skill 套件 —— 面向银企直联调度日志的只读性能归因分析。

全量扫描 `logging-YYYY-MM-DD.N.log` 分卷调度日志，覆盖两类归因路径：

- **报文往返型**：配对请求与返回报文，量化银企 RTT、空查询占比、本地处理耗时，判定慢在**「入库慢 / 慢SQL / 报文往返慢」**三候选中的哪一类。
- **并发明细型**：多线程逐笔处理明细型调度，核实调度实参、重建工作线程会话、间隔归因、无打点区间定位，判定慢在**「数据积压 / SQL慢 / 单笔固定开销 / 无效功放大」**四候选中的哪一类。

产出 Markdown 分析报告与分级（P0/P1/P2）优化建议。

## 包含的 Skills

| Skill | 目录 | 用途 |
|---|---|---|
| 财司历史明细获取性能分析 | `hisdetails-perf-analysis/` | 计划任务 HisDetails：逐页配对 REQ→RESP，空页占比、查询日期水位分析 |
| 电子回单获取性能分析 | `getbankreceipts-perf-analysis/` | 计划任务 GetBankReceipts：发送→接收→下载→上传影像→入库逐笔事件链，空查询长尾识别 |
| 调度任务性能分析（通用版） | `scheduler-perf-analysis/` | 任意调度任务：业务标记自动发现、配对类型自动选择、标记映射可扩展 |
| 并发明细型调度任务性能分析 | `bank-scheduler-perf-analysis/` | 收款自动入账类：traceId+线程名会话重建、单笔耗时分布、间隔归因、调度实参核查、失败原因 TOP |
| 调度卡死线程快照分析 | `scheduler-thread-dump-analysis/` | jstack/trace dump 多快照归因：签名分类、静态卡死一致性判定、Redisson/银企 Socket/HTTP 卡点识别、历史复卡同源判定 |

五个 skill 均为**只读分析**：不修改日志与源码、不执行数据库操作、仅用 Python 标准库（≥3.10）。

## 安装

任选其一：

- **ClawHub / SkillHub**：`npx clawhub install <skill名>`（SkillHub 商店内容与 ClawHub 自动同步，也可在 SkillHub 网页搜索安装）；
- **手动安装**：将任一 skill 文件夹复制到 WorkBuddy 用户级技能目录 `~/.workbuddy/skills/`，或直接上传 `SKILL.md` 在根目录的 ZIP 安装。

## 打包与发布

```bash
# 打包：产物统一输出到 dist/（ZIP 安装包 + 使用说明 + manifest 清单）
python pack_skills.py                 # 按 pack.config.json 的 format 生成（默认 md）
python pack_skills.py --format html   # 用户指定格式，覆盖配置
```

产物与命名规则：

| 产物 | 路径与命名 |
|---|---|
| 安装包 | `dist/<skill名>-v<版本>.zip`（SKILL.md 在 zip 根，可直接上传安装） |
| 使用说明 | `dist/docs/<skill名>-v<版本>-guide.md` 或 `.html`（格式优先级：命令行 `--format` > `pack.config.json` 的 `format` > 默认 md） |
| 清单 | `dist/pack-manifest.json`（版本、路径、sha256、生成时间） |

发布到 SkillHub（官方 CLI，见 https://skillhub.cn/tutorials#publish-via-cli ）：

```bash
# 安装 CLI（Mac/Linux/WSL）
curl -fsSL https://skillhub.cn/install/install.sh | bash -s -- --cli-only

# 登录（API key 在 skillhub.cn 个人中心 → API keys 创建）
skillhub login --key skh_xxx --host https://api.skillhub.cn

# 本地预检 + 发布（SKILL.md 需含 slug/displayName/version 必填字段，summary/license 建议）
skillhub publish <skill目录> --host https://api.skillhub.cn --dry-run
skillhub publish <skill目录> --host https://api.skillhub.cn --changelog "变更说明"
```

发布成功后进入平台审核，审核通过后详情页自动可见；更新时保持 `slug` 不变、递增 `version`。本仓库 5 个 skill 已按此规范在 frontmatter 补齐 `slug`/`displayName`/`summary` 字段。

## 使用示例

对通用版，一条消息给齐三要素即可一次出完整报告：

```
分析一下<调度任务名>为什么这么慢，出个性能分析报告。
日志目录：<日志根目录路径>
执行表现：<起> ~ <止>，<N>条
工程源码路径：<可选，给的话优化建议能落到具体类和行>
```

只给日志目录也能跑（业务标记自动发现、时间自动推断），但对账环节会标注"无条数基准"。

线程快照分析（`scheduler-thread-dump-analysis`）的推荐问法：

```
读取一下 <快照目录> 日志，最新的 3 次线程快照。
上次卡死过，分析结论文档在：<历史分析文档路径>，工程路径和报错在：<txt 路径>。
本次卡死线程是 <线程名>。请对照上次结论做卡死归因分析，输出报告。
```

并发明细型（`bank-scheduler-perf-analysis`）的推荐问法（完整 4 套模板见技能内 `references/prompt-template.md`）：

```
分析<调度任务名>为什么慢，出个性能分析报告。
日志目录：<日志根目录，logging-*.log 分卷，gz 已包含>
关注：是数据量大、SQL 慢，还是单笔固定开销高/无效明细拖累？
工程源码：<可选，给的话优化建议能落到具体类和行>
```

参数调整后"反而更慢"的复查：给新旧两份日志目录 + 调整内容，要求先核实新参数是否真生效（以日志实参为准），再做单笔耗时/吞吐/归因对比。

## 实测战绩

- 大型历史明细拉取（3.6GB / 720 卷日志）：上万次请求 RTT 中位稳定、方差极小 → 定位 95% 以上墙钟耗在等银企报文返回，账户×日期组合串行 + 高空页占比为根因
- 电子回单下载（882MB / 123 卷日志）：绝大多数往返是"返回 0 张回单"的空查询，最长单次数分钟 → 定位银行侧长尾 + 单线程串行瓶颈
- 并发收款自动入账（两环境对比）：单笔 0.3s 无打点固定开销占总耗时 48.7%，积压放大总量；"调参后反而更慢"复查发现线程数配置实际未生效（日志实参与配置描述不符）、失败明细因主数据缺失每轮重新入队自我放大

## License

MIT
