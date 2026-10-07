const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../extension/background.js'), 'utf8');
const navigation = source.slice(source.indexOf('async function cmdNavigate('), source.indexOf('// ───────────────────────── 截图'));
const note = 'https://www.xiaohongshu.com/explore/note1';

test('recommendation extraction supports arrays and Vue ref wrappers', () => {
  const python = fs.readFileSync(require('node:path').join(__dirname, '../scripts/xhs/feeds.py'), 'utf8');
  const script = python.match(/_EXTRACT_FEEDS_JS = """([\s\S]*?)"""/)[1];
  const feeds = [{ id: 'note1', xsecToken: 'token', noteCard: { displayTitle: 'title' } }];
  for (const wrap of [x => x, x => ({ value: x }), x => ({ _value: x })]) {
    const context = vm.createContext({ window: { __INITIAL_STATE__: { feed: { feeds: wrap(feeds) } } } });
    assert.deepEqual(JSON.parse(vm.runInContext(script, context)), feeds);
  }
});

test('profile extraction supports plain state and Vue ref wrappers', () => {
  const python = fs.readFileSync(require('node:path').join(__dirname, '../scripts/xhs/user_profile.py'), 'utf8');
  const userScript = python.match(/_EXTRACT_USER_DATA_JS = """([\s\S]*?)"""/)[1];
  const notesScript = python.match(/_EXTRACT_USER_NOTES_JS = """([\s\S]*?)"""/)[1];
  const data = { basicInfo: { nickname: 'tester' }, interactions: [] };
  const notes = [[{ id: 'note1' }]];
  for (const wrap of [x => x, x => ({ value: x }), x => ({ _value: x })]) {
    const context = vm.createContext({ window: { __INITIAL_STATE__: {
      user: { userPageData: wrap(data), notes: wrap(notes) },
    } } });
    assert.deepEqual(JSON.parse(vm.runInContext(userScript, context)), data);
    assert.deepEqual(JSON.parse(vm.runInContext(notesScript, context)), notes);
  }
});

function harness(states) {
  let tick = 0, index = 0, navigations = 0, reads = 0;
  const context = vm.createContext({
    URL, console, binding: { platform: 'xiaohongshu' },
    normalizePlatform: x => x, platformForUrl: () => 'xiaohongshu',
    getOrOpenPlatformTab: async () => ({ id: 1, url: states[0].url }),
    detectNavigationBlock: async () => null,
    Date: { now: () => tick }, sleep: async ms => { tick += ms; },
    chrome: {
      tabs: { get: async () => ({ id: 1, status: 'loading', url: states[index].url }),
        update: async () => { navigations++; } },
      scripting: { executeScript: async ({ func }) => {
        if (func.toString().includes('window.location.href = targetUrl')) navigations++;
        return [];
      } },
    },
  });
  vm.runInContext(navigation, context);
  context.readNavigationState = async () => {
    reads++;
    const state = states[Math.min(index, states.length - 1)];
    index = Math.min(index + 1, states.length - 1);
    return state;
  };
  return { context, reads: () => reads, navigations: () => navigations, time: () => tick };
}
const ready = (url = note, documentId = 1) => ({ url, documentId, ready: true, reason: 'note', readyState: 'interactive' });

test('browser error page recovers with one tab navigation', async () => {
  const h = harness([{ ...ready(), ready: false }, ready(note, 2)]);
  h.context.chrome.scripting.executeScript = async () => {
    throw new Error('Frame with ID 0 is showing error page');
  };
  await h.context.cmdNavigate({ url: note });
  assert.equal(h.navigations(), 1);
});
test('inaccessible platform page recovers with one tab navigation', async () => {
  const h = harness([{ ...ready(), ready: false }, ready(note, 2)]);
  h.context.chrome.scripting.executeScript = async () => {
    throw new Error('Cannot access contents of the page. Extension manifest must request permission');
  };
  await h.context.cmdNavigate({ url: note });
  assert.equal(h.navigations(), 1);
});

test('unrelated navigation errors remain visible', async () => {
  const h = harness([{ ...ready(), ready: false }]);
  h.context.chrome.scripting.executeScript = async () => { throw new Error('permission denied'); };
  await assert.rejects(h.context.cmdNavigate({ url: note }), /permission denied/);
  assert.equal(h.navigations(), 0);
});

test('ready note is reused across detail and comment despite loading resources', async () => {
  const h = harness([ready()]);
  const result = await h.context.cmdNavigate({ url: note + '?xsec_token=fresh' });
  assert.equal(result.reused, true);
  assert.equal(h.navigations(), 0);
  assert.equal(result.tab_status, 'loading');
  await h.context.cmdWaitForLoad({});
  assert.equal(h.time(), 0);
});

test('old complete document cannot satisfy a navigation to another note', async () => {
  const h = harness([ready(), ready(), ready(note.replace('note1', 'note2'), 2)]);
  await h.context.cmdNavigate({ url: note.replace('note1', 'note2') });
  assert.equal(h.navigations(), 1);
  assert.ok(h.time() >= 200);
});

test('matching URL with old document waits for committed navigation', async () => {
  const h = harness([ready(note, 1), ready(note, 2)]);
  const result = await h.context.waitForTabComplete(1, note, 1000, 1);
  assert.equal(result.elapsed_ms, 200);
});

test('route hydration can finish while Chrome remains loading', async () => {
  const h = harness([{ ...ready(), ready: false }, ready()]);
  const result = await h.context.waitForTabComplete(1, note, 1000);
  assert.equal(result.readiness, 'note');
  assert.equal(h.time(), 200);
});

test('wrong target times out and leaves no background polls', async () => {
  const h = harness([ready(note.replace('note1', 'other'))]);
  await assert.rejects(h.context.waitForTabComplete(1, note, 600), /页面加载超时/);
  assert.equal(h.reads(), 3);
});

test('home navigation accepts the committed recommendation redirect', async () => {
  const home = 'https://www.xiaohongshu.com';
  const state = (url, id) => ({ ...ready(url, id), reason: 'dom' });
  const h = harness([state(note, 1), state(home + '/explore', 2)]);
  const result = await h.context.cmdNavigate({ url: home });
  assert.equal(result.readiness, 'dom');
  assert.equal(h.navigations(), 1);
  assert.ok(h.time() < 1000);
});

test('search navigation accepts query reordering and retains keyword identity', () => {
  const h = harness([ready()]);
  const url = 'https://www.xiaohongshu.com/search_result';
  assert.equal(h.context.sameNavigationTarget(url + '?source=web&keyword=a',
    url + '?keyword=a&source=web'), true);
  assert.equal(h.context.sameNavigationTarget(url + '?keyword=a', url + '?keyword=b'), false);
  assert.equal(h.context.sameNavigationTarget(url + '/?keyword=life&source=web_explore_feed&type=51',
    url + '?keyword=life&source=web_explore_feed'), true);
  assert.equal(h.context.sameNavigationTarget(url + '?keyword=life&type=51',
    url + '?keyword=life&type=54'), false);
  assert.equal(h.context.sameNavigationTarget('https://www.rednote.com/explore',
    'https://www.xiaohongshu.com/'), false);
});

test('blocked redirect returns promptly for the existing block handler', async () => {
  const h = harness([{ ...ready('https://www.xiaohongshu.com/404', 2), reason: 'blocked' }]);
  assert.equal((await h.context.waitForTabComplete(1, note, 60000, 1)).readiness, 'blocked');
  assert.equal(h.time(), 0);
});

test('route comparison preserves host, profile and search query boundaries', () => {
  const h = harness([ready()]);
  assert.equal(h.context.sameNavigationTarget(note, note.replace('xiaohongshu', 'rednote')), false);
  assert.equal(h.context.sameNavigationTarget(note, note.replace('note1', 'note2')), false);
  assert.equal(h.context.sameNavigationTarget('https://x/search?q=a', 'https://x/search?q=b'), false);
});

for (const route of ['note', 'profile']) {
  test(`${route} readiness requires route data and DOM`, async () => {
    let selectorPresent = false;
    const state = { note: { noteDetailMap: { note1: { note: { noteId: 'note1' } } } },
      user: { userPageData: { value: { basicInfo: { nickname: 'tester' } } } } };
    const path = route === 'note' ? '/explore/note1' : '/user/profile/u1';
    const context = vm.createContext({ URL, setTimeout, clearTimeout,
      window: { __INITIAL_STATE__: state },
      location: { href: 'https://www.xiaohongshu.com' + path, pathname: path },
      performance: { timeOrigin: 1 },
      document: { readyState: 'interactive', body: {}, querySelectorAll: () => [], querySelector: selector =>
        selector.includes('access-wrapper') ? null : selectorPresent ? {} : null },
      chrome: { scripting: { executeScript: async ({ func }) => [{ result: func() }] } },
    });
    vm.runInContext(navigation, context);
    assert.equal((await context.readNavigationState(1)).ready, false);
    selectorPresent = true;
    assert.equal((await context.readNavigationState(1)).ready, true);
    context.window.__INITIAL_STATE__ = {};
    assert.equal((await context.readNavigationState(1)).ready, false);
  });
}

test('stalled page-state injection returns control to the navigation loop', async () => {
  const context = vm.createContext({
    setTimeout: callback => { callback(); return 1; }, clearTimeout: () => {},
    chrome: {scripting: {executeScript: () => new Promise(() => {})}},
  });
  vm.runInContext(navigation, context);
  await assert.rejects(context.readNavigationState(1), /页面状态读取超时/);
});

test('ready home survives repeated business steps without navigation', async () => {
  const url = 'https://www.xiaohongshu.com/explore';
  const h = harness([{ ...ready(url), reason: 'dom' }]);
  assert.equal((await h.context.cmdNavigate({ url: 'https://www.xiaohongshu.com/' })).reused, true);
  assert.equal(h.navigations(), 0);
});

test('search and profile refresh retain collection freshness', async () => {
  for (const path of ['/search_result?keyword=a', '/user/profile/u1']) {
    const url = 'https://www.xiaohongshu.com' + path;
    const reason = path.includes('profile') ? 'profile' : 'dom';
    const h = harness([{ ...ready(url, 1), reason }, { ...ready(url, 2), reason }]);
    assert.equal((await h.context.cmdNavigate({ url })).reused, false);
    assert.equal(h.navigations(), 1);
  }
});
