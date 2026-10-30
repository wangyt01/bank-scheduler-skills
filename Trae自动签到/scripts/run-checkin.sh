#!/usr/bin/env bash
# Trae Work 每日签到的 cron 包装脚本：切到 skill 目录、跑签脚本、追加日志。
# 独立于 TRAE 客户端运行（只依赖系统 node 与本地登录态）。
# 收到服务端“参与用户太多”等瞬时限流时，按退避序列自动重试（幂等，不会重复发放）。

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="${TRAE_CHECKIN_NODE:-/usr/local/bin/node}"
LOG_DIR="$SKILL_DIR/logs"
mkdir -p "$LOG_DIR"

# 每次失败后再试的等待秒数（短退避，总计约 2 分钟）
BACKOFF=(5 10 15 30 60)

log() { printf '%s\n' "$*" >> "$LOG_DIR/checkin.log"; }

log ""
log "==== [$(date '+%Y-%m-%d %H:%M:%S %Z')] 开始 ===="
cd "$SKILL_DIR" || exit 1

rc=0
for wait in "${BACKOFF[@]}"; do
  "$NODE_BIN" scripts/checkin.js >> "$LOG_DIR/checkin.log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    log "签到成功或今日已签，无需继续重试"
    break
  fi
  log "本轮未签到成功（退出码 $rc），${wait}s 后重试"
  sleep "$wait"
done

if [ "$rc" -ne 0 ]; then
  log "达到最大重试次数仍失败，请稍后手动重跑 scripts/checkin.js"
fi
log "==== [$(date '+%Y-%m-%d %H:%M:%S %Z')] 结束（退出码 $rc） ===="
exit "$rc"