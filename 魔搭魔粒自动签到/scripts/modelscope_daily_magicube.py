#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔搭每日登录领魔粒（纯 requests，无 Playwright，无浏览器依赖）。

== 已扒实的机制 ==
- 魔粒余额接口：GET https://www.modelscope.cn/openapi/v1/magicubes/balance （认网站 cookie，无需 Bearer Token）
- 「当日登录发魔粒」是后端会话副作用，没有独立的 claim / checkin / sign 领取接口
  （OpenAPI 规范仅暴露 balance 查询；前端 chunk 全文无领取调用；盲猜领取接口全 404；
   官方文案 "登录即享 200 魔粒" 是登录奖励而非点击动作）
- 发放规则：每日首次以已登录会话访问网站即 +200（含绑阿里云 +50、社区互动等叠加），有"今日已发"去重
- 因此脚本逻辑 = 带有效 cookie 模拟当日浏览 → 后端自动发 → 查余额前后对比

== 用法（最简） ==
  # 环境变量（任意格式：JSON / 纯文本都行）
  export MS_COOKIE='cookie2=abc; _tb_token_=xyz'
  python modelscope_daily_magicube.py

  # 直接粘 cookie 字符串
  python modelscope_daily_magicube.py -c "cookie2=abc; _tb_token_=xyz"

  # 传文件路径（.json/.txt，自动判断；Cookie-Editor 导出原文亦可）
  python modelscope_daily_magicube.py -c cookies.json

  # 配合系统定时任务（cron / Windows 计划任务 / WorkBuddy 自动化）：每天跑一次即可拿到 200 魔粒
"""
import os, json, sys, time, argparse, re, requests
from datetime import datetime
from pathlib import Path

_LOGGED = False


def ensure_output(log_name="magicube_daily.log"):
    """保证输出可见、可落盘（幂等，可重复调用）。

    - pythonw.exe（无控制台）：sys.stdout/stderr 为 None，print 会被静默丢弃
      → 改写到脚本同目录的日志文件，供计划任务/后台运行排查。
    - cmd(GBK) 终端、或 >> 重定向到文件：统一 UTF-8，避免 emoji/中文 UnicodeEncodeError。
    """
    global _LOGGED
    if _LOGGED:
        return
    _LOGGED = True
    if sys.stdout is None or sys.stderr is None:
        f = open(Path(__file__).with_name(log_name), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
        return
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


ensure_output()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
BASE = "https://www.modelscope.cn"
BALANCE = BASE + "/openapi/v1/magicubes/balance"
LOGIN_INFO = BASE + "/api/v1/users/login/info"
RULES = BASE + "/openapi/v1/magicubes/earn/rules"
_SEP = "─" * 58


# ---------- 日志（各入口共用：运行分隔 + 归因 + 汇总） ----------

def log_banner(title):
    """每次运行打一条带时间戳的分隔标题——日志累积后能一眼分清各次运行"""
    print(f"\n{_SEP}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {title}\n{_SEP}")


def log_footer(status, detail=""):
    """运行结束汇总：状态 + 关键数字 + 耗时 + 时间戳"""
    print(f"[结束] {status}" + (f" · {detail}" if detail else "")
          + f" · {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(_SEP)


def read_rules(s):
    """魔粒规则表 -> {rule_key: rule}；失败返回 {}（不阻断主流程）"""
    try:
        d = s.get(RULES, timeout=20).json().get("data", [])
        return {r["rule_key"]: r for r in d if r.get("rule_key")}
    except Exception:
        return {}


def login_earned(s):
    """登录类规则今日已得 (daily_active, aliyun_bindlogin)；规则表读不到返回 None。

    这是**收益的权威口径**：累计发放额，不受 24h 过期影响。
    余额会因短期魔粒到期而下降，把它当收益会算出负数（实测 2026-09-14 08:00：
    前一日 08:00 发的魔粒恰好在本次运行中过期，余额被拉低 53，实际收益 +40 却显示 -13）。

    同时，它也能区分「打卡后没涨」的两种原因：今天已经发过（正常）／登录态其实没建立（异常）。
    """
    r = read_rules(s)
    if not r:
        return None
    return (r.get("daily_active", {}).get("today_earned_amount", 0),
            r.get("aliyun_bindlogin", {}).get("today_earned_amount", 0))


AUTO_RULES = ("daily_active", "aliyun_bindlogin", "interaction_like")


def auto_progress(s):
    """今日「可自动化部分」进度 (已得, 上限)。

    上限 = 已得 + 剩余次数×单次，动态算出来的：没绑阿里云时那条规则贡献 0，
    上限自然降到 240，不会误报「240/290 未达标」。拿满时 got == cap。
    """
    got = cap = 0
    for k, x in read_rules(s).items():
        if k in AUTO_RULES:
            got += x.get("today_earned_amount", 0)
            cap += x.get("today_earned_amount", 0) + x.get("today_remain", 0) * x.get("amount", 0)
    return got, cap


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _scrub(d):
    """剥掉 name/value 里的控制字符——HTTP 头不允许换行，手动粘贴的脏数据常见"""
    return {_CTRL.sub("", str(k)): _CTRL.sub("", str(v))
            for k, v in d.items() if _CTRL.sub("", str(k))}


def clean_cookie_input(raw):
    """把任意来源的 cookie 清洗成 {name: value} 字典。

    兼容以下来源形态（用户从哪粘都行）：
      - Cookie-Editor 导出的 JSON 数组  [{"name","value"}, ...]
      - JSON 对象                       {"name": "value", ...}
      - DevTools 手动复制的纯文本        "name=value; name2=value2; ..."
      - 浏览器 document.cookie 直接粘    （同上，按 ; 或换行切）
    自动处理：Cookie: 前缀、首尾引号、空白、换行、空值、同名去重（后者覆盖）。
    """
    if not raw:
        return {}
    raw = raw.strip()
    if not raw:
        return {}

    # 0) Netscape 格式（Cookie-Editor 导出 cookie.txt / curl -c 落盘）
    if "Netscape HTTP Cookie File" in raw or raw.lstrip().startswith("#HttpOnly"):
        out = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # 跳过纯注释行（# Netscape / # https://...），但保留 #HttpOnly_ 开头的真实 cookie 行
            if line.startswith("#") and not line.startswith("#HttpOnly"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name, value = parts[-2], parts[-1]
            elif "=" in line:
                name, _, value = line.partition("=")
            else:
                continue
            name = name.strip().lstrip("#").strip('"').strip("'")
            value = value.strip().strip('"').strip("'")
            if name:
                out[name] = value
        if out:
            return _scrub(out)

    # 1) 先试 JSON（数组 / 对象）；strict=False 容忍值里的控制字符（手动粘贴常见坑）
    try:
        obj = json.loads(raw, strict=False)
        if isinstance(obj, list):
            out = {c["name"]: c.get("value", "")
                   for c in obj if isinstance(c, dict) and c.get("name")}
            return _scrub({k: v for k, v in out.items() if k})
        if isinstance(obj, dict):
            return _scrub({k: v for k, v in obj.items() if k and v})
    except (ValueError, TypeError) as e:
        # 看着像 JSON 却解析失败 → 明确报错，避免掉进纯文本分支解析出垃圾
        if raw.lstrip().startswith(("[", "{")):
            raise ValueError(f"cookie 内容疑似 JSON 但解析失败: {e}") from e

    # 2) 纯文本：去掉可能的 "Cookie:" HTTP 头前缀
    text = raw
    m = re.match(r"^[Cc]ookie\s*:\s*", text)
    if m:
        text = text[m.end():]

    # 3) 按分号 / 换行切，逐条 name=value（value 内可能含 =，用首个 = 拆分）
    out = {}
    for part in re.split(r"[;\n]", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip().strip('"').strip("'")
        value = value.strip().strip('"').strip("'")
        if name:
            out[name] = value
    return _scrub(out)


def load_cookies(cookie_arg=None):
    raw = cookie_arg or os.environ.get("MS_COOKIE")
    if not raw:
        sys.exit("缺少 cookie：传 -c/--cookie（字符串或文件路径），或设置环境变量 MS_COOKIE")
    # 路径则读文件，否则当 cookie 字符串直接清洗（自动判断）
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            raw = f.read()
    try:
        return clean_cookie_input(raw)
    except ValueError as e:
        sys.exit(f"[失败] {e}\n建议: 用 Cookie-Editor 插件重新导出(Export -> JSON)，别手动复制粘贴。")


def get_balance(s):
    r = s.get(BALANCE, timeout=20)
    if r.status_code == 401:
        sys.exit("[失败] cookie 无效/已失效（401）。请重新登录 modelscope.cn，"
                 "用 Cookie-Editor 导出 JSON 后立刻再跑。")
    r.raise_for_status()
    j = r.json()
    d = j.get("data") or j.get("Data") or {}
    return d.get("total_balance"), d.get("available_balance")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--cookie",
                    help="cookie 字符串，或 cookie 文件路径（自动判断；任意格式都行）")
    args = ap.parse_args()

    t0 = time.time()
    log_banner("魔搭魔粒 · 登录打卡（模式 B / 纯 requests）")

    s = requests.Session()
    s.cookies.update(load_cookies(args.cookie))
    s.headers.update({"User-Agent": UA, "Referer": BASE,
                      "Accept": "application/json, text/plain, */*"})

    # 1) 校验登录态（login/info 未登录时返回 {"Code":...,"Message":"禁止访问"}，无 Data 键）
    try:
        j = s.get(LOGIN_INFO, timeout=20).json()
        data = j.get("Data") or j.get("data") or {}
        if data and data.get("NickName"):
            print(f"[登录] 账号 {data['NickName']} · 登录态有效")
        else:
            msg = j.get("Message") or j.get("message") or j
            print(f"[登录] ⚠️ 校验未通过（{msg}），仍尝试打卡")
    except Exception as e:
        print(f"[登录] ⚠️ 校验异常: {e}，仍尝试打卡")

    # 2) 打卡前：今日登录类已得
    p0 = login_earned(s)
    print(f"[打卡] 前 今日已得 {sum(p0) if p0 else '?'}"
          + (f"（登录 {p0[0]} + 阿里云 {p0[1]}）" if p0 else "（规则表不可用）"))

    # 3) 模拟当日登录浏览（触发后端每日发放）
    codes = [s.get(u, timeout=30, allow_redirects=True).status_code
             for u in (BASE + "/", LOGIN_INFO, BASE + "/magicube/usage")]
    print(f"[打卡] 已访问 首页 + 登录信息 + 用量页 -> HTTP {'/'.join(map(str, codes))}")

    # 4) 打卡后：收益以规则表「今日已得」为准（余额会被 24h 过期拉低，不能当收益）
    p1 = login_earned(s)
    gain = 0
    if p0 is not None and p1 is not None:
        gain = sum(p1) - sum(p0)
        print(f"[打卡] 后 今日已得 {sum(p1)}（本次 {gain:+d}）")
        if gain > 0:
            print(f"[打卡] ✅ 今日登录魔粒已发放（登录 {p1[0]} + 阿里云 {p1[1]}）")
            status = "成功"
        elif sum(p1) > 0:
            print("[打卡] ℹ️ 今日已发过，无新增属正常")
            status = "已发过"
        else:
            print("[打卡] ⚠️ 今日未发放且累计为 0 —— 登录态可能未真正建立")
            status = "异常"
    else:
        print("[打卡] ⚠️ 规则表不可用，无法核算收益")
        status = "异常"

    got, cap = auto_progress(s)   # 余额只在结尾查这一次
    log_footer(status, f"本次 {gain:+d} 粒 · 今日累计 {got}/{cap} 粒"
               + (" ✅" if cap and got >= cap else "")
               + f" · 余额 {get_balance(s)[1]} · 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
