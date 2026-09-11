/* Employee unit overview, shared daily plans and profile receipts. */
window.AutoXhsEmployeePanel = function (services) {
  "use strict";
  const {state, workerDayMetrics, localDateValue, resultDate, resultAccount,
    resultLinks, resultActionKind, workerTaskDate, accountPersona, labelFor, operationalExceptions} = services;
  const host = document.querySelector("#employee-unit-content");
  let legacyTemplates;
  try { legacyTemplates = JSON.parse(localStorage.getItem("auto-xhs:employee-plan-templates") || "{}"); }
  catch { legacyTemplates = {}; }
  const metricFields = [["browse", "浏览"], ["publish", "发帖"], ["like", "点赞"],
    ["comment", "评论"], ["private_message", "私信发送"], ["friend_request", "关注博主"],
    ["reply", "评论回复"], ["private_reply", "私信回复"], ["new_followers", "新增粉丝"],
    ["received_likes", "收获点赞"], ["received_comments", "收到新评论"],
    ["received_private_messages", "收到私信"]];
  const actionKinds = ["publish", "like", "comment", "friend_request", "private_message", "private_reply"];
  const executionStatus = (value) => ({SKIP: "已跳过", SKIPPED: "已跳过",
    PENDING_MANUAL: "待人工处理", PENDING: "待处理", RESULT_UNKNOWN: "结果待核对",
    NOT_ATTEMPTED: "未执行"})[String(value || "").toUpperCase()] || labelFor(value);
  const businessText = (value) => String(value || "").replaceAll("RESULT_UNKNOWN", "结果待核对")
    .replaceAll("PENDING_MANUAL", "待人工处理");
  const metricValues = (values) => Object.fromEntries(metricFields.map(([kind]) =>
    [kind, values?.[kind] ?? null]));
  const expanded = new Map();
  const drafts = new Map();
  let selected = "";
  let renderedSelection = null;
  let planDate = localDateValue(new Date(Date.now() + 86400000));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const number = (value) => value === null || value === undefined ? "—" : esc(value);
  const metricActual = (metric) => {
    const text = metric.actual_display ?? metric.actual;
    const details = [];
    if (metric.metric_semantics === "daily_event_count") {
      details.push("按平台发生日期统计；至少表示已确认的部分事件，待确认表示全天数量尚不确定");
      if (metric.current_total != null) details.push(`截至采集时总量：${metric.current_total}`);
      if (metric.sampled_net_change) details.push(`采样净变化：${metric.sampled_net_change.value}（${metric.sampled_net_change.from} 至 ${metric.sampled_net_change.to}）`);
      details.push(`当日首次入库：${metric.newly_discovered ?? 0} 条记录`);
    }
    return `<span title="${esc(details.join("；"))}">${number(text)}</span>`;
  };
  const key = (account) => `${account.machine_id}:${account.account_slot}`;
  const accountName = (account) => account.nickname || account.account_slot;
  const platformName = (account) => account.platform === "rednote" ? "RedNote" : "小红书";
  const machineFor = (account) => state.machines.find((machine) =>
    machine.machine_id === account.machine_id) || {machine_id: account.machine_id};
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
    const observed = (state.employeeSummary?.daily_metrics || []).find((row) =>
      row.machine_id === account.machine_id && row.account_slot === account.account_slot && row.date === date);
    return metricFields.map(([kind, label]) => {
      const local = metrics.find((item) => item.kind === kind);
      const metric = observed?.metrics[kind];
      return {kind, label, ...(metric || local || {
        target: null, actual: null, status: "等待采集", tone: "info", incomplete: false})};
    });
  }

  function receipts(account, date) {
    return state.results.filter((result) => result.machine_id === account.machine_id
      && resultAccount(result) === account.account_slot && (!date || resultDate(result) === date));
  }

  function actions(account, date) {
    return receipts(account, date).flatMap((result) => {
      const request = result.task_request || state.tasks.find((task) => task.task_id === result.task_id)?.request || {};
      const capability = result.capability || request.capability || "";
      const labels = {"home-engagement": "浏览笔记与点赞", "favorite-feed": "收藏笔记",
        "like-feed": "点赞笔记", "post-comment": "发表评论", "random-comment": "发表评论",
        "reply-comment": "回复评论", "reply-inbox": request.parameters?.acceptance_action === "reply"
          ? "回复收到的评论" : request.parameters?.acceptance_action === "private_reply"
            ? "回复收到的私信" : "处理收到的评论或私信",
        "follow-user": "关注博主", "send-private-messages": "发送私信",
        "collect-note-comments": "采集收到的评论", "collect-private-messages": "采集收到的私信",
        "collect-operations-metrics": "采集账号与笔记指标", "search-feeds": "搜索笔记",
        "get-feed-detail": "读取笔记详情", "reply-cycle": "巡检并处理收到的消息", "fill-publish": "图文发布",
        "fill-publish-video": "视频发布", "long-article": "长文发布"};
      const body = result.business_result || {};
      const items = Array.isArray(result.item_results) && result.item_results.length
        ? result.item_results : [result];
      return items.map((item, index) => ({
        id: `${result.result_id || result.task_id}:${index}`,
        recordId: result.record_id || "", itemIndex: index,
        kind: resultActionKind(result), label: labels[capability]
          || (/patrol/i.test(result.task_id || "") ? "定时巡检" : "") || request.request_summary
          || result.request_summary || result.message || "任务记录（动作类型待补全）",
        taskId: result.task_id, capability,
        target: item.nickname || item.target?.nickname || body.target?.nickname || item.title
          || item.note_title || item.user_id || item.feed_id || "",
        outcome: businessText(item.message || result.message || result.result_summary
          || (typeof result.summary === "string" ? result.summary : "")),
        steps: [item.browse ? `阅读：${executionStatus(item.browse.status || "UNKNOWN")}` : "",
          item.like ? `点赞：${executionStatus(item.like.status || "UNKNOWN")}`
            : item.browse ? "点赞：未记录执行" : ""].filter(Boolean).join("；"),
        at: item.completed_at || result.completed_at || result.created_at || result.collected_at,
        url: resultLinks(item)[0]?.url || resultLinks(result)[0]?.url || "",
        content: item.comment_text || item.content || item.reply_text || "",
        contentLabel: /comment|reply|send-private/.test(capability) ? "本次文本" : "内容摘要",
        status: item.state || item.status || result.business_state || result.state || "UNKNOWN",
        source: item.execution_source || result.execution_source || "machine",
        attributed: Boolean(item.attribution_updated_at || result.attribution_updated_at),
      }));
    }).sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  }

  function historyDates(account) {
    const today = localDateValue(new Date());
    return [...new Set([
      ...(state.dailyPlans || []).filter((plan) => plan.accounts.some((row) => key(row) === key(account)))
        .map((plan) => plan.plan_date),
      ...(state.employeeSummary?.daily_metrics || []).filter((row) => key(row) === key(account))
        .map((row) => row.date),
      ...receipts(account).map(resultDate),
      ...state.tasks.filter((task) => task.machine_id === account.machine_id
        && task.request?.account_slot === account.account_slot).map(workerTaskDate),
      ...state.goals.filter((goal) => goal.period_start === goal.period_end
        && goal.targets?.some((target) => key(target) === key(account))).map((goal) => goal.period_start),
    ].filter((date) => date && date < today))].sort().reverse().slice(0, 30);
  }

  function planDraft() {
    const saved = (state.dailyPlans || []).find((plan) => plan.plan_date === planDate);
    let draft = drafts.get(planDate);
    if (!draft || (!draft.dirty && draft.version !== saved?.version)) {
      const legacy = !saved && legacyTemplates[planDate];
      draft = {date: planDate, rows: (saved?.accounts || legacy?.rows || []).map((row) =>
        ({...row, metrics: metricValues(row.metrics)})),
        updatedAt: saved?.updated_at || "", version: saved?.version, dirty: Boolean(legacy)};
      drafts.set(planDate, draft);
    }
    state.accounts.forEach((account) => {
      if (draft.rows.some((row) => key(row) === key(account))) return;
      const row = saved?.accounts.find((item) => key(item) === key(account));
      draft.rows.push({machine_id: account.machine_id, account_slot: account.account_slot,
        participating: row?.participating ?? account.health_state === "READY",
        stage: row?.stage || account.stage || "", pressure: row?.pressure || account.pressure || "", notes: row?.notes || "",
        metrics: metricValues(row?.metrics)});
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
      <summary><div><strong>${esc(accountName(account || row))}</strong><small>所属机器 ${esc(row.machine_id)} · ${esc(row.account_slot)}</small><span data-plan-caption>${esc(stage)} · ${esc(pressure)}</span></div>
        <span data-plan-badge>${badge(row.participating ? "参与规划" : "静默观察", row.participating ? "good" : "warn")}</span>
        <div class="strategy-plan-preview">${metricFields.map(([kind, label]) =>
          `<span>${label}<b data-plan-preview="${kind}">${number(row.metrics[kind])}</b></span>`).join("")}</div></summary>
      <div class="strategy-node-body"><div class="strategy-node-settings">
        <label><span>是否参与</span><select data-plan-field="participating">${options(
          [["yes", "参与明日规划"], ["no", "暂停 / 静默"]], row.participating ? "yes" : "no")}</select></label>
        <label><span>账号阶段</span><select data-plan-field="stage">${options(["", "稳定运营", "新机第 1 天", "新机第 2–3 天", "账号安全观察"], row.stage)}</select></label>
        ${input("压力等级", row.pressure, "text", 'data-plan-field="pressure" placeholder="高压 / 低压"')}
      </div><div class="strategy-exact-grid">${metricFields.map(([kind, label]) => input(label,
        row.metrics[kind] ?? "", "number", `min="0" step="1" placeholder="—" data-plan-metric="${kind}" aria-label="${esc(row.machine_id)} ${esc(row.account_slot)} ${label}"`)).join("")}</div>
      <div class="strategy-evidence-grid" aria-label="${esc(row.machine_id)} 自动运营依据">${facts([
        ["已采集主页帖子", evidence.lifetime_posts ?? "未读取"], ["上次发帖日期", evidence.last_post_date || "未读取"],
        ["运营天数", account?.operating_days ?? "未读取"], ["暖互动线索", evidence.warm_candidate_count ?? "未读取"],
      ])}</div><div class="strategy-range-line"><span>长期区间：评论 ${esc(evidence.comment_range || "—")}</span>
        <span>${esc(row.notes || "等待经理 Agent 配置运营依据。")}</span></div>
      </div></details>`;
  }

  function planSummary() {
    const draft = planDraft();
    const rows = draft.rows.filter((row) => row.participating);
    const values = rows.flatMap((row) => ["browse", "publish", "like", "comment", "reply", "friend_request"].map((kind) => row.metrics[kind]))
      .filter((value) => value !== null);
    const configs = rows.map((row) => state.accounts.find((a) => key(a) === key(row)) || {});
    const rules = configs.map((a) => {
      const rule = a.publish_rules || {};
      return rule.daily_limit != null || rule.min_interval_minutes != null
        ? `${a.machine_id}/${a.account_slot}：每日 ${rule.daily_limit ?? "—"} 篇，间隔 ${rule.min_interval_minutes ?? "—"} 分钟` : "";
    }).filter(Boolean);
    const policies = configs.map((a) => a.exception_policy ? `${a.machine_id}/${a.account_slot}：${a.exception_policy}` : "").filter(Boolean);
    return facts([["参与规划", `${rows.length} 个账号`],
      ["计划动作上限", values.length ? values.reduce((sum, value) => sum + value, 0) : "—", "统计浏览、发帖、点赞、评论、回复与关注博主"],
      ["发帖规则", rules.join("；") || "待配置", "经理 Agent 配置的发帖总量与间隔"],
      ["账号异常", policies.join("；") || "待配置", "经理 Agent 配置的异常处理策略"]]);
  }

  function planningPanel() {
    const draft = planDraft();
    const submitted = (state.dailyPlans || []).find((plan) => plan.plan_date === draft.date)?.submissions?.[0];
    return `<section class="panel strategy-panel" data-panel="tomorrow-plan">
      <div class="strategy-heading"><div><p class="eyebrow">经理机运营策略</p><h2>明日任务规划</h2>
        <p class="plain-help">生成建议后按账号调整，保存版本并预览执行材料；提交后进入经理执行队列。</p></div>
      <div class="strategy-save-bar">${input("计划日期", planDate, "date", 'data-plan-date')}
        <button type="button" data-unit-action="save-plan">保存计划</button>
        <button type="button" data-unit-action="generate-plan">生成智能建议</button>
        <button type="button" data-unit-action="preview-plan"${draft.dirty || !draft.version ? " disabled" : ""}>预览执行材料</button>
        <button type="button" data-unit-action="submit-plan"${draft.dirty || !draft.version || submitted ? " disabled" : ""}>${submitted ? `已提交 v${esc(submitted.version)}` : "提交经理执行"}</button>
        <small class="save-state" data-plan-message role="status">${draft.dirty ? "已修改，待保存" : draft.updatedAt ? `计划上次保存 ${stamp(draft.updatedAt)}` : "尚未保存"}</small></div></div>
      <div class="strategy-summary" data-plan-summary>${planSummary()}</div>
      ${submitted ? `<p class="plain-help">执行与日报依据已提交的 v${esc(submitted.version)}；目标与素材可在原每日任务中追加。</p>` : ""}
      <div class="strategy-rules">${["执行材料包含账号人设", "发帖经过预览确认", "点赞约为评论的 1/3", "关注博主仅针对暖互动对象", "按配置检查真实入站", "完成率按硬指标分别封顶"]
        .map((text) => `<span>${text}</span>`).join("")}</div>
      <div class="strategy-node-grid">${draft.rows.map(planCard).join("") || '<p class="empty-state">等待员工机账号同步。</p>'}</div>
      <div class="dispatch-template" data-dispatch-template${draft.updatedAt ? "" : " hidden"}>
        <div class="dispatch-template-head"><div><strong>复制到经理 Agent 的下发模板</strong><span>模板包含每个账号的运营数量和所属机器。</span></div>
        <button type="button" data-unit-action="copy-template">复制模板</button></div>
        <textarea rows="16" readonly aria-label="下发模板">${esc(dispatchText(draft))}</textarea>
        <small class="save-state" data-copy-message role="status"></small></div></section>`;
  }

  function dispatchText(draft) {
    return `${draft.date} 账号运营计划\n\n${draft.rows.map((row) =>
      `${accountName(state.accounts.find((account) => key(account) === key(row)) || row)} · ${row.participating ? "参与规划" : "暂停"}\n所属机器：${row.machine_id}；账号：${row.account_slot}\n`
      + `账号阶段：${row.stage || "待配置"}；压力等级：${row.pressure || "待配置"}\n`
      + metricFields.map(([kind, label]) => `${label}：${row.metrics[kind] ?? "未设置"}`).join("；")
      + (row.notes ? `\n说明：${row.notes}` : "")).join("\n\n")}\n\n计划版本：${draft.version ?? "待保存"}。浏览、发帖、点赞、评论、回复为硬指标；关注博主按机会预算执行，增长数据单独观察。\n请预览执行材料，核对账号人设、时间窗口、间隔、发帖规则和待办要求后提交经理执行。`;
  }

  function accountCard(account) {
    const machine = machineFor(account);
    const persona = accountPersona(account);
    return `<button type="button" class="overview-node-card tone-${tone(account.health_state)}"
      data-employee-account-select="${esc(key(account))}">
      <div class="overview-node-head"><div><span>${platformName(account)} · ${esc(account.account_slot)}</span>
        <h3>${esc(accountName(account))}</h3></div>${badge(labelFor(account.health_state), tone(account.health_state))}</div>
      <dl>${[["所属机器", machine.host_alias || machine.machine_id], ["账号人设", persona?.display_name || persona?.persona_id || "友好评论者（默认）"],
        ["设备连接", labelFor(machine.ssh_state || "UNKNOWN")],
        ["当前任务", account.current_task_title || "暂无任务"]].map(([name, value]) => `<div><dt>${name}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
      <div class="overview-account-line"><span>账号 UID</span><strong>${esc(account.current_uid || account.uid || "身份待同步")}</strong></div>
      <small class="overview-node-updated">更新 ${stamp(account.updated_at)}</small></button>`;
  }

  function dailyReport() {
    const date = new Date(); date.setDate(date.getDate() - 1);
    const yesterday = localDateValue(date);
    const plan = (state.dailyPlans || []).find((item) => item.plan_date === yesterday);
    if (!plan) return `<section class="panel daily-report-panel" data-panel="daily-report">
      <div class="section-heading compact"><div><p class="eyebrow">昨日日报</p><h2>${yesterday}</h2></div>${badge("暂无日报")}</div>
      <p class="daily-report-empty">没有找到该日期保存的运营计划</p></section>`;
    const report = state.employeeSummary?.daily_report;
    if (!report || report.date !== yesterday) return `<section class="panel daily-report-panel" data-panel="daily-report"><h2>${yesterday} 运营日报</h2><p>等待日报数据。</p></section>`;
    const rows = report.rows;
    const percent = (value) => value == null ? "—" : `${Math.round(value * 100)}%`;
    return `<section class="panel daily-report-panel" data-panel="daily-report"><div class="daily-report-heading">
      <div><p class="eyebrow">昨日计划复盘</p><h2>${yesterday} 运营日报</h2><p>自动对比保存计划与员工机正式回执。</p></div>${badge("持续汇总", "accent")}</div>
      <div class="daily-report-summary">${facts([["计划账号", plan.accounts.filter((row) => row.participating).length],
        ["所属机器", report.planned_machines], ["计划动作", report.target],
        ["实际动作", report.actual], ["出现异常", report.exception_count]])}</div>
      <div class="pressure-conclusion"><div><span>高压 / 低压结论</span><strong>${esc(report.conclusion)}</strong></div>
      <small>完成率采用有完整动作回执的账号；异常率按已回收任务计算。</small></div>
      <div class="pressure-group-grid">${report.groups.map((group) => `<div><strong>${esc(group.label)}</strong><span>纳入对比 ${group.analyzed_count}/${group.account_count} · 动作 ${number(group.actual)}</span><span>平均完成率 ${percent(group.completion_rate)} · 异常率 ${percent(group.exception_rate)}</span></div>`).join("")}</div>
      <div class="daily-report-table-wrap"><table class="daily-report-table"><thead><tr>
      ${["账号 / 所属机器", "压力", ...metricFields.map(([, label]) => label), "外部动作", "完成率", "结果"].map((label) => `<th>${label}</th>`).join("")}
      </tr></thead><tbody>${rows.map((row) => {const metrics = dailyMetrics(row, yesterday); return `<tr>
        <th>${esc(accountName(state.accounts.find((account) => key(account) === key(row)) || row))}<small>${esc(row.machine_id)} · ${esc(row.account_slot)}</small></th><td>${esc(row.pressure || "待分组")}</td>
        ${metrics.map((metric) => `<td>${number(metric.target)} / ${metricActual(metric)}</td>`)
          .join("")}<td>${number(row.external_actions)}</td><td>${percent(row.completion_rate)}</td><td>${esc(row.status)}</td></tr>`;}).join("")}</tbody></table></div>
      <small class="daily-report-footnote">每格格式为“目标 / 实际”。</small></section>`;
  }

  function overview() {
    const alerts = operationalExceptions();
    const online = state.machines.filter((machine) => machine.ssh_state === "ONLINE").length;
    const today = localDateValue(new Date());
    const metrics = state.accounts.flatMap((account) => workerDayMetrics(account.machine_id, account.account_slot, today));
    const actual = metrics.filter((item) => actionKinds.includes(item.kind) && item.actual != null);
    return `<div class="workspace-overview"><div class="workspace-heading"><div><p class="eyebrow">经理机管理面板</p>
      <h2>账号运营总览</h2><p class="plain-help">每个账号独立展示运营计划与执行结果，机器标明账号所属设备。</p></div>${badge("实时只读")}</div>
      <div class="overview-summary">${facts([["账号单元", state.accounts.length, "每个账号一个运营单元"],
        ["今日已记录动作", actual.length ? actual.reduce((sum, r) => sum + r.actual, 0) : null, "来自员工机回执"],
        ["所属机器", state.machines.length, `${online} 台设备连接正常`], ["需要查看", alerts.length, "告警和待处理项"]])}</div>
      <section class="overview-node-panel" data-panel="accounts"><div class="overview-section-head"><div><strong>账号单元</strong>
        <span>状态颜色：绿色正常，黄色受限，红色需要人工处理</span></div><span>${state.accounts.length} 个账号</span></div>
        <div class="overview-node-grid">${state.accounts.map(accountCard).join("") || '<p class="empty-state">等待员工机账号同步</p>'}</div></section>
      ${dailyReport()}<div data-plan-host>${planningPanel()}</div>
      <div class="overview-lower-grid"><section class="panel plain-panel" data-panel="actual-summary">
        <div class="section-heading compact"><div><p class="eyebrow">今日进度</p><h2>实际动作汇总</h2></div><span>${state.accounts.length} 个账号</span></div>
        <div class="result-grid">${facts([["物理员工机", state.machines.length, `${online} 台连接正常`],
          ["需要人工查看", alerts.length, "员工机上报后仍未处理的异常"],
          ["尚未绑定账号", state.machines.filter((machine) => !state.accounts.some((a) => a.machine_id === machine.machine_id)).length, "员工机账号绑定情况"]]).replaceAll("<div>", '<div class="result-item">')}</div></section>
      <section class="panel plain-panel" data-panel="alerts"><div class="section-heading compact"><div><p class="eyebrow">需要查看</p><h2>告警与提醒</h2></div><span>${alerts.length} 条</span></div>
        <div class="alert-list">${alerts.slice(0, 6).map((alert) => `<article class="alert-row"><span>${esc(labelFor(alert.state))}</span><div><strong>${esc(alert.title)}</strong><small>${esc(alert.object)} · ${esc(alert.message)}</small></div></article>`).join("")
          || '<p class="empty-state">现在没有未关闭告警</p>'}</div></section></div></div>`;
  }

  function nextDay(account) {
    const today = localDateValue(new Date());
    const plan = [...(state.dailyPlans || [])].filter((item) => item.plan_date > today
      && item.accounts.some((row) => key(row) === key(account) && row.participating))
      .sort((a, b) => a.plan_date.localeCompare(b.plan_date))[0];
    const row = plan?.accounts.find((item) => key(item) === key(account) && item.participating);
    const saved = row;
    return `<section class="panel node-strategy-card" data-panel="next-day"><div class="node-strategy-head"><div>
      <p class="eyebrow">下一个运营日</p><h3>${plan ? esc(`${plan.plan_date} · ${saved?.stage || "账号阶段待配置"}`) : "等待运营规划"}${saved?.pressure ? ` · ${esc(saved.pressure)}` : ""}</h3></div>
      ${badge(plan ? "已规划" : "待规划", plan ? "good" : "muted")}</div>
      <div class="node-strategy-plan">${facts(metricFields.map(([kind, label]) => [label, saved?.metrics?.[kind] ?? row?.metrics?.[kind]]))}</div>
      <p>${esc(row?.notes || "等待经理 Agent 下发该账号的下一运营日目标。")}</p></section>`;
  }

  function todayTable(account, date, accountActions) {
    const uid = account.current_uid || account.uid || "待同步";
    return `<div class="daily-targets" data-panel="today-targets"><div class="daily-targets-head"><strong>今日目标 / 实际</strong><small>${esc(date)} · ${esc(account.current_task_title || "等待运营任务回传")}</small></div>
      <div class="daily-account-snapshot"><span>今日执行账号</span><strong>${esc(account.nickname || account.account_slot)}</strong><small>Profile ${esc(uid)}</small></div>
      <div class="daily-targets-table"><div class="daily-target-row daily-target-header"><span>指标</span><span>目标</span><span>实际</span><span>状态</span></div>
      ${dailyMetrics(account, date).map((metric) => `${metric.kind === "browse" ? '<div class="daily-metric-group">主动动作</div>' : metric.kind === "reply" ? '<div class="daily-metric-group" title="员工机定时巡检执行或采集，经 Outbox 回传后展示">被动动作与信息</div>' : ""}<div class="daily-target-row"><span${metric.execution_source === "worker_polling" ? ' title="员工机定时巡检执行或采集，经 Outbox 回传后展示"' : ""}>${metric.label}</span><span>${number(metric.target)}</span>
        <span class="daily-actual-cell"><strong>${metricActual(metric)}</strong>
        <span class="daily-action-links">${accountActions.filter((a) => a.kind === metric.kind && a.url).map((a, index) => link(a.url, `机器${index + 1}`)).join("")}</span></span>
        <span>${badge(metric.status, ({success: "good", warning: "warn", info: "muted"}[metric.tone] || "muted"))}
        ${metric.collection?.last_collected_at ? `<small>最近采集：${esc(metric.collection.last_collected_at)}</small>` : ""}</span></div>`).join("")}</div></div>`;
  }

  function historyTable(account) {
    const dates = historyDates(account);
    return `<details class="history-details" data-panel="history" data-expanded="history:${esc(key(account))}"${open(`history:${key(account)}`, dates.length > 0)}>
      <summary>往日目标 / 实际（${dates.length} 天）</summary>
      <div class="history-table-wrap"><table class="history-table"><thead><tr><th>日期</th><th>当日执行账号</th>
      ${metricFields.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>
      ${dates.map((date) => {
        const receipt = receipts(account, date)[0] || {};
        const identity = receipt.account_snapshot || {};
        return `<tr><th>${date}</th><td class="history-account-cell">${esc(identity.nickname || receipt.account_name || account.account_slot)}<small>Profile ${esc(identity.uid || receipt.account_uid || "未记录")}</small></td>
        ${dailyMetrics(account, date).map((metric) => `<td>${number(metric.target)} / ${metricActual(metric)}</td>`).join("")}</tr>`;
      }).join("")
        || `<tr><td colspan="${metricFields.length + 2}">还没有读取到往日的任务记录。</td></tr>`}</tbody></table></div>
      <small class="history-note">每格为“目标 / 实际”。被动信息按发生日期统计；“至少”为已确认部分，“待确认”为全天数量待确认。鼠标悬停查看总量与采样净变化。</small></details>`;
  }

  function actionList(account, accountActions) {
    return `<details class="action-details" data-panel="action-links" data-expanded="actions:${esc(key(account))}"${open(`actions:${key(account)}`)}>
      <summary>执行记录（${accountActions.length} 条，含未完成任务）</summary><div class="action-list">
      ${accountActions.map((action) => `<div class="action-row"><div><strong>${esc(action.label)}</strong>
        <time>${stamp(action.at)}</time><small class="action-account">账号：${esc(account.nickname || account.account_slot)} · Profile ${esc(account.current_uid || account.uid || "未记录")}</small></div>
        ${action.url ? link(action.url, "查看对应笔记或会话") : ""}<div class="visibility-state">${badge(executionStatus(action.status), tone(action.status))}</div>
        <div>${action.target ? `<p>操作对象：${esc(action.target)}</p>` : ""}
        ${action.content ? `<p>${esc(action.contentLabel)}：${esc(action.content)}</p>` : ""}
        ${action.steps ? `<p>${esc(action.steps)}</p>` : ""}
        <p>结果说明：${esc(action.outcome || "该回执未提供具体执行说明，尚无法确认做了哪些操作。")}</p></div>
        <details><summary>技术信息与执行归属</summary><p>任务：${esc(action.taskId)} · 命令：${esc(action.capability || "未记录")}</p>
        <div class="action-management-controls"><label><span>执行者</span><select data-action-record="${esc(action.recordId)}" data-action-item="${action.itemIndex}" data-action-source="${esc(action.source)}"${action.recordId ? "" : " disabled"}>${options([["machine", "机器"], ["human", "人工"]], action.source)}</select></label><small data-attribution-message role="status"></small>
        </div></details></div>`).join("")
        || '<p>当前账号暂无执行记录。</p>'}</div></details>`;
  }

  function moduleDetails(account) {
    const streams = [["comment", "收到的评论与回复", account.comment_reply_alerts, "当前快照没有收到的评论或回复。"],
      ["friend", "关注博主明细", account.friend_events, "当前快照没有关注博主明细。"],
      ["private", "私聊明细", account.private_message_events, "当前快照没有私聊明细。"]];
    return `<div class="module-details-grid">${streams.map(([kind, title, items, empty]) => {
      const records = Array.isArray(items) ? items : [];
      return `<details class="module-details${kind === "comment" ? " comment-reply-details" : ""}" data-panel="${kind}-details"
        data-expanded="${kind}:${esc(key(account))}"${open(`${kind}:${key(account)}`, records.some((item) => item.requires_reply))}>
        <summary><span>${title}</span><strong>${records.length}</strong></summary>
        ${records.length ? `<div class="module-event-list">${records.map((item) => `<article class="module-event${item.requires_reply ? " needs-attention" : ""}">
          <div class="module-event-head"><strong>${esc(item.actor_name || item.peer_name || "用户信息待补全")}</strong>${badge(({NEW: "待处理", IGNORED: "已忽略", REPLIED: "已回复", PENDING_MANUAL: "待人工处理", RESULT_UNKNOWN: "结果待核对"})[item.handling_state] || labelFor(item.handling_state || "UNKNOWN"))}</div>
          <small>${esc(({note_comment: "收到评论", comment_reply: "收到评论回复", private_message: "收到私信", follow_user: "关注博主"})[item.event_type] || "收到的事件")}${item.historical_backfill ? " · 历史补采" : ""}</small>
          <time>${stamp(item.occurred_at)}</time><p>${esc(item.reply_text || item.message_preview || item.note || "内容未回传")}</p>
          ${item.summary ? `<small>处理说明：${esc(item.summary)}</small>` : ""}
          ${item.parent_comment_text ? `<small>回复了：${esc(item.parent_comment_text)}</small>` : ""}
          ${item.location_hint ? `<small>${esc(item.location_hint)}</small>` : ""}
          ${kind === "private" ? `<small>平台阅读状态：${esc(({READ: "已读", UNREAD: "采集前未读", UNKNOWN: "未读取"})[item.platform_read_status] || "未读取")} · 处理状态：${esc(labelFor(item.handling_state || "NEW"))}</small>` : ""}
          ${link(item.target_url || item.thread_url || item.profile_url, item.target_label || (kind === "private" ? "打开会话" : kind === "friend" ? "查看用户主页" : "打开笔记并查看评论"))}</article>`).join("")}</div>` : `<p>${empty}</p>`}</details>`;
    }).join("")}</div>`;
  }

  function profileCard(account, machine) {
    const date = localDateValue(new Date());
    const accountActions = actions(account, date);
    const last = accountActions[0];
    const uid = account.current_uid || account.uid || "未绑定";
    const domain = account.platform === "rednote" ? "www.rednote.com" : "www.xiaohongshu.com";
    const profileUrl = account.profile_url || (uid !== "未绑定" ? `https://${domain}/user/profile/${encodeURIComponent(uid)}` : "");
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
      <div class="machine-profile-link-row"><div><span>${platformName(account)}账号主页</span><strong>Profile ${esc(uid)}</strong></div>${link(profileUrl, "打开主页", "facebook-profile-link")}</div>
      ${todayTable(account, date, accountActions)}${historyTable(account)}
      <div class="worker-business-strip" aria-label="${esc(account.account_slot)} 业务数据">${facts(
        dailyMetrics(account, date).filter((metric) => !actionKinds.includes(metric.kind))
          .map((metric) => [metric.label, metric.actual]))}</div>
      <div class="machine-task">${facts([["当前阶段", account.stage || "待配置"], ["今日已采集会话", account.session_count ?? null],
        ["运营天数", account.operating_days == null ? null : `${account.operating_days} 天`],
        ["最后动作", last ? `${last.label} · ${stamp(last.at)}` : "暂无动作"]])}
      <div class="machine-task-wide"><span>最后动作链接</span>${link(last?.url, "打开最后动作", "last-action-link")}</div>
      <div class="machine-task-wide"><span>发帖受众</span><strong>${esc(account.publish_audience || "待配置")}</strong></div>
      <div class="machine-task-wide"><span>当前任务</span><strong>${esc(account.current_task_title || tasks[0]?.request?.title || "暂无任务")}</strong></div></div>
      ${actionList(account, accountActions)}${moduleDetails(account)}
      <div class="machine-footer"><span>${esc(labelFor(machine.status))}</span><span>心跳 ${stamp(machine.updated_at)} · 抓取 ${stamp(machine.outbox_collection?.observed_at)}</span></div></div></details>`;
  }

  function detail(account) {
    const machine = machineFor(account);
    return `<div class="workspace-detail"><div class="workspace-heading detail-heading"><div><p class="eyebrow">账号运营详情 · ${platformName(account)}</p>
      <h2>${esc(accountName(account))}</h2><p class="plain-help">所属机器 ${esc(account.machine_id)} · 账号 ${esc(account.account_slot)} · 独立查看运营计划、今日进度与历史回执。</p></div>
      <button type="button" data-employee-account-select="">返回全部账号</button></div>${nextDay(account)}
      <div class="node-machine-list">${profileCard(account, machine)}</div></div>`;
  }

  function render(accountKey = selected) {
    selected = state.accounts.some((account) => key(account) === accountKey) ? accountKey : "";
    if (!host) return;
    host.querySelectorAll("details[data-expanded]").forEach((item) => expanded.set(item.dataset.expanded, item.open));
    if (renderedSelection === selected && host.contains(document.activeElement)
      && document.activeElement.matches("input, select, textarea")) return;
    const preservedPlan = !selected && renderedSelection === "" ? host.querySelector("[data-plan-host]") : null;
    const account = state.accounts.find((item) => key(item) === selected);
    host.innerHTML = account ? detail(account) : overview();
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
    host.addEventListener("change", async (event) => {
      if (event.target.matches("[data-action-record]")) {
        const select = event.target;
        const message = select.closest(".action-management-controls").querySelector("[data-attribution-message]");
        select.disabled = true;
        try {
          await services.mutateJson(`${services.managerApi}/results/${encodeURIComponent(select.dataset.actionRecord)}/attribution`, "PATCH", {
            item_index: Number(select.dataset.actionItem), execution_source: select.value,
          });
          select.dataset.actionSource = select.value;
          message.textContent = "执行者已保存";
          await services.load?.();
        } catch (error) {select.value = select.dataset.actionSource; message.textContent = error.message;}
        finally {select.disabled = false;}
        return;
      }
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
      if (["generate-plan", "preview-plan", "submit-plan"].includes(action)) {
        const draft = planDraft();
        if (action !== "generate-plan" && (draft.dirty || !draft.version)) {
          host.querySelector("[data-plan-message]").textContent = "请先保存当前修改。";
          return;
        }
        try {
          button.disabled = true;
          const suffix = action.split("-")[0];
          const payload = await services.mutateJson(`${services.managerApi}/daily-plans/${suffix}`, "POST", {
            plan_date: draft.date, version: draft.version, accounts: draft.rows,
          });
          if (action === "generate-plan") {
            draft.rows = payload.plan.accounts.map((row) => ({...row, metrics: metricValues(row.metrics)}));
            draft.dirty = true;
            host.querySelector("[data-plan-host]").innerHTML = planningPanel();
            host.querySelector("[data-plan-message]").textContent = "智能建议已生成，请审阅后保存。";
          } else if (action === "preview-plan") {
            host.querySelector("[data-dispatch-template] textarea").value = JSON.stringify(payload, null, 2);
            host.querySelector("[data-plan-message]").textContent = "执行材料已显示，可查看规则、预算和待办要求。";
          } else {
            host.querySelector("[data-plan-message]").textContent = "已提交经理执行队列，可在运营任务中查看进度。";
            await services.load?.();
          }
        } catch (error) { host.querySelector("[data-plan-message]").textContent = error.message; }
        finally { button.disabled = false; }
      } else if (action === "save-plan") {
        const draft = planDraft();
        if (draft.rows.some((row) => Object.values(row.metrics).some((value) => value !== null
          && (!Number.isInteger(value) || value < 0)))) {
          host.querySelector("[data-plan-message]").textContent = "请填写大于或等于 0 的整数。";
          return;
        }
        try {
          button.disabled = true;
          const payload = await services.mutateJson(`${services.managerApi}/daily-plans`, "POST", {
            plan_date: draft.date, accounts: draft.rows,
          });
          state.dailyPlans = [...(state.dailyPlans || []).filter((p) => p.plan_date !== draft.date), payload.plan];
          draft.dirty = false;
          host.querySelector("[data-plan-host]").innerHTML = planningPanel();
          host.querySelector("[data-plan-message]").textContent = "计划已保存，经理 Agent 可读取并安排执行。";
          await services.load?.();
        } catch (error) { host.querySelector("[data-plan-message]").textContent = error.message; }
        finally { button.disabled = false; }
      } else if (action === "copy-template") {
        try {
          await navigator.clipboard.writeText(host.querySelector("[data-dispatch-template] textarea").value);
          host.querySelector("[data-copy-message]").textContent = "已复制";
        } catch { host.querySelector("[data-copy-message]").textContent = "请在文本框中全选复制。"; }
      }
    });
  }
  return {init, render, dailyMetrics, historyDates, actions};
};
