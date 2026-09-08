#!/usr/bin/env python3
"""Skill 套件打包工具。

将本仓库内的 skill 统一打包输出到 dist/：
  - dist/<skill名>-v<版本>.zip                    安装包（SKILL.md 位于 zip 根，可直接上传安装）
  - dist/docs/<skill名>-v<版本>-guide.<md|html>   使用说明（格式可选）
  - dist/pack-manifest.json                       清单（版本/路径/sha256/生成时间）

文档格式选择优先级：命令行 --format > pack.config.json 的 format 字段 > 默认 md。
每次打包前会清理同 skill 的旧版产物，保证 dist 与本次打包结果一致。

用法：
  python pack_skills.py [--format md|html] [--skills 名1,名2] [--output dist] [--config pack.config.json]
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.S)
    fm: dict = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def zip_skill(skill_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(skill_dir.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            zf.write(p, p.relative_to(skill_dir).as_posix())


def _inline(s: str) -> str:
    s = html_mod.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


def md_to_html(md_text: str, title: str, footer_html: str = "") -> str:
    """轻量 Markdown -> HTML 转换：标题/段落/表格/列表/代码块/分隔线/行内样式。"""
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                out.append(
                    "<pre><code>" + html_mod.escape("\n".join(code_buf)) + "</code></pre>"
                )
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:\-|]+\|?\s*$", lines[i + 1] or ""
        ):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body = rows[0], rows[2:]
            out.append(
                "<table><thead><tr>"
                + "".join(f"<th>{_inline(c)}</th>" for c in header)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                    for r in body
                )
                + "</tbody></table>"
            )
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            out.append("<hr>")
            i += 1
            continue
        if line.strip():
            out.append(f"<p>{_inline(line.strip())}</p>")
        i += 1
    body = "\n".join(out) + footer_html
    style = (
        "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
        "max-width:900px;margin:24px auto;padding:0 16px;line-height:1.65;color:#1f2328}"
        "h1,h2,h3{border-bottom:1px solid #e5e7eb;padding-bottom:6px}"
        "code{background:#f3f4f6;padding:2px 5px;border-radius:4px;font-size:.92em}"
        "pre{background:#f6f8fa;padding:12px;border-radius:8px;overflow:auto}"
        "pre code{background:none;padding:0}"
        "table{border-collapse:collapse;width:100%;margin:12px 0}"
        "th,td{border:1px solid #d0d7de;padding:6px 10px;text-align:left}"
        "th{background:#f6f8fa}"
    )
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html_mod.escape(title)}</title>\n"
        f"<style>{style}</style>\n</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Skill 套件打包工具（产物输出到 dist/）")
    ap.add_argument("--format", choices=["md", "html"], help="使用说明文档格式；缺省读配置，再缺省 md")
    ap.add_argument("--skills", help="逗号分隔的 skill 目录名列表；缺省读配置")
    ap.add_argument("--output", help="输出目录；缺省读配置（默认 dist）")
    ap.add_argument("--config", default=str(ROOT / "pack.config.json"))
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    fmt = args.format or cfg.get("format") or "md"
    if fmt not in ("md", "html"):
        print(f"错误：不支持的文档格式 {fmt!r}（仅支持 md/html）")
        return 2
    skills = (
        [s.strip() for s in args.skills.split(",") if s.strip()]
        if args.skills
        else [s for s in cfg.get("skills") or []]
    )
    if not skills:
        print("错误：未指定 skill 列表（--skills 或 pack.config.json）")
        return 2
    out_root = ROOT / (args.output or cfg.get("output") or "dist")
    docs_dir = out_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    failures: list[tuple[str, str]] = []
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    for name in skills:
        skill_dir = ROOT / name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            failures.append((name, "缺少 SKILL.md"))
            continue
        fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        version = fm.get("version") or "0.0.0"
        slug = fm.get("name") or name

        # 清理同 skill 旧版产物，保证 dist 与本次打包一致
        for old in out_root.glob(f"{slug}-v*.zip"):
            old.unlink()
        for old in docs_dir.glob(f"{slug}-v*-guide.*"):
            old.unlink()

        zip_path = out_root / f"{slug}-v{version}.zip"
        zip_skill(skill_dir, zip_path)

        md_text = skill_md.read_text(encoding="utf-8")
        footer_note = f"{slug} v{version} · 打包时间 {stamp} · 由 pack_skills.py 生成"
        if fmt == "md":
            guide_path = docs_dir / f"{slug}-v{version}-guide.md"
            guide_path.write_text(md_text.rstrip() + f"\n---\n\n*{footer_note}*\n", encoding="utf-8")
        else:
            guide_path = docs_dir / f"{slug}-v{version}-guide.html"
            footer_html = f"<hr><p><em>{html_mod.escape(footer_note)}</em></p>"
            guide_path.write_text(
                md_to_html(md_text, f"{slug} v{version} 使用说明", footer_html), encoding="utf-8"
            )

        entries.append(
            {
                "skill": slug,
                "version": version,
                "zip": zip_path.relative_to(out_root).as_posix(),
                "zip_sha256": sha256_file(zip_path),
                "guide": guide_path.relative_to(out_root).as_posix(),
                "description": fm.get("description", ""),
            }
        )
        print(f"[OK] {slug} v{version} -> {zip_path.name} + {guide_path.name}")

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "format": fmt,
        "output_root": out_root.name,
        "skills": entries,
    }
    (out_root / "pack-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n打包完成：{len(entries)} 成功 / {len(failures)} 失败；说明文档格式 {fmt}")
    for name, reason in failures:
        print(f"[失败] {name}: {reason}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
