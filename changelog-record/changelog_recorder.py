# -*- coding: utf-8 -*-
"""
changelog_recorder.py — changelog-record skill 自带脚本（与 SKILL.md 同级，随 skill 分发）

调用方式（用 skill 目录绝对路径拼接，与 SKILL.md 保持一致）:
    python "<skill目录>/changelog_recorder.py" \
        --type "优化改进" --summary "short-summary-in-english" \
        --out-dir "<当前工程根目录>/需求设计/changelogs" [其他参数...]

说明:
- 时间戳自动用 datetime.now() 生成，文件名与内容保证一致
- --out-dir 指向工程的「需求设计/changelogs」目录，脚本内自动按 YYYYMM 建月份子目录
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
    """文件名摘要清洗：小写、空格/下划线转连字符、去非法字符、压缩连字符。"""
    safe = summary.lower().replace(" ", "-").replace("_", "-")
    safe = "".join(c for c in safe if c.isalnum() or c == "-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-")[:50]
    return safe or "untitled"


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="changelog-record skill 变更日志生成器（脚本随 skill 自带，勿在工作区另找脚本）")
    parser.add_argument("--type", required=True, help="变更类型：Bug修复/功能优化/新增功能/重构代码/配置变更")
    parser.add_argument("--summary", required=True, help="变更摘要（英文小写+连字符，用于生成文件名，如 fix-login-bug）")
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
    month = now_timestamp()[:6]  # YYYYMM
    target_dir = os.path.join(out_root, month)
    os.makedirs(target_dir, exist_ok=True)

    filename = "{}-{}.md".format(now_timestamp(), clean_summary(args.summary))
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
