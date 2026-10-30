---
name: modelscope-daily-checkin
slug: modelscope-daily-checkin
displayName: 魔搭 ModelScope 魔粒自动签到
summary: 用系统 Edge 独立 profile 免登录态维护地完成魔搭（ModelScope）每日魔粒打卡与点赞刷分：首次窗口模式登录一次，之后无头模式日常执行，输出今日累计/上限/余额判读，支持定时任务接入与失败如实上报。
description: "ModelScope（魔搭）每日魔粒自动签到。当用户说\"魔搭签到\"\"领魔粒\"\"ModelScope 每日打卡\"\"执行魔粒任务\"\"今天魔粒领了吗\"时使用。原理：魔粒「当日登录发」是后端会话副作用，带登录态访问首页即触发；再用登录态刷满当日点赞额度。cookie 由系统 Edge 独立 profile 自动续期，无需手动导出；收益口径以规则表「今日已得」为准（余额受 24h 过期干扰仅作参考）。含失败判读（登录态失效需人工重登）与定时任务接入模板。脚本自带，需要 Python3.10+ / playwright / requests / 系统 Edge。仅访问 modelscope.cn 域，不打印任何 cookie 内容。"
version: 1.0.0
category: devtools
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# ModelScope 魔粒自动签到

用系统 Edge（channel=msedge）+ 独立 profile 完成魔搭每日魔粒打卡 + 点赞刷分。
核心设计：cookie 由浏览器 profile 自己维护（自动续期、免导出免清洗）；「当日登录发」靠带登录态访问首页触发；点赞额度由服务端规则表下发，不硬编码。

## 依赖

- Python ≥ 3.10，第三方包：`playwright`（sync API）、`requests`
- 系统 Microsoft Edge（脚本用 `channel="msedge"` 启动，无需单独下载浏览器）
- 首次运行需在弹出的 Edge 窗口手动登录 modelscope.cn 一次（profile 保存在 `~/.modelscope_magicube_profile`）

## 执行

脚本随本 skill 自带（`scripts/` 下 3 个文件：`modelscope_playwright.py` + 依赖模块 `modelscope_daily_magicube.py`、`modelscope_like_farm.py`，需在同一目录）。

```bash
# 首次使用（只需一次）：弹 Edge 窗口手动登录，检测到登录态后自动打卡并退出
python "<skill目录>/scripts/modelscope_playwright.py"

# 日常打卡（可挂定时任务）
python "<skill目录>/scripts/modelscope_playwright.py" --headless

# profile 失登录态时的一键刷新：用 Cookie-Editor 导出 cookies.json 注入
python "<skill目录>/scripts/modelscope_playwright.py" --import-cookie cookies.json

# 可选参数：--no-like 跳过点赞刷分 / --model M 指定刷分模型 / --wait-login N / -v 明细
```

日志：写在 `scripts/magicube_daily.log`（脚本同目录），每次运行以时间戳分隔线开头、以「结束」汇总行收尾。定时任务里查看最后 10 行即可。

## 结果判读（按日志尾行，如实汇报）

| 日志特征 | 判定 | 汇报要点 |
|---|---|---|
| 「结束 成功 · 本次收益 +N 粒 · 今日累计 X/Y 粒 ✅ · 余额 Z」 | 成功 | 本次 +N、今日 X/Y、余额 Z |
| 「今日已得」≥ 当日上限（如 250/290）且本次 +0 | 成功（已领过） | 今日已领满，属正常 |
| 「登录态失效」/「cookie 失效(401)」/「profile 无登录态」 | 失败 | **需要人工重新登录**：窗口模式跑一次或 `--import-cookie`；不要尝试自行登录 |
| 其他异常堆栈 | 失败 | 原样汇报报错信息，不猜原因 |

规则：最多重试 1 次；无论成败只汇报，不做其他动作。

## 安全约束

1. 严禁在输出/汇报/文件中打印 cookie 值、会话内容；
2. 只访问 `modelscope.cn` 域（首页 + 用量页 + 官方 openapi），不访问其他地址；
3. `~/.modelscope_magicube_profile` 是登录态载体，勿删除、勿上传；
4. 点赞只刷「服务端规则表下发的当日额度」，不超刷。

## 定时接入（WorkBuddy 计划任务模板）

建议每日 08:00（`FREQ=DAILY;BYHOUR=8;BYMINUTE=0`）。计划任务 prompt 直接写：

```text
你是签到助手，执行 ModelScope 魔粒每日领取：
python "<skill目录>/scripts/modelscope_playwright.py" --headless
查看日志最后 10 行，按「结果判读」表汇报：状态 / 本次获得 / 今日累计 / 余额。
登录态失效时只汇报"需要人工重新登录"。最多重试 1 次。
```
