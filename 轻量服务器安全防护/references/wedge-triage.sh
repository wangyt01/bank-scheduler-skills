#!/bin/bash
# ============================================================
# 轻量云 guest 僵死取证脚本（只读，可反复执行）
# 用法：重启实例后【立即】通过 Orcaterm/WebShell 粘贴执行；
#       输出自动存盘，即使系统再次僵死证据也在。
# ============================================================
OUT=/root/wedge-triage-$(date +%H%M%S).log
{
echo "===== 取证时间: $(date '+%F %T')  uptime: $(uptime) ====="

echo; echo "===== [1] 内存 ====="
free -m
echo "--- 内存 TOP10 进程 ---"
ps aux --sort=-rss | head -12

echo; echo "===== [2] 进程总量与 fork 迹象（fork 炸弹会看到数量爆炸/同名进程成百上千）====="
ps aux | wc -l
ps -eo comm | sort | uniq -c | sort -rn | head -15

echo; echo "===== [3] D 状态（不可中断）与 Z 状态（僵尸）进程 ====="
ps -eo pid,stat,wchan,cmd | awk '$2 ~ /^D/ || $2 ~ /^Z/' | head -20

echo; echo "===== [4] 负载与磁盘 IO ====="
cat /proc/loadavg
iostat -x 1 2 2>/dev/null | tail -15 || vmstat 1 2

echo; echo "===== [5] 内核错误/OOM/进程表相关（最近 50 行）====="
dmesg -T 2>/dev/null | grep -iE 'oom|fork|cannot allocate|task.*blocked|hung' | tail -30
dmesg -T 2>/dev/null | tail -20

echo; echo "===== [6] systemd 失败单元 ====="
systemctl --failed --no-pager

echo; echo "===== [7] 腾讯云 agent 存活状态（重点！）====="
for s in tat_agent stargate sgagent; do
  printf '%-12s: %s\n' "$s" "$(systemctl is-active $s 2>/dev/null)"
done
ps aux | grep -E 'tat_agent|stargate|sgagent|barad' | grep -v grep

echo; echo "===== [8] 重点嫌疑：openclaw / docker / 宝塔相关进程 ====="
ps aux | grep -iE 'openclaw|claw' | grep -v grep | head -10
docker ps --format '{{.Names}}  {{.Status}}' 2>/dev/null
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.PIDs}}' 2>/dev/null | head -15

echo; echo "===== [9] 定时任务全量（找 15-18 点之间会跑的东西）====="
crontab -l 2>/dev/null
ls /etc/cron.d/ 2>/dev/null
tail -30 /var/log/cron 2>/dev/null || journalctl -u crond --since '3 hours ago' --no-pager 2>/dev/null | tail -30

echo; echo "===== [10] 登录与 SSH 记录（谁在操作）====="
last -15
grep -E 'Accepted|session opened' /var/log/secure 2>/dev/null | tail -10

echo; echo "===== [11] 文件系统（磁盘满/inode 耗尽也会僵死）====="
df -h / 
df -i / 

echo; echo "===== 取证完成: $(date '+%F %T') 存于 $OUT ====="
} > "$OUT" 2>&1
cat "$OUT"
echo "===> 完整报告已存: $OUT （把文件内容发回给 AI 分析）"
