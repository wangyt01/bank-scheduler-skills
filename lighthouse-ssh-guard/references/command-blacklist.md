# 高危命令黑名单速查

## A 级（绝对禁止，见即拦截）

| 命令 | 后果 |
|---|---|
| `iptables -F` / `iptables -X` | 清空全部规则，默认策略可能回退为 DROP，立即失联 |
| `iptables -P INPUT DROP` | 默认策略丢包，SSH 立即断开 |
| `ufw reset` | 恢复防火墙出厂状态，所有放行规则丢失 |
| `ufw enable`（未先放行 22 时） | 启用后系统层静默拦截 SSH |
| `ufw deny 22` / `ufw delete allow 22` | 直接关闭 22 系统层通道 |
| `firewall-cmd --permanent --remove-port=22/tcp && firewall-cmd --reload` | 关闭 22 |
| `systemctl stop sshd` / `systemctl disable sshd` | SSH 服务停止/禁用 |
| 删除/注释配置中已存在的显式 `Port 22` | 22 失去显式声明（事故 2 反向版） |
| `rm /etc/ssh/sshd_config` | 配置丢失，sshd 无法启动 |
| `visudo` 之外的任何 sudoers 编辑 | 语法错误 → 全员失去 sudo |

## B 级（需用户确认 + 完整流程才可执行）

| 命令 | 风险 | 必须搭配的防护动作 |
|---|---|---|
| 追加 `Port <新端口>` 到 sshd_config | **显式即排他**：若原文件 22 靠默认值隐式生效，追加后 22 被顶掉（事故 2） | 确保文件同时显式含 `Port 22`；改后 `ss -tln` 验证双监听 |
| `systemctl restart sshd` | restart 失败则 SSH 断 | 优先 reload；确需 restart 前先 `sshd -t`，并备好 WebShell 兜底 |
| `sed -i` / 正则批量改 sshd_config | 极易误伤其它指令行；且 sed 成功 ≠ 语义正确 | 改后 `sshd -t` + `sshd -T` 核对 port 列表 + `ss -tln` 验证 |
| 修改 sshd_config 其他项（MaxStartups/LoginGraceTime 等） | 语法错误可导致 sshd 起不来 | 备份 + `sshd -t` + reload + 监听验证四连 |
| `ufw allow <新端口>/tcp` / `firewall-cmd --add-port=<新端口>/tcp` | 本身安全 | 只加不删；删除规则仅限人工 |
| 腾讯控制台防火墙规则变更 | 误删 22 规则即失联 | 建议只加不删 |

## 流程级违规（命令本身无害，但缺验证 = 事故）

| 违规 | 事故案例 |
|---|---|
| sshd 配置变更后**只跑 `sshd -t` 不验证监听** | 事故 2：`sshd -t` 通过、reload 成功，22 却消失了 |
| 变更与验证**不在同一会话闭环** | 事故 2：上一会话改配置，下一会话才发现断连 |
| 文件上传不做 md5 校验 | 远程传输（TAT 分块）随机丢/翻字节，坏脚本上服务器 |
| 用客户端网络现象代替服务端取证 | 事故 1/2 均因此绕弯路；应先 `ss -tln` + 日志 |

## 判断口诀

> **写 Port 必写全，22 显式在文件；reload 之后必 ss，双端监听才算成；
> 服务用 reload 不用 restart，时好时坏查 MaxStartups，先取证后归因。**
