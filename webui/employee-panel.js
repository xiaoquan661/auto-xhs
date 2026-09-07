/* Employee unit overview, planning template and profile receipts. */
window.AutoXhsEmployeePanel = function (services) {
  "use strict";
  const {state, workerDayMetrics, nextWorkerPlan, localDateValue, resultDate, resultAccount,
    resultLinks, resultActionKind, workerTaskDate, accountPersona, labelFor, operationalExceptions} = services;
  const host = document.querySelector("#employee-unit-content");
  const storageKey = "auto-xhs:employee-plan-templates";
  const planFields = [["publish", "发帖"], ["group_apply", "入群申请"],
    ["group_intro", "群介绍"], ["comment", "评论"], ["like", "点赞"],
    ["friend_request", "暖好友上限"]];
  const dayFields = [["publish", "发帖"], ["like", "点赞"], ["comment", "评论"],
    ["friend_request", "加好友"], ["new_followers", "新增粉丝"],
    ["received_likes", "收获点赞"], ["received_comments", "收到新评论"],
    ["received_private_messages", "收到私信"]];
  const expanded = new Map();
  const drafts = new Map();
  let selected = "";
  let renderedSelection = null;
  let planDate = localDateValue(new Date(Date.now() + 86400000));
  let savedTemplates;
  try { savedTemplates = JSON.parse(localStorage.getItem(storageKey) || "{}"); }
  catch { savedTemplates = {}; }
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const number = (value) => value === null || value === undefined ? "—" : esc(value);
  const key = (account) => `${account.machine_id}:${account.account_slot}`;
  const tone = (status) => ({READY: "good", SUCCESS: "good", COMPLETED: "good",
    WARNING: "warn", OFFLINE: "warn", BLOCKED: "bad", FAILED: "bad"}[status] || "muted");
  const badge = (text, color = "muted") => `<span class="status-pill tone-${color}">${esc(text)}</span>`;
  const stamp = (value) => {
    if (!value) return "暂无";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? esc(value) : new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false}).format(date);
  };
  const link = (url, text, cls = "") => /^https?:\/\//i.test(url || "")
    ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`
    : `<span class="unconnected-value">${esc(text === "打开主页" ? "主页待同步" : "链接未回传")}</span>`;
  const open = (id, initial = false) => (expanded.get(id) ?? initial) ? " open" : "";
  const facts = (items) => items.map(([name, value, note]) =>
    `<div><span>${esc(name)}</span><strong>${number(value)}</strong>${note ? `<small>${esc(note)}</small>` : ""}</div>`).join("");
  const options = (items, value) => items.map((item) => {
    const [id, label] = Array.isArray(item) ? item : [item, item];
    return `<option value="${esc(id)}"${id === value ? " selected" : ""}>${esc(label)}</option>`;
  }).join("");
  const input = (label, value = "", type = "text", extra = "") =>
    `<label><span>${esc(label)}</span><input type="${type}" value="${esc(value)}" ${extra}></label>`;

  function dailyMetrics(account, date) {
    const metrics = workerDayMetrics(account.machine_id, account.account_slot, date);
    return dayFields.map(([kind, label]) => ({kind, label,
      ...(metrics.find((item) => item.kind === kind) || {
        target: null, actual: null, status: "未接入", tone: "info", incomplete: false})}));
  }

  function receipts(account, date) {
    return state.results.filter((result) => result.machine_id === account.machine_id
      && resultAccount(result) === account.account_slot && (!date || resultDate(result) === date));
  }

  function actions(account, date) {
    return receipts(account, date).filter((result) => !/^collect-/i.test(
      services.resultCapability?.(result) || result.capability || result.action || "")).flatMap((result) => {
      const items = Array.isArray(result.item_results) && result.item_results.length
        ? result.item_results : [result];
      return items.map((item, index) => ({
        id: `${result.result_id || result.task_id}:${index}`,
        kind: resultActionKind(result), label: ({publish: "发帖", like: "点赞", comment: "评论",
          browse: "浏览", reply: "回复"}[resultActionKind(result)] || "执行动作"),
        at: item.completed_at || result.completed_at || result.created_at || result.collected_at,
        url: resultLinks(item)[0]?.url || "",
        content: item.comment_text || item.content || item.title || item.note_title || result.summary || "",
        status: item.state || item.status || result.business_state || result.state || "UNKNOWN",
        source: item.execution_source || "machine",
      }));
    }).sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  }

  function historyDates(account) {
    const today = localDateValue(new Date());
    return [...new Set([
      ...(state.dailyPlans || []).filter((plan) => plan.accounts.some((row) => key(row) === key(account)))
        .map((plan) => plan.plan_date),
      ...receipts(account).map(resultDate),
      ...state.tasks.filter((task) => task.machine_id === account.machine_id
        && task.request?.account_slot === account.account_slot).map(workerTaskDate),
      ...state.goals.filter((goal) => goal.period_start === goal.period_end
        && goal.targets?.some((target) => key(target) === key(account))).map((goal) => goal.period_start),
    ].filter((date) => date && date < today))].sort().reverse().slice(0, 30);
  }

  function planDraft() {
    let draft = drafts.get(planDate);
    if (!draft) {
      const source = savedTemplates[planDate];
      draft = {date: planDate, rows: structuredClone(source?.rows || []),
        updatedAt: source?.updatedAt || "", dirty: false};
      drafts.set(planDate, draft);
    }
    const saved = (state.dailyPlans || []).find((plan) => plan.plan_date === planDate);
    state.accounts.forEach((account) => {
      if (draft.rows.some((row) => key(row) === key(account))) return;
      const row = saved?.accounts.find((item) => key(item) === key(account));
      draft.rows.push({machine_id: account.machine_id, account_slot: account.account_slot,
        participating: row?.participating ?? account.health_state === "READY",
        stage: "", pressure: "", notes: row?.notes || "",
        metrics: Object.fromEntries(planFields.map(([kind]) => [kind, row?.metrics?.[kind] ?? null]))});
    });
    return draft;
  }

  function planCard(row) {
    const rowKey = key(row);
    const account = state.accounts.find((item) => key(item) === rowKey);
    const stage = row.stage || "账号阶段待配置";
    const pressure = row.pressure || "压力等级待配置";
    const evidence = account?.planning_evidence || {};
    return `<details class="strategy-node-card ${row.participating ? "is-enabled" : "is-paused"}"
      data-expanded="plan:${esc(rowKey)}" data-plan-row="${esc(rowKey)}"${open(`plan:${rowKey}`, row.participating)}>
      <summary><div><strong>${esc(row.machine_id)}</strong><span data-plan-caption>${esc(stage)} · ${esc(pressure)}</span></div>
        <span data-plan-badge>${badge(row.participating ? "参与规划" : "静默观察", row.participating ? "good" : "warn")}</span>
        <div class="strategy-plan-preview">${planFields.map(([kind, label]) =>
          `<span>${label}<b data-plan-preview="${kind}">${number(row.metrics[kind])}</b></span>`).join("")}</div></summary>
      <div class="strategy-node-body"><div class="strategy-node-settings">
        <label><span>是否参与</span><select data-plan-field="participating">${options(
          [["yes", "参与明日规划"], ["no", "暂停 / 静默"]], row.participating ? "yes" : "no")}</select></label>
        <label><span>账号阶段</span><select data-plan-field="stage">${options(["", "稳定运营", "新机第 1 天", "新机第 2–3 天", "账号安全观察"], row.stage)}</select></label>
        ${input("压力等级", row.pressure, "text", 'data-plan-field="pressure" placeholder="待配置"')}
      </div><div class="strategy-exact-grid">${planFields.map(([kind, label]) => input(label,
        row.metrics[kind] ?? "", "number", `min="0" step="1" placeholder="—" data-plan-metric="${kind}" aria-label="${esc(row.machine_id)} ${esc(row.account_slot)} ${label}"`)).join("")}</div>
      <div class="strategy-evidence-grid" aria-label="${esc(row.machine_id)} 自动运营依据">${facts([
        ["累计主页帖子", evidence.lifetime_posts ?? "未读取"], ["上次发帖日期", evidence.last_post_date || "未读取"],
        ["合格介绍积压", evidence.eligible_intro_backlog ?? "未读取"], ["暖互动线索", evidence.warm_candidate_count ?? "未读取"],
      ])}</div><div class="strategy-range-line"><span>长期区间：评论 ${esc(evidence.comment_range || "—")}，群介绍 ${esc(evidence.intro_range || "—")}</span>
        <span>${esc(row.notes || "等待经理 Agent 配置运营依据。")}</span></div>
      </div></details>`;
  }

  function planSummary() {
    const draft = planDraft();
    const rows = draft.rows.filter((row) => row.participating);
    const values = rows.flatMap((row) => Object.values(row.metrics)).filter((value) => value !== null);
    return facts([["参与规划", `${new Set(rows.map((row) => row.machine_id)).size} 台`],
      ["计划动作上限", values.length ? values.reduce((sum, value) => sum + value, 0) : "—", "好友申请按机会执行，计入的是上限"],
      ["发帖规则", "待配置", "等待经理 Agent 配置发帖总量与间隔"],
      ["账号异常", "待配置", "手机验证、人脸验证或安全检查"]]);
  }

  function planningPanel() {
    const draft = planDraft();
    return `<section class="panel strategy-panel" data-panel="tomorrow-plan">
      <div class="strategy-heading"><div><p class="eyebrow">经理机运营策略</p><h2>明日任务规划</h2>
        <p class="plain-help">这里决定每台机器的确切数量。保存计划不会创建 Trigger，也不会启动员工机。</p></div>
      <div class="strategy-save-bar">${input("计划日期", planDate, "date", 'data-plan-date')}
        <button type="button" class="smart-generate-button" data-unit-action="generate">智能生成明日计划</button>
        <button type="button" data-unit-action="save-plan">保存计划</button>
        <small class="save-state" data-plan-message role="status">${draft.dirty ? "已修改，待保存" : draft.updatedAt ? `本机模板上次保存 ${stamp(draft.updatedAt)}` : "尚未保存"}</small></div></div>
      <div class="strategy-summary" data-plan-summary>${planSummary()}</div>
      <div class="strategy-rules">${["评论必须符合对应账号人设", "图片不能重复使用", "点赞约为评论的 1/3", "好友申请只看暖互动机会", "每天检查真实入站"]
        .map((text) => `<span>${text}</span>`).join("")}</div>
      <div class="strategy-node-grid">${draft.rows.map(planCard).join("") || '<p class="empty-state">等待员工机账号同步。</p>'}</div>
      <div class="dispatch-template" data-dispatch-template${draft.updatedAt ? "" : " hidden"}>
        <div class="dispatch-template-head"><div><strong>复制到经理 Agent 的下发模板</strong><span>模板包含当前保存的逐机数量和运营说明。</span></div>
        <button type="button" data-unit-action="copy-template">复制模板</button></div>
        <textarea rows="16" readonly aria-label="下发模板">${esc(dispatchText(draft))}</textarea>
        <small class="save-state" data-copy-message role="status"></small></div></section>`;
  }

  function dispatchText(draft) {
    return `${draft.date} 员工机运营计划\n\n${draft.rows.map((row) =>
      `${row.machine_id} / ${row.account_slot} · ${row.participating ? "参与规划" : "暂停"}\n`
      + `账号阶段：${row.stage || "待配置"}；压力等级：${row.pressure || "待配置"}\n`
      + planFields.map(([kind, label]) => `${label}：${row.metrics[kind] ?? "未设置"}`).join("；")
      + (row.notes ? `\n说明：${row.notes}` : "")).join("\n\n")}\n\n请经理 Agent 审阅这些数量，补齐未设置字段后安排执行。`;
  }

  function machineCard(machine) {
    const accounts = state.accounts.filter((account) => account.machine_id === machine.machine_id);
    const residents = accounts.map(accountPersona).filter(Boolean);
    return `<button type="button" class="overview-node-card tone-${tone(machine.status)}"
      data-employee-machine-select="${esc(machine.machine_id)}">
      <div class="overview-node-head"><div><span>员工机 · ${esc(machine.machine_id)}</span>
        <h3>${esc(machine.host_alias || machine.machine_id)}</h3></div>${badge(labelFor(machine.status), tone(machine.status))}</div>
      <dl>${[["物理资产", machine.machine_id], ["执行单元", machine.worker_id || accounts.map((a) => a.account_slot).join("、") || "待绑定"],
        ["Resident", residents.map((p) => p.display_name || p.persona_id).join("、") || "待配置"],
        ["运行状态", labelFor(machine.status)]].map(([name, value]) => `<div><dt>${name}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
      ${accounts.map((a) => `<div class="overview-account-line"><span>账号</span><strong>${esc(a.nickname || a.account_slot)} · ${esc(a.account_slot)} · ${esc(a.current_uid || a.uid || "身份待同步")}</strong></div>`).join("")
        || '<p class="overview-node-detail">等待账号绑定回执</p>'}
      <small class="overview-node-updated">更新 ${stamp(machine.outbox_collection?.observed_at || machine.updated_at)}</small></button>`;
  }

  function dailyReport() {
    const date = new Date(); date.setDate(date.getDate() - 1);
    const yesterday = localDateValue(date);
    const plan = (state.dailyPlans || []).find((item) => item.plan_date === yesterday);
    if (!plan) return `<section class="panel daily-report-panel" data-panel="daily-report">
      <div class="section-heading compact"><div><p class="eyebrow">昨日日报</p><h2>${yesterday}</h2></div>${badge("暂无日报")}</div>
      <p class="daily-report-empty">没有找到该日期保存的运营计划</p></section>`;
    const rows = plan.accounts.filter((row) => row.participating);
    const dayResults = state.results.filter((item) => resultDate(item) === yesterday);
    const values = rows.flatMap((row) => dailyMetrics(row, yesterday))
      .filter((metric) => ["publish", "like", "comment", "friend_request"].includes(metric.kind));
    const actual = values.filter((item) => item.actual !== null);
    return `<section class="panel daily-report-panel" data-panel="daily-report"><div class="daily-report-heading">
      <div><p class="eyebrow">昨日计划复盘</p><h2>${yesterday} 运营日报</h2><p>自动对比保存计划与员工机正式回执。</p></div>${badge("已回收", "accent")}</div>
      <div class="daily-report-summary">${facts([["计划机器", new Set(rows.map((r) => r.machine_id)).size],
        ["实际执行", new Set(dayResults.map((r) => r.machine_id)).size],
        ["计划动作", values.reduce((sum, r) => sum + (r.target || 0), 0)],
        ["实际动作", actual.length ? actual.reduce((sum, r) => sum + r.actual, 0) : null], ["出现异常", null]])}</div>
      <div class="pressure-conclusion"><div><span>高压 / 低压结论</span><strong>等待运营分析结果</strong></div>
      <small>压力分组与对比结论尚未回传。</small></div>
      <div class="pressure-group-grid">${["高压组", "低压组"].map((group) => `<div><strong>${group}</strong><span>纳入对比 — · 动作 —</span><span>平均完成率 — · 异常率 —</span></div>`).join("")}</div>
      <div class="daily-report-table-wrap"><table class="daily-report-table"><thead><tr>
      ${["员工机", "压力", ...dayFields.map(([, label]) => label), "外部动作", "完成率", "结果"].map((label) => `<th>${label}</th>`).join("")}
      </tr></thead><tbody>${rows.map((row) => {const metrics = dailyMetrics(row, yesterday); return `<tr>
        <th>${esc(row.machine_id)}<small>${esc(row.account_slot)}</small></th><td>—</td>
        ${metrics.map((metric) => `<td>${number(metric.target)} / ${number(metric.actual)}</td>`)
          .join("")}<td>—</td><td>—</td><td>等待分析</td></tr>`;}).join("")}</tbody></table></div>
      <small class="daily-report-footnote">每格格式为“目标 / 实际”。</small></section>`;
  }

  function overview() {
    const alerts = operationalExceptions();
    const online = state.machines.filter((machine) => machine.ssh_state === "ONLINE").length;
    const today = localDateValue(new Date());
    const metrics = state.accounts.flatMap((account) => workerDayMetrics(account.machine_id, account.account_slot, today));
    const actual = metrics.filter((item) => item.actual !== null);
    return `<div class="workspace-overview"><div class="workspace-heading"><div><p class="eyebrow">经理机管理面板</p>
      <h2>员工机集群与执行单元</h2><p class="plain-help">点击任意卡片，右侧查看这台机器和账号的详细情况。</p></div>${badge("实时只读")}</div>
      <div class="overview-summary">${facts([["物理机器总数", state.machines.length, "不含已退役机器"],
        ["今日已记录动作", actual.length ? actual.reduce((sum, r) => sum + r.actual, 0) : null, "来自员工机回执"],
        ["设备连接正常", online, "经理机可以正常收取节点数据"], ["需要查看", alerts.length, "告警和待处理项"]])}</div>
      <section class="overview-node-panel" data-panel="machines"><div class="overview-section-head"><div><strong>机器与账号</strong>
        <span>状态颜色：绿色正常，黄色受限，红色需要人工处理</span></div><span>${state.machines.length} 个管理单元</span></div>
        <div class="overview-node-grid">${state.machines.map(machineCard).join("") || '<p class="empty-state">等待员工机同步</p>'}</div></section>
      ${dailyReport()}<div data-plan-host>${planningPanel()}</div>
      <div class="overview-lower-grid"><section class="panel plain-panel" data-panel="actual-summary">
        <div class="section-heading compact"><div><p class="eyebrow">今日进度</p><h2>实际动作汇总</h2></div><span>${online}/${state.machines.length} 员工已接入</span></div>
        <div class="result-grid">${facts([["物理员工机", state.machines.length, `${online} 台连接正常`],
          ["需要人工查看", alerts.length, "员工机上报后仍未处理的异常"],
          ["尚未绑定账号", state.machines.filter((machine) => !state.accounts.some((a) => a.machine_id === machine.machine_id)).length, "员工机账号绑定情况"]]).replaceAll("<div>", '<div class="result-item">')}</div></section>
      <section class="panel plain-panel" data-panel="alerts"><div class="section-heading compact"><div><p class="eyebrow">需要查看</p><h2>告警与提醒</h2></div><span>${alerts.length} 条</span></div>
        <div class="alert-list">${alerts.slice(0, 6).map((alert) => `<article class="alert-row"><span>${esc(labelFor(alert.state))}</span><div><strong>${esc(alert.title)}</strong><small>${esc(alert.object)} · ${esc(alert.message)}</small></div></article>`).join("")
          || '<p class="empty-state">现在没有未关闭告警</p>'}</div></section></div></div>`;
  }

  function nextDay(machine) {
    const plan = nextWorkerPlan(machine.machine_id, localDateValue(new Date()));
    const row = plan?.accounts.find((item) => item.machine_id === machine.machine_id && item.participating);
    const saved = plan && savedTemplates[plan.plan_date]?.rows.find((item) => key(item) === key(row));
    return `<section class="panel node-strategy-card" data-panel="next-day"><div class="node-strategy-head"><div>
      <p class="eyebrow">下一个运营日</p><h3>${plan ? esc(`${plan.plan_date} · ${saved?.stage || "账号阶段待配置"}`) : "等待运营规划"}${saved?.pressure ? ` · ${esc(saved.pressure)}` : ""}</h3></div>
      ${badge(plan ? "已规划" : "待规划", plan ? "good" : "muted")}</div>
      <div class="node-strategy-plan">${facts(planFields.map(([kind, label]) => [label, saved?.metrics?.[kind] ?? row?.metrics?.[kind]]))}</div>
      <p>${esc(row?.notes || "等待经理 Agent 下发该员工机的下一运营日目标。")}</p></section>`;
  }

  function todayTable(account, date, accountActions) {
    const uid = account.current_uid || account.uid || "待同步";
    return `<div class="daily-targets" data-panel="today-targets"><div class="daily-targets-head"><strong>今日目标 / 实际</strong><small>${esc(date)} · ${esc(account.current_task_title || "等待运营任务回传")}</small></div>
      <div class="daily-account-snapshot"><span>今日执行账号</span><strong>${esc(account.nickname || account.account_slot)}</strong><small>Profile ${esc(uid)}</small></div>
      <div class="daily-targets-table"><div class="daily-target-row daily-target-header"><span>指标</span><span>目标</span><span>实际</span><span>状态</span></div>
      ${dailyMetrics(account, date).map((metric) => `<div class="daily-target-row"><span>${metric.label}</span><span>${number(metric.target)}</span>
        <span class="daily-actual-cell"><strong>${number(metric.actual)}${metric.incomplete ? "（部分回传）" : ""}</strong>
        <span class="daily-action-links">${accountActions.filter((a) => a.kind === metric.kind && a.url).map((a, index) => link(a.url, `机器${index + 1}`)).join("")}</span></span>
        <span>${badge(metric.status, ({success: "good", warning: "warn", info: "muted"}[metric.tone] || "muted"))}</span></div>`).join("")}</div></div>`;
  }

  function historyTable(account) {
    const dates = historyDates(account);
    return `<details class="history-details" data-panel="history" data-expanded="history:${esc(key(account))}"${open(`history:${key(account)}`, dates.length > 0)}>
      <summary>往日目标 / 实际（${dates.length} 天）</summary>
      <div class="history-table-wrap"><table class="history-table"><thead><tr><th>日期</th><th>当日执行账号</th>
      ${dayFields.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>
      ${dates.map((date) => {
        const receipt = receipts(account, date)[0] || {};
        const identity = receipt.account_snapshot || {};
        return `<tr><th>${date}</th><td class="history-account-cell">${esc(identity.nickname || receipt.account_name || account.account_slot)}<small>Profile ${esc(identity.uid || receipt.account_uid || "未记录")}</small></td>
        ${dailyMetrics(account, date).map((metric) => `<td>${number(metric.target)} / ${number(metric.actual)}</td>`).join("")}</tr>`;
      }).join("")
        || `<tr><td colspan="${dayFields.length + 2}">还没有读取到往日的任务记录。</td></tr>`}</tbody></table></div>
      <small class="history-note">每格格式为“目标 / 实际”，最多显示最近 30 天；— 表示尚未设置或未回传。</small></details>`;
  }

  function actionList(account, accountActions) {
    return `<details class="action-details" data-panel="action-links" data-expanded="actions:${esc(key(account))}"${open(`actions:${key(account)}`)}>
      <summary>动作与对应链接（${accountActions.length}）</summary><div class="action-list">
      ${accountActions.map((action) => `<div class="action-row"><div><strong>${esc(action.label)} · ${action.source === "human" ? "人工" : "机器"}执行（回执导入）</strong>
        <time>${stamp(action.at)}</time><small class="action-account">账号：${esc(account.nickname || account.account_slot)} · Profile ${esc(account.current_uid || account.uid || "未记录")}</small></div>
        ${link(action.url, "查看目标链接")}<div class="visibility-state">${badge(labelFor(action.status), tone(action.status))}<small>执行回执状态</small></div>
        <p>${esc(action.content)}</p><div class="action-management-controls"><label><span>执行者</span><select disabled>${options([["machine", "机器"], ["human", "人工"]], action.source)}</select></label>
        <button type="button" class="action-delete-button" data-unit-action="unconnected" data-feature="动作记录管理">删除</button><small data-local-message role="status"></small></div></div>`).join("")
        || '<p>当前回执没有可展示的发帖、点赞或评论链接。</p>'}</div></details>`;
  }

  function moduleDetails(account) {
    const streams = [["comment", "评论回复提醒", account.comment_reply_alerts, "当前快照没有新增评论回复。"],
      ["friend", "好友模块明细", account.friend_events, "当前快照没有好友模块明细。"],
      ["private", "私聊明细", account.private_message_events, "当前快照没有私聊明细。"]];
    return `<div class="module-details-grid">${streams.map(([kind, title, items, empty]) => {
      const records = Array.isArray(items) ? items : [];
      return `<details class="module-details${kind === "comment" ? " comment-reply-details" : ""}" data-panel="${kind}-details"
        data-expanded="${kind}:${esc(key(account))}"${open(`${kind}:${key(account)}`, records.some((item) => item.requires_reply))}>
        <summary><span>${title}</span><strong>${records.length}</strong></summary>
        ${records.length ? `<div class="module-event-list">${records.map((item) => `<article class="module-event${item.requires_reply ? " needs-attention" : ""}">
          <div class="module-event-head"><strong>${esc(item.actor_name || item.peer_name || "未知用户")}</strong>${badge(labelFor(item.read_status || item.event_type || "UNKNOWN"))}</div>
          <time>${stamp(item.occurred_at)}</time><p>${esc(item.reply_text || item.message_preview || item.note || "内容未回传")}</p>
          ${item.parent_comment_text ? `<small>回复了：${esc(item.parent_comment_text)}</small>` : ""}
          ${link(item.target_url || item.thread_url || item.profile_url, kind === "private" ? "打开会话" : kind === "friend" ? "查看用户主页" : "查看评论位置")}</article>`).join("")}</div>` : `<p>${empty}</p>`}</details>`;
    }).join("")}</div>`;
  }

  function profileCard(account, machine) {
    const date = localDateValue(new Date());
    const accountActions = actions(account, date);
    const last = accountActions[0];
    const uid = account.current_uid || account.uid || "未绑定";
    const profileUrl = account.profile_url || (uid !== "未绑定" ? `https://www.xiaohongshu.com/user/profile/${encodeURIComponent(uid)}` : "");
    const tasks = state.tasks.filter((task) => task.machine_id === machine.machine_id
      && task.request?.account_slot === account.account_slot && workerTaskDate(task) === date);
    return `<details class="machine-card tone-${tone(account.health_state)}" data-panel="employee-profile"
      data-expanded="profile:${esc(key(account))}"${open(`profile:${key(account)}`, true)}><summary class="machine-card-summary">
      <div class="machine-card-head"><div class="machine-identity"><span class="worker-icon">EMP</span><div><p>EMPLOYEE / PROFILE</p>
        <h3>${esc(account.nickname || account.account_slot)}</h3><small>${esc(machine.machine_id)} · ${esc(account.account_slot)} · Profile ${esc(uid)}</small></div></div>
      <div class="machine-live"><i class="signal tone-${tone(machine.status)}"></i><strong>${machine.ssh_state === "ONLINE" ? "设备连接正常" : esc(labelFor(machine.ssh_state || "UNKNOWN"))}</strong>
      <small>${stamp(machine.updated_at)}</small><span class="account-status-inline">${esc(labelFor(account.health_state))}</span>
      <small class="machine-summary-activity">运营 ${number(account.operating_days)} 天 · 最后 ${last ? `${esc(last.label)} ${stamp(last.at)}` : "暂无动作"}</small></div></div></summary>
      <div class="machine-card-body"><div class="account-status-editor"><div><strong>账号状态</strong><small>员工机回执 · ${stamp(account.updated_at)}</small></div>
        <select aria-label="${esc(account.account_slot)} 账号状态" disabled>${options([[account.health_state, labelFor(account.health_state)]], account.health_state)}</select></div>
      <div class="machine-profile-link-row"><div><span>小红书账号主页</span><strong>Profile ${esc(uid)}</strong></div>${link(profileUrl, "打开主页", "facebook-profile-link")}</div>
      ${todayTable(account, date, accountActions)}${historyTable(account)}
      <div class="worker-business-strip" aria-label="${esc(account.account_slot)} 业务数据">${facts([
        ["申请进群", "未接入"], ["进群成功", "未接入"], ["申请加友", "未接入"], ["成功加友", "未接入"], ["活跃好友", "未接入"],
      ])}</div><div class="machine-task">${facts([["当前阶段", account.stage || "未接入"], ["今日会话", account.session_count ?? null],
        ["运营天数", account.operating_days === undefined ? null : `${account.operating_days} 天`],
        ["最后动作", last ? `${last.label} · ${stamp(last.at)}` : "暂无动作"]])}
      <div class="machine-task-wide"><span>最后动作链接</span>${link(last?.url, "打开最后动作", "last-action-link")}</div>
      <div class="machine-task-wide"><span>发帖受众</span><strong>${esc(account.publish_audience || "未接入")}</strong></div>
      <div class="machine-task-wide"><span>当前任务</span><strong>${esc(account.current_task_title || tasks[0]?.request?.title || "暂无任务")}</strong></div></div>
      ${actionList(account, accountActions)}${moduleDetails(account)}
      <div class="machine-footer"><span>${esc(labelFor(machine.status))}</span><span>心跳 ${stamp(machine.updated_at)} · 抓取 ${stamp(machine.outbox_collection?.observed_at)}</span></div></div></details>`;
  }

  function detail(machine) {
    const accounts = state.accounts.filter((account) => account.machine_id === machine.machine_id);
    return `<div class="workspace-detail"><div class="workspace-heading detail-heading"><div><p class="eyebrow">单台员工机详情 · ${esc(machine.machine_id)}</p>
      <h2>${esc(machine.host_alias || machine.machine_id)} · 员工机</h2><p class="plain-help">机器、账号、今日任务和实际回执都集中在这里。</p></div>
      <button type="button" data-employee-machine-select="">返回总览</button></div>${nextDay(machine)}
      <div class="node-machine-list">${accounts.map((account) => profileCard(account, machine)).join("")
        || '<section class="panel node-detail-empty"><h3>等待账号同步</h3><p>该机器的 Employee / Profile 在账号回执到达后显示。</p></section>'}</div></div>`;
  }

  function render(machineId = selected) {
    selected = machineId;
    if (!host) return;
    host.querySelectorAll("details[data-expanded]").forEach((item) => expanded.set(item.dataset.expanded, item.open));
    if (renderedSelection === selected && host.contains(document.activeElement)
      && document.activeElement.matches("input, select, textarea")) return;
    const preservedPlan = !selected && renderedSelection === "" ? host.querySelector("[data-plan-host]") : null;
    const machine = state.machines.find((item) => item.machine_id === selected);
    host.innerHTML = machine ? detail(machine) : overview();
    if (preservedPlan && planDraft().dirty) host.querySelector("[data-plan-host]").replaceWith(preservedPlan);
    renderedSelection = selected;
  }

  function updatePlan(target) {
    const rowNode = target.closest("[data-plan-row]");
    if (!rowNode) return;
    const draft = planDraft();
    const row = draft.rows.find((item) => key(item) === rowNode.dataset.planRow);
    if (target.dataset.planMetric) {
      const kind = target.dataset.planMetric;
      row.metrics[kind] = target.value === "" ? null : Number(target.value);
      rowNode.querySelector(`[data-plan-preview="${kind}"]`).textContent = target.value || "—";
    } else if (target.dataset.planField) {
      const field = target.dataset.planField;
      row[field] = field === "participating" ? target.value === "yes" : target.value;
    } else return;
    draft.dirty = true;
    rowNode.classList.toggle("is-enabled", row.participating);
    rowNode.classList.toggle("is-paused", !row.participating);
    rowNode.querySelector("[data-plan-badge]").innerHTML = badge(row.participating ? "参与规划" : "静默观察", row.participating ? "good" : "warn");
    rowNode.querySelector("[data-plan-caption]").textContent = `${row.stage || "账号阶段待配置"} · ${row.pressure || "压力等级待配置"}`;
    host.querySelector("[data-plan-summary]").innerHTML = planSummary();
    host.querySelector("[data-plan-message]").textContent = "已修改，待保存";
  }

  function init() {
    host.addEventListener("input", (event) => updatePlan(event.target));
    host.addEventListener("change", (event) => {
      if (event.target.matches("[data-plan-date]")) {
        if (!event.target.value) return;
        planDate = event.target.value;
        host.querySelector("[data-plan-host]").innerHTML = planningPanel();
      } else updatePlan(event.target);
    });
    host.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-unit-action]");
      if (!button) return;
      const action = button.dataset.unitAction;
      if (action === "generate") {
        host.querySelector("[data-plan-message]").textContent = "智能生成尚未接入，可手动调整数量并保存模板。";
      } else if (action === "save-plan") {
        const draft = planDraft();
        if (draft.rows.some((row) => Object.values(row.metrics).some((value) => value !== null
          && (!Number.isInteger(value) || value < 0)))) {
          host.querySelector("[data-plan-message]").textContent = "请填写大于或等于 0 的整数。";
          return;
        }
        try {
          const saved = {...draft, dirty: false, updatedAt: new Date().toISOString()};
          const templates = {...savedTemplates, [planDate]: saved};
          localStorage.setItem(storageKey, JSON.stringify(templates));
          savedTemplates = templates;
          drafts.set(planDate, saved);
          host.querySelector("[data-plan-host]").innerHTML = planningPanel();
          host.querySelector("[data-plan-message]").textContent = "模板已保存到本机，可复制给经理 Agent 审阅并安排执行。";
        } catch { host.querySelector("[data-plan-message]").textContent = "本机模板保存失败，请复制模板保留当前数量。"; }
      } else if (action === "copy-template") {
        try {
          await navigator.clipboard.writeText(host.querySelector("[data-dispatch-template] textarea").value);
          host.querySelector("[data-copy-message]").textContent = "已复制";
        } catch { host.querySelector("[data-copy-message]").textContent = "请在文本框中全选复制。"; }
      } else if (action === "unconnected") {
        const surface = button.closest(".action-management-controls");
        surface.querySelector("[data-local-message]").textContent = `${button.dataset.feature}尚未接入，请在经理 Agent 中配置。`;
      }
    });
  }
  return {init, render, dailyMetrics, historyDates, actions};
};
