const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const manuals = ["项目框架结构.html", "项目框架结构-连续流程版.html"];

for (const filename of manuals) {
  const html = fs.readFileSync(path.join(root, "docs", filename), "utf8");
  const match = html.match(/<script id="manual-data" type="application\/json">([\s\S]*?)<\/script>/);
  assert.ok(match, filename + " 包含内嵌手册数据");
  const data = JSON.parse(match[1]);

  test(filename + " 的数据与内嵌脚本可解析", () => {
    assert.ok(Object.keys(data.codeModules).length > 0);
    let scripts = 0;
    for (const block of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      if (/application\/json/.test(block[1])) continue;
      assert.doesNotMatch(block[1], /\bsrc=/);
      new vm.Script(block[2], { filename });
      scripts += 1;
    }
    assert.ok(scripts > 0);
  });

  test(filename + " 的图连线连接已有节点", () => {
    for (const group of [data.graphs, data.graphDetails]) {
      for (const [name, graph] of Object.entries(group)) {
        const ids = new Set(graph.nodes.map(node => node.id));
        assert.equal(ids.size, graph.nodes.length, name + " 节点标识唯一");
        for (const edge of graph.edges) {
          assert.ok(ids.has(edge.a), name + " 起点 " + edge.a);
          assert.ok(ids.has(edge.b), name + " 终点 " + edge.b);
        }
      }
    }
  });

  test(filename + " 的源码引用在仓库内存在", () => {
    for (const [name, source] of Object.entries(data.modulePaths)) {
      const relative = path.relative(data.root, source);
      assert.ok(fs.existsSync(path.join(root, relative)), name + ": " + relative);
    }
  });
}

test("工程维护指南中的本地文档与脚本链接有效", () => {
  const guidePath = path.join(root, "docs", "ENGINEERING-MAINTENANCE.md");
  const guide = fs.readFileSync(guidePath, "utf8");
  for (const match of guide.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    if (/^https?:/.test(match[1])) continue;
    const target = decodeURIComponent(match[1].split("#")[0]);
    assert.ok(fs.existsSync(path.resolve(path.dirname(guidePath), target)), target);
  }
});
