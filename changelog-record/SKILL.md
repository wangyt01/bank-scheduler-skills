---
name: changelog-record
description: 当用户进行项目代码修改、修复bug、新增需求、优化功能或任何项目变更时自动触发，负责识别变更点并生成标准化变更日志，保存至需求设计/changelogs目录。
version: 1.2.0
agent_created: true
slug: changelog-record
displayName: 变更日志记录
summary: 项目代码变更时自动生成标准化变更日志（问题描述/根因/方案/文件清单/验证/效果），按月份目录归档。
license: MIT
category: devtools
---

# 变更日志记录技能

## 触发条件

满足以下任意场景时自动启用本技能：
1. 用户修改代码文件（新增、修改、删除）
2. 用户修复bug或解决问题
3. 用户新增功能需求
4. 用户优化或重构代码
5. 用户进行配置变更
6. 用户提交代码更改请求

## 执行步骤


### 第一步：识别变更点

在开始编写变更日志前，必须明确以下信息：
- 变更时间：精确到分钟的时间戳（格式：YYYY-MM-DD HH:MM:SS）
- 变更类型：新增功能/修复缺陷/优化改进/重构代码/配置变更
- 变更内容：详细描述变更点，包含问题描述、解决方案、修改的文件和代码
- 变更人：记录执行变更的人员
- 影响范围：受影响的模块、功能、接口或数据库表

### 第二步：使用脚本生成变更日志

> ⚠️ **脚本位置（重要，务必先读）**：脚本**随本 skill 自带**，与 SKILL.md 同级，即
> `<skill目录>/changelog_recorder.py`。
> - WorkBuddy 已安装用户：用户主目录下 `.workbuddy\skills\changelog-record\changelog_recorder.py`
> - 其他环境：将 `<skill目录>` 替换为 skill 实际存放路径。
> **禁止**在当前工作区搜索 `scripts/generate_changelog.py` 或任何同名脚本——那是旧文档笔误，
> 工程里从来没有这个文件。直接按下方方式调用，任何工作区均可用。

```bash
python "<skill目录>/changelog_recorder.py" \
    --type "Bug修复" \
    --summary "short-summary-in-english" \
    --out-dir "<当前工程根目录>/需求设计/changelogs" \
    --trigger "变更触发原因" \
    --description "问题详细描述..." \
    --root_cause "根因分析..." \
    --solution "解决方案..." \
    --files "文件路径|修改类型|修改内容;文件路径|修改类型|修改内容" \
    --verification "测试验证内容..." \
    --effects "效果1,效果2,效果3" \
    --related "关联变更..."
```

**参数说明**：
- `--type`：变更类型（Bug修复、功能优化、新增功能、重构代码、配置变更等）
- `--summary`：变更摘要（英文小写+连字符，用于生成文件名，如 `fix-login-bug`，必填）
- `--out-dir`：输出根目录，指向**当前工程**的 `需求设计/changelogs` 目录（脚本自动在其下建 `YYYYMM` 月份子目录；省略时默认 `<当前工作目录>/需求设计/changelogs`）
- `--trigger`：变更触发原因
- `--description`：问题描述
- `--root_cause`：根因分析
- `--solution`：解决方案
- `--files`：修改的文件，格式为 `文件路径|修改类型|修改内容`，多个文件用分号分隔
- `--verification`：测试验证内容
- `--effects`：变更效果，多个效果用逗号分隔
- `--related`：关联变更

**脚本功能**：
- ✅ 自动使用 `datetime.now()` 获取当前时间（文件名时间戳与内容生成时间保证一致）
- ✅ 自动在 `--out-dir` 下按 `YYYYMM` 创建月份目录
- ✅ `--files` 自动转换为标准 Markdown 表格行，`--effects` 自动转换为 `✅` 列表
- ✅ 生成符合本 skill 标准格式的变更日志文件
- ✅ 自动生成文件名（格式：`YYYYMMDDHHMMSS-变更摘要.md`）
- ✅ 确保文件名时间戳与内容时间戳一致
- ✅ 生成标准化格式的变更日志

### 第三步：手动编写（仅当脚本不可用时）

如果脚本不可用，必须严格按照以下步骤手动编写：

#### 3.1 获取当前时间（强制）

**必须使用以下Python代码获取当前时间，禁止手动输入时间**：

```python
from datetime import datetime

# 获取当前时间戳（格式：YYYYMMDDHHMMSS）
timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

# 获取格式化的当前时间（格式：YYYY-MM-DD HH:MM:SS）
formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

**关键注意事项**：
- 必须使用 `datetime.now()` 获取本地时间，禁止使用 `datetime.utcnow()`
- **禁止手动输入时间戳**，必须通过代码自动生成
- 文件名中的时间戳和文件内容中的"生成时间"必须一致

#### 3.2 时间验证（强制）

在保存变更日志之前，必须执行以下验证步骤：

```python
from datetime import datetime

# 获取当前系统时间
current_time = datetime.now()
current_timestamp = current_time.strftime("%Y%m%d%H%M%S")
current_formatted = current_time.strftime("%Y-%m-%d %H:%M:%S")

# 验证时间戳与系统时间是否一致（允许±2分钟误差）
timestamp_diff = abs(int(timestamp) - int(current_timestamp))
if timestamp_diff > 200:  # 超过2分钟差异
    print(f"警告：时间戳 {timestamp} 与当前时间 {current_timestamp} 不一致！")
    print(f"请使用 datetime.now() 重新生成时间戳")
    # 强制使用当前时间
    timestamp = current_timestamp
    formatted_time = current_formatted

# 验证文件名时间戳与内容时间戳一致
file_timestamp = os.path.basename(file_path).split('-')[0]
if file_timestamp != timestamp:
    print(f"错误：文件名时间戳 {file_timestamp} 与内容时间戳 {timestamp} 不一致！")
    raise ValueError("时间戳不一致，请修正")
```

**验证规则**：
1. 时间戳必须与系统当前时间一致（允许±2分钟误差）
2. 文件名中的时间戳必须与文件内容中的"生成时间"完全一致
3. 如果时间不一致，必须重新获取当前时间并修正

#### 3.3 标准格式

按照以下标准格式编写变更日志：

```markdown
# 变更记录 · {timestamp}

> 生成时间：{formatted_time}（GMT+8）｜ 本次类型：{变更类型} ｜ 状态：已完成
> 触发：{变更触发原因}

## 1. 问题描述

{详细描述问题现象、复现步骤}

## 2. 根因分析

{分析问题根本原因，包含代码片段和问题说明}

## 3. 解决方案

{描述解决方案，包含修改的文件和关键代码}

## 4. 修改的文件

| 文件 | 修改类型 | 修改内容 |
| --- | --- | --- |
| {文件路径} | {新增/修改/删除} | {简要说明} |

## 5. 测试验证

{测试结果和验证说明}

## 6. 变更效果

- ✅ {效果1}
- ✅ {效果2}

## 7. 关联变更

{关联的需求、修复记录等}
```

### 第四步：保存变更日志

#### 4.1 文件命名规则（独立文件）

**规则**：每个需求变更单独记录为一个文件，月底由用户自行合并。

- **文件命名**：`{timestamp}-{变更摘要}.md`
  - 时间戳格式：YYYYMMDDHHMMSS（通过 `datetime.now().strftime("%Y%m%d%H%M%S")` 生成）
  - 变更摘要：简短描述，使用英文小写字母和连字符

- **保存目录（按月份分组）**：`\需求设计\changelogs\{YYYYMM}/`
  - 例如：2026年7月的变更日志保存到 `changelogs/202607/` 目录
  - 月份文件夹从时间戳中提取前6位（YYYYMM）自动生成
  - 模板文件 `changelog_template.md` 保留在 `changelogs/` 根目录，不放入月份子目录

#### 4.2 合并策略

1. **日常记录**：每次变更创建独立文件，保存在对应月份目录下
2. **月底合并**：由用户在月底手动进行月度合并，生成 `{YYYYMM}_changelog.md`
3. **归档保留**：合并后可选择保留或删除原始独立文件

#### 4.3 变更文件格式

```markdown
# 变更记录 · {变更编号}

> 生成时间：{YYYY-MM-DD HH:MM:SS}（GMT+8）｜ 本次类型：{变更类型} ｜ 状态：{状态}
> 触发：{变更触发原因}

## 1. 问题描述

{详细描述问题现象、复现步骤}

## 2. 根因分析

{分析问题根本原因，包含代码片段和问题说明}

## 3. 解决方案

{描述解决方案，包含修改的文件和关键代码}

## 4. 修改的文件

| 文件 | 修改类型 | 修改内容 |
| --- | --- | --- |
| {文件路径} | {新增/修改/删除} | {简要说明} |

## 5. 测试验证

{测试结果和验证说明}

## 6. 变更效果

- ✅ {效果1}
- ✅ {效果2}

## 7. 关联变更

{关联的需求、修复记录等}
```

### 第五步：文件删除规范（强制）

**删除文件时必须遵循以下规则**：

1. **禁止直接删除**：禁止使用 DeleteFile 工具、`os.remove()`、`rm` 命令等直接删除文件
2. **使用回收站**：必须将文件移动到系统回收站，确保后续可恢复
3. **备份确认**：删除前必须确认文件内容已备份或已推送到 Git 仓库
4. **记录删除**：在变更日志中详细记录被删除的文件信息

**实现方式**：
- 使用 Python 的 `send2trash` 库将文件移动到回收站
- 代码示例：
  ```python
  from send2trash import send2trash
  
  # 删除前确认文件存在
  if os.path.exists(file_path):
      # 确认已备份到Git
      import subprocess
      result = subprocess.run(['git', 'log', '--oneline', '-1', '--', file_path], 
                              capture_output=True, text=True)
      if result.returncode == 0 and result.stdout.strip():
          print(f"✅ 文件 {file_path} 已备份到Git，继续删除")
      else:
          print(f"⚠️  文件 {file_path} 未备份到Git，请先确认已另存备份，再移至回收站（可恢复）")
      
      # 移动到回收站
      send2trash(file_path)
      print(f"✅ 文件 {file_path} 已移动到回收站")
  else:
      print(f"❌ 文件 {file_path} 不存在")
  ```

**恢复操作**：
如果需要恢复已删除的文件：
1. 从系统回收站恢复（如果文件被移动到回收站）
2. 从 Git 历史恢复（如果文件已提交到仓库）：
   ```bash
   git checkout HEAD -- {文件路径}
   ```

## 注意事项

1. 每次项目修改完成后，必须立即编写并保存变更日志
2. 每个变更创建独立文件，保存在对应月份目录下
3. 修改的文件列表必须包含所有受影响的文件
4. 测试验证部分必须包含实际测试结果
5. 关联变更部分需引用相关的需求文档或修复记录
6. 禁止手动输入时间，必须使用 `datetime.now()` 获取当前时间
7. 月底由用户自行进行月度合并

## 优先级规则

本技能优先级高于其他技能，确保变更日志记录不会被遗漏或跳过。无论其他任务执行结果如何，变更日志必须在任务完成后立即生成。

**开发约束优先级**：
1. 文件操作（必须遵守删除规范）
2. 变更日志记录（任务完成后必须执行）
