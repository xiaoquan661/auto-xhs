const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../extension/background.js'), 'utf8');
const start = source.indexOf('async function cmdSelectPublishTab(');
const code = source.slice(start, source.indexOf('\nloadBinding()', start));

function harness(tabs, matches) {
  const queried = [];
  const context = vm.createContext({
    URL, binding: { platform: 'xiaohongshu' }, normalizePlatform: x => x,
    platformSpec: () => ({ tabUrls: ['https://*.xiaohongshu.com/*'] }),
    platformForUrl: url => new URL(url).hostname.endsWith('.xiaohongshu.com') ? 'xiaohongshu' : null,
    chrome: {
      tabs: {
        query: async () => tabs,
        get: async id => tabs.find(tab => tab.id === id),
      },
      scripting: { executeScript: async ({ target, args }) => {
        queried.push([target.tabId, args[0]]);
        return [{ result: matches.includes(target.tabId) }];
      } },
    },
  });
  vm.runInContext(code, context);
  return { context, queried };
}

const tabs = [
  { id: 1, url: 'https://www.xiaohongshu.com/explore', active: true },
  { id: 2, url: 'https://creator.xiaohongshu.com/publish/publish' },
  { id: 3, url: 'https://creator.xiaohongshu.com/publish/publish' },
];

test('publish binds the matching editor while the feed tab is active', async () => {
  const h = harness(tabs, [2]);
  const result = await h.context.cmdSelectPublishTab({ expected_title: 'title' });
  assert.equal(result.tab_id, 2);
  assert.deepEqual(h.queried, [[2, 'title'], [3, 'title']]);
  assert.equal((await h.context.getOrOpenPlatformTab(result.tab_id)).id, 2);
});

test('missing or duplicate previews return a result without choosing a tab', async () => {
  for (const [matches, status] of [[[], 'preview_unavailable'], [[2, 3], 'ambiguous']]) {
    const h = harness(tabs, matches);
    assert.equal((await h.context.cmdSelectPublishTab({ expected_title: 'title' })).status, status);
  }
});
