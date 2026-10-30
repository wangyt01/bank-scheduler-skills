#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qoder签到助手 核心脚本（Python 3.8+；自动解密登录态与刷新 token 需 cryptography）

Qoder 签到链路（已从 Qoder CN 桌面端逆向确认）：
  A. Campaign（每日主链路，「专属活动权益 · 每天领 100 Credits」）：
       每天 10:00（UTC+8）服务端生成一个【新的 campaign 实例（新 campaignId）】，
       所以必须先 GET 当日列表拿到新 id，再 POST claim；用旧 id 重放永远 replayed=true。
       GET  /sash/api/v1/me/campaigns
            → {uid, showCampaign, claimable, campaigns:[{campaignId, campaignKey,
               actionType:"CLAIM_BENEFIT", claimStatus:"CLAIMABLE|CLAIMED", benefit:{amount:100}}]}
       POST /sash/api/v1/me/campaigns/<campaignId>/claim
            → {status:"CLAIMED", replayed:bool, claimedAt, expiresAt, ...}
       ⚠️ 必须带 Cosy-Machine* 机器指纹头（见下），否则服务端返回【空列表】；
       ⚠️ 新领取（replayed=false）会过风控，指纹必须是客户端原生 runtime-info.exe（umid）
          实时生成的，伪造指纹会 503 RISK_DEPENDENCY_UNAVAILABLE。
  B. Activity（另一套签到体系，CN1.0 会话不可用）：
       GET  /api/v2/activity/claim/eligibility → {code:0, data:[...]}
       CN1.0 会话（login_source=qodercn）服务端禁用：data:[]，claim 报 "not available for CN 1.0"。
       本脚本仍会尝试，对可用账号生效。

  机器指纹（umid）：<Qoder安装>\\resources\\umid\\runtime-info.exe
       用法：echo {"account":"<账号>"} | runtime-info.exe <任意串> --account-stdin
       stdout 第一个空格前的 JSON = {machineToken, machineType, machineCode, ...}
       请求头：Cosy-ClientType:10 / Cosy-Version:0.3.3 / Cosy-MachineOS / User-Agent:Qoder
               + Cosy-MachineToken / Cosy-MachineCode / Cosy-MachineType / Cosy-MachineId

  登录态读取（每个数据目录两种来源，谁在取谁，按文件修改时间取最新；都用同一把密钥）：
     A. 旧客户端：User/globalStorage/state.vscdb 里的 secret://aicoding.auth.userInfo
     B. 新版客户端：数据目录根下的 auth.v1.dat
        （明文 JSON：{schemaVersion, token:"dt-…", refreshToken:"drt-…", expiresAt, user:{…}}）
     两者均为 Chromium OSCrypt：v10 + AES-256-GCM；密钥取自**同目录** Local State 的
     os_crypt.encrypted_key（Windows 走 DPAPI）。
     多目录候选：com.qodercn.app.stable（新版 CN 客户端）/ QoderCN / Qoder / QoderWork CN 等。
     ⚠️ 只认 state.vscdb 是历史盲区：新版客户端目录下**没有** state.vscdb，
        登录态只在 auth.v1.dat —— 早期版本因此在新装机器上必然取不到 token。
  域名按客户端自动选择（关键！选错必然 401）：
       - QoderCN（新版 CN 客户端）→ https://openapi.qoder.com.cn
       - 其余（国际/旧版）        → https://openapi.qoder.sh
  token 过期时可自行刷新：POST /api/v1/deviceToken/refresh  Body {"refresh_token":"<drt->"}
  余额：GET /api/v2/quota/usage → addOnQuota.remaining（资源包，签到 Credits 落在这里）。
  桌面通知 + 可选微信推送；输出 JSON，全程脱敏 token。

注意：
  * 本脚本只读取 Qoder 登录态文件，不修改；刷新后的新 token 写入
    ~/.workbuddy/scripts/.qoder_token_cache.json（600 权限，按数据目录隔离）。
  * 对 campaign 重复 POST claim 是幂等重放（replayed=true），安全；activity 同理。

用法：
  python qoder_checkin.py                # 完整签到（自动解密 + 必要时刷新 + 领取）
  python qoder_checkin.py --check-only   # 仅查询（不领取）
  python qoder_checkin.py --no-notify    # 跳过推送
  python qoder_checkin.py --force-refresh  # 强制刷新 token
  python qoder_checkin.py --config PATH
"""

import argparse
import base64
import json
import os
import platform
import stat
import subprocess
import sys

# 统一消息通知渠道（与 WorkBuddy 签到助手同源的多渠道推送引擎，纯标准库）
try:
    import push_message as pm
except Exception:  # noqa: BLE001
    pm = None
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 常量（已逆向确认，一般无需改动）
# ---------------------------------------------------------------------------

DEFAULT_OPENAPI_BASE_CN = "https://openapi.qoder.com.cn"   # QoderCN（新版 CN 客户端）
DEFAULT_OPENAPI_BASE_INTL = "https://openapi.qoder.sh"     # 国际/旧版
REFRESH_ENDPOINT = "/api/v1/deviceToken/refresh"
CAMPAIGNS_ENDPOINT = "/sash/api/v1/me/campaigns"
CAMPAIM_CLAIM_TMPL = "/sash/api/v1/me/campaigns/%s/claim"
QUOTA_ENDPOINT = "/api/v2/quota/usage"
# 真实每日签到（Activity 体系，独立于 Campaign 网页弹窗）
# CN1.0 会话（login_source=qodercn）会被服务端返回空 data:[] 且 claimActivity 拒绝
ACTIVITY_ELIGIBILITY = "/api/v2/activity/claim/eligibility"
ACTIVITY_CLAIM_TMPL = "/api/v2/activity/claim?activityId=%s"

# 服务端「机器指纹」校验头（缺了它 GET campaigns 会被软性反爬返回空列表，
# 新领取会被风控 503 RISK_DEPENDENCY_UNAVAILABLE）。取值与客户端一致。
COSY_CLIENT_TYPE = "10"
COSY_VERSION = "0.3.3"
COSY_UA = "Qoder"
# Campaign 可领判据（不要用顶层 claimable 或 c.claimable，真实字段是这两个）
CAMPAIGN_ACTION_CLAIM = "CLAIM_BENEFIT"
CAMPAIGN_STATUS_CLAIMABLE = "CLAIMABLE"

SYSTEM = platform.system().lower()
WINDOWS = (SYSTEM == "windows")

# Qoder 桌面端数据目录候选（会按 state.vscdb 修改时间自动挑最新的活跃目录）
QODER_DATA_DIRS = {
    "windows": [
        os.path.join(os.environ.get("APPDATA", ""), "com.qodercn.app.stable"),  # 新版 CN 客户端（当前活跃）
        os.path.join(os.environ.get("APPDATA", ""), "QoderCN"),
        os.path.join(os.environ.get("APPDATA", ""), "Qoder"),
        os.path.join(os.environ.get("APPDATA", ""), "QoderWork CN"),
        os.path.join(os.environ.get("APPDATA", ""), "QoderWork"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Qoder CN"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Lingma"),
    ],
    "darwin": [
        os.path.expanduser("~/Library/Application Support/com.qodercn.app.stable"),
        os.path.expanduser("~/Library/Application Support/QoderCN"),
        os.path.expanduser("~/Library/Application Support/Qoder"),
        os.path.expanduser("~/Library/Application Support/QoderWork CN"),
    ],
    "linux": [
        os.path.expanduser("~/.config/com.qodercn.app.stable"),
        os.path.expanduser("~/.config/QoderCN"),
        os.path.expanduser("~/.config/Qoder"),
        os.path.expanduser("~/.config/QoderWork"),
    ],
}

LOCAL_STATE_NAMES = ["Local State", "local-state.json"]
USER_INFO_KEY = "secret://aicoding.auth.userInfo"
# 新版 Qoder CN 客户端（com.qodercn.app.stable 等，无 User/globalStorage/state.vscdb）
# 把登录态直接存在数据目录根下：v10 + AES-256-GCM，密钥仍取自同目录 Local State。
AUTH_FILE_NAMES = ["auth.v1.dat"]

# umid 机器指纹（Cosy-Machine* 头的来源）：<Qoder安装目录>/resources/umid/runtime-info.exe
UMID_REL_PATHS = [
    os.path.join("resources", "umid", "runtime-info.exe"),
    os.path.join("resources", "umid", "runtime-info"),
]
# 安装目录搜索根（会再往下探 2 层找 resources/umid/runtime-info.exe）
UMID_SEARCH_ROOTS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    os.environ.get("ProgramFiles", r"C:\Program Files") or r"C:\Program Files",
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)") or r"C:\Program Files (x86)",
    "D:\\SunApp\\Program",   # 常见自定义安装根（按需增删）
    "C:\\Program Files\\Qoder",
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def mask(token):
    if not token:
        return "<none>"
    if len(token) <= 12:
        return "****"
    return token[:8] + "..." + token[-4:]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def now_ms():
    return int(time.time() * 1000)


def to_ms(v):
    """把秒/毫秒数字或 ISO8601 字符串（如 2026-10-18T15:38:12Z）统一成毫秒；失败返回 0。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        n = int(v)
        return n * 1000 if (0 < n < 1e11) else n
    s = str(v).strip()
    if not s:
        return 0
    try:
        n = int(s)
        return n * 1000 if (0 < n < 1e11) else n
    except Exception:  # noqa: BLE001
        pass
    try:  # ISO 8601
        import datetime
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return 0


def base_for_dir(data_dir):
    """按数据目录名选择 openapi 域名：QoderCN/QoderWork CN → CN 域，否则国际域。"""
    name = os.path.basename(data_dir.rstrip("\\/")).lower()
    if "qodercn" in name or "qoderwork" in name or name.endswith("qoder cn"):
        return DEFAULT_OPENAPI_BASE_CN
    return DEFAULT_OPENAPI_BASE_INTL


# ---------------------------------------------------------------------------
# 登录态解密（Chromium OSCrypt）
# ---------------------------------------------------------------------------

def _dpapi_unprotect(data):
    if not WINDOWS:
        return None
    try:
        import ctypes
        import ctypes.wintypes
        crypt = ctypes.windll.crypt32

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        indata = DATA_BLOB(len(data),
                           ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                       ctypes.POINTER(ctypes.c_char)))
        out = DATA_BLOB()
        res = crypt.CryptUnprotectData(ctypes.byref(indata), None, None, None, None, 0,
                                       ctypes.byref(out))
        if not res:
            return None
        size = out.cbData
        return bytes(ctypes.cast(out.pbData, ctypes.POINTER(ctypes.c_char * size)).contents)
    except Exception:  # noqa: BLE001
        return None


def _os_crypt_key(data_dir):
    for name in LOCAL_STATE_NAMES:
        ls = os.path.join(data_dir, name)
        if not os.path.isfile(ls):
            continue
        try:
            with open(ls, "r", encoding="utf-8") as f:
                st = json.load(f)
            b64 = st["os_crypt"]["encrypted_key"]
            blob = base64.b64decode(b64)[5:]  # 去掉 "DPAPI" 前缀
            return _dpapi_unprotect(blob)
        except Exception:  # noqa: BLE001
            continue
    return None


def _aes_gcm_decrypt(key, raw):
    if raw[:3] != b"v10":
        return None
    payload = raw[3:]
    nonce, cipher = payload[:12], payload[12:]
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, cipher, None)
    except Exception:  # noqa: BLE001
        return None


def _read_auth_file(data_dir):
    """读新版客户端的 <数据目录>/auth.v1.dat（v10 + AES-256-GCM，密钥取自同目录 Local State）。

    解密后是一段 JSON：{"schemaVersion":1,"token":"dt-...","refreshToken":"drt-...",
    "expiresAt":..., "refreshTokenExpiresAt":..., "user":{id,name,email,phone,...}}。
    返回与 state.vscdb 同构的 ui dict（含 _source 标记），失败返回 None。
    """
    for name in AUTH_FILE_NAMES:
        f = os.path.join(data_dir, name)
        if not os.path.isfile(f):
            continue
        key = _os_crypt_key(data_dir)
        if not key:
            continue
        try:
            with open(f, "rb") as fh:
                raw = fh.read()
            plain = _aes_gcm_decrypt(key, raw)
            if not plain:
                continue
            obj = json.loads(plain.decode("utf-8"))
            if not isinstance(obj, dict) or not obj.get("token"):
                continue
            user = obj.get("user") if isinstance(obj.get("user"), dict) else {}
            return {
                "token": obj.get("token"),
                "refreshToken": obj.get("refreshToken"),
                "expireTime": obj.get("expiresAt"),
                "refreshTokenExpireTime": obj.get("refreshTokenExpiresAt"),
                "name": user.get("name") or user.get("id"),
                "id": user.get("id"),
                "email": user.get("email"),
                "phone": user.get("phone"),
                "_source": "auth-file:%s" % name,
            }
        except Exception:  # noqa: BLE001
            continue
    return None


def decrypt_all_userinfos():
    """解密所有候选目录的登录态，返回 ui 列表（按来源文件修改时间新→旧）。

    每个目录两种来源，谁在就取谁（同目录同时存在时按修改时间取新的）：
      A. 旧客户端（VS Code 系）: User/globalStorage/state.vscdb → secret://aicoding.auth.userInfo
      B. 新版客户端: 数据目录根下 auth.v1.dat
    两者都用同一把密钥（该目录 Local State 的 os_crypt.encrypted_key + DPAPI）。
    """
    found = []
    for d in QODER_DATA_DIRS.get(SYSTEM, []):
        if not os.path.isdir(d):
            continue
        # A) state.vscdb（旧客户端）
        vscdb = os.path.join(d, "User", "globalStorage", "state.vscdb")
        if os.path.isfile(vscdb):
            try:
                mtime = os.path.getmtime(vscdb)
            except Exception:  # noqa: BLE001
                mtime = 0
            ui = None
            key = _os_crypt_key(d)
            if key:
                try:
                    import sqlite3
                    con = sqlite3.connect(vscdb)
                    row = con.execute(
                        "SELECT value FROM ItemTable WHERE key=?", (USER_INFO_KEY,)
                    ).fetchone()
                    con.close()
                    if row:
                        raw = bytes(json.loads(row[0])["data"])
                        plain = _aes_gcm_decrypt(key, raw)
                        if plain:
                            cand = json.loads(plain.decode("utf-8"))
                            if cand.get("token"):
                                ui = cand
                except Exception:  # noqa: BLE001
                    ui = None
            if ui:
                ui["_store_dir"] = d
                ui["_store_mtime"] = mtime
                ui["_store_rank"] = 0
                ui["_source"] = "vscdb:state.vscdb"
                ui["_base"] = base_for_dir(d)
                found.append(ui)
        # B) auth.v1.dat（新版客户端）
        for name in AUTH_FILE_NAMES:
            f = os.path.join(d, name)
            if not os.path.isfile(f):
                continue
            try:
                mtime = os.path.getmtime(f)
            except Exception:  # noqa: BLE001
                mtime = 0
            ui = _read_auth_file(d)
            if ui:
                ui["_store_dir"] = d
                ui["_store_mtime"] = mtime
                ui["_store_rank"] = 1      # 同为最新时优先新版来源
                ui["_base"] = base_for_dir(d)
                found.append(ui)
    found.sort(key=lambda u: (u.get("_store_mtime", 0), u.get("_store_rank", 0)),
               reverse=True)
    return found


# ---------------------------------------------------------------------------
# 配置 / token 缓存
# ---------------------------------------------------------------------------

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path():
    """配置查找顺序：脚本同目录优先 → ~/.workbuddy/scripts（兼容旧安装）。"""
    local = os.path.join(_script_dir(), "qoder_checkin_config.json")
    if os.path.isfile(local):
        return local
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "scripts",
                        "qoder_checkin_config.json")


def default_cache_path():
    """token 缓存：沿用已有缓存（任一位置），都没有则写脚本同目录。"""
    local = os.path.join(_script_dir(), ".qoder_token_cache.json")
    if os.path.isfile(local):
        return local
    legacy = os.path.join(os.path.expanduser("~"), ".workbuddy", "scripts",
                          ".qoder_token_cache.json")
    return legacy if os.path.isfile(legacy) else local


def load_config(path=None):
    cfg_path = path or default_config_path()
    if not os.path.isfile(cfg_path):
        return {}, cfg_path
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f), cfg_path
    except Exception as e:  # noqa: BLE001
        log("配置文件读取失败，将使用默认值：%s" % e)
        return {}, cfg_path


def load_cache(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def save_cache(path, auth):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(auth, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as e:  # noqa: BLE001
        log("缓存写入失败：%s" % e)
        return False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_call(url, token, method="GET", payload=None, timeout=20, extra_headers=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    for k, v in (extra_headers or {}).items():
        if v not in (None, ""):
            req.add_header(k, str(v))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            pass
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return 0, "ERR:%s" % e


def parse_body(body):
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return {"_raw": body[:500]}


# ---------------------------------------------------------------------------
# 机器指纹（umid / runtime-info.exe）—— Cosy-Machine* 头的来源
#   服务端用它对「新领取」做风控：不带 → 返回空列表；伪造 → 503 RISK_DEPENDENCY_UNAVAILABLE。
#   合法做法：调用 Qoder 自带的原生二进制实时生成（与客户端行为一致）。
# ---------------------------------------------------------------------------

_UMID_CACHE = {}


def _probe_umid_dir(d):
    if not d or not os.path.isdir(d):
        return None
    for rel in UMID_REL_PATHS:
        p = os.path.join(d, rel)
        if os.path.isfile(p):
            return p
    return None


def _running_qoder_dirs():
    """从运行中的 Qoder 进程推断安装目录（拿不到就返回空）。"""
    dirs = []
    try:
        if WINDOWS:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Where-Object { $_.Path -and ($_.Path -match 'Qoder') } | "
                 "Select-Object -ExpandProperty Path -Unique"],
                capture_output=True, text=True, timeout=25).stdout or ""
            for line in out.splitlines():
                p = line.strip().strip('"')
                if p.lower().endswith(".exe"):
                    dirs.append(os.path.dirname(p))
        elif SYSTEM == "darwin":
            out = subprocess.run(["pgrep", "-fl", "Qoder"], capture_output=True,
                                 text=True, timeout=10).stdout or ""
            for line in out.splitlines():
                for tok in line.split():
                    if tok.startswith("/") and tok.endswith(".app"):
                        dirs.append(tok)
    except Exception:  # noqa: BLE001
        pass
    return dirs


def find_umid_binary(config=None):
    """定位 runtime-info.exe：配置/环境变量 → 运行进程 → 已知安装根下探 2 层。"""
    p = ((config or {}).get("umid_path") or "").strip()
    if p and os.path.isfile(p):
        return p
    p = os.environ.get("QODER_UMID_PATH", "").strip()
    if p and os.path.isfile(p):
        return p
    if _UMID_CACHE.get("path") and os.path.isfile(_UMID_CACHE["path"]):
        return _UMID_CACHE["path"]

    for d in _running_qoder_dirs():
        hit = _probe_umid_dir(d)
        if hit:
            _UMID_CACHE["path"] = hit
            return hit

    for root in UMID_SEARCH_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        hit = _probe_umid_dir(root)          # 根目录本身就是安装目录的情况
        if hit:
            _UMID_CACHE["path"] = hit
            return hit
        try:
            subs = os.listdir(root)
        except Exception:  # noqa: BLE001
            continue
        for sub in subs:
            base = os.path.join(root, sub)
            if not os.path.isdir(base):
                continue
            hit = _probe_umid_dir(base)
            if hit:
                _UMID_CACHE["path"] = hit
                return hit
            try:
                subs2 = os.listdir(base)
            except Exception:  # noqa: BLE001
                continue
            for sub2 in subs2:
                if not sub2.lower().startswith("qoder"):
                    continue
                hit = _probe_umid_dir(os.path.join(base, sub2))
                if hit:
                    _UMID_CACHE["path"] = hit
                    return hit
    return None


def get_machine_identity(account, config=None):
    """调用 runtime-info.exe 生成真实设备指纹；失败返回 None。
    用法（与客户端一致）：stdin 传 '{"account":"<账号>"} '，参数 <environment> --account-stdin，
    stdout 首行第一个空格前的 JSON 即 {machineToken, machineType, machineCode, ...}。"""
    exe = find_umid_binary(config)
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "x", "--account-stdin"],
            input=json.dumps({"account": account or ""}) + " ",
            capture_output=True, text=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        log("machine identity 生成失败（无法执行 %s）：%s" % (exe, e))
        return None
    out = (proc.stdout or "").strip()
    if not out:
        log("machine identity 生成失败：runtime-info.exe 无输出")
        return None
    try:
        obj = json.loads(out.split(" ")[0])
    except Exception:  # noqa: BLE001
        log("machine identity 解析失败：%s" % out[:200])
        return None
    if not obj.get("machineToken"):
        log("machine identity 缺少 machineToken：%s" % out[:200])
        return None
    _UMID_CACHE["identity"] = obj
    return obj


def cosy_headers(identity=None):
    """构造 Cosy-* 机器指纹请求头（与服务端校验一致）。"""
    h = {
        "User-Agent": COSY_UA,
        "Cosy-ClientType": COSY_CLIENT_TYPE,
        "Cosy-Version": COSY_VERSION,
        "Cosy-MachineOS": ("Windows" if WINDOWS
                           else "macOS" if SYSTEM == "darwin" else "Linux"),
    }
    if identity:
        code = identity.get("machineCode", "")
        h["Cosy-MachineId"] = code
        h["Cosy-MachineToken"] = identity.get("machineToken", "")
        h["Cosy-MachineCode"] = code
        h["Cosy-MachineType"] = identity.get("machineType", "")
    return h


# ---------------------------------------------------------------------------
# 鉴权：读取 / 刷新
# ---------------------------------------------------------------------------

def refresh_token(base, auth):
    """用 refreshToken 换新 token；成功返回新 auth dict，失败返回 None。"""
    if not auth.get("refreshToken"):
        return None
    url = base + REFRESH_ENDPOINT
    code, body = http_call(url, auth.get("token"), "POST",
                           {"refresh_token": auth["refreshToken"]})
    obj = parse_body(body)
    if code == 200 and isinstance(obj, dict) and obj.get("device_token"):
        return {
            "token": obj.get("device_token"),
            "refreshToken": obj.get("refresh_token") or auth.get("refreshToken"),
            "expireTime": to_ms(obj.get("expires_at") or 0),
            "refreshTokenExpireTime": to_ms(obj.get("refresh_token_expires_at") or 0),
        }
    log("token 刷新失败 HTTP %s：%s" % (code, body[:200]))
    return None


def pick_auth(config, cache_path, force_refresh):
    """返回 (auth_dict, base, source_str)；失败返回 (None, base, reason)。"""
    stores = decrypt_all_userinfos()
    cache = load_cache(cache_path)
    if not stores:
        # 无实时登录态（机器上没有任何 Qoder 数据目录 / 未登录）：
        # 回退到缓存 token（含后端的 refreshToken，可自助续期）。
        if cache and cache.get("token"):
            base = ((config.get("openapi_base")
                     or (base_for_dir(cache["store_dir"]) if cache.get("store_dir") else "")
                     or DEFAULT_OPENAPI_BASE_CN).strip().rstrip("/"))
            expired = to_ms(cache.get("expireTime")) and to_ms(cache.get("expireTime")) <= now_ms() + 60000
            if force_refresh or expired:
                new_auth = refresh_token(base, cache)
                if new_auth:
                    new_auth["store_dir"] = cache.get("store_dir")
                    new_auth["account"] = cache.get("account")
                    new_auth["known_campaign_ids"] = cache.get("known_campaign_ids") or []
                    save_cache(cache_path, new_auth)
                    return new_auth, base, "cache:%s(account=%s) + refreshed(HTTP 200)" % (
                        cache_path, new_auth.get("account"))
            if to_ms(cache.get("expireTime")) > now_ms() + 60000:
                return cache, base, "cache:%s(account=%s, no live store)" % (
                    cache_path, cache.get("account"))
        return None, "", ("qoder-store（未找到/解密失败）。已尝试：各数据目录的 "
                          "User/globalStorage/state.vscdb（旧客户端）与 auth.v1.dat（新版客户端）。"
                          "请确认已安装并登录过 Qoder 桌面端，或把有效 token 写入缓存/配置。")
    ui = stores[0]  # 最新的活跃登录态
    base = (config.get("openapi_base") or ui.get("_base") or DEFAULT_OPENAPI_BASE_CN).strip().rstrip("/")
    store_dir = ui.get("_store_dir", "")

    auth = {
        "token": ui.get("token"),
        "refreshToken": ui.get("refreshToken"),
        "expireTime": to_ms(ui.get("expireTime")),
        "refreshTokenExpireTime": to_ms(ui.get("refreshTokenExpireTime")),
        "store_dir": store_dir,
        "account": ui.get("name") or ui.get("id"),
    }
    src = "qoder-store:%s[%s](account=%s)" % (
        store_dir, ui.get("_source") or "unknown", auth.get("account"))

    # 命中同目录且未过期的缓存 token 则优先复用（避免频繁刷新）
    if cache and not force_refresh \
            and cache.get("store_dir") == store_dir and cache.get("token") \
            and to_ms(cache.get("expireTime")) > now_ms() + 60000:
        return cache, base, "cache:%s(account=%s)" % (cache_path, cache.get("account"))

    expired = auth["expireTime"] and auth["expireTime"] <= now_ms() + 60000
    if force_refresh or expired:
        new_auth = refresh_token(base, auth)
        if new_auth:
            new_auth["store_dir"] = store_dir
            new_auth["account"] = auth.get("account")
            save_cache(cache_path, new_auth)
            return new_auth, base, src + " + refreshed(HTTP 200)"
        # 刷新失败仍返回原 token，让后续接口试一次（有的域名旧 dt 仍可用）
        return auth, base, src + " (refresh failed, using stored token)"
    return auth, base, src


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------

def notify_desktop(title, message):
    try:
        if WINDOWS:
            ps = (
                '[Windows.UI.Notifications.ToastNotificationManager, '
                'Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
                '$t = [Windows.UI.Notifications.ToastNotificationManager]::'
                'GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
                '$lines = $t.GetElementsByTagName("text");'
                '$lines.Item(0).AppendChild($t.CreateTextNode("%s")) > $null;'
                '$lines.Item(1).AppendChild($t.CreateTextNode("%s")) > $null;'
                '$n = [Windows.UI.Notifications.ToastNotificationManager]::'
                'CreateToastNotifier("Qoder签到助手");$n.Show($t);'
            ) % (title.replace('"', "'"), message.replace('"', "'"))
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        elif SYSTEM == "darwin":
            subprocess.run(["osascript", "-e",
                            'display notification "%s" with title "%s"'
                            % (message.replace('"', "'"), title.replace('"', "'"))],
                           capture_output=True, timeout=20)
        elif SYSTEM == "linux":
            subprocess.run(["notify-send", title, message], capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


# 微信三通道推送已统一到 push_message 引擎（见 _push_unified），此处不再保留独立实现。


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def fetch_balance(base, token, headers=None):
    code, body = http_call(base + QUOTA_ENDPOINT, token, "GET", extra_headers=headers)
    if code != 200:
        return None
    q = parse_body(body)
    if not isinstance(q, dict):
        return None
    add_on = q.get("addOnQuota") or {}
    user_q = q.get("userQuota") or {}
    return {
        "resource_pack": add_on.get("remaining"),   # 资源包（签到 Credits 落在这里）
        "plan": user_q.get("remaining"),            # 套餐内
    }


def claim_activities(base, token, headers=None):
    """真实每日签到（Activity 体系）：eligibility 取可领活动，逐个 POST claim。
    CN1.0 会话（login_source=qodercn）服务端直接返回 data:[]，此函数为空操作。"""
    detail = {"activity_http": None, "activity_count": 0, "activity_claimable": 0,
              "activity_claimed": [], "activity_failed": []}
    code, body = http_call(base + ACTIVITY_ELIGIBILITY, token, "GET", extra_headers=headers)
    detail["activity_http"] = code
    if code != 200:
        detail["msg"] = "查询 Activity 失败（HTTP %s）：%s" % (code, body[:200])
        return [], detail
    obj = parse_body(body)
    if not isinstance(obj, dict):
        detail["msg"] = "Activity 响应异常：%s" % body[:200]
        return [], detail
    data = obj.get("data") or []
    acts = [a for a in data if isinstance(a, dict) and a.get("canClaim") and a.get("activityId")]
    detail["activity_count"] = len(data)
    detail["activity_claimable"] = len(acts)
    claimed = []
    for a in acts:
        aid = a["activityId"]
        url = base + (ACTIVITY_CLAIM_TMPL % urllib.parse.quote(str(aid)))
        c_code, c_body = http_call(url, token, "POST", {}, extra_headers=headers)
        c_obj = parse_body(c_body)
        if c_code == 200 and isinstance(c_obj, dict) and c_obj.get("success"):
            claimed.append({"activityId": aid})
        else:
            detail["activity_failed"].append({
                "activityId": aid, "http": c_code,
                "msg": (c_obj.get("msg") if isinstance(c_obj, dict) else c_body[:150])})
    detail["activity_claimed"] = claimed
    return claimed, detail


def run_checkin(config, check_only, no_notify, force_refresh):
    result = {"status": "error", "action": None, "detail": {}, "token_masked": None, "notify": None}
    cache_path = config.get("cache_path") or default_cache_path()

    auth, base, auth_src = pick_auth(config, cache_path, force_refresh)
    result["detail"]["token_source"] = auth_src
    result["detail"]["openapi_base"] = base
    if not auth or not auth.get("token"):
        result["detail"]["reason"] = "未取得有效 Qoder 登录态：%s" % auth_src
        return result
    token = auth["token"]
    result["token_masked"] = mask(token)
    result["detail"]["account"] = auth.get("account")

    # 机器指纹（Cosy-Machine*）：不带 → 服务端对 GET campaigns 返回空列表；伪造 → 新领取 503 风控
    identity = get_machine_identity(auth.get("account"), config)
    headers = cosy_headers(identity)
    result["detail"]["machine_identity"] = (
        "ok(type=%s,code=%s)" % (identity.get("machineType"), identity.get("machineCode"))
        if identity else "unavailable")
    if not identity:
        result["detail"]["machine_note"] = (
            "未能生成本机机器指纹（runtime-info.exe 未找到或执行失败）。服务端可能返回空活动列表"
            "或拒绝新领取；可在配置文件里用 umid_path 显式指定 runtime-info.exe 路径。")

    def flow(tok, hdrs):
        """完整一次签到流程（同时覆盖 Campaign 网页弹窗 与 Activity 真实每日签到）。"""
        detail = {}
        # 1) Campaign（每日主链路，网页弹窗「每天领 100 Credits」）
        code, body = http_call(base + CAMPAIGNS_ENDPOINT, tok, "GET", extra_headers=hdrs)
        detail["campaigns_http"] = code
        if code == 401:
            return "auth_failed", detail
        if code == 0:
            detail["msg"] = "网络请求失败：%s" % body[:200]
            return "network_error", detail
        if code != 200:
            detail["msg"] = "查询 Campaign 失败（HTTP %s）：%s" % (code, body[:200])
            return "status_failed", detail
        obj = parse_body(body)
        campaigns = (obj.get("campaigns") or []) if isinstance(obj, dict) else []
        # 可领判据：actionType=CLAIM_BENEFIT 且 claimStatus=CLAIMABLE（顶层 claimable 不可靠）
        claimable = [c for c in campaigns
                     if isinstance(c, dict) and c.get("campaignId")
                     and c.get("actionType") == CAMPAIGN_ACTION_CLAIM
                     and c.get("claimStatus") == CAMPAIGN_STATUS_CLAIMABLE]
        if not claimable:   # 兼容旧版结构
            legacy = [c for c in campaigns
                      if isinstance(c, dict) and c.get("campaignId") and c.get("claimable")]
            if legacy and bool((obj or {}).get("claimable")):
                claimable = legacy
        detail["campaign_count"] = len(campaigns)
        detail["campaign_claimable"] = len(claimable)
        detail["campaigns"] = [
            {"campaignId": c.get("campaignId"), "campaignKey": c.get("campaignKey"),
             "actionType": c.get("actionType"), "claimStatus": c.get("claimStatus"),
             "name": c.get("name") or c.get("title")}
            for c in campaigns if isinstance(c, dict)
        ][:10]
        detail["balance"] = fetch_balance(base, tok, hdrs)

        if check_only:
            return "skip_check_only", detail

        # 2) 领取 Campaign（每天是新的 campaignId，必须用当日列表里的 id）
        camp_claimed, camp_replayed, camp_failed = [], [], []
        for cid in [c["campaignId"] for c in claimable]:
            url = base + CAMPAIM_CLAIM_TMPL % urllib.parse.quote(str(cid))
            c_code, c_body = http_call(url, tok, "POST", {}, extra_headers=hdrs)
            c_obj = parse_body(c_body)
            if c_code == 200 and isinstance(c_obj, dict) and c_obj.get("status") == "CLAIMED":
                rec = {"campaignId": cid,
                       "campaignKey": c_obj.get("campaignKey"),
                       "replayed": bool(c_obj.get("replayed")),
                       "claimedAt": c_obj.get("claimedAt")}
                (camp_replayed if rec["replayed"] else camp_claimed).append(rec)
            else:
                camp_failed.append({"campaignId": cid, "http": c_code,
                                    "msg": (c_obj.get("errorMessage") or c_obj.get("msg")
                                            if isinstance(c_obj, dict) else c_body[:150])})

        # 3) Activity（真实每日签到；CN1.0 会话服务端返回空 data:[]，本步为空操作）
        act_claimed, act_detail = claim_activities(base, tok, hdrs)
        detail["activity"] = act_detail

        # 记录已知 campaign id（供后续排查），并落盘刷新后的 token
        merged = dict(load_cache(cache_path) or {})
        known = sorted(set([c["campaignId"] for c in claimable])
                       | set(merged.get("known_campaign_ids") or []))
        merged["known_campaign_ids"] = known
        merged.setdefault("store_dir", auth.get("store_dir"))
        merged.setdefault("account", auth.get("account"))
        if auth.get("token"):
            merged["token"] = auth["token"]
            merged["refreshToken"] = auth.get("refreshToken")
            merged["expireTime"] = auth.get("expireTime")
        save_cache(cache_path, merged)

        detail["campaign_claimed"] = camp_claimed
        detail["campaign_replayed"] = camp_replayed
        detail["campaign_failed"] = camp_failed

        if camp_claimed or act_claimed:
            detail["claimed"] = camp_claimed + [{"activityId": a["activityId"]} for a in act_claimed]
            detail["msg"] = "签到成功，新领取 %d 个（Campaign %d + Activity %d）。" % (
                len(camp_claimed) + len(act_claimed), len(camp_claimed), len(act_claimed))
            return "claimed", detail
        if camp_replayed:
            at = "; ".join(filter(None, (r.get("claimedAt") for r in camp_replayed)))
            detail["replayed"] = camp_replayed
            detail["msg"] = ("今日 Campaign 已领取过（服务端幂等重放，领取时间 %s），不会重复发放。"
                             % (at or "见 claimedAt"))
            return "already_claimed", detail
        if camp_failed:
            detail["msg"] = "领取失败：%s" % json.dumps(camp_failed, ensure_ascii=False)[:300]
            if any("RISK" in str(f.get("msg", "")).upper() for f in camp_failed):
                detail["msg"] += "（疑似风控拦截：机器指纹无效或缺失，请确认 runtime-info.exe 可用）"
            return "claim_failed", detail
        # 列表里没有「可领」，但存在当日已领取的 CLAIM_BENEFIT → 明确报告「今日已领取」
        claimed_before = [c for c in campaigns
                          if isinstance(c, dict) and c.get("actionType") == CAMPAIGN_ACTION_CLAIM
                          and c.get("claimStatus") == "CLAIMED"]
        if claimed_before:
            detail["campaign_claimed_before"] = [
                {"campaignId": c.get("campaignId"), "campaignKey": c.get("campaignKey"),
                 "claimedAt": c.get("claimedAt") or c.get("grantedAt") or c.get("claimTime")}
                for c in claimed_before]
            detail["msg"] = "今日奖励已领取（campaignKey %s），不会重复发放。" % (
                ", ".join(filter(None, (str(c.get("campaignKey")) for c in claimed_before))) or "见 campaigns")
            return "already_claimed", detail
        # 两套体系都无可领
        if act_detail.get("activity_count"):
            detail["msg"] = ("Campaign 列表为空；Activity 体系返回 %d 个活动但无 claimable 项，"
                             "以服务端为准，今日暂无可领。" % act_detail["activity_count"])
        else:
            detail["msg"] = ("今日无可领：Campaign 列表为空（活动未开始/已领取），"
                             "Activity 真实每日签到体系对当前 CN1.0 会话被服务端禁用（data:[]）。")
        return "skip_no_claimable", detail

    action, extra = flow(token, headers)

    # 401 → 刷新 token + 重生成机器指纹，各重试一次
    if action == "auth_failed":
        new_auth = refresh_token(base, auth)
        if new_auth:
            new_auth["store_dir"] = auth.get("store_dir")
            new_auth["account"] = auth.get("account")
            save_cache(cache_path, new_auth)
            result["detail"]["token_source"] += " + refreshed(HTTP 200)"
            result["token_masked"] = mask(new_auth["token"])
            identity = get_machine_identity(auth.get("account"), config)
            headers = cosy_headers(identity)
            action, extra = flow(new_auth["token"], headers)
        else:
            result["status"] = "error"
            result["action"] = "auth_failed"
            result["detail"].update(extra)
            result["detail"]["msg"] = ("接口返回 401 且刷新 token 失败。"
                                       "请在 Qoder 客户端重新登录一次后重跑。")
            _finalize(result, config, no_notify)
            return result

    result["detail"].update(extra)
    if action in ("claimed", "already_claimed", "skip_no_claimable", "skip_check_only"):
        result["status"] = "ok"
    result["action"] = action
    if action == "skip_check_only":
        cc = extra.get("campaign_claimable", 0)
        ac = (extra.get("activity") or {}).get("activity_claimable", 0)
        result["detail"]["msg"] = "仅查询完成（未领取），Campaign 可领 %d 个、Activity 可领 %d 个。" % (cc, ac)

    _finalize(result, config, no_notify)
    return result


def _build_push_config(config):
    """合并统一通知配置与 qoder 内嵌旧结构 notify 段，返回归一化前的原始 dict。

    优先级（按渠道合并，新结构优先于旧字段）：
      1) 用户指定的 notify_config_path 文件（与 WorkBuddy 签到助手共享）
      2) 默认 ~/.workbuddy/scripts/notify_config.json
      3) qoder 自身 config["notify"] 旧结构段（wecom_webhook/pushplus_token/bark_url）作兜底补齐
    """
    cfg_path = config.get("notify_config_path")
    unified = {}
    if pm is not None:
        try:
            unified = pm.load_raw_config(cfg_path) or {}
        except Exception:  # noqa: BLE001
            unified = {}
    qn = config.get("notify") or {}
    merged = dict(unified)
    ch = merged.get("channels")
    if not isinstance(ch, dict):
        ch = {}
        merged["channels"] = ch
    if "wecom" not in ch and qn.get("wecom_webhook"):
        ch["wecom"] = {"webhook": qn["wecom_webhook"]}
    if "wechat" not in ch and qn.get("pushplus_token"):
        ch["wechat"] = {"pushplus_token": qn["pushplus_token"]}
    if "bark" not in ch and qn.get("bark_url"):
        if pm is not None:
            s, k = pm._split_bark_url(qn["bark_url"])
            if s and k:
                ch["bark"] = {"server": s, "device_key": k}
    if "enabled" not in merged and "enabled" in qn:
        merged["enabled"] = qn["enabled"]
    if "success_notify" not in merged and "success_notify" in qn:
        merged["success_notify"] = qn["success_notify"]
    return merged


def _push_unified(title, msg, merged):
    """走 push_message 统一引擎发送；返回聚合结果（含渠道名与状态，无密钥值）。"""
    if pm is None:
        return {"enabled": False, "skipped": "push_message 模块不可用"}
    if merged.get("enabled") is False:
        return {"enabled": False, "skipped": "enabled=false"}
    configured = pm.normalize_channels(merged)
    if not configured:
        return {"enabled": True, "skipped": "未配置任何渠道"}
    msgdict = {"title": title, "content": msg, "content_type": "markdown"}
    results = []
    for ch in sorted(configured.keys()):
        if ch in pm.PAID_CHANNELS:  # 付费渠道（sms）默认不发，需显式确认
            results.append({"channel": ch, "status": "skipped",
                            "detail": "付费渠道，未确认不发送"})
            continue
        try:
            results.append(pm.send_one(ch, configured[ch], msgdict, 10))
        except Exception as exc:  # noqa: BLE001
            results.append({"channel": ch, "status": "failed", "detail": str(exc)[:500]})
    ok_all = all(r.get("status") == "success" for r in results)
    return {"ok": ok_all, "results": results}


def _finalize(result, config, no_notify):
    title = {
        "claimed": "Qoder 签到成功",
        "already_claimed": "Qoder 今日已签到",
        "skip_no_claimable": "Qoder 今日无需签到",
        "skip_check_only": "Qoder 签到查询",
        "auth_failed": "Qoder 登录态失效",
        "network_error": "Qoder 网络异常",
        "status_failed": "Qoder 查询失败",
        "claim_failed": "Qoder 领取失败",
    }.get(result.get("action"), "Qoder 签到")
    msg = result.get("detail", {}).get("msg", "")
    bal = result.get("detail", {}).get("balance")
    if isinstance(bal, dict):
        parts = []
        if bal.get("resource_pack") is not None:
            parts.append("资源包 %s" % bal["resource_pack"])
        if bal.get("plan") is not None:
            parts.append("套餐内 %s" % bal["plan"])
        if parts:
            msg += " 余额：" + "，".join(parts)
    # 1) 桌面通知（默认开启，受 --no-notify 抑制）
    notify_desktop(title, msg)
    # 2) 统一消息通知渠道（与 WorkBuddy 签到助手共享 ~/.workbuddy/scripts/notify_config.json）
    if no_notify:
        result["notify"] = {"enabled": False, "skipped": True}
        return
    merged = _build_push_config(config)
    success = result.get("status") == "ok"
    if success and not merged.get("success_notify", False):
        result["notify"] = {"enabled": True, "skipped": "success_notify=false"}
        return
    result["notify"] = _push_unified(title, msg, merged)


def main():
    ap = argparse.ArgumentParser(description="Qoder 每日签到助手")
    ap.add_argument("--check-only", action="store_true", help="仅查询可领活动，不领取")
    ap.add_argument("--no-notify", action="store_true", help="跳过全部推送")
    ap.add_argument("--force-refresh", action="store_true", help="强制刷新 token")
    ap.add_argument("--config", default=None, help="指定配置文件路径")
    args = ap.parse_args()

    config, cfg_path = load_config(args.config)
    if not os.path.isfile(cfg_path):
        log("提示：未找到配置文件 %s，将使用内置默认值。" % cfg_path)

    result = run_checkin(config, args.check_only, args.no_notify, args.force_refresh)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
