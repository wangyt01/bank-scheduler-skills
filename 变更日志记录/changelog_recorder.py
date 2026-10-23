# -*- coding: utf-8 -*-
"""
changelog_recorder.py — changelog-record skill 自带脚本（与 SKILL.md 同级，随 skill 分发）

调用方式（用 skill 目录绝对路径拼接，与 SKILL.md 保持一致）:
    python "<skill目录>/changelog_recorder.py" \
        --type "优化改进" --summary "中文摘要" [--ticket "795803"] \
        --out-dir "<当前工程根目录>/需求设计/changelogs" [其他参数...]

追加模式（同一需求/同一 bug 的后续修改，不新建文档）:
    python "<skill目录>/changelog_recorder.py" --append "<既有文档绝对路径>" \
        --type "Bug修复" --summary "中文摘要" [其他参数...]

说明:
- 时间戳自动用 datetime.now() 生成，文件名与内容保证一致
- --out-dir 指向工程的「需求设计/changelogs」目录，脚本内自动按 YYYYMM 建月份子目录
- 文件名: {时间戳}-{工单号}-{中文摘要}.md，工单号缺省时为 {时间戳}-{中文摘要}.md；摘要必须中文
- --ticket 问题号/需求工单号（可选），紧跟在文件名日期时间戳后面
- --append 向既有文档追加「变更轮次」小节，不新建文件
- --files 格式: "路径|修改类型|修改内容" 多文件用分号分隔
- --effects 多个效果用逗号分隔
"""
import argparse
import os
import sys
from datetime import datetime

try:  # Windows GBK 控制台兜底，避免中文输出报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def now_formatted() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_summary(summary: str) -> str:
    """文件名摘要清洗：保留中文，空白转连字符，清除文件名非法字符。"""
    safe = (summary or "").strip()
    for ch in '\\/:*?"<>|':
        safe = safe.replace(ch, "-")
    safe = "-".join(safe.split())
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-")[:50]
    return safe or "未命名"


def clean_ticket(ticket: str) -> str:
    """工单号清洗：只保留字母数字、下划线与连字符。"""
    safe = "".join(c for c in (ticket or "").strip() if c.isalnum() or c in "-_")
    return safe[:30]


def files_to_table(files_str: str) -> str:
    """'路径|类型|内容;路径|类型|内容' -> markdown 表格行。"""
    rows = []
    for item in (files_str or "").split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        while len(parts) < 3:
            parts.append("—")
        rows.append("| {} | {} | {} |".format(*parts[:3]))
    return "\n".join(rows) if rows else "| 待补充 | 待补充 | 待补充 |"


def effects_to_list(effects_str: str) -> str:
    """'效果1,效果2' -> '- ✅ 效果1' 列表。"""
    items = [e.strip() for e in (effects_str or "").split(",") if e.strip()]
    return "\n".join(f"- ✅ {e}" for e in items) if items else "- ✅ 待补充"


def build_content(changelog_type, trigger, description, root_cause, solution,
                  files_table, verification, effects_list, related) -> str:
    ts, ft = now_timestamp(), now_formatted()
    return f"""# 变更记录 · {ts}

> 生成时间：{ft}（GMT+8）｜ 本次类型：{changelog_type} ｜ 状态：已完成
> 触发：{trigger}

## 1. 问题描述

{description}

## 2. 根因分析

{root_cause}

## 3. 解决方案

{solution}

## 4. 修改的文件

| 文件 | 修改类型 | 修改内容 |
| --- | --- | --- |
{files_table}

## 5. 测试验证

{verification}

## 6. 变更效果

{effects_list}

## 7. 关联变更

{related}
"""


def build_round_section(changelog_type, trigger, description, root_cause, solution,
                        files_table, verification, effects_list) -> str:
    """追加模式：同一需求/同一 bug 的后续修改，在既有文档末尾追加的变更轮次小节。"""
    ts, ft = now_timestamp(), now_formatted()
    return f"""

---

## 变更轮次 · {ts}（{changelog_type}）

> 时间：{ft}（GMT+8）｜ 触发：{trigger}

### 问题描述

{description}

### 根因分析

{root_cause}

### 解决方案

{solution}

### 修改的文件

| 文件 | 修改类型 | 修改内容 |
| --- | --- | --- |
{files_table}

### 测试验证

{verification}

### 变更效果

{effects_list}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="changelog-record skill 变更日志生成器（脚本随 skill 自带，勿在工作区另找脚本）")
    parser.add_argument("--type", required=True, help="变更类型：Bug修复/功能优化/新增功能/重构代码/配置变更")
    parser.add_argument("--summary", required=True, help="变更摘要（必须中文，用于生成文件名，如：修复登录超时）")
    parser.add_argument("--ticket", default="", help="问题号/需求工单号（可选，添加在文件名日期后面，如 795803）")
    parser.add_argument("--append", default="", metavar="DOC_PATH",
                        help="追加模式：向指定既有变更日志文档追加「变更轮次」小节，不新建文件（同一需求/同一bug的后续修改必须用此模式）")
    parser.add_argument("--out-dir", default=None,
                        help="输出根目录，指向工程的 需求设计/changelogs（默认: 当前工作目录/需求设计/changelogs）")
    parser.add_argument("--trigger", default="用户执行代码修改", help="变更触发原因")
    parser.add_argument("--description", default="待补充", help="问题描述")
    parser.add_argument("--root_cause", default="待补充", help="根因分析")
    parser.add_argument("--solution", default="待补充", help="解决方案")
    parser.add_argument("--files", default="", help='修改文件: "路径|修改类型|修改内容"，多文件用分号分隔')
    parser.add_argument("--verification", default="待补充", help="测试验证内容")
    parser.add_argument("--effects", default="", help="变更效果，逗号分隔")
    parser.add_argument("--related", default="无", help="关联变更")
    args = parser.parse_args()

    out_root = args.out_dir or os.path.join(os.getcwd(), "需求设计", "changelogs")

    round_section = build_round_section(
        args.type, args.trigger, args.description, args.root_cause, args.solution,
        files_to_table(args.files), args.verification, effects_to_list(args.effects))

    # 追加模式：同一需求/同一 bug 的后续修改，写入既有文档，不新建
    if args.append:
        doc_path = os.path.abspath(args.append)
        if not os.path.isfile(doc_path):
            print("追加失败：文档不存在: {}".format(doc_path))
            return 1
        with open(doc_path, "a", encoding="utf-8") as f:
            f.write(round_section)
        print("变更轮次已追加: {}".format(doc_path))
        return 0

    month = now_timestamp()[:6]  # YYYYMM
    target_dir = os.path.join(out_root, month)
    os.makedirs(target_dir, exist_ok=True)

    parts = [now_timestamp()]
    ticket = clean_ticket(args.ticket)
    if ticket:
        parts.append(ticket)
    parts.append(clean_summary(args.summary))
    filename = "-".join(parts) + ".md"
    filepath = os.path.join(target_dir, filename)

    content = build_content(
        args.type, args.trigger, args.description, args.root_cause, args.solution,
        files_to_table(args.files), args.verification, effects_to_list(args.effects), args.related)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print("变更日志已创建: {}".format(filepath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
