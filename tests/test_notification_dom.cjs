const { scriptSourcePath } = require("./project_paths.cjs");
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(scriptSourcePath("xhs/notification_history.py"), 'utf8');
const select = source.match(/_SELECT_NOTIFICATION_TAB_JS = r"""([\s\S]*?)"""/)[1];
const observedTabs = JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures/notifications/notification-tabs.json'), 'utf8'));

test('selects the received follower label through its unread badge and async activation', async () => {
  let pending, waits = 0;
  const clicks = [];
  const tabs = observedTabs.map(row => ({
    textContent: row.text,
    querySelector: () => ({ textContent: row.label }),
    classList: { contains: name => name === 'active' && row.class.split(' ').includes('active') },
    getAttribute: () => null,
    click: () => { clicks.push(row.label); pending = row; },
  }));
  const context = vm.createContext({
    document: { querySelectorAll: () => tabs },
    setTimeout: resolve => {
      waits++;
      if (pending) pending.class += ' active';
      resolve();
    },
  });

  const selected = await vm.runInContext(select.replace('LABELS', '["新增关注"]'), context);

  assert.equal(selected, true);
  assert.deepEqual(clicks, ['新增关注']);
  assert.equal(waits, 1);
});

test('keeps the current comment label selected without another click', async () => {
  const tabs = observedTabs.map(row => ({
    textContent: row.text,
    querySelector: () => ({ textContent: row.label }),
    classList: { contains: name => name === 'active' && row.class.split(' ').includes('active') },
    getAttribute: () => null,
    click: () => assert.fail('current comment label is already active'),
  }));
  const context = vm.createContext({ document: { querySelectorAll: () => tabs } });

  assert.equal(await vm.runInContext(select.replace('LABELS', '["评论和@"]'), context), true);
});

test('reads received comment cards from the observed notification structure', () => {
  const python = fs.readFileSync(scriptSourcePath("xhs/notifications.py"), 'utf8');
  const script = python.match(/script = f"""([\s\S]*?)"""/)[1]
    .replaceAll('{{', '{').replaceAll('}}', '}').replace('{int(max_items)}', '100');
  const card = (relation, content, reader) => {
    const user = { href: `https://www.xiaohongshu.com/user/profile/${reader}`, innerText: reader };
    const time = { innerText: '昨天 19:20', getAttribute: () => null };
    const info = {
      innerText: `${reader}\n${relation}昨天 19:20\n${content}\n回复`,
      getAttribute: () => null,
      querySelector: selector => ({
        '.interaction-hint > span': { innerText: relation },
        '.interaction-content': { innerText: content },
        '.user-info a': user,
        'time,[data-time],[class*="time"]': time,
      })[selector] || null,
      querySelectorAll: selector => selector.includes('/user/profile/') ? [user] : [],
    };
    return { querySelector: selector => selector === '.info' ? info : null };
  };
  const cards = [card('评论了你的笔记', '收到评论一', 'reader-1'),
    card('回复了你的评论', '收到回复二', 'reader-2'),
    card('在评论中提到了你', '提及内容', 'reader-3')];
  const context = vm.createContext({ URL, document: {
    querySelectorAll: selector => {
      assert.equal(selector, '.notification-page .tabs-content-container > .container');
      return cards;
    },
  } });

  const rows = JSON.parse(JSON.stringify(vm.runInContext(script, context)));

  assert.deepEqual(rows.map(row => row.content), ['收到评论一', '收到回复二']);
  assert.deepEqual(rows.map(row => row.user_id), ['reader-1', 'reader-2']);
});
