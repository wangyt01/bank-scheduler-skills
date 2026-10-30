#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discover_qoder.py —— 只读探测本机 Qoder 桌面端登录态、API 域名与机器指纹二进制位置。

仅读取，不修改、不删除任何文件；不打印任何真实 token（仅显示目录/键名/文件时间/位置）。
登录态有两种可能来源，本脚本都会列出来：
  A. 旧客户端：<数据目录>/User/globalStorage/state.vscdb（键 secret://aicoding.auth.userInfo）
  B. 新版客户端：<数据目录>/auth.v1.dat（根目录下）
用途：首次搭建时确认「登录态来源 → openapi 域名 → runtime-info.exe」三件事，
      并输出一段可直接使用的建议配置片段。

用法：
  python discover_qoder.py
"""

import json
import os
import platform
import time

SYSTEM = platform.system().lower()
WINDOWS = (SYSTEM == "windows")

# 数据目录候选（与 qoder_checkin.py 保持一致；CN 客户端优先）
CANDIDATE_DIRS = {
    "windows": [
        ("APPDATA/com.qodercn.app.stable（新版 CN 客户端，当前活跃）",
         os.path.join(os.environ.get("APPDATA", ""), "com.qodercn.app.stable")),
        ("APPDATA/QoderCN（旧 CN 客户端）",
         os.path.join(os.environ.get("APPDATA", ""), "QoderCN")),
        ("APPDATA/Qoder（国际/旧版）",
         os.path.join(os.environ.get("APPDATA", ""), "Qoder")),
        ("APPDATA/QoderWork CN",
         os.path.join(os.environ.get("APPDATA", ""), "QoderWork CN")),
        ("APPDATA/QoderWork",
         os.path.join(os.environ.get("APPDATA", ""), "QoderWork")),
        ("LOCALAPPDATA/Qoder CN",
         os.path.join(os.environ.get("LOCALAPPDATA", ""), "Qoder CN")),
        ("LOCALAPPDATA/Lingma",
         os.path.join(os.environ.get("LOCALAPPDATA", ""), "Lingma")),
    ],
    "darwin": [
        ("~/Library/Application Support/com.qodercn.app.stable",
         os.path.expanduser("~/Library/Application Support/com.qodercn.app.stable")),
        ("~/Library/Application Support/QoderCN",
         os.path.expanduser("~/Library/Application Support/QoderCN")),
        ("~/Library/Application Support/Qoder",
         os.path.expanduser("~/Library/Application Support/Qoder")),
        ("~/Library/Application Support/QoderWork CN",
         os.path.expanduser("~/Library/Application Support/QoderWork CN")),
    ],
    "linux": [
        ("~/.config/com.qodercn.app.stable", os.path.expanduser("~/.config/com.qodercn.app.stable")),
        ("~/.config/QoderCN", os.path.expanduser("~/.config/QoderCN")),
        ("~/.config/Qoder", os.path.expanduser("~/.config/Qoder")),
        ("~/.config/QoderWork", os.path.expanduser("~/.config/QoderWork")),
    ],
}

CN_BASE = "https://openapi.qoder.com.cn"
INTL_BASE = "https://openapi.qoder.sh"

UMID_REL_PATHS = [
    os.path.join("resources", "umid", "runtime-info.exe"),
    os.path.join("resources", "umid", "runtime-info"),
]
UMID_SEARCH_ROOTS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    os.environ.get("ProgramFiles", r"C:\Program Files") or r"C:\Program Files",
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)") or r"C:\Program Files (x86)",
    "D:\\SunApp\\Program",
]

# 新版客户端把登录态直接放在数据目录根下（旧客户端放在 User/globalStorage/state.vscdb）
AUTH_FILE_NAMES = ["auth.v1.dat"]
VSDB_REL = os.path.join("User", "globalStorage", "state.vscdb")


def base_for_dir(data_dir):
    """按目录名推断 openapi 域名（CN 客户端 → .com.cn，其余 → .sh）。"""
    name = os.path.basename(data_dir.rstrip("\\/")).lower()
    if "qodercn" in name or "qoderwork" in name or name.endswith("qoder cn"):
        return CN_BASE
    return INTL_BASE


def probe_umid_dir(d):
    if not d or not os.path.isdir(d):
        return None
    for rel in UMID_REL_PATHS:
        p = os.path.join(d, rel)
        if os.path.isfile(p):
            return p
    return None


def find_umid():
    """定位 runtime-info.exe（配置/环境变量 → 已知安装根下探 2 层）。"""
    p = os.environ.get("QODER_UMID_PATH", "").strip()
    if p and os.path.isfile(p):
        return p
    for root in UMID_SEARCH_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        hit = probe_umid_dir(root)
        if hit:
            return hit
        try:
            subs = os.listdir(root)
        except Exception:  # noqa: BLE001
            continue
        for sub in subs:
            base = os.path.join(root, sub)
            if not os.path.isdir(base):
                continue
            hit = probe_umid_dir(base)
            if hit:
                return hit
            try:
                subs2 = os.listdir(base)
            except Exception:  # noqa: BLE001
                continue
            for sub2 in subs2:
                if not sub2.lower().startswith("qoder"):
                    continue
                hit = probe_umid_dir(os.path.join(base, sub2))
                if hit:
                    return hit
    return None


def fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:  # noqa: BLE001
        return "-"


def main():
    print("=" * 64)
    print("Qoder 登录态探测（只读，不修改任何文件，不打印 token）")
    print("系统：%s" % SYSTEM)
    print("=" * 64)

    dirs = CANDIDATE_DIRS.get(SYSTEM, [])
    active = []          # (mtime, dir, base, 来源说明)
    found_any = False

    for label, path in dirs:
        if not path or not os.path.isdir(path):
            continue
        found_any = True
        vscdb = os.path.join(path, VSDB_REL)
        local_state = os.path.join(path, "Local State")
        base = base_for_dir(path)
        print("\n[发现目录] %s" % label)
        print("  路径：%s" % path)
        print("  推断域名：%s" % base)
        print("  Local State：%s" % ("存在（可解出解密密钥）" if os.path.isfile(local_state)
                                     else "缺失（无法解密登录态）"))
        if os.path.isfile(vscdb):
            mt = os.path.getmtime(vscdb)
            print("  state.vscdb：存在，最后修改 %s（旧客户端）" % fmt_ts(mt))
            print("    登录态键：secret://aicoding.auth.userInfo")
            active.append((mt, path, base, "vscdb:state.vscdb"))
        else:
            print("  state.vscdb：不存在（非旧客户端目录）")
        for name in AUTH_FILE_NAMES:
            f = os.path.join(path, name)
            if os.path.isfile(f):
                mt = os.path.getmtime(f)
                print("  %s：存在，最后修改 %s（新版客户端登录态，%d 字节）"
                      % (name, fmt_ts(mt), os.path.getsize(f)))
                active.append((mt, path, base, "auth-file:%s" % name))
            else:
                print("  %s：不存在" % name)

    if not found_any:
        print("\n未发现本机 Qoder 安装目录。请先安装并登录 Qoder 桌面端，再运行本脚本。")
        print("候选目录（供手动核对）：")
        for _label, path in dirs:
            print("  - %s" % path)

    # 机器指纹二进制
    umid = find_umid()
    print("\n" + "-" * 64)
    print("[机器指纹] runtime-info.exe（Cosy-Machine* 头的来源，必需）")
    if umid:
        print("  已找到：%s" % umid)
        print("  调用方式（不要在本脚本里执行，仅说明）：")
        print('    echo {"account":"<账号>"} | "<该路径>" x --account-stdin')
    else:
        print("  未找到。请确认 Qoder 客户端安装完整，或在配置里用 umid_path 指定路径。")
        print("  搜索范围：%s" % ", ".join(r for r in UMID_SEARCH_ROOTS if r))

    # 推荐活跃目录
    print("\n" + "-" * 64)
    print("[结论]")
    if active:
        active.sort(key=lambda x: x[0], reverse=True)
        mt, path, base, src = active[0]
        print("  最新活跃登录态：%s" % path)
        print("  来源：%s（最后修改 %s）" % (src, fmt_ts(mt)))
        print("  对应 openapi 域名：%s" % base)
        print("  → 脚本会按「文件修改时间取最新」自动选中它，无需手工配置。")
    else:
        print("  未发现任何可用的登录态文件（state.vscdb / auth.v1.dat 均不存在）。")
        print("  请确认 Qoder 桌面端已安装【并登录过】，然后重跑本脚本。")
        print("  仅当确实没有任何实时登录态时，脚本才会回退到缓存 token")
        print("  （~/.workbuddy/scripts/.qoder_token_cache.json）。")

    print("\n" + "=" * 64)
    print("建议配置片段（写入 ~/.workbuddy/scripts/qoder_checkin_config.json）：")
    print("=" * 64)
    cfg = {
        "openapi_base": "",     # 留空 = 按登录态目录自动判断
        "umid_path": umid or "",  # 留空 = 自动搜索 runtime-info.exe
        "cache_path": "",
        "token": {"source": "auto"},
        "notify": {"enabled": False, "success_notify": False,
                   "wecom_webhook": "", "pushplus_token": "", "bark_url": ""},
    }
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("\n下一步：运行 qoder_checkin.py --check-only，确认 machine_identity=ok(...) 且 campaigns_http=200。")


if __name__ == "__main__":
    main()
