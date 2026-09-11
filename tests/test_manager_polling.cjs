const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Node {
  constructor() { this.children = []; this.events = {}; this.value = ""; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.events[name] = callback; }
  reportValidity() { return true; }
}
const nodes = new Map();
const get = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, new Node());
  return nodes.get(selector);
};
const state = {snapshot: {polling: {
  intervals: {health_accounts_seconds: 600, results_seconds: 300},
  status_stale_seconds: 1800,
  fields: [
    {key: "health_accounts_seconds", label: "机器与账号检查", min: 30, max: 3600},
    {key: "results_seconds", label: "执行结果回收", min: 30, max: 3600},
  ],
}}, sync: {health_accounts: {completed_at: "2026-09-09T08:00:00Z"}}};
const context = vm.createContext({window: {}, document: {
  querySelector: get, createElement: () => new Node(),
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../webui/manager-polling.js"), "utf8"), context);
let request, fail = false;
const view = context.window.AutoXhsPolling({state, managerApi: "/api/v1/manager",
  mutateJson: async (url, method, values) => {
    request = {url, method, values};
    if (fail) throw new Error("连接中断");
    return {...state.snapshot.polling, intervals: {...values}};
  }, load: async () => {}, formatTime: (value) => value,
});
const form = get("#manager-polling-form");
const submit = () => form.events.submit({preventDefault() {}});
(async () => {
  view.render();
  const row = get("#manager-polling-fields").children[0];
  const input = row.children[1];
  assert.equal(input.value, 600);
  assert.match(row.children[3].textContent, /当前每 10 分钟/);
  assert.match(row.children[3].textContent, /08:10:00/);
  input.value = "900";
  input.events.input();
  view.render();
  assert.equal(input.value, "900", "自动刷新保留未保存的输入");
  await submit();
  assert.equal(request.url, "/api/v1/manager/polling-settings");
  assert.equal(request.method, "POST");
  assert.equal(request.values.health_accounts_seconds, 900);
  assert.equal(request.values.results_seconds, 300);
  assert.equal(input.value, 900);
  assert.match(row.children[3].textContent, /08:15:00/);
  fail = true;
  input.value = "1200";
  input.events.input();
  await submit();
  assert.equal(input.value, "1200");
  assert.equal(state.snapshot.polling.intervals.health_accounts_seconds, 900);
  assert.match(get("#manager-polling-message").textContent, /保存失败/);
  assert.equal(get("#manager-polling-save").disabled, false);
  console.log("PASS: polling defaults, dirty draft, save, updated schedule and failed-save retention");
})().catch((error) => { console.error(error); process.exitCode = 1; });
