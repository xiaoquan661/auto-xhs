const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../webui/manager-account-tools.js"), "utf8");
class Node {
  constructor(tag) { Object.assign(this, {tag, value: "", children: [], events: {}}); }
  append(...children) {
    children.forEach((child) => {child.parentElement = this; this.children.push(child);});
  }
  setAttribute() {}
  addEventListener(type, listener) {this.events[type] = listener;}
  async emit(type) {await this.events[type]({preventDefault() {}});}
  showModal() {}
}
function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {const match = find(child, predicate); if (match) return match;}
}
function setup(action, account = {}) {
  const body = new Node("body"); const calls = [];
  const context = vm.createContext({window: {}, document: {body, createElement: (tag) => new Node(tag)}});
  vm.runInContext(source, context);
  context.window.AutoXhsAccountTools({state: {machines: [{machine_id: "PC-DEMO"}]},
    managerApi: "/api/manager", fetchJson: async () => ({}), load: async () => {},
    mutateJson: async (_url, _method, request) => {
      calls.push(request);
      return {operation: {pairing: {platform: "rednote", pairing_bundle: "demo-test-text"}}};
    },
  }).open(action, {machine_id: "PC-DEMO", account_slot: "demo", ...account});
  return {calls, body, form: find(body, (node) => node.tag === "form"),
    field: (name) => find(body, (node) => node.name === name)};
}
(async () => {
  for (const mode of ["create", "import"]) {
    const test = setup("create");
    test.field("name").value = "red-account";
    test.field("mode").value = mode;
    assert.equal(test.field("platform").required, true);
    await test.form.emit("submit");
    assert.equal(test.calls.length, 0);
    test.field("platform").value = "rednote";
    test.field("profile").value = JSON.stringify({profile_path: "C:/Chrome/Profile 2",
      profile_directory: "Profile 2", display_name: "测试"});
    await test.form.emit("submit");
    assert.equal(test.calls[0].action, mode);
    assert.equal(test.calls[0].values.platform, "rednote");
    assert.equal(test.calls[0].values.name, "red-account");
    if (mode === "import") assert.equal(test.calls[0].values.profile_directory, "Profile 2");
  }
  const fresh = setup("pair-begin", {platform: "xiaohongshu", paired: false});
  assert.equal(fresh.field("platform").disabled, false);
  fresh.field("platform").value = "rednote";
  await fresh.form.emit("submit");
  assert.equal(fresh.calls[0].values.platform, "rednote");
  assert.equal(fresh.field("platform").disabled, true);
  assert.ok(find(fresh.body, (node) => node.textContent?.startsWith("配对平台：RedNote")));
  const bound = setup("pair-begin", {platform: "rednote", paired: true});
  assert.equal(bound.field("platform").value, "rednote");
  assert.equal(bound.field("platform").disabled, true);
  await bound.form.emit("submit");
  assert.equal(bound.calls[0].values.platform, undefined);
  console.log("PASS: platform required for create/import, RedNote request, initial correction and bound pairing");
})().catch((error) => {console.error(error); process.exitCode = 1;});
