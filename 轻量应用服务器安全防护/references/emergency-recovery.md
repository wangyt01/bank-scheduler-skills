# SSH 失联应急恢复手册

## 入口：腾讯轻量控制台 WebShell/VNC（唯一兜底）

路径：腾讯云控制台 → 轻量应用服务器 → 点击实例卡片 → 右上角"登录"/"远程连接"（WebShell / Orcaterm）。此通道独立于 22 端口，SSH 全断时依然可用。

## 恢复步骤（按顺序执行）

### 第 -1 步：先判断是不是"guest 僵死"（事故 3 场景，直接跳重启）

四条同时成立 → 别按下面 1-5 步走了，直接**控制台重启实例**：

1. TCP 到 22/443 能握手，但反复抓取**收不到 SSH banner**（非时好时坏）；
2. TAT 执行报 `DELIVER_FAILED` 或 **"当前未安装 TAT"**；
3. 云监控（CPU/内存/带宽）全指标无数据；
4. 80/3001 等 Web 服务仍正常（常驻进程幸存）。

WebShell 若能登录，重启前可快速取证：`uptime; free -m; ps aux | wc -l; dmesg | tail -20`。

**⚠️ 反复僵死（重启后数小时内再次僵死）**：说明 guest 内有进程在持续触发（fork 炸弹、失控 agent、插件泄漏）。此时单纯重启是跑步机——重启后**立即**通过 WebShell 执行 `references/wedge-triage.sh`（只读取证，11 个维度：内存/进程数/D-Z 状态/OOM 日志/云 agent 存活/openclaw 与 docker 嫌疑/cron/登录/磁盘 inode），输出自动存盘 `/root/wedge-triage-*.log`。拿到报告锁定元凶后再修复，否则僵死会无限循环。

### 第 0 步：服务端实况三连（30 秒定位 90% 的失联）

```bash
ss -tln | grep sshd                       # sshd 实际在监听哪些端口？
sshd -T 2>/dev/null | grep -E '^port '    # 生效配置声明的端口
systemctl is-active sshd                  # 服务是否活着
```

先看结果再动手——三种典型结论：

| 观察结果 | 结论 | 跳到 |
|---|---|---|
| 监听列表里**没有 :22** | 八成是 `Port` 指令顶掉了隐式默认 22（事故 2 场景） | 第 1 步 |
| sshd 未运行 | 服务挂了或配置语法错误 | 第 1 步 |
| :22 在监听但外部连不上 | 防火墙层（第 2-4 步）或 MaxStartups 丢连接（见文末） | 第 2 步 |

### 1. 修复 sshd 监听/配置（事故 2 主场景）

```bash
# 备份现场（若尚无备份）
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%F-%H%M)

# 查看当前 Port 指令
grep -n '^Port' /etc/ssh/sshd_config

# 场景 A：只有 Port 443（或其它端口）而没有 Port 22 → 补上并存
#   用编辑器把 Port 区块改成：
#     Port 22
#     Port 443

# 场景 B：配置完全坏掉 → 直接回滚备份
cp /etc/ssh/sshd_config.bak_20260928 /etc/ssh/sshd_config

sshd -t && systemctl reload sshd
ss -tln | grep -E ':(22|443)\b'    # 必须双端口都在
```

### 2. 检查系统防火墙

```bash
# Ubuntu
sudo ufw status verbose
sudo ufw allow 22/tcp

# CentOS/OpenCloudOS
sudo firewall-cmd --list-all
sudo firewall-cmd --permanent --add-port=22/tcp
sudo firewall-cmd --reload
```

### 3. 检查 iptables 残留

```bash
sudo iptables -L -n --line-numbers | grep -E "22|DROP|REJECT"
# 若有针对 22 的 DROP/REJECT，按行号删除：
sudo iptables -D INPUT <行号>
```

### 4. 检查腾讯控制台防火墙

控制台 → 实例详情 → 防火墙标签页 → 确认存在规则：
`协议 TCP，端口 22，来源 0.0.0.0/0（或你的管理 IP），策略 允许`
缺失则点"添加规则"补回。

### 5. 外部验证后才关闭 WebShell

在**本地终端**（不是服务器上）验证：

```bash
# banner 测试（服务端本地测试不算数）
python3 -c "
import socket
s = socket.create_connection(('服务器IP', 22), timeout=8); s.settimeout(8)
print(s.recv(64))
s.close()"
# 真实登录测试
ssh user@服务器IP
```

全部通过后才允许关闭控制台会话。

### 附：症状三（TCP 通但无 banner / 时好时坏）的处理

```bash
# 量化爆破压力
grep -c 'Failed password' /var/log/secure*
# 查看当前 MaxStartups
sshd -T | grep -i maxstartups
# 若为默认 10:30:100 且爆破量上万 → 按下述调优
```

sshd_config 追加：
```
MaxStartups 100:30:300
LoginGraceTime 30
LogLevel VERBOSE
```
`sshd -t` → `systemctl reload sshd` → 外部复测。爆破持续恶化则部署 fail2ban，不要关 22。

## 事故复盘模板（每次失联后填写，存入 references/）

```
日期：
失联时长：
症状分类（refused / timeout / TCP通无banner）：
根因（Port隐式排他 / sshd未运行 / 防火墙 / MaxStartups / 其他）：
触发操作来源（AI/人工/脚本）：
服务端取证命令与关键输出（ss -tln / sshd -T / 日志摘要）：
恢复耗时：
预防措施（更新 SKILL.md 哪条铁律/清单）：
```
