---
name: qoder-credits-claim
slug: qoder-credits-claim
displayName: Qoder 积分自动领取
summary: Qoder CN 每日 100 Credits 自动领取：脚本自带，接口直签，机器指纹由 Qoder 自带 runtime-info.exe 生成；固定活动生成后 5 分钟运行（错过不补领），以 actionType==CLAIM_BENEFIT 且 claimStatus==CLAIMABLE 为可领判据，输出单行 JSON 按六种 action 如实判读，含风控拦截识别、首次配置探测与定时接入模板。
description: "Qoder CN 每日 Credits 自动领取。当用户说\"Qoder 签到\"\"领 Qoder 积分\"\"领取 Credits\"\"Qoder 每日任务\"时使用。脚本随技能自带（接口直签，纯标准库）：机器指纹由 Qoder CN 自带 runtime-info.exe 生成，token 缓存与配置写在脚本同目录。活动每天 10:00（UTC+8）生成新 campaignId，固定 10:05 运行，错过不补领；每笔奖励领取后 30 天有效。可领判据必须是 actionType==CLAIM_BENEFIT 且 claimStatus==CLAIMABLE（不要用顶层 claimable 标志）。输出单行 JSON，按六种 action 如实判读（claimed/already_claimed/skip_no_claimable/claim_failed/auth_failed/network_error），含风控拦截（RISK_DEPENDENCY_UNAVAILABLE）识别、首次配置探测脚本 discover_qoder.py 与定时接入模板。严禁打印 token。"
version: 1.1.0
category: devtools
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# Qoder 积分自动领取

Qoder CN「100 Credits」每日自动领取。脚本随本技能自带（`scripts/qoder_checkin.py`，接口直签，纯 Python 标准库）：机器指纹由 Qoder CN 自带 `runtime-info.exe` 生成，token 仅缓存在本地文件、不打印。

## 关键背景（决定调度时间）

- 活动每天 **10:00（UTC+8）** 服务端生成新 campaignId → 任务固定 **10:05** 运行，**错过不补领**；
- 每笔奖励自领取起 30 天有效；
- 可领判据 = `actionType==CLAIM_BENEFIT` 且 `claimStatus==CLAIMABLE`。**不要用顶层 claimable 标志**（旧版曾因此误判）。

## 前置条件（任何用户）

1. Python ≥ 3.10（纯标准库，无需 pip 安装）；
2. 本机安装 **Qoder CN 桌面端**并登录过（登录态/机器指纹由客户端提供）；
3. 首次搭建先跑探测脚本，确认「登录态来源 → openapi 域名 → runtime-info.exe 位置」三件事：

```bash
python "<skill目录>/scripts/discover_qoder.py"   # 只读探测，不打印真实 token
```

机器指纹位置自动搜索 `Program Files` 等常见安装目录；若 Qoder CN 装在非常规路径（如其他盘符），在**脚本同目录**建 `qoder_checkin_config.json` 指定：

```json
{ "umid_path": "D:\\Program Files\\Qoder CN\\resources\\umid\\runtime-info.exe" }
```

## 执行

```bash
python "<skill目录>/scripts/qoder_checkin.py" --no-notify
```

输出单行 JSON，按 `action` 字段判读。

## 结果判读（按 action 如实汇报，禁止把失败说成成功）

| action | 判定 | 汇报要点 |
|---|---|---|
| `claimed` | 成功 | 新领取（附 campaignKey 与 balance 余额）；提醒可在 Qoder CN 设置→Usage 核对到账 |
| `already_claimed` | 成功（已领过） | 今日已领，属正常，不算失败（附余额） |
| `skip_no_claimable` | 无可领活动 | 如实说明；若 `machine_identity=unavailable` 说明未取到机器指纹（服务端返回空列表），提示检查 Qoder CN 客户端安装与 `umid_path` 配置，**不要断言"活动下线"** |
| `claim_failed` | 失败 | 如实报告 msg；若含 `RISK_DEPENDENCY_UNAVAILABLE` 是被风控拦截（机器指纹无效），退出码非 0 时必须写明「本次未领到，需要人工处理」 |
| `auth_failed` | 失败 | 如实报告 msg；建议打开 Qoder CN 桌面端重新登录一次 |
| `network_error` / `status_failed` | 失败 | 如实报告 msg |

## 定时接入（WorkBuddy 计划任务模板）

固定每日 10:05（`FREQ=DAILY;BYHOUR=10;BYMINUTE=5`）。计划任务 prompt 模板：

```text
执行 Qoder 每日 100 Credits 自动领取：
python "<skill目录>/scripts/qoder_checkin.py" --no-notify
按 action 字段如实汇报（claimed/already_claimed/skip_no_claimable/claim_failed/
auth_failed/network_error 六类），禁止把失败说成成功；
claim_failed 含 RISK_DEPENDENCY_UNAVAILABLE 时写明"需要人工处理"。
```

## 安全约束

1. **严禁**在任何输出/汇报/文件中打印 token、refreshToken、machineToken（脚本输出已脱敏，直接引用即可）；
2. 不修改 Qoder 客户端目录下任何文件；
3. 不访问 Qoder 官方域以外的地址；
4. 配置与 token 缓存均写在脚本同目录（`qoder_checkin_config.json` / `.qoder_token_cache.json`），含敏感信息勿外传。

## 关于作者

本技能由公众号「技术日记本」维护。
每天 08:30，3 分钟读懂 AI 圈最重要的事：大模型、AI 工具、行业动态，每条资讯附主编解读 —— 微信搜索公众号「技术日记本」即可关注。
技能问题与需求反馈，欢迎在 SkillHub 私信或评论区留言。
