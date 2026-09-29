const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync(require("node:path").join(__dirname, "../webui/manager-console.js"), "utf8");
const start = source.indexOf("  function accountCardSection(");
const end = source.indexOf("  function resultLinks(", start);
class Node {
  constructor(tag, cls = "", text = "") {Object.assign(this, {tag, className: cls, textContent: text, children: [], dataset: {}, events: {}, isConnected: true});}
  append(...nodes) {this.children.push(...nodes);}
  appendChild(node) {this.children.push(node); return node;}
  setAttribute(name, value) {this[name] = value;}
  addEventListener(name, fn) {this.events[name] = fn;}
}
function setup(mutateJson) {
  const account = {machine_id: "PC 052", account_slot: "slot/a", account_note: "保留备注", scheduling_enabled: false, uid: "uid-one", health_state: "PAUSED"};
  const state = {accounts: [account], tasks: [], personas: []};
  const context = vm.createContext({state, managerApi: "/manager", mutateJson,
    employeeAccountKey: a => `${a.machine_id}:${a.account_slot}`,
    renderAll: () => {}, load: async () => {}, accountDisplayState: a => a,
    element: (...args) => new Node(...args), append: (p, ...args) => p.append(...args),
    stateBadge: text => new Node("badge", "", text), labelFor: v => v || "空闲",
    accountSignalLabel: (_k, v) => v || "未知", formatTime: v => v || "—",
    factList: rows => Object.assign(new Node("dl"), {rows}),
    accountActionButton: (text, action) => Object.assign(new Node("button", "", text), {action}),
    accountPersona: () => null, accountPersonaPanel: () => new Node("details", "persona"), accountTools: {open: () => {}}
  });
  vm.runInContext(source.slice(start,end), context);
  return {context, state, account};
}
test("卡片默认展示四项信息并保留详情与操作", () => {
  const {context, account} = setup();
  const card = context.compactAccountCard(account);
  assert.equal(card.children.find(n=>n.tag === "dl").rows.length, 4);
  const details = card.children.find(n=>n.children[0]?.textContent === "连接与同步详情");
  assert.equal(details.open, false);
  assert.ok(details.children[1].rows.some(r=>r[0] === "UID" && r[1] === "uid-one"));
  details.open = true; details.events.toggle();
  assert.equal(context.compactAccountCard(account).children.find(n=>n.children[0]?.textContent === "连接与同步详情").open, true);
  const actions = card.children.find(n=>n.className === "manager-card-actions");
  assert.deepEqual(actions.children.filter(n=>n.tag === "button").map(n=>n.textContent), ["管理信息", "检查身份"]);
  assert.ok(actions.children.some(n=>n.tag === "label"));
});
test("调度开关保留备注、使用原接口并显示同步状态", async () => {
  const calls = [];
  const {context, state, account} = setup(async (path, method, body) => {
    calls.push({path, method, body}); return {status: "SUCCESS", account: {...account, scheduling_enabled: true}};
  });
  await context.updateAccountScheduling(account, true);
  assert.equal(calls[0].path, "/manager/machines/PC%20052/accounts/slot%2Fa/management");
  assert.equal(calls[0].body.account_note, "保留备注");
  assert.equal(calls[0].body.scheduling_enabled, true);
  assert.equal(state.accounts[0].scheduling_enabled, true);
  assert.equal(state.accountSchedulingUpdates["PC 052:slot/a"].pending, false);
});
test("保存失败保留原调度状态并允许重试", async () => {
  const {context, state, account} = setup(async () => {throw new Error("网络中断");});
  await context.updateAccountScheduling(account, true);
  assert.equal(state.accounts[0].scheduling_enabled, false);
  assert.match(state.accountSchedulingUpdates["PC 052:slot/a"].message, /网络中断/);
  assert.equal(state.accountSchedulingUpdates["PC 052:slot/a"].pending, false);
});
