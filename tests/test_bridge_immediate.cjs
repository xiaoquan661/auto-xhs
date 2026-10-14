const { extensionAssetPath } = require("./project_paths.cjs");
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(extensionAssetPath("background.js"), 'utf8');

for (const name of ['cmdEvaluateInMainWorld', 'cmdDomInMainWorld', 'readNavigationState', 'detectNavigationBlock']) {
  test(`${name} executes while background resources are still loading`, async () => {
    let idleRequested = false;
    const code = source.match(new RegExp(`async function ${name}\\([^]*?\\n}`))[0];
    const context = vm.createContext({
      setTimeout, clearTimeout,
      getOrOpenPlatformTab: async () => ({ id: 57, status: 'loading' }),
      mainWorldExecutor: () => {}, domExecutor: () => {},
      chrome: { scripting: { executeScript: async options => {
        assert.equal(options.target.tabId, 57);
        if (!options.injectImmediately) { idleRequested = true; return []; }
        return [{ result: null }];
      } } },
    });
    vm.runInContext(code, context);
    if (name.startsWith('cmd')) await context[name]('mouse_move', {});
    else await context[name](57, 'https://www.xiaohongshu.com/explore', 'https://www.xiaohongshu.com/explore');
    assert.equal(idleRequested, false);
  });
}
