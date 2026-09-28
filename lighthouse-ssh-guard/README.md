# lighthouse-ssh-guard 安装说明

腾讯轻量应用服务器（Lighthouse）SSH 失联防护 Skill，v2.0.0。基于 **2026-09-28 两次真实 22 端口失联事故**提炼，核心防线是"显式即排他""监听验证四连""爆破压力防护"三条铁律，而非泛泛的防火墙检查。

## 它防什么（真实事故对照）

| 事故 | 症状 | 本技能的拦截点 |
|---|---|---|
| 追加 `Port 443` 备用通道，22 被顶掉 | sshd 只监听 443，所有 22 连接失败 | 铁律 1"显式即排他"：写任何 Port 前必须显式保留 `Port 22`；铁律 2：变更后 `ss -tln` 必须看到双监听 |
| 互联网爆破 5.3 万次，MaxStartups 默认值随机丢合法连接 | 22 端口时好时坏、TCP 通但收不到 banner | 铁律 3：MaxStartups 基线调优 + 症状特征识别 |
| 防火墙误操作（历史通用风险） | 22 被系统/控制台防火墙拦截 | 禁改清单 + 命令黑名单 |

## 目录结构

```
lighthouse-ssh-guard/
├── SKILL.md                          # 主文件（三条铁律 + 禁改清单 + 诊断决策树 + 流程）
└── references/
    ├── incident-2026-09-28.md        # 两次真实事故完整复盘（事实依据）
    ├── emergency-recovery.md         # 失联应急手册（含第 0 步"服务端实况三连"）
    └── command-blacklist.md          # 高危命令黑名单 + 流程级违规清单
```

## 安装方法

**WorkBuddy**：把 `lighthouse-ssh-guard/` 整个文件夹拷贝到：
- 用户级（全局生效）：`~/.workbuddy/skills/lighthouse-ssh-guard/`
- 项目级（仅当前工作空间）：`{工作空间}/.workbuddy/skills/lighthouse-ssh-guard/`
- 重启 WorkBuddy 或新建对话即可生效

**Claude Code**：拷贝到 `~/.claude/skills/lighthouse-ssh-guard/`（全局）或项目内 `.claude/skills/lighthouse-ssh-guard/`，无需重启。

## 验证是否生效

新开一个对话，对 AI 说：

> "帮我在服务器 sshd_config 里加一行 Port 2222，开放个新端口"

正确的反应是：AI 提示"显式即排他"风险，确认文件同时显式包含 `Port 22`，给出变更命令清单等待确认，执行后强制 `ss -tln` 监听验证 + 外部 banner 实测，**绝不**改完就宣布成功。

> "服务器 SSH 连不上了"

正确的反应是：先要求服务端取证（`ss -tln` / `sshd -T` / 日志），按诊断决策树三分支归因，而不是直接怀疑网络。

## 使用提醒

- 触发靠 frontmatter `description` 的关键词（sshd_config / Port / 防火墙 / 连不上 / 失联 / MaxStartups 等均命中），铁律写在正文最前且措辞强硬。
- `references/emergency-recovery.md` 建议单独存一份到本地或收藏夹——真失联时打开控制台 WebShell 照着敲即可，不依赖 AI 响应速度。
- 适用任何通过 TAT/MCP 远程执行命令的腾讯轻量云运维场景（自动化无交互会话，验证必须脚本化闭环）。
