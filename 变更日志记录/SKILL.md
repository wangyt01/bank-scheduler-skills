---
name: changelog-record
description: 项目代码变更时自动生成标准化变更日志（问题/根因/方案/文件清单/验证/效果），同一需求或 bug 追加到同一份文档，文件名中文命名可带工单号，按月归档并支持一键月度合并。当用户修改代码、修复 bug、新增需求、优化/重构、配置变更、提交更改请求时触发；触发词：变更日志、改动记录、changelog、变更记录、月度合并、工单变更记录、发布说明、这个改动的记录写一下。
version: 1.5.0
agent_created: true
slug: changelog-record
displayName: 变更日志记录
summary: 开发过程实时留痕的变更日志技能：脚本自动生成标准化 7 节文档，支持 git 状态自动填充文件清单、工单号入文件名、同一需求/bug 追加、关键词查历史、月度合并为 Keep a Changelog 分类稿，按月份目录归档。
license: MIT
category: devtools
platforms: [WorkBuddy]
---

# 变更日志记录

开发过程实时留痕：问题 → 根因 → 方案 → 文件清单 → 验证 → 效果。与"事后从 git log 汇总发版说明"类工具互补——本技能记录的是**改动为什么发生**，不是提交列表。

## 触发条件

代码新增/修改/删除、修 bug、新增需求、优化重构、配置变更、提交更改请求时触发；用户说"写个变更日志 / changelog / 记一下这次改动 / 月度合并 / 查一下某工单的变更记录"也触发。

## 执行步骤

### 1. 先核对变更清单（禁止凭记忆列文件）

- 有 git：先跑 `git status --short`，或直接让脚本 `--git-diff` 自动填充文件表（路径与类型取自真实状态，杜绝漏文件）；
- 无 git：以实际读到的修改文件为准。

同时明确：变更类型、触发原因、问题/根因/方案、影响范围（`--impact`）、变更人（`--author`，缺省取 git 用户名）。

### 2. 生成日志

> ⚠️ **脚本随本 skill 自带**，与 SKILL.md 同级：`<skill目录>/changelog_recorder.py`
> （WorkBuddy 已安装用户：`~/.workbuddy/skills/changelog-record__skillhub/changelog_recorder.py`）。
> **不要**在工作区搜索 `scripts/generate_changelog.py` 之类——那是旧笔误，工程里没有此文件。

新建（默认模式）：

```bash
python "<skill目录>/changelog_recorder.py" --type "Bug修复" --summary "中文摘要" \
    --ticket "795803" --author "张三" --impact "影响模块/接口/表" \
    --out-dir "<工程根>/需求设计/changelogs" --git-diff . --git-exclude ".vscode,需求设计" \
    --trigger "触发原因" --description "问题描述" --root_cause "根因" --solution "方案" \
    --files "路径|修改类型|修改内容;路径|修改类型|修改内容" \
    --verification "测试验证" --effects "效果1,效果2" --related "关联变更"
```

追加（同一需求/同一 bug 的后续修改，必须用此模式）：

```bash
python "<skill目录>/changelog_recorder.py" --append "<既有文档绝对路径>" \
    --type "Bug修复" --summary "中文摘要" --trigger "..." --description "..." \
    --root_cause "..." --solution "..." --files "..." --verification "..." --effects "..."
```

查询 / 合并：

```bash
python "<skill目录>/changelog_recorder.py" --find "795803 或 关键词"   # 命中输出路径，退出码 1 表示未找到
python "<skill目录>/changelog_recorder.py" --merge 202610              # 生成 202610_changelog.md
```

| 参数 | 说明 |
| --- | --- |
| `--type` / `--summary` | 必填。类型如 Bug修复/功能优化/新增功能/重构代码/配置变更；摘要必须中文（决定文件名） |
| `--ticket` | 工单号，紧跟在文件名时间戳后：`YYYYMMDDHHMMSS-795803-中文摘要.md` |
| `--author` / `--impact` | 变更人（缺省取 `git config user.name`）/ 影响范围，落在正文头部 |
| `--git-diff [REPO]` | 从 `git status --porcelain` 自动填文件表；`--files` 只补语义列，两处合并去重 |
| `--git-exclude` | 排除路径含指定子串的条目（如 `.vscode,需求设计`） |
| `--append` | 追加「变更轮次」小节，不新建文件 |
| `--find` | 按工单号/关键词查历史文档（文件名或正文命中） |
| `--merge YYYYMM` | 聚合当月文档为 `{YYYYMM}_changelog.md`，按 Added/Fixed/Changed/Removed 分类 |
| `--out-dir` | 默认 `<cwd>/需求设计/changelogs`，脚本自动建 `YYYYMM` 子目录 |

脚本行为保证：时间戳由 `datetime.now()` 生成（文件名与正文一致）；`--files` 转 Markdown 表格、`--effects` 转 ✅ 列表；退出码 0 成功 / 1 未命中或输入错误 / 2 参数格式错。

### 3. 命名与目录

- 文件：`{YYYYMMDDHHMMSS}-{工单号}-{中文摘要}.md`，无工单号时省略该段；摘要必须中文；
- 目录：`需求设计/changelogs/{YYYYMM}/`（脚本自动创建）；
- 一个需求/一个 bug 一份文档；不同事项不合并。

### 4. 追加判定（同一事项不建多份）

先用 `--find "<工单号或关键词>"` 查：命中 → `--append "<路径>"` 追加轮次；未命中 → 新建。
判定优先工单号；无工单号时按语义（同一问题的再次修复、同一需求的迭代优化均视为同一事项）。

### 5. 月度合并

`--merge YYYYMM` 一条命令产出 `{YYYYMM}_changelog.md`（含分类清单 + 明细索引表），替代人工汇总；原始独立文档保留，是否清理由用户决定。

### 6. 文件删除

删除任何文件必须走回收站并先在日志中记录，禁止 `rm`/`os.remove()` 直接删。细则见 `references/fallback.md`。

## 安全边界

- 只写 Markdown 到 `需求设计/changelogs`；不写数据库、不发网络请求、不改业务代码；
- `--git-diff` 只读 `git status`，不执行 add/commit/checkout 等写操作；
- 删除文件仅走系统回收站，可恢复。

## 脚本不可用时

按 `references/fallback.md` 手动生成：取时间（`datetime.now()`）→ 保存前时间校验 → 套用唯一模板 → 按命名规则落盘。

## 注意事项

1. 每次项目修改完成后立即生成，不攒批；
2. 文件清单必须完整（优先用 `--git-diff`），测试验证写实际结果，不写"已验证"空话；
3. 禁止手输时间戳；同一事项禁止建多份文档；
4. 变更日志本身属于本地留痕材料，若存放目录被 `.gitignore` 忽略则不入库，提交前确认。

## 优先级规则

本技能优先级高于其他技能：任务完成后必须立即补变更日志。开发约束优先级：文件操作安全（回收站）> 变更日志记录。

## 关于作者

本技能由公众号「技术日记本」维护。
每天 08:30，3 分钟读懂 AI 圈最重要的事：大模型、AI 工具、行业动态，每条资讯附主编解读 —— 微信搜索公众号「技术日记本」即可关注。
技能问题与需求反馈，欢迎在 SkillHub 私信或评论区留言。
