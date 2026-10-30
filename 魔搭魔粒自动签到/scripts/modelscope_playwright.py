#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔搭每日魔粒打卡 —— Playwright + 系统 Edge（独立 profile，免导出免清洗）

原理：
  - 用系统 Edge(channel=msedge) + 独立 user-data-dir，cookie 由浏览器自己维护：
    自动续期、解密好、无脏字符 → 完全绕开「手动导出 cookies.json + 程序清洗」。
  - 魔粒「当日登录发」是后端会话副作用：带登录态访问一次首页即触发。

首次使用（建 profile，只需一次）：
  python modelscope_playwright.py
    → 弹出 Edge 窗口，手动登录 modelscope.cn 一次
    → 检测到登录态后自动完成当日打卡并退出，profile 保存在 ~/.modelscope_magicube_profile

之后日常打卡（可挂定时任务）：
  python modelscope_playwright.py --headless

参数：
  --headless      无头模式（需 profile 已建立登录态）
  --wait-login N  首次登录等待上限秒数（默认 180）
  --no-like       跳过点赞刷分（默认打卡后会刷满当日额度，额度由服务端下发）
  --model M       刷分用的模型（默认 Qwen/Qwen3.8-Flash-Next）
  --like-delay N  点赞每步间隔秒数（默认 1.5）
  -v, --verbose   逐轮查询并打印点赞明细（默认不查，只输出一行汇总）

日志：每次运行以带时间戳的分隔线开头、以「结束」汇总行收尾；
      收益/达标一律看规则表，余额只在结尾打一次；失败/异常始终单独打印。
"""
import os, sys, time, json, re, argparse, requests
from pathlib import Path
from playwright.sync_api import sync_playwright
from modelscope_daily_magicube import (ensure_output, log_banner, log_footer,
                                       login_earned, auto_progress)
from modelscope_like_farm import make_session, farm, login_name

# 输出引导：pythonw(无控制台) 下改写日志文件；cmd/重定向下统一 UTF-8
ensure_output()

BASE = "https://www.modelscope.cn"
BALANCE = BASE + "/openapi/v1/magicubes/balance"
PROFILE = str(Path.home() / ".modelscope_magicube_profile")
# 存在任一即视为已登录（魔搭会话体系，实测 2026-09）
SESSION_KEYS = ("m_session_id", "csrf_session", "h_uid")


def ms_cookies(ctx):
    cs = {}
    for c in ctx.cookies():
        dom = (c.get("domain") or "").lower()
        if "modelscope.cn" in dom and c.get("value"):
            cs[c["name"]] = c["value"]
    return cs


def logged_in(cs):
    return any(cs.get(k) for k in SESSION_KEYS)


def seed_cookies(ctx, path):
    """把 Cookie-Editor 导出的 cookies.json 注入 profile（用于 profile 失登录态时一键刷新）"""
    CTRL = re.compile(r'[\x00-\x1f\x7f]')
    raw = open(path, encoding="utf-8").read()
    pw = []
    try:
        arr = json.loads(raw, strict=False)
    except Exception:
        arr = None
    if isinstance(arr, list):
        for c in arr:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            ss = (c.get("sameSite") or "no_restriction")
            ss = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax",
                  "strict": "Strict", "None": "None"}.get(ss, "Lax")
            pw.append({
                "name": CTRL.sub("", c["name"]), "value": CTRL.sub("", c.get("value", "")),
                "domain": c.get("domain", ".modelscope.cn"), "path": c.get("path", "/"),
                "expires": int(c["expirationDate"]) if c.get("expirationDate") else -1,
                "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
                "sameSite": ss,
            })
    else:
        from modelscope_daily_magicube import clean_cookie_input
        for k, v in clean_cookie_input(raw).items():
            pw.append({"name": k, "value": v, "domain": ".modelscope.cn", "path": "/",
                       "expires": -1, "httpOnly": False, "secure": True, "sameSite": "None"})
    if pw:
        ctx.add_cookies(pw)
    return len(pw)


def balance(cs):
    s = requests.Session()
    s.cookies.update(cs)
    r = s.get(BALANCE, timeout=20)
    if r.status_code == 401:
        return None, "cookie 失效(401)"
    j = r.json()
    d = j.get("data") or j.get("Data") or {}
    return d.get("total_balance"), d.get("available_balance")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="无头模式（需 profile 已登录）")
    ap.add_argument("--wait-login", type=int, default=180, help="首次登录等待秒数")
    ap.add_argument("--no-like", action="store_true", help="跳过点赞刷分")
    ap.add_argument("--model", default="Qwen/Qwen3.8-Flash-Next", help="刷分用的模型")
    ap.add_argument("--like-delay", type=float, default=1.5, help="点赞每步间隔秒数")
    ap.add_argument("--import-cookie", metavar="FILE",
                    help="从 cookies.json 注入登录态到 profile（profile 失登录态时用）")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐轮打印点赞明细（默认只输出汇总）")
    args = ap.parse_args()

    t0 = time.time()
    log_banner(f"魔搭魔粒 · 模式 A · {'无头' if args.headless else '窗口'}"
               f" · 点赞={'关' if args.no_like else '开'}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, channel="msedge", headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if args.import_cookie:
            n = seed_cookies(ctx, args.import_cookie)
            print(f"[导入] 已从 {args.import_cookie} 注入 {n} 个 cookie 到 profile")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        if not args.headless:
            time.sleep(3)  # 等页面与 cookie 就位
        cs = ms_cookies(ctx)

        if not logged_in(cs):
            if args.headless:
                print("[登录] ✗ 无头模式但 profile 未登录，需先窗口模式手动登录一次。")
                ctx.close()
                log_footer("失败", "profile 无登录态")
                return 1
            print(f"[登录] 首次使用：请在弹出的 Edge 窗口登录 modelscope.cn"
                  f"（最长等 {args.wait_login}s，登录后自动继续）…")
            deadline = time.time() + args.wait_login
            while time.time() < deadline:
                time.sleep(3)
                try:
                    page.goto(BASE, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                cs = ms_cookies(ctx)
                if logged_in(cs):
                    break
            if not logged_in(cs):
                print("[登录] ✗ 等待登录超时，请重跑。")
                ctx.close()
                log_footer("失败", "等待登录超时")
                return 1

        s = make_session(cs)
        who = login_name(s)
        if not who:
            print("[登录] ✗ 登录态已失效（cookie 在但服务端不认），需重新登录或 --import-cookie")
            ctx.close()
            log_footer("失败", "登录态失效")
            return 1
        print(f"[登录] 账号 {who} · 登录态有效（profile 已保持，之后免登录）")

        p0 = login_earned(s)   # 今日登录类已得 (daily_active, aliyun)；None=规则表不可用
        print(f"[打卡] 前 今日已得 {sum(p0) if p0 else '?'}"
              + (f"（登录 {p0[0]} + 阿里云 {p0[1]}）" if p0 else "（规则表不可用）"))

        ok = 0
        for u in (BASE + "/", BASE + "/magicube/usage"):
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)
                ok += 1
            except Exception as e:
                print(f"[打卡] ✗ 访问 {u} 异常: {e}")
        print(f"[打卡] 已访问 首页 + 用量页（{ok}/2 成功），触发每日登录发放")

        cs = ms_cookies(ctx)
        ctx.close()

        # 收益一律以规则表「今日已得」为准：余额受 24h 过期干扰，不作为收益口径
        p1 = login_earned(make_session(cs))
        lgain = 0
        if p0 is not None and p1 is not None:
            lgain = sum(p1) - sum(p0)
            print(f"[打卡] 后 今日已得 {sum(p1)}（本次 {lgain:+d}）")
            if lgain > 0:
                print(f"[打卡] ✅ 今日登录魔粒已发放（登录 {p1[0]} + 阿里云 {p1[1]}）")
            elif sum(p1) > 0:
                print("[打卡] ℹ️ 今日已发过，无新增属正常")
            else:
                print("[打卡] ⚠️ 今日未发放且累计为 0 —— 登录态可能未真正建立")
        else:
            print("[打卡] ⚠️ 规则表不可用，无法核算登录收益")

        # ---- 接 like_farm：用同一登录态刷点赞魔粒（额度从规则表读，勿硬编码）----
        like_gain, like_err = 0, ""
        if args.no_like:
            print("[点赞] 已按 --no-like 跳过")
        else:
            s = make_session(cs)
            if login_name(s):
                st = farm(s, model=args.model, delay=args.like_delay, verbose=args.verbose)
                like_gain, like_err = st["gain"], st["err"]
            else:
                print("[点赞] ✗ 登录态不可用，跳过刷分")

        # 余额只在结尾查这一次；今日累计/上限取自规则表，不受过期干扰
        got, cap = auto_progress(make_session(cs))
        total = lgain + like_gain
        log_footer("异常" if like_err else "成功",
                   f"本次收益 {total:+d} 粒（登录 {lgain:+d} / 点赞 {like_gain:+d}）"
                   f" · 今日累计 {got}/{cap} 粒" + (" ✅" if cap and got >= cap else "")
                   + f" · 余额 {balance(cs)[1]} · 耗时 {time.time() - t0:.0f}s")
        return 0


if __name__ == "__main__":
    sys.exit(main())
