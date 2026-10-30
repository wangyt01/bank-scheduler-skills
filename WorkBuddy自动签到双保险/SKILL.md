---
name: workbuddy-dual-checkin
slug: workbuddy-dual-checkin
displayName: WorkBuddy 自动签到双保险
summary: WorkBuddy「Buddy 加油站」自动签到 + 派猫猫旅行闭环：双引擎自带（主路径签到助手、备份路径积分助手），均支持 5.6.2+ 加密登录态（AES-256-GCM 信封解密，密钥优先扫运行中客户端进程内存），主路径失败自动切换备份，两条路径都失败才判定失败，含 action 判读表与 travel 状态汇报规则。
description: "WorkBuddy Buddy 加油站自动签到与派猫猫旅行闭环。当用户说\"WorkBuddy 签到\"\"Buddy 加油站打卡\"\"领积分\"\"派猫猫旅行\"\"自动签到\"\"今天签到了吗\"时使用。双引擎随技能自带：主路径 workbuddy_checkin.py，备份路径 buddy_station.py checkin --travel-auto --json；主路径失败或结论缺失时自动切换备份，备份成功（code=0 新签 / code=10001 今日已签）即视为完成，两条路径都失败才判定失败。均支持 WorkBuddy 5.6.2+ AtRestEncryption 加密登录态（AES-256-GCM，密钥优先扫运行中 WorkBuddy.exe 进程内存，可回退明文或 WORKBUDDY_ATREST_KEY 环境变量）。含 action 判读表（clicked/skip_already_signed/error）、猫猫旅行状态汇报规则与定时接入模板。严禁打印任何 token；每条路径最多跑 1 次。"
version: 1.0.0
category: devtools
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# WorkBuddy 自动签到双保险

WorkBuddy「Buddy 加油站」自动签到 + 派猫猫旅行闭环。双引擎随本技能自带：主路径（`scripts/workbuddy_checkin.py`）失败时自动切备份路径（`scripts/buddy_station.py`），**两条路径都失败才判定为签到失败**。

## 前置条件（任何用户）

1. Python ≥ 3.10（纯标准库，无需 pip 安装）；
2. 本机安装 **WorkBuddy 桌面端**并登录过；
3. 加密登录态支持（WorkBuddy **5.6.2+** AtRestEncryption）：两个引擎均内置 AES-256-GCM 信封解密，密钥获取顺序为——扫运行中的 WorkBuddy.exe 进程内存 → `~/.workbuddy/auth` 明文兜底 → `WORKBUDDY_ATREST_KEY` 环境变量（44 字符 base64）；
4. **定时执行隐患**：若计划任务触发时客户端未启动、且无明文兜底/无 KEY 环境变量，两条路径会同时失败。建议保证客户端已启动，或配置 `WORKBUDDY_ATREST_KEY`。

## 执行

【主路径】

```bash
python "<skill目录>/scripts/workbuddy_checkin.py"
```

【备份路径】仅当主路径 `status=error`、退出码非 0、或未产生明确签到结论（action 为 null/缺失）时执行：

```bash
python "<skill目录>/scripts/buddy_station.py" checkin --travel-auto --json
```

备份成功（`checkin_result` code=0 新签 / code=10001 今日已签）即视为完成，汇报时注明「经备份引擎完成」。

## 结果判读（如实汇报，2-4 句，不贴原始 JSON）

| 输出特征 | 判定 | 汇报要点 |
|---|---|---|
| `action=clicked` | 成功 | 签到成功 +N 积分、连续第 X 天；含 balance 则报「当前积分余额」 |
| `action=skip_already_signed` | 成功（已签过） | 今日已签到，无需重复（附余额） |
| 主路径 `status=error` | 主路径失败 | 如实报告 msg，**不得谎报成功**；已执行备份路径则以备份结果为准 |
| 备份路径 `--json` 成功 | 成功 | 按 total_credits 报余额、streak_days 报连续天数、travel 报旅行状态 |
| 两条路径都失败 | 失败 | 如实报告最后失败原因（多为客户端未启动/登录态失效），提示打开 WorkBuddy 客户端后重试 |

【travel 字段单独汇报一句】有旅行积分可领/已领 → 报积分数；新派遣 → 报地点与预计回报；旅行中 → 报到达倒计时；今日派遣达上限 → 「猫猫今日派遣次数已用完，次日恢复」。

## 约束

1. **严禁**在任意输出中打印 token / accessToken / refreshToken；
2. 不无限重试：每条路径最多跑 1 次（脚本内部自带最多 1 次重试）；
3. 两条路径都失败时如实报告，不谎报成功。

## 定时接入（WorkBuddy 计划任务模板）

建议每日 08:00（`FREQ=DAILY;BYHOUR=8;BYMINUTE=0`）。计划任务 prompt 模板：

```text
执行 WorkBuddy Buddy 加油站自动签到 + 派猫猫旅行闭环：
1) python "<skill目录>/scripts/workbuddy_checkin.py"
2) 仅当主路径 status=error / 退出码非 0 / 无明确签到结论时，改跑：
   python "<skill目录>/scripts/buddy_station.py" checkin --travel-auto --json
按 action 判读表汇报（clicked / skip_already_signed / error），
travel 字段单独汇报一句。两条路径都失败才判定失败，如实报告，不谎报成功。
```

## 安全约束

1. token 只在内存与本地缓存中使用，**严禁**在任意输出中打印；
2. 推送/通知等可选功能由脚本同目录的本地配置文件决定，未配置时自动跳过，不影响签到主流程；
3. 登录态文件（`workbuddy-desktop.info`、`auth/`、`atrest.key`）属敏感文件，勿外传、勿上传。
