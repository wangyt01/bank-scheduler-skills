# -*- coding: utf-8 -*-
"""
changelog_recorder.py — changelog-record skill 自带脚本（与 SKILL.md 同级，随 skill 分发）

新建（默认模式）:
    python "<skill目录>/changelog_recorder.py" --type "Bug修复" --summary "中文摘要" \
        [--ticket "795803"] [--author "张三"] [--impact "影响范围"] \
        --out-dir "<工程根目录>/需求设计/changelogs" --trigger "..." --description "..." \
        --root_cause "..." --solution "..." --files "路径|类型|内容;..." \
        --verification "..." --effects "效果1,效果2" [--related "..."] \
        [--git-diff [仓库路径]] [--git-exclude ".vscode,需求设计"]

追加（同一需求/同一 bug 的后续修改，不新建文档）:
    python "<skill目录>/changelog_recorder.py" --append "<既有文档绝对路径>" \
        --type "Bug修复" --summary "中文摘要" [其他参数...]

查询历史文档（先查再建）:
    python "<skill目录>/changelog_recorder.py" --find "795803 或 登录超时" [--out-dir ...]

月度合并（生成 {YYYYMM}_changelog.md，按 Keep a Changelog 分类）:
    python "<skill目录>/changelog_recorder.py" --merge 202610 [--out-dir ...]

说明:
- 时间戳自动用 datetime.now() 生成，文件名与内容保证一致
- --out-dir 指向工程的「需求设计/changelogs」目录，脚本内自动按 YYYYMM 建月份子目录
- 文件名: {时间戳}-{工单号}-{中文摘要}.md，工单号缺省时为 {时间戳}-{中文摘要}.md；摘要必须中文
- --files 格式: "路径|修改类型|修改内容" 多文件用分号分隔
- --git-diff: 从 git status 自动填充修改文件表（路径/类型取真实状态），--files 只补语义列
- --effects 多个效果用逗号分隔
- 退出码: 0 成功 / 1 未命中或输入错误 / 2 参数格式错误
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

try:  # Windows GBK 控制台兜底，避免中文输出报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEEP_A_CHANGELOG_CATS = ("Added", "Fixed", "Changed", "Removed")


def now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def now_formatted() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_author() -> str:
    """变更人缺省值：git config user.name > 系统用户名。"""
    name = ""
    try:
        out = subprocess.run(["git", "config", "user.name"], capture_output=True, timeout=10)
        if out.returncode == 0:
            name = out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        pass
    if not name:
        name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return name or "未填写"


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


def _norm(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def parse_files_arg(files_str: str):
    """'路径|类型|内容;路径|类型|内容' -> [(路径, 类型, 内容)]"""
    rows = []
    for item in (files_str or "").split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        while len(parts) < 3:
            parts.append("—")
        rows.append((parts[0], parts[1], parts[2]))
    return rows


def git_status_rows(repo: str, excludes):
    """从 git status --porcelain -z 取变更文件；返回 (rows, err)。rows = [(路径, 修改类型)]"""
    cmd = ["git", "-C", repo, "status", "--porcelain", "-z"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30)
    except Exception as exc:
        return None, "调用 git 失败: {}".format(exc)
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip()[:200]
        return None, "git status 返回码 {}: {}".format(out.returncode, err)
    tokens = out.stdout.decode("utf-8", "replace").split("\0")
    rows, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if len(tok) < 4:
            i += 1
            continue
        xy, path = tok[:2], _norm(tok[3:])
        i += 1
        renamed = "R" in xy or "C" in xy
        if renamed and i < len(tokens) and tokens[i]:
            i += 1  # -z 模式下重命名条目的下一个 token 是旧路径
        if excludes and any(x and x in path for x in excludes):
            continue
        if xy.strip() == "??" or "A" in xy:
            change = "新增"
        elif "D" in xy:
            change = "删除"
        elif renamed:
            change = "修改（重命名）"
        else:
            change = "修改"
        rows.append((path, change))
    return rows, ""


def merge_file_rows(ai_rows, git_rows):
    """AI 语义行 + git 状态行合并：同文件以 AI 语义为准，git 独有行类型取真实状态。"""
    if not git_rows:
        return ai_rows
    merged = list(ai_rows)
    ai_by_path = {_norm(p).lower(): idx for idx, (p, _t, _c) in enumerate(merged)}
    ai_by_base = {}
    for idx, (p, _t, _c) in enumerate(merged):
        ai_by_base.setdefault(os.path.basename(_norm(p)).lower(), idx)
    for gpath, gtype in git_rows:
        key = gpath.lower()
        if key in ai_by_path:
            continue
        base = os.path.basename(key)
        if base in ai_by_base:
            continue
        merged.append((gpath, gtype, "（来自 git 状态，内容待补充）"))
    return merged


def files_to_table(rows) -> str:
    """[(路径, 类型, 内容)] -> markdown 表格行。"""
    if not rows:
        return "| 待补充 | 待补充 | 待补充 |"
    return "\n".join("| {} | {} | {} |".format(_norm(p) or "—", t or "—", c or "—")
                     for p, t, c in rows)


def effects_to_list(effects_str: str) -> str:
    """'效果1,效果2' -> '- ✅ 效果1' 列表。"""
    items = [e.strip() for e in (effects_str or "").split(",") if e.strip()]
    return "\n".join(f"- ✅ {e}" for e in items) if items else "- ✅ 待补充"


def build_content(changelog_type, author, impact, trigger, description, root_cause, solution,
                  files_table, verification, effects_list, related) -> str:
    ts, ft = now_timestamp(), now_formatted()
    return f"""# 变更记录 · {ts}

> 生成时间：{ft}（GMT+8）｜ 本次类型：{changelog_type} ｜ 变更人：{author} ｜ 状态：已完成
> 触发：{trigger} ｜ 影响范围：{impact}

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


def build_round_section(changelog_type, author, impact, trigger, description, root_cause,
                        solution, files_table, verification, effects_list) -> str:
    """追加模式：同一需求/同一 bug 的后续修改，在既有文档末尾追加的变更轮次小节。"""
    ts, ft = now_timestamp(), now_formatted()
    return f"""

---

## 变更轮次 · {ts}（{changelog_type}）

> 时间：{ft}（GMT+8）｜ 触发：{trigger}
> 变更人：{author} ｜ 影响范围：{impact}

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


def keep_a_changelog_cat(changelog_type: str) -> str:
    """变更类型 -> Keep a Changelog 分类（Added / Fixed / Changed / Removed）。"""
    tl = (changelog_type or "").lower()
    if "删除" in tl:
        return "Removed"
    if "新增" in tl:
        return "Added"
    if "修复" in tl or "bug" in tl:
        return "Fixed"
    return "Changed"


def cmd_find(keyword: str, out_root: str) -> int:
    kw = (keyword or "").strip().lower()
    if not kw:
        print("未提供关键词")
        return 1
    if not os.path.isdir(out_root):
        print("目录不存在: {}".format(out_root))
        return 1
    hits = []
    for root, _dirs, names in os.walk(out_root):
        for n in names:
            if not n.lower().endswith(".md"):
                continue
            fp = os.path.join(root, n)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                text = ""
            if kw in n.lower() or kw in text.lower():
                hits.append(fp)
    hits.sort()
    if not hits:
        print("未找到匹配文档：{}（按新需求新建即可）".format(keyword))
        return 1
    print("命中 {} 份文档：".format(len(hits)))
    for h in hits:
        print("  {}".format(h))
    return 0


def cmd_merge(month: str, out_root: str) -> int:
    m = (month or "").strip()
    if not re.fullmatch(r"\d{6}", m):
        print("月份格式应为 YYYYMM，如 202610")
        return 2
    month_dir = os.path.join(out_root, m)
    if not os.path.isdir(month_dir):
        print("目录不存在: {}".format(month_dir))
        return 1
    docs = [f for f in sorted(os.listdir(month_dir))
            if f.lower().endswith(".md") and not f.endswith("_changelog.md")]
    if not docs:
        print("该月份无变更日志文档")
        return 1
    cats = {c: [] for c in KEEP_A_CHANGELOG_CATS}
    detail = []
    for name in docs:
        fp = os.path.join(month_dir, name)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            text = ""
        mt = re.search(r"本次类型：\s*([^｜|\n]+)", text)
        mtm = re.search(r"生成时间：\s*([^（(\n]+)", text)
        ctype = mt.group(1).strip() if mt else "未标注"
        ftime = mtm.group(1).strip() if mtm else "—"
        summary = re.sub(r"^\d{14}-", "", os.path.splitext(name)[0]) or name
        # 去掉工单号前缀（英文/数字/下划线/连字符 + 连字符 + 中文摘要），保留纯中文摘要
        m_ticket = re.match(r"^[A-Za-z0-9_-]+-(.+[\u4e00-\u9fff].*)$", summary)
        if m_ticket:
            summary = m_ticket.group(1)
        cats[keep_a_changelog_cat(ctype)].append((summary, name))
        detail.append((name, ctype, ftime))

    _ts, ft = now_timestamp(), now_formatted()
    parts = [
        "# 变更日志 · {}".format(m),
        "",
        "> 生成时间：{}（GMT+8）｜ 来源：需求设计/changelogs/{}（{} 份变更记录）".format(ft, m, len(docs)),
        "> 分类参考 Keep a Changelog（Added / Fixed / Changed / Removed）",
        "",
    ]
    for cat in KEEP_A_CHANGELOG_CATS:
        if cats[cat]:
            parts += ["## {}".format(cat), ""]
            parts += ["- {}（`{}`）".format(s, n) for s, n in cats[cat]]
            parts.append("")
    parts += ["## 明细", "", "| 文件 | 类型 | 生成时间 |", "| --- | --- | --- |"]
    parts += ["| {} | {} | {} |".format(n, t, f) for n, t, f in detail]
    out_path = os.path.join(month_dir, "{}_changelog.md".format(m))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts).rstrip() + "\n")
    print("月度变更日志已生成: {}".format(out_path))
    for cat in KEEP_A_CHANGELOG_CATS:
        if cats[cat]:
            print("  {}: {} 条".format(cat, len(cats[cat])))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="changelog-record skill 变更日志生成器（脚本随 skill 自带，勿在工作区另找脚本）")
    parser.add_argument("--type", help="变更类型：Bug修复/功能优化/新增功能/重构代码/配置变更")
    parser.add_argument("--summary", help="变更摘要（必须中文，用于生成文件名，如：修复登录超时）")
    parser.add_argument("--ticket", default="", help="问题号/需求工单号（可选，添加在文件名日期后面，如 795803）")
    parser.add_argument("--author", default="", help="变更人（缺省取 git config user.name 或系统用户名）")
    parser.add_argument("--impact", default="未填写", help="影响范围：受影响的模块/功能/接口/表")
    parser.add_argument("--append", default="", metavar="DOC_PATH",
                        help="追加模式：向指定既有变更日志文档追加「变更轮次」小节，不新建文件")
    parser.add_argument("--find", default=None, metavar="KEYWORD",
                        help="查询历史变更日志：按工单号/关键词匹配文件名或正文，命中输出路径")
    parser.add_argument("--merge", default=None, metavar="YYYYMM",
                        help="月度合并：聚合该月目录全部变更记录为 {YYYYMM}_changelog.md")
    parser.add_argument("--out-dir", default=None,
                        help="输出根目录，指向工程的 需求设计/changelogs（默认: 当前工作目录/需求设计/changelogs）")
    parser.add_argument("--trigger", default="用户执行代码修改", help="变更触发原因")
    parser.add_argument("--description", default="待补充", help="问题描述")
    parser.add_argument("--root_cause", default="待补充", help="根因分析")
    parser.add_argument("--solution", default="待补充", help="解决方案")
    parser.add_argument("--files", default="", help='修改文件: "路径|修改类型|修改内容"，多文件用分号分隔')
    parser.add_argument("--git-diff", nargs="?", const=".", default=None, metavar="REPO",
                        help="从 git status 自动填充修改文件表（REPO 缺省为当前目录），--files 只补语义列")
    parser.add_argument("--git-exclude", default="", metavar="子串1,子串2",
                        help="--git-diff 时排除路径含这些子串的条目，如 .vscode 或 需求设计")
    parser.add_argument("--verification", default="待补充", help="测试验证内容")
    parser.add_argument("--effects", default="", help="变更效果，逗号分隔")
    parser.add_argument("--related", default="无", help="关联变更")
    args = parser.parse_args()

    out_root = args.out_dir or os.path.join(os.getcwd(), "需求设计", "changelogs")

    # 查询 / 合并模式不需要 --type/--summary
    if args.find is not None:
        return cmd_find(args.find, out_root)
    if args.merge is not None:
        return cmd_merge(args.merge, out_root)

    if not args.type:
        parser.error("--type 必填（新建/追加模式）")
    if not args.summary:
        parser.error("--summary 必填（新建/追加模式）")

    author = args.author.strip() or default_author()

    # 文件清单：--git-diff 提供真实路径/状态，--files 提供语义
    ai_rows = parse_files_arg(args.files)
    git_rows = None
    if args.git_diff is not None:
        excludes = [x.strip() for x in (args.git_exclude or "").split(",") if x.strip()]
        git_rows, err = git_status_rows(args.git_diff, excludes)
        if git_rows is None:
            print("[提示] git 状态获取失败，仅使用 --files：{}".format(err), file=sys.stderr)
            git_rows = []
        else:
            print("[提示] git status 取到 {} 个变更文件".format(len(git_rows)), file=sys.stderr)
    file_rows = merge_file_rows(ai_rows, git_rows or [])

    round_section = build_round_section(
        args.type, author, args.impact, args.trigger, args.description, args.root_cause,
        args.solution, files_to_table(file_rows), args.verification, effects_to_list(args.effects))

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
        args.type, author, args.impact, args.trigger, args.description, args.root_cause,
        args.solution, files_to_table(file_rows), args.verification,
        effects_to_list(args.effects), args.related)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print("变更日志已创建: {}".format(filepath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
