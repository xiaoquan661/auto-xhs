const { webuiAssetPath } = require("./project_paths.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(webuiAssetPath("manager-account-tools.js"), "utf8");
const bundle = `demo-pairing-text:${"员工机测试/abcdef0123456789+=".repeat(30)}`;
class Node {
  constructor(tag) {
    Object.assign(this, {tag, children: [], events: {}, attributes: {}, textContent: ""});
  }
  append(...children) {
    children.forEach((child) => { child.parentElement = this; this.children.push(child); });
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, listener) { this.events[type] = listener; }
  async emit(type) { await this.events[type]({preventDefault() {}}); }
  showModal() { this.open = true; }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  setSelectionRange(start, end) { this.selection = [start, end]; }
}
function find(root, predicate) {
  if (predicate(root)) return root;
  for (const child of root.children) {
    const match = find(child, predicate); if (match) return match;
  }
}
async function scenario({clipboard, fallback = true}) {
  const body = new Node("body");
  let copyCalls = 0;
  const operations = [];
  const document = {body, createElement: (tag) => new Node(tag), execCommand: (command) => {
    assert.equal(command, "copy"); copyCalls += 1;
    const field = find(body, (item) => item.tag === "textarea");
    assert.equal(field.focused, true);
    assert.equal(field.selected, true);
    assert.deepEqual(field.selection, [0, bundle.length]);
    assert.equal(field.value, bundle);
    assert.equal(field.parentElement.parentElement.open, true);
    return fallback;
  }};
  const context = vm.createContext({window: {}, document, navigator: {clipboard}});
  vm.runInContext(source, context);
  context.window.AutoXhsAccountTools({state: {machines: []}, managerApi: "/api/manager",
    mutateJson: async (_url, _method, values) => {
      operations.push(values.action);
      return {operation: {pairing: {pairing_bundle: bundle}}};
    }, fetchJson: async () => ({}), load: async () => {},
  }).open("pair-begin", {machine_id: "PC-DEMO", account_slot: "demo", platform: "xiaohongshu"});
  await find(body, (item) => item.tag === "form").emit("submit");
  const button = find(body, (item) => item.textContent === "复制配对信息");
  await button.emit("click");
  assert.equal(button.disabled, false);
  assert.deepEqual(operations, ["pair-begin"], "Copy must not start another pairing operation");
  const message = button.parentElement.children[button.parentElement.children.indexOf(button) + 1];
  assert.equal(message.attributes.role, "status");
  return {body, button, message, copyCalls};
}

(async () => {
  let copied;
  const secure = await scenario({clipboard: {writeText: async (text) => {copied = text;}}});
  assert.equal(copied, bundle);
  assert.equal(secure.copyCalls, 0);
  assert.equal(secure.button.textContent, "已复制配对信息");
  const lan = await scenario({});
  assert.equal(lan.copyCalls, 1);
  assert.match(lan.message.textContent, /^已复制/);
  const rejected = await scenario({clipboard: {writeText: async () => {throw Error("NotAllowedError");}}});
  assert.equal(rejected.copyCalls, 1);
  assert.equal(rejected.button.textContent, "已复制配对信息");
  const manual = await scenario({fallback: false});
  assert.equal(manual.button.textContent, "重新复制配对信息");
  assert.equal(manual.message.textContent, "已选中全部配对信息，请按 Ctrl+C 复制。");
  console.log("PASS: secure clipboard, LAN fallback, denied access, full selection and accurate feedback");
})().catch((error) => { console.error(error); process.exitCode = 1; });
