# -*- coding: utf-8 -*-
"""
魔搭「点赞刷魔粒」脚本（纯 requests，无需浏览器）

原理（2026-09-10 实测，2026-10-23 复核）：
  规则 interaction_like「收藏/喜欢」= 对其他用户发布点击收藏，每次 +2 魔粒。
  每日上限由服务端下发，**不要硬编码**：2026-09-10 是 20 次，2026-10-21 起改成 10 次（=20 粒）。
  接口：PUT /api/v1/models/{owner}/{name}/stars    收藏   -> +2
        PUT /api/v1/models/{owner}/{name}/unstar   取消收藏 -> 不扣分、不减次数
  因此对同一模型反复 star -> unstar -> star，即可把当日额度刷满。

  ⚠️ 状态残留是这套机制唯一的死穴：star 之后若进程崩溃/超时没走到 unstar，
  模型会永久停在「已收藏」。此后每次 star 都返回 400「重复操作」，脚本若一见失败就
  break，就再也不会去 unstar —— 自锁死循环。2026-10-15 ~ 10-23 连续 9 天零收益即此故。
  对策：每轮 unstar 放进 finally 保证执行；遇重复操作错误先 unstar 再重试一次。

用法：
  python modelscope_like_farm.py -c cookies.json                 # 刷满当日剩余额度
  python modelscope_like_farm.py -c cookies.json --rounds 5      # 只刷 5 轮
  python modelscope_like_farm.py -c cookies.json --model Qwen/Qwen3.8-Flash-Next
  set MS_COOKIE=...  &  python modelscope_like_farm.py           # 环境变量喂 cookie

  -c/--cookie 既可传文件路径，也可直接传 cookie 字符串（自动识别）。
"""
import os, sys, time, argparse, requests
from modelscope_daily_magicube import (clean_cookie_input, ensure_output,
                                       log_banner, log_footer, auto_progress)

# 输出引导：pythonw(无控制台) 下改写日志文件；cmd/重定向下统一 UTF-8
ensure_output()

BASE = "https://www.modelscope.cn"
DEFAULT_MODEL = "Qwen/Qwen3.8-Flash-Next"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def load_cookie(arg=None):
    raw = arg or os.environ.get("MS_COOKIE")
    if raw and os.path.exists(raw):
        raw = open(raw, encoding="utf-8").read()
    if not raw:
        sys.exit("缺少 cookie：用 -c <文件或字符串>，或设置环境变量 MS_COOKIE")
    cj = clean_cookie_input(raw)
    if not cj:
        sys.exit("cookie 解析为空，请检查格式")
    return cj


def make_session(cookie):
    s = requests.Session()
    s.cookies.update(cookie)
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE,
        "Content-Type": "application/json",
    })
    return s


def rule_state(s, model):
    """点赞规则今日进度——1 次请求。

    上限/单次一律从服务端读，不硬编码：官方调整过额度（20 次 → 10 次），
    写死的 20/40 会让日志和达标判断一起失真。
    返回 {"used","remain","earned","cap","amount"}；读不到时给保守值。
    """
    h = {"Referer": f"{BASE}/models/{model}"}
    rules = s.get(f"{BASE}/openapi/v1/magicubes/earn/rules", headers=h, timeout=20).json().get("data", [])
    lk = next((x for x in rules if x.get("rule_key") == "interaction_like"), {})
    used = lk.get("today_used", 0)
    return {
        "used": used,
        "remain": lk.get("today_remain", 0),
        "earned": lk.get("today_earned_amount", 0),
        "amount": lk.get("amount", 2),
        "cap": lk.get("daily_cap", used + lk.get("today_remain", 0)),
    }


def balance(s, model):
    """魔粒余额——只在结尾展示用，1 次请求"""
    h = {"Referer": f"{BASE}/models/{model}"}
    d = s.get(f"{BASE}/openapi/v1/magicubes/balance", headers=h, timeout=20).json().get("data", {})
    return d.get("available_balance", d.get("total_balance"))


def star(s, model, on=True):
    act = "stars" if on else "unstar"
    h = {"Referer": f"{BASE}/models/{model}"}
    return s.put(f"{BASE}/api/v1/models/{model}/{act}", headers=h, json={}, timeout=30)


REPEAT_CODE = 10010901002   # 服务端：「重复操作，请稍后重试」= 该模型已处于收藏态


def err_code(r):
    try:
        return r.json().get("Code")
    except Exception:
        return None


def ensure_unstarred(s, model):
    """把模型恢复到「未收藏」—— 幂等，不扣次数、不减额度。

    这是自愈的关键：任何一次 star 之后若进程崩溃或超时（2026-10-14 实测 ReadTimeout），
    服务端已记下收藏，本地却没走到 unstar，模型就永久卡在 starred。
    之后每天首次 star 都 400「重复操作」，光看错误码根本不知道是状态脏了。
    """
    try:
        return star(s, model, False).status_code == 200
    except requests.RequestException:
        return False


def login_name(s):
    """校验登录态，返回昵称；无效返回 None"""
    try:
        j = s.get(f"{BASE}/api/v1/users/login/info", timeout=20).json()
        d = j.get("Data") or {}
        return d.get("NickName") or d.get("Name")
    except Exception:
        return None


def do_round(s, model, i):
    """一轮 star(+2) → unstar(复位)。返回 (ok, err)。

    unstar 放在 finally 里：即使 star 失败、超时、抛异常，也必须把状态清回去。
    这一步是整套机制的安全阀 —— 少了它，一次崩溃就能让后续每天都 400。
    """
    try:
        try:
            r = star(s, model, True)
        except requests.RequestException as e:
            return False, f"第{i}轮 star 网络异常 {e.__class__.__name__}"
        if r.status_code != 200 and err_code(r) == REPEAT_CODE:
            # 已处于收藏态（上次崩溃/超时残留）：先取消，再重试一次
            ensure_unstarred(s, model)
            try:
                r = star(s, model, True)
            except requests.RequestException as e:
                return False, f"第{i}轮 star 重试网络异常 {e.__class__.__name__}"
        if r.status_code != 200:
            return False, f"第{i}轮 star 失败 HTTP {r.status_code}: {r.text[:120]}"
        return True, ""
    finally:
        try:
            star(s, model, False)
        except requests.RequestException:
            pass


def farm(s, model=DEFAULT_MODEL, rounds=0, delay=1.5, verbose=False):
    """刷点赞魔粒。rounds=0 表示刷满当日剩余额度。返回统计 dict。

    上限与单次从规则表读，不硬编码 —— 官方把每日次数从 20 改成 10 时，
    写死的「/40」会让日志和达标判断一起失真（2026-10-21 起实测 cap=10）。
    **不做「点一次查一次」**：默认只输出汇总，第 1 轮校验一次，verbose 才逐轮查。
    """
    t0 = time.time()
    st0 = rule_state(s, model)
    cap, amount = st0["cap"], st0["amount"]
    print(f"[点赞] 起始 今日已得 {st0['earned']}/{cap * amount} 魔粒 剩余 {st0['remain']} 次（单次 +{amount}）")

    if st0["remain"] <= 0:
        print(f"[点赞] 今日额度已满（{st0['used']}/{cap}），跳过")
        return {"done": 0, "gain": 0, "used": st0["used"], "remain": 0,
                "earned": st0["earned"], "cap": cap, "err": ""}

    ensure_unstarred(s, model)   # 清掉上次崩溃可能残留的收藏态（幂等）

    target = st0["remain"] if rounds <= 0 else min(rounds, st0["remain"])
    done, err, used_cur = 0, "", st0["used"]
    for i in range(1, target + 1):
        ok, msg = do_round(s, model, i)
        if not ok:
            err = msg
            break
        time.sleep(delay)
        if verbose or i == 1:
            # 第 1 轮校验一次，确认机制真的生效；系统性失败（风控/失效）不白跑满 target 轮
            now = rule_state(s, model)
            if now["used"] <= used_cur:
                err = f"第{i}轮 star 返回 200 但次数未增加（疑似风控）"
                break
            used_cur = now["used"]
            if verbose:
                print(f"  第{i}轮 OK  已用={now['used']}/{cap} 已得={now['earned']}")
        time.sleep(delay)
        done += 1

    if err:
        print(f"[点赞] ✗ 中断：{err}")

    st1 = rule_state(s, model)
    gain = st1["earned"] - st0["earned"]   # 权威口径：规则表累计发放额，不受过期影响
    print(f"[点赞] 完成 {done}/{target} 轮，本次 +{gain} 粒，耗时 {time.time() - t0:.1f}s")
    print(f"[点赞] 今日进度 {st1['used']}/{cap}，已得 {st1['earned']}/{cap * amount} 魔粒"
          + ("，已拿满 ✅" if st1["remain"] == 0 else ""))
    return {"done": done, "gain": gain, "used": st1["used"], "remain": st1["remain"],
            "earned": st1["earned"], "cap": cap, "err": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--cookie", help="cookies.json 路径，或直接粘 cookie 字符串")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"刷分用的模型，默认 {DEFAULT_MODEL}")
    ap.add_argument("--rounds", type=int, default=0, help="最多刷几轮，0=刷满当日剩余")
    ap.add_argument("--delay", type=float, default=1.5, help="每步间隔秒数，默认 1.5")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐轮打印（默认只输出汇总）")
    args = ap.parse_args()

    t0 = time.time()
    log_banner("魔搭魔粒 · 点赞刷分（模式 B / 纯 requests）")

    s = make_session(load_cookie(args.cookie))
    who = login_name(s)
    if not who:
        print("[点赞] ✗ cookie 无效或已过期，请重新导出")
        log_footer("失败", "登录态无效")
        sys.exit(1)
    print(f"[登录] 账号: {who}")

    st = farm(s, model=args.model, rounds=args.rounds, delay=args.delay, verbose=args.verbose)
    got, cap = auto_progress(s)   # 余额只在结尾查这一次
    log_footer("异常" if st["err"] else "成功",
               f"本次 {st['gain']:+d} 粒（点赞） · 今日累计 {got}/{cap} 粒"
               + (" ✅" if cap and got >= cap else "")
               + f" · 余额 {balance(s, args.model)} · 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
