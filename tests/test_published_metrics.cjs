const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../webui/manager-console.js'), 'utf8');
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0);
  return source.slice(start, source.indexOf('\n  }', start) + 4);
}
function element(tag, cls, text) {
  return {tag, text, children: [], appendChild(child) { this.children.push(child); }};
}
const publication = {machine_id: 'PC052', account_slot: 'a', kind: 'publish',
  item_results: [{note_id: 'note1', title: '测试笔记'}]};
function receipt(machine, slot, time, views) {
  return {machine_id: machine, account_slot: slot, kind: 'collection',
    business_result: {source: 'creator_manager', captured_at: time,
      notes: [{entity_id: 'note1', metrics: {views, likes: 0, comments: 10}}]}};
}
const state = {results: [publication,
  receipt('PC052', 'a', '2026-09-16T03:00:00Z', 119),
  receipt('PC052', 'a', '2026-09-16T04:00:00Z', 121),
  receipt('PC052', 'a', '2026-09-15T15:50:00Z', 100),
  receipt('PC057', 'a', '2026-09-16T05:00:00Z', 999),
  receipt('PC052', 'b', '2026-09-16T05:00:00Z', 888)]};
const context = vm.createContext({state, Intl, Date, Map, element,
  resultAccount: r => r.account_slot, resultActionKind: r => r.kind,
  resultMatchesFilters: () => true, formatTime: t => t,
  factList: pairs => ({pairs}), append: (parent, ...children) => children.forEach(c => parent.appendChild(c))});
for (const name of ['publishedMetricHistory', 'publishedNoteMetrics', 'filteredResults', 'resultItemDetails']) {
  vm.runInContext(extract(name), context);
}
const history = context.publishedMetricHistory(publication, 'note1');
assert.equal(history.length, 2);
assert.equal(history[0].metrics.views, 121);
assert.equal(history[1].day, '2026-09-15');
let rendered = JSON.stringify(context.publishedNoteMetrics(publication));
assert.ok(rendered.includes('["点赞",0]'));
assert.ok(rendered.includes('["收藏","待更新"]'));
assert.ok(rendered.includes('每日记录（2 天）'));
assert.ok(!rendered.includes('阅读时长'));
assert.equal(context.resultItemDetails(publication), null);
assert.equal(context.filteredResults().length, 1);
state.results = [publication];
rendered = JSON.stringify(context.publishedNoteMetrics(publication));
assert.ok(rendered.includes('待下发采集任务'));
assert.ok(rendered.includes('["浏览量","待更新"]'));
console.log('PASS: published note metrics, account isolation, daily snapshots, zero and pending');
vm.runInContext(extract('factList'), context);
const zeroFacts = context.factList([['点赞', 0], ['收藏', null]]);
assert.equal(zeroFacts.children[0].children[1].text, '0');
assert.equal(zeroFacts.children[1].children[1].text, '—');
console.log('PASS: actual fact renderer preserves numeric zero');
