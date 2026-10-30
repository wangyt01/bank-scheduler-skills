#!/usr/bin/env node
/**
 * Trae Work 只读积分查询
 * 查询积分/额度余额（usage_summary + 积分包），以及每日签到状态。
 * 只读不改动；令牌仅在内存。端点/加密方法逆向自官方客户端，可能随版本变化。
 *
 * 用法：node balance.js [--json]
 */
"use strict";
const { createClient } = require("./lib.js");

async function main() {
  const jsonOut = process.argv.includes("--json");
  const client = await createClient();
  const auth = client.lib;

  const [usage, checkin] = await Promise.all([
    client.post("/trae/api/v2/pay/ide_user_ent_usage", { require_usage: true, req_source: 1 }),
    client.post("/trae/api/v2/ug/checkin_credits/status", {}),
  ]);

  if (jsonOut) {
    console.log(JSON.stringify({ usage, checkin }, null, 2));
    return;
  }

  const region = auth.userRegion;
  const us = usage.json.usage_summary || {};
  const packs = usage.json.user_entitlement_pack_list || [];
  const creditBilling = usage.json.is_credits_billing !== false;

  console.log("== Trae Work 积分查询 ==");
  console.log("账号区域 :", region ? (region.region || region._aiRegion || "CN") : "CN",
              creditBilling ? "· 积分制" : "· 美元用量计费");
  console.log("┌─ 积分/额度 ─────────────────────────");
  if (us.total_amount != null) {
    const total = Number(us.total_amount);
    const consumed = Number(us.consumed_amount || 0);
    const ratio = Number(us.consumption_ratio || 0);
    console.log(`│ 总量       : ${total}`);
    console.log(`│ 已消耗     : ${consumed}`);
    console.log(`│ 剩余(估算) : ${total - consumed}`);
    if (ratio > 0) console.log(`│ 消耗比例   : ${(ratio * 100).toFixed(2)}%`);
    console.log(`│ 积分包数量 : ${packs.length}`);
    packs.forEach((p, i) => {
      const info = p.entitlement_base_info || {};
      const end = info.end_time ? new Date(info.end_time * 1000).toISOString().slice(0, 10) : "";
      console.log(`│   [${i + 1}] ${p.display_desc || info.entitlement_id || "?"}${end ? "  到期 " + end : ""}`);
    });
  } else {
    console.log("│（usage_summary 为空，可能接口/计费形态变化）");
  }
  console.log("└──────────────────────────────────────");
  console.log("┌─ 每日签到状态 ───────────────────────");
  if (checkin.json && checkin.json.code === 0) {
    const c = checkin.json;
    console.log(`│ 今日是否已签 : ${c.checked_in ? "是" : "否"}`);
    if (c.credits != null) console.log(`│ 可领基础积分 : ${c.credits}`);
    if (c.extra_credits != null) console.log(`│ 额外积分     : ${c.extra_credits}`);
    console.log(`│ 达标可领     : ${c.enable ? "是" : "否（未达标/不可领）"}`);
  } else {
    console.log("│（读取失败：" + ((checkin.json && checkin.json.message) || `HTTP ${checkin.status}`) + "）");
  }
  console.log("└──────────────────────────────────────");
}

main().catch((e) => { console.error("错误：" + e.message); process.exit(1); });