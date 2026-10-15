const { webuiAssetPath } = require("./project_paths.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const date = "2026-09-15";
const account = {machine_id: "m", account_slot: "a", nickname: "运营账号",
  comment_reply_alerts: [{event_id: "event-1", actor_name: "读者甲",
    message_preview: "收到的原文", target_url: "https://example.com/comment"}]};
const root = {innerHTML: "", querySelectorAll: () => []};
const context = vm.createContext({window: {}, document: {querySelector: () => root},
  localStorage: {getItem: () => null}, Date, Intl});
vm.runInContext(fs.readFileSync(webuiAssetPath("employee-panel.js"), "utf8"), context);
function panel(receipts) {
  const results = receipts.map((row, i) => ({...account, date, task_id: `task-${i}`,
    record_id: `record-${i}`, completed_at: `${date}T08:00:00Z`, ...row}));
  return context.window.AutoXhsEmployeePanel({state: {results, accounts: [account],
    machines: [{machine_id: "m"}], tasks: [], dailyPlans: [], goals: []},
  localDateValue: () => date, resultAccount: (row) => row.account_slot,
  resultDate: (row) => row.date, resultLinks: (row) => row.url ? [{url: row.url}] : [],
  workerDayMetrics: () => [], workerTaskDate: () => date, accountPersona: () => null,
  labelFor: (value) => value, operationalExceptions: () => []});
}
function actions(receipts) { return panel(receipts).actions(account, date); }

const submittedComment = {status: "BLOCKED", success: false, comment_id: "submitted-1",
  submitted: true, platform_readback: true, identity_match: false};
assert.equal(actions([{capability: "post-comment", item_results: [submittedComment]}])[0].status,
  "SUBMITTED_PENDING_REVIEW");
panel([{capability: "post-comment", item_results: [submittedComment]}]).render("m:a");
assert.match(root.innerHTML, /已发送·归属待核对/);
assert.equal(actions([{capability: "post-comment", item_results: [{...submittedComment,
  status: "SUCCESS", success: true, attribution_review: {status: "CONFIRMED"}}]}])[0].status,
"SUCCESS");

const compound = {capability: "home-engagement", state: "BLOCKED", item_results: [
  {action: "browse", status: "SUCCESS", title: "笔记一"},
  {action: "comment", success: true, platform_readback: true, comment_text: "正文<测试>"},
  {action: "follow", status: "RESULT_UNKNOWN", success: true, platform_readback: true},
  {action: "like", status: "SUCCESS"},
]};
let rows = actions([compound]);
assert.deepEqual(Array.from(rows, (row) => [row.kind, row.status]), [
  ["browse", "SUCCESS"], ["comment", "SUCCESS"],
  ["friend_request", "RESULT_UNKNOWN"], ["like", "UNKNOWN"],
]);
assert.equal(rows[1].itemIndex, 1);
assert.equal(rows[1].recordId, "record-0");
console.log("PASS: flat behavior identity, independent status, readback and attribution");

rows = actions([{capability: "home-engagement", item_results: [{state: "FAILED",
  title: "嵌套笔记", browse: {status: "success"}, like: {status: "skipped"}}]}]);
assert.deepEqual(Array.from(rows, (row) => [row.kind, row.status]), [
  ["browse", "SUCCESS"], ["like", "SKIPPED"],
]);
rows = actions([{capability: "home-engagement", state: "SUCCESS", item_results_status: "missing"}]);
assert.equal(rows.length, 2);
assert.ok(rows.every((row) => row.status === "UNKNOWN"));
console.log("PASS: nested receipts split into actions; missing items stay unresolved");

const patrol = {task_id: "patrol-1", state: "BLOCKED", business_result: {
  round_id: "round-1", collections: [
    {metric: "received_comments", coverage: "complete", observed_count: 2},
    {metric: "received_private_messages", coverage: "complete", observed_count: 0},
  ], new_comment_events: 0,
  replies: {private_message: {success: 0, pending: 0, remaining: 0, display: "无"}},
}, item_results: [{channel: "comment", event_id: "event-1", state: "BLOCKED",
  reason: "回复规则不匹配", message: "任务摘要"},
{channel: "comment", event_id: "event-2", state: "PENDING_LOCAL", reason: "需人工处理"}]};
rows = actions([patrol]);
const collection = rows.find((row) => row.kind === "collection");
assert.equal(collection.status, "SUCCESS");
assert.equal(collection.channels[0].status, "NOT_COLLECTED");
assert.equal(collection.channels[0].count, null);
assert.equal(collection.channels[2].count, 2);
assert.equal(collection.channels[3].count, 0);
assert.equal(collection.recordId, "");
assert.equal(rows.find((row) => row.kind === "reply").outcome, "回复规则不匹配");
assert.equal(rows.find((row) => row.kind === "reply").target, "读者甲");
assert.equal(rows.find((row) => row.kind === "reply").incoming, "收到的原文");
assert.equal(rows.find((row) => row.kind === "reply").content, "");
assert.equal(rows.find((row) => row.kind === "private_reply").status, "NO_ACTION");
assert.equal(rows.filter((row) => row.kind === "collection").length, 1);
console.log("PASS: patrol collection, blocked replies and empty inbox have separate results");

rows = actions([{capability: "collect-private-messages", state: "SUCCESS",
  business_result: {coverage: "UNKNOWN", count: 0}}]);
assert.equal(rows[0].channels[3].count, null);
assert.equal(rows[0].status, "UNKNOWN");
rows = actions([{capability: "reply-cycle", incremental_data: [{
  event_type: "outbound_reply", payload: {reply_task_id: "reply-1", event_id: "event-1",
    channel: "PRIVATE_MESSAGE_REPLY", send_state: "CONFIRMED"},
}], item_results: [{reply_task_id: "reply-1", channel: "private_message", state: "SUCCESS",
  platform_readback: true}]}]);
assert.equal(rows.filter((row) => row.kind === "private_reply").length, 1);
assert.equal(rows.find((row) => row.kind === "private_reply").status, "SUCCESS");
console.log("PASS: unknown collection zero and duplicate reply observations");

rows = actions([{capability: "home-engagement", item_results: [
  {action: "collect-operations-metrics", status: "SUCCESS"},
  {action: "reply-inbox-collect", status: "SUCCESS"},
]}]);
assert.deepEqual(Array.from(rows, (row) => row.kind), ["collection", "collection"]);
assert.equal(rows[0].label, "采集粉丝与获赞信息");
assert.ok(rows.every((row) => row.status === "SUCCESS"));
console.log("PASS: collection steps inside a compound task use the collection group");

rows = actions([{capability: "reply-inbox", state: "SUCCESS", business_result: {
  collection_success: true, frozen_event_count: 0, pending_count: 0,
}}]);
assert.equal(rows.filter((row) => row.status === "NO_ACTION").length, 2);
assert.equal(rows.filter((row) => row.kind === "collection").length, 1);
console.log("PASS: confirmed empty inbox produces collection and no-action reply records");

const ui = panel([compound, patrol, {...patrol, task_id: "patrol-2"}]);
ui.render("m:a");
for (const text of ["按行为查看", "浏览笔记", "发表评论", "关注博主", "巡检采集",
  "回复规则不匹配", "正文&lt;测试&gt;", "本次采集记录 2 条", "本轮新发现评论 0 条",
  "未采集到结果", "无需执行", "技术信息与执行归属"]) {
  assert.ok(root.innerHTML.includes(text), text);
}
assert.equal((root.innerHTML.match(/data-behavior="collection"/g) || []).length, 1);
assert.ok(root.innerHTML.includes("2 条记录 · 2 轮"));
assert.ok(!root.innerHTML.includes("本轮新增评论 2"));
assert.equal(ui.actions({...account, machine_id: "other"}, date).length, 0);
console.log("PASS: grouped rendering, distinct rounds, collection semantics and account isolation");
const published = {capability: 'publish', state: 'SUCCESS', business_result: {
  note_url: 'https://www.xiaohongshu.com/explore/123456789012345678901234?xsec_token=sample'
}, item_results: [{title: '作品', status: 'SUCCESS', verified: true}]};
assert.equal(actions([published])[0].url, published.business_result.note_url);
panel([published]).render('m:a');
assert.ok(root.innerHTML.includes('打开笔记 · 人工核对'));
assert.equal(actions([{...published, business_result: {note_id: '123456789012345678901234'}}])[0].url,
  'https://www.xiaohongshu.com/explore/123456789012345678901234');
panel([{...published, business_result: {}}]).render('m:a');
assert.ok(root.innerHTML.includes('笔记链接待回传'));
console.log('PASS: published note links and missing-link display');
