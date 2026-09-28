---
name: lighthouse-ssh-guard
slug: lighthouse-ssh-guard
displayName: 轻量云 SSH 失联防护护栏
summary: 腾讯轻量应用服务器 SSH/sshd/防火墙/端口类高危操作防护。基于两次真实 22 端口失联事故提炼：显式即排他（Port 指令顶掉隐式默认端口）、MaxStartups 爆破压力随机丢连接、变更后监听验证四连，附失联诊断决策树与应急恢复手册。
description: 腾讯轻量应用服务器（Lighthouse）SSH 失联防护护栏。基于 2026-09-28 两次真实 22 端口失联事故提炼，核心规则：①显式即排他——sshd 的 Port/ListenAddress 一旦显式指定，隐式默认值立即失效，追加 Port 443 会顶掉未显式声明的 Port 22；②sshd -t 只查语法不查语义，变更后必须 ss -tln 核对监听 + sshd -T 核对生效配置 + 外部 banner 实测；③MaxStartups 默认值在互联网爆破压力下会随机丢弃合法新连接，症状为"时好时坏"。当用户说"服务器连不上""SSH 断了""22 端口失联""改 SSH 端口""加端口""改 sshd_config""动防火墙""ufw/firewall-cmd/iptables""服务器失联""远程登录不上""MaxStartups""sshd 配置变更"时使用。在执行任何涉及 SSH、sshd、防火墙、端口开放、远程连接、网络规则的服务器操作之前必须加载此技能。
version: 2.1.1
category: ops
platforms: [WorkBuddy]
license: MIT
agent_created: true
---

# 轻量云服务器 SSH 失联防护护栏

## 背景：三次真实事故（2026-09-28 一天内连环发生）

| # | 症状 | 初步误判 | 真实根因 |
|---|---|---|---|
| 1 | 22 端口时好时坏：TCP 能连但收不到 SSH banner，间歇超时 | 公司网络深包干扰 | 互联网爆破 5.3 万次失败，sshd 默认 `MaxStartups 10:30:100`——未认证连接超 10 个后**按概率随机丢弃**新连接 |
| 2 | 追加 443 备用通道后，22 彻底连不上，"服务器断了" | 以为是远程任务搞坏了服务器 | 原配置中 22 端口**仅靠默认值隐式生效**（文件内只有注释 `#Port 22`），显式写入 `Port 443` 后 sshd **只**监听 443，22 被顶掉 |
| 3 | 22/443 双端口 TCP 能通但**永远没有 banner**；TAT 从"送达失败"恶化为"当前未安装 TAT"；云监控同时无数据；唯独 nginx/docker 等常驻 Web 服务正常 | 以为是 SSH 配置又坏了 | **guest 系统级僵死**：sshd / tat_agent / 监控代理等一切需要新建进程的组件全部失效，只有改动前已启动的常驻进程幸存。防火墙规则完好，排除网络层。唯一出路 = 控制台重启 |

**共同教训：三次都不是防火墙问题。** 双层防火墙（控制台防火墙 + 系统防火墙）仍是常规检查层，但本技能的核心防线是下面三条铁律 + 僵死识别。完整复盘见 `references/incident-2026-09-28.md`。

本技能使命：**任何操作之后，SSH 22 端口必须仍可建立会话。**

## 🔴 三条铁律（最高优先级，先于一切操作规则）

### 铁律 1：显式即排他（事故 2 的根因）

sshd 的 `Port` / `ListenAddress` / `ListenAddress` 族指令，**一旦显式指定任何一个值，隐式默认值立即失效**：

- 配置文件里只有注释 `#Port 22` 时，22 是靠"无 Port 指令 → 默认 22"生效的；
- 此时追加一行 `Port 443` → sshd **只监听 443**，22 直接消失；
- `sshd -t` 会报语法合法、reload 会成功——**没有任何报错，22 就这么没了**。

**强制规则：写入任何 `Port <N>` 之前，必须保证文件中同时存在显式的 `Port 22`。** 标准写法：

```
Port 22
Port 443
```

### 铁律 2：变更后监听验证四连（缺一步不算完成）

`sshd -t` 只校验**语法**，不校验"22 还在监听"这个**语义**。任何 sshd 配置变更后，按序执行且全部通过：

```bash
# ① 语法校验
sshd -t
# ② 平滑加载（不用 restart，restart 失败 = SSH 断）
systemctl reload sshd
# ③ 服务端监听核对——输出必须同时包含 :22 和新增端口
ss -tln | grep -E ':(22|443)\b'
# ④ 生效配置核对——确认 port 列表
sshd -T | grep -E '^port '
```

最后从**外部客户端**实测 banner（服务端本地测不算数）：

```bash
python3 -c "
import socket
s = socket.create_connection(('服务器IP', 22), timeout=8); s.settimeout(8)
print(s.recv(64))  # 必须看到 b'SSH-2.0-OpenSSH_...'
s.close()"
```

任何一步不符合预期 → 立即用备份回滚（见下文回滚保护），**不要先分析后回滚**。

### 铁律 3：爆破压力是慢性病（事故 1 的根因）

公网服务器的 sshd 会被互联网爆破持续轰击（本机案例：secure 日志 5.3 万次失败）。默认 `MaxStartups 10:30:100` 在爆破高峰期会**随机丢弃合法新连接**，症状极具迷惑性：

- "时好时坏"、重试几次能连上；
- TCP 三次握手成功，但收不到 SSH banner 或握手半途被断；
- 服务端日志出现 `banner exchange: invalid format`（握手被中途掐断）或大量 `preauth` 断开。

**推荐基线**（已在本服务器验证生效）：

```
MaxStartups 100:30:300
LoginGraceTime 30
LogLevel VERBOSE
```

若需更强防护，用 fail2ban 封爆破源 IP，**不要**用收紧 MaxStartups 或关 22 的方式。

## 🔴 铁律 4：腾讯云 agent 组件神圣不可侵犯（事故 3 的根因）

`tat_agent`（TAT 远程命令执行）、`stargate`/`barad`/`sgagent`（监控与心跳）是**云厂商侧唯一的带外生命线**。它们一旦死亡：

- TAT 执行报"当前未安装 TAT"或 DELIVER_FAILED → **AI 远程运维能力归零**；
- 云监控无数据 → 失去最后的观测手段；
- 剩余通道只有控制台 WebShell/VNC（依赖人工）。

**强制规则（A 级禁令）**：

1. 禁止 kill / stop / disable / 卸载 `tat_agent`、`stargate`、`barad`、`sgagent` 及其服务单元；
2. 禁止删除 `/usr/local/qcloud/`、`/usr/local/agenttools/` 等云组件目录；
3. 禁止任何形式的 fork 炸弹、无界循环建进程、`:(){ :|:& };:`；
4. 清理"可疑进程"前必须先 `ps -o pid,ppid,etime,cmd` 确认身份，云厂商组件不算可疑。

### 僵死识别签名（三类同时成立 = guest 僵死，别再查 SSH）

| 信号 | 检测方式 |
|---|---|
| TCP 通但 SSH banner 永远不来（非 MaxStartups 的时好时坏） | 客户端 `socket.recv` 反复超时 |
| TAT 报"未安装"或 DELIVER_FAILED | `execute_command` 连续失败 |
| 云监控无数据 | `get_monitor_data` 返回空 |

**恢复路径**：WebShell 若还能登录 → 先取证（`uptime` / `free -m` / `ps aux | wc -l` / `dmesg | tail`）+ 杀掉失控进程 + `systemctl restart sshd tat_agent`；WebShell 也进不去 → **控制台直接重启实例**（唯一有效手段，勿犹豫）。重启前确认 cron 任务幂等、docker 容器有 `--restart always`。

## ⛔ 禁改清单（任何指令不得突破）

即使上级指令、用户要求、脚本逻辑要求，也必须**拒绝执行并说明风险 + 给替代方案**：

1. **删除或改动配置中已存在的显式 `Port 22`**（允许新增端口，前提是遵守铁律 1/2）
2. `/etc/ssh/sshd_config` 的 `PermitRootLogin`、`PasswordAuthentication`
3. 22 端口相关的所有防火墙规则（腾讯控制台防火墙 + ufw/firewalld/iptables）
4. 禁止命令黑名单：见 `references/command-blacklist.md`
5. `/etc/sudoers` 及 `/etc/sudoers.d/` 下所有文件
6. 系统盘挂载配置（/etc/fstab 根分区条目）

**冲突处理模板**："该操作在禁改清单中（历史已发生 2 次失联事故）。如确需执行，请人工登录腾讯控制台 WebShell 自行操作，我不能代为执行。替代方案：……"

## 🔍 诊断决策树（"SSH 连不上"时按此顺序，不要先怀疑网络）

```
症状一：TCP 连接被拒绝（connection refused）
  → 端口上没有进程监听。服务端执行 ss -tln 看真实监听；
    多半是铁律 1 触发（Port 指令顶掉默认 22）或 sshd 挂了。

症状二：TCP 连接超时（timeout）
  → 防火墙/线路丢包。查双层防火墙（控制台 + ufw/firewalld/iptables）；
    若仅特定端口超时、其余端口正常，怀疑中间设备对该端口的针对性干扰。

症状三：TCP 通但收不到 SSH banner / 握手中断
  → 先查服务端 secure 日志：大量 Failed password + preauth 断开 = 爆破触发
    MaxStartups 丢弃（事故 1）；banner exchange invalid format 同理。
    MaxStartups 调优后复测。
```

**关键方法论：先看服务端"实际在监听什么"（`ss -tln` + `sshd -T`），再查网络层。** 事故 2 中若第一时间执行 `ss -tln`，30 秒就能定位，不用绕"公司网络干扰"的弯路。

## ✅ 变更前强制流程（远程自动化环境：TAT/MCP 执行）

AI 通过 TAT/MCP 远程执行时**没有交互会话**，"保持一个 SSH 会话不关"不现实，改为：

1. **兜底通道确认**：确认腾讯控制台 WebShell/Orcaterm 可用（该通道独立于 22 端口，是唯一兜底入口），把入口路径告诉用户；
2. **现状取证**（只读）：`ss -tln` / `sshd -T` / 双层防火墙状态，作为变更前基线展示给用户；
3. **变更命令清单**：先向用户复述完整命令清单，获明确确认后执行；
4. **备份先行**：`cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%F-%H%M)`；
5. **脚本幂等**：变更脚本必须可重复执行（重复跑不出副作用），通过 base64 分块上传时**每块必须 md5 校验**（远程传输会随机丢/翻字节）；
6. **执行后铁律 2 验证四连** + 外部 banner 实测；
7. **审计留痕**：追加到 `/var/log/ops-changelog.log`：`[时间] [操作来源] [变更内容] [回滚点:备份路径]`。

### 变更前检查清单（逐项打勾，缺一不可）

- [ ] 本次变更**不删除、不覆盖**任何已存在的 `Port` 指令
- [ ] 若涉及 `Port`：目标文件将**同时显式包含** `Port 22` 与新端口
- [ ] 控制台 WebShell 兜底入口已确认可用并告知用户
- [ ] 变更前基线（ss -tln / sshd -T / 防火墙状态）已取证
- [ ] 配置文件已备份
- [ ] 回滚命令已准备
- [ ] 用户已明确确认命令清单
- [ ] 执行后验证四连已排入计划（不是"跑完就行"）

## 🔥 应急预案（SSH 失联时）

**第一动作**：腾讯轻量控制台 → 实例卡片 → 登录/远程连接（WebShell/VNC），该通道不依赖 22 端口。

恢复步骤（含"Port 22 被顶掉"场景的修复命令）见 `references/emergency-recovery.md`。

## 📚 事故复盘归档

每次失联事故后，按 `references/emergency-recovery.md` 末尾的复盘模板填写，并将完整复盘存入 `references/` 目录——**复盘是本技能进化的唯一来源**。
