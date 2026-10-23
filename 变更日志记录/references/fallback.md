# 兜底手册（脚本不可用时 + 时间校验 + 文件删除规范）

SKILL.md 的主路径是脚本 `changelog_recorder.py`。本文件只在脚本不可用、或需要手动删除文件时按需加载。

## 1. 手动生成（脚本不可用时）

### 1.1 取时间（强制，禁止手输）

```python
from datetime import datetime

timestamp = datetime.now().strftime("%Y%m%d%H%M%S")       # 文件名时间戳
formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 正文生成时间
```

必须用 `datetime.now()`（本地时间），禁止 `datetime.utcnow()`；文件名时间戳与正文"生成时间"必须一致。

### 1.2 保存前校验

```python
from datetime import datetime
import os

now = datetime.now()
cur_ts = now.strftime("%Y%m%d%H%M%S")
if abs(int(timestamp) - int(cur_ts)) > 200:   # 允许 ±2 分钟
    timestamp = cur_ts
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")

if os.path.basename(file_path).split("-")[0] != timestamp:
    raise ValueError("文件名时间戳与内容时间戳不一致，请修正")
```

### 1.3 标准格式（唯一模板，勿再复制多份）

```markdown
# 变更记录 · {timestamp}

> 生成时间：{formatted_time}（GMT+8）｜ 本次类型：{变更类型} ｜ 变更人：{变更人} ｜ 状态：已完成
> 触发：{变更触发原因} ｜ 影响范围：{影响范围}

## 1. 问题描述
{现象、复现步骤}

## 2. 根因分析
{根因，必要时贴关键代码片段}

## 3. 解决方案
{方案，含关键改动}

## 4. 修改的文件

| 文件 | 修改类型 | 修改内容 |
| --- | --- | --- |
| {路径} | {新增/修改/删除} | {说明} |

## 5. 测试验证
{实际测试结果}

## 6. 变更效果
- ✅ {效果1}

## 7. 关联变更
{关联需求/工单/文档}
```

保存路径：`需求设计/changelogs/{YYYYMM}/{timestamp}-{工单号}-{中文摘要}.md`。

## 2. 文件删除规范（强制）

1. **禁止直接删除**：不用 `os.remove()` / `rm` / DeleteFile。
2. **走回收站**：用 `send2trash` 移至系统回收站，保证可恢复。
3. **删除前确认已备份**：已提交 Git 或已另存备份。
4. **在变更日志中记录**被删除文件的路径与原因。

```python
import os
import subprocess
from send2trash import send2trash

if os.path.exists(file_path):
    r = subprocess.run(["git", "log", "--oneline", "-1", "--", file_path],
                       capture_output=True, text=True)
    if not (r.returncode == 0 and r.stdout.strip()):
        print("⚠️ 该文件未提交 Git，请先确认已另存备份再移入回收站")
    send2trash(file_path)
    print("✅ 已移至回收站: {}".format(file_path))
```

恢复：系统回收站还原，或 `git checkout HEAD -- {文件路径}`。

## 3. 月度合并的等价手动做法

脚本 `--merge YYYYMM` 不可用时：按 `Added / Fixed / Changed / Removed`（Keep a Changelog）分组当月目录下各文档的「中文摘要」，末尾附「明细表（文件 | 类型 | 生成时间）」，输出到 `{YYYYMM}_changelog.md`。
