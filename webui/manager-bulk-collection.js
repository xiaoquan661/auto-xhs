window.AutoXhsBulkCollection = function ({state, mutateJson, managerApi, load}) {
  const $ = (selector) => document.querySelector(selector);
  const buttons = [...document.querySelectorAll('[data-recovery-bulk]')];
  const singleButtons = ['#manager-collect-results', '#manager-collect-notifications', '#manager-sync-outbox']
    .map($).filter(Boolean);
  const titles = {creator: '作品采集', notifications: '通知采集', outbox: '回执回收'};
  const labels = {PENDING: '等待执行', RUNNING: '正在执行', SUCCESS: '成功',
    PARTIAL_SUCCESS: '部分完成', PAUSED: '已暂停', DISABLED: '已停用', DEFERRED: '账号忙，待重试',
    BLOCKED: '受阻', OFFLINE: '离线', FAILED: '失败', RESULT_UNKNOWN: '结果待核对'};
  let running = false;
  let rows = [];
  let title = '';

  function targets(action) {
    const machines = [...new Set((state.machines || []).map(row => row.machine_id).filter(Boolean))];
    if (action === 'outbox') return machines.map(machine => ({machine, slot: ''}));
    const seen = new Set();
    return (state.accounts || []).filter(row => machines.includes(row.machine_id) && row.account_slot)
      .filter(row => {
        const key = JSON.stringify([row.machine_id, row.account_slot]);
        if (seen.has(key)) return false;
        seen.add(key); return true;
      }).map(row => ({machine: row.machine_id, slot: row.account_slot}));
  }

  function renderScope() {
    const accounts = targets('creator');
    $('#recovery-bulk-scope').textContent = `${targets('outbox').length} 台员工机 · ${accounts.length} 个账号 · 覆盖全部账号`;
    if (!running) buttons.forEach(button => {
      button.disabled = targets(button.dataset.recoveryBulk).length === 0;
    });
  }

  function renderProgress() {
    const finished = rows.filter(row => !['PENDING', 'RUNNING'].includes(row.status));
    const successful = finished.filter(row => row.status === 'SUCCESS').length;
    $('#recovery-bulk-message').textContent = `${title}：已处理 ${finished.length}/${rows.length}，成功 ${successful}，需关注 ${finished.length - successful}。`
      + (running ? ' 执行期间请保持页面打开。' : '');
    $('#recovery-bulk-progress').replaceChildren(...rows.map(row => {
      const item = document.createElement('li');
      item.dataset.status = row.status;
      item.textContent = `${row.machine}${row.slot ? ` / ${row.slot}` : ''} · ${labels[row.status] || row.status}`
        + (row.count == null ? '' : ` · ${row.count} ${row.unit}`)
        + (row.message ? ` · ${row.message}` : '');
      return item;
    }));
  }

  async function execute(row, action, evidence) {
    row.status = 'RUNNING'; renderProgress();
    const path = action === 'outbox'
      ? `${managerApi}/machines/${encodeURIComponent(row.machine)}/collect`
      : `${managerApi}/${action === 'creator' ? 'creator-metrics' : 'notifications'}/collect-now`;
    const body = action === 'outbox' ? {download_evidence: evidence}
      : {machine_id: row.machine, account_slot: row.slot};
    let payload;
    try {
      payload = await mutateJson(path, 'POST', body);
    } catch (error) {
      if (error.payload && !error.payload.error) payload = error.payload;
      else {
        row.status = 'FAILED'; row.message = error.message;
        renderProgress(); return;
      }
    }
    const account = payload.accounts?.find(item => item.account_slot === row.slot);
    row.status = account?.status || payload.status || (payload.success ? 'SUCCESS' : 'FAILED');
    row.count = action === 'outbox' ? payload.collected : account?.count;
    row.unit = action === 'creator' ? '篇' : action === 'outbox' ? '份回执' : '条';
    row.message = account?.message || payload.message || '';
    if (payload.database_status === 'PENDING' && (payload.received || payload.collected) > 0) {
      if (row.status === 'SUCCESS') row.status = 'PARTIAL_SUCCESS';
      row.message += '；业务数据库待同步';
      if (payload.database_error) row.message += `：${payload.database_error}`;
    }
    renderProgress();
  }

  async function start(action) {
    if (running) return;
    if (singleButtons.some(button => button.disabled)) {
      $('#recovery-bulk-message').textContent = '当前账号操作正在执行，请待其结束后启动全部账号操作。';
      return;
    }
    rows = targets(action).map(row => ({...row, status: 'PENDING'}));
    if (!rows.length) {
      $('#recovery-bulk-message').textContent = '当前没有可执行的员工机或账号。';
      return;
    }
    running = true; title = titles[action];
    const evidence = $('#recovery-bulk-evidence').checked;
    [...buttons, ...singleButtons].forEach(button => { button.disabled = true; });
    $('#recovery-bulk-details').hidden = false;
    $('#recovery-bulk-details').open = true;
    renderProgress();
    try {
      // Keep accounts on one machine serial and use up to three machines concurrently.
      const groups = [...new Set(rows.map(row => row.machine))]
        .map(machine => rows.filter(row => row.machine === machine));
      let next = 0;
      await Promise.all(Array.from({length: Math.min(3, groups.length)}, async () => {
        while (next < groups.length) {
          const group = groups[next++];
          for (const row of group) await execute(row, action, evidence);
        }
      }));
      const refreshed = await load({fresh: true});
      running = false; renderProgress();
      if (!refreshed) $('#recovery-bulk-message').textContent += ' 页面刷新失败，请点击刷新。';
    } finally {
      running = false;
      [...buttons, ...singleButtons].forEach(button => { button.disabled = false; });
      renderScope();
    }
  }

  function init() {
    buttons.forEach(button => button.addEventListener('click', () => {
      start(button.dataset.recoveryBulk).catch(error => {
        $('#recovery-bulk-message').textContent += ` ${error.message}`;
      });
    }));
    renderScope();
  }
  return {init, renderScope, start};
};
