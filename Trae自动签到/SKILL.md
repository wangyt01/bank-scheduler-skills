---
name: trae-daily-checkin
slug: trae-daily-checkin
displayName: Trae 自动签到
summary: Trae Work 自动签到与积分领取：脚本自带，从本机 Trae/TraeWork 客户端 storage.json 读取登录态直签（无需启动客户端），令牌仅在内存，服务端按天去重、幂等可重复，输出今日是否已签与积分数量，含失败判读与定时接入模板。
description: "Trae Work 自动签到与积分领取。当用户说\"Trae 签到\"\"领 Trae 积分\"\"执行 Trae 每日任务\"\"自动签到\"\"今天 Trae 签了吗\"时使用。脚本随技能自带（Node 标准库实现，无第三方依赖）：从 %APPDATA% 下 Trae/TraeWork 客户端 User/globalStorage/storage.json 解密登录态（令牌仅在内存，不落盘不打印），先查 /checkin_credits/status，未签则调 /checkin_credits/claim；服务端按天去重、幂等，重复执行安全。支持 --json 机器可读输出与 balance.js 余额查询。含结果判读（已签/新签/积分数量）、失败处理（登录态解密失败提示打开一次 Trae 客户端）与定时接入模板。需要 Node.js 与已登录的 Trae/TraeWork 桌面端。"
version: 1.0.0
category: devtools
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# Trae 自动签到

Trae Work 自动签到与积分领取。脚本随本技能自带（`scripts/checkin.js` + `lib.js`，仅用 Node 标准库 crypto/fs/os/path）：从本机 Trae/TraeWork 客户端的 `storage.json` 解密登录态，令牌只在内存中使用，先查签到状态、未签则领取。领取接口幂等：服务端按天去重，重复运行不会重复发放。

## 前置条件（任何用户）

1. 安装 **Node.js**（任意 LTS 版本）；
2. 本机安装 **Trae / TraeWork 桌面端**并登录过一次（登录态存于 `%APPDATA%\Trae\TraeWork\User\globalStorage\storage.json`，脚本自动定位多候选路径，无需启动客户端）。

## 执行

```bash
# 自动签到（人读输出）
node "<skill目录>/scripts/checkin.js"

# 机器可读输出（定时任务/判读用）
node "<skill目录>/scripts/checkin.js" --json

# 查询积分余额
node "<skill目录>/scripts/balance.js"
```

端点与解密方法逆向自官方客户端，可能随客户端版本变化；令牌仅在内存，不写入任何文件。

## 结果判读（如实汇报，不谎报）

| 输出特征 | 判定 | 汇报要点 |
|---|---|---|
| `签到成功 ✔ 领取积分 N` | 成功 | 今日已签 + 领取积分数量 |
| `今日已签 : 是（跳过领取）` | 成功（已签过） | 今日已签，属正常；可附"明日可领：基础 X + 额外 Y" |
| `[签到状态读取失败] …`（退出码 2） | 失败 | 原样报告 message；多为登录态失效或接口变更 |
| `签到失败 : …`（退出码 3） | 失败 | 原样报告 message 与原始返回摘要 |
| `错误：未找到 Trae 本地登录态（storage.json）` | 失败 | 提示先安装并**登录一次 Trae/TraeWork 桌面端** |
| 解密失败类报错 | 失败 | 提示用户**打开一次 Trae 客户端**刷新登录态后重试 |

规则：失败不循环重试，如实报告并给出人工恢复路径。

## 定时接入（WorkBuddy 计划任务模板）

建议每日 07:30（`FREQ=DAILY;BYHOUR=7;BYMINUTE=30`）。计划任务 prompt 模板：

```text
执行 Trae Work 自动签到领取积分：
node "<skill目录>/scripts/checkin.js"
按输出判读汇报：今日是否已签、领取积分数量。
失败（状态读取失败/解密失败/未找到登录态）时不要循环重试，如实报告原因，
提示打开一次 Trae 客户端确认登录态有效。
```

## 安全约束

1. 令牌仅在内存，**严禁**在任意输出中打印 token / 登录态内容；
2. 只读客户端 `storage.json`，不修改 Trae 客户端目录下任何文件；
3. 只访问 Trae 官方 API 域名，不访问其他地址；每次执行最多 1 轮。
