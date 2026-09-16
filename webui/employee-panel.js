/* Employee unit overview, shared daily plans and profile receipts. */
window.AutoXhsEmployeePanel = function (services) {
  "use strict";
  const {state, workerDayMetrics, localDateValue, resultDate, resultAccount,
    resultLinks, workerTaskDate, accountPersona, labelFor, operationalExceptions} = services;
  const host = document.querySelector("#employee-unit-content");
  const metricFields = [["browse", "浏览"], ["publish", "发帖"], ["like", "点赞"],
    ["comment", "评论"], ["private_message", "私信发送"], ["friend_request", "关注博主"],
    ["reply", "评论回复"], ["private_reply", "私信回复"], ["new_followers", "新增粉丝"],
    ["received_likes", "收获点赞"], ["received_comments", "收到新评论"],
    ["received_private_messages", "收到私信"]];
  const overviewKinds = ["browse", "publish", "like", "comment", "private_message", "friend_request"];
  const overviewFields = metricFields.filter(([kind]) => overviewKinds.includes(kind));
  const actionKinds = ["publish", "like", "comment", "friend_request", "private_message", "private_reply"];
  const executionStatus = (value) => ({SKIP: "已跳过", SKIPPED: "已跳过",
    PENDING_MANUAL: "待 Agent 处理", PENDING: "待处理", RESULT_UNKNOWN: "结果待核对",
    NOT_ATTEMPTED: "未执行"})[String(value || "").toUpperCase()] || labelFor(value);
  const businessText = (value) => String(value || "").replaceAll("RESULT_UNKNOWN", "结果待核对")
    .replaceAll("PENDING_MANUAL", "待 Agent 处理");
  const expanded = new Map();
  let selected = "";
  let renderedSelection = null;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const number = (value) => value === null || value === undefined ? "—" : esc(value);
  const metricActual = (metric) => {
    const text = metric.actual_display ?? metric.actual;
    const details = [];
    if (metric.metric_semantics === "daily_event_count") {
      details.push("北京时间每日新增，零点切换新一天；累计量与历史日报单独保留");
      if (metric.as_of) details.push(`统计截至：${stamp(metric.as_of)}`);
      if (metric.collection_reason) details.push(metric.collection_reason);
      if (metric.collection_progress?.last_collected_at) details.push(`最近采集：${stamp(metric.collection_progress.last_collected_at)}`);
      if (metric.undated_observations) details.push(`缺少明确日期的历史记录：${metric.undated_observations}`);
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
      hour12: false, timeZone: "Asia/Shanghai"}).format(date);
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
    const events = new Map();
    [...(account.comment_reply_alerts || []), ...(account.private_message_events || [])]
      .forEach((event) => {
        [event.event_id, event.platform_event_id].filter(Boolean)
          .forEach((id) => events.set(id, event));
      });
    return receipts(account, date).flatMap((result) => {
      const request = result.task_request || state.tasks.find((task) => task.task_id === result.task_id)?.request || {};
      const capability = result.capability || request.capability || "";
      const body = result.business_result || {};
      const patrol = /patrol/i.test(result.task_id || "") || capability === "reply-cycle"
        || body.task_type === "message_patrol";
      const observations = receiptObservations(result.incremental_data);
      let collectionRows = Array.isArray(body.collections) ? body.collections
        : Array.isArray(body.coverage) ? body.coverage : observations
          .filter((row) => row.event_type === "collection_observation").map((row) => row.payload || {});
      if (!collectionRows.length && Array.isArray(body.collection_observations)) {
        collectionRows = body.collection_observations.map((row) => row.payload || row);
      }
      if (!collectionRows.length && ["collect-note-comments", "collect-private-messages"]
        .includes(capability) && body.coverage) {
        collectionRows = [{...body, metric: capability === "collect-note-comments"
          ? "received_comments" : "received_private_messages", observed_count: body.count,
        collection_state: result.state}];
      }
      const collecting = patrol || capability.startsWith("collect-")
        || body.collection_success !== undefined || collectionRows.length > 0;
      const items = Array.isArray(result.item_results) && result.item_results_status !== "missing"
        ? result.item_results : [];
      const make = (item, index, kind, extra = {}) => {
        const event = events.get(item.event_id) || events.get(item.original_comment_id)
          || events.get(item.comment_id) || {};
        return {
        id: `${result.record_id || result.result_id || result.task_id}:${index}:${kind}`,
        recordId: index >= 0 ? result.record_id || "" : "", itemIndex: index,
        kind, label: kind === "collection" ? ({"collect-operations-metrics": "采集粉丝与获赞信息",
          "collect-note-comments": "采集收到的评论", "collect-private-messages": "采集收到的私信",
          "reply-inbox-collect": "采集评论与私信"}[item.action || item.operation || capability]
          || behaviorLabels.collection) : kind === "other" ? ({"check-login": "检查登录状态",
          "employee-agent-probe": "检查执行服务", "list-feeds": "读取推荐列表",
          "private-message-context": "读取私信上下文"}[capability]
          || request.request_summary || behaviorLabels.other) : behaviorLabels[kind],
        taskId: result.task_id, capability, roundId: body.round_id || body.cycle?.cycle_id || "",
        target: item.nickname || item.target?.nickname || body.target?.nickname || item.title
          || item.note_title || item.peer_name || event.actor_name || event.peer_name
          || item.user_id || item.feed_id
          || item.conversation_uid || item.original_comment_id || item.event_id || "",
        incoming: event.message_preview || event.reply_text || "",
        outcome: businessText(item.reason || item.message || item.error?.message
          || (items.length ? "" : result.message || result.result_summary
            || (typeof result.summary === "string" ? result.summary : ""))),
        at: item.completed_at || item.sent_at || result.completed_at || body.finished_at
          || result.recorded_at || result.created_at || result.collected_at,
        url: kind === "publish" ? publishedNoteUrl(item, items.length > 1 ? {} : result)
          : resultLinks(item)[0]?.url || event.target_url || event.thread_url
            || resultLinks(result)[0]?.url || "",
        content: item.comment_text || item.content || item.reply_text || "",
        contentLabel: ["comment", "reply", "private_reply", "private_message"].includes(kind)
          ? "本次文本" : "内容摘要",
        status: behaviorStatus(item, kind),
        source: item.execution_source || result.execution_source || "machine",
        attributed: Boolean(item.attribution_updated_at || result.attribution_updated_at),
        ...extra,
        };
      };
      const rows = [];
      if (collecting) {
        const channels = collectionMetrics.map(([metric, label]) => {
          const observation = collectionRows.find((row) => row.metric === metric);
          const coverage = String(observation?.coverage || "").toLowerCase();
          const status = ["FAILED", "BLOCKED"].includes(observation?.collection_state) ? "FAILED"
            : coverage === "complete" ? "SUCCESS" : observation ? "UNKNOWN" : "NOT_COLLECTED";
          const count = observation?.observed_count;
          return {label, status, count: typeof count === "number"
            && (count > 0 || status === "SUCCESS") ? count : null,
          reason: observation?.reason || "", at: observation?.period_end || ""};
        });
        const observed = channels.filter((row) => row.status !== "NOT_COLLECTED");
        const completed = observed.filter((row) => row.status === "SUCCESS").length;
        const status = observed.length ? completed === observed.length ? "SUCCESS"
          : completed ? "PARTIAL_SUCCESS" : observed.some((row) => row.status === "FAILED")
            ? "FAILED" : "UNKNOWN" : body.collection_success === true ? "SUCCESS"
              : body.collection_success === false ? "FAILED" : "UNKNOWN";
        rows.push(make({}, -1, "collection", {status, channels,
          outcome: observed.length ? `本轮已完成 ${completed}/${observed.length} 项采集。`
            : body.collection_success === true ? "采集已完成，各渠道数量未回传。"
              : "各渠道采集结果未回传。",
          steps: typeof body.new_comment_events === "number"
            ? `本轮新发现评论 ${body.new_comment_events} 条` : "",
        }));
      }
      items.forEach((item, index) => {
        if (["home-engagement", "keyword-engagement"].includes(item.action || item.operation)) {
          ["browse", "like"].forEach((kind) => rows.push(make(item, index, kind, {
            status: "UNKNOWN", outcome: item.reason || "复合操作未提供该行为的独立结果。",
          })));
          return;
        }
        const nested = Object.entries(item.actions || item)
          .filter(([name, value]) => behaviorLabels[name] && value && typeof value === "object"
            && !Array.isArray(value));
        if (nested.length) {
          nested.forEach(([kind, detail]) => rows.push(make({...item, ...detail,
            state: detail.state, status: detail.status, success: detail.success,
            platform_readback: detail.platform_readback, verified: detail.verified,
            changed: detail.changed, reason: detail.reason, message: detail.message,
            error: detail.error}, index, kind)));
          return;
        }
        const kind = behaviorKind(item.action || item.operation)
          || channelKind(item.channel) || capabilityKind(capability, request);
        rows.push(make(item, index, kind));
      });
      const outbound = observations.filter((row) => row.event_type === "outbound_reply");
      // A reply may also be carried by an incremental observation in the same receipt.
      outbound.forEach((row) => {
        const reply = row.payload || {};
        if (items.some((item) => (reply.reply_task_id && item.reply_task_id === reply.reply_task_id)
          || (reply.event_id && item.event_id === reply.event_id))) return;
        rows.push(make({...reply, sent_at: row.occurred_at}, -1,
          channelKind(reply.channel) || "reply"));
      });
      for (const [channel, summary] of Object.entries(body.replies || {})) {
        const kind = channelKind(channel);
        if (!kind || rows.some((row) => row.kind === kind)) continue;
        const idle = summary.success === 0 && summary.pending === 0 && summary.remaining === 0
          && !(summary.blocked || summary.failed || summary.result_unknown);
        rows.push(make({}, -1, kind, {status: idle ? "NO_ACTION" : "UNKNOWN",
          outcome: summary.display && summary.display !== "无" ? businessText(summary.display)
            : idle ? "本轮没有待回复消息。" : "回复汇总已回传，逐条结果未提供。"}));
      }
      if (body.collection_success === true && body.frozen_event_count === 0
        && body.pending_count === 0 && !(body.failed_count || body.result_unknown_count)) {
        const selectedKind = behaviorKind(request.parameters?.acceptance_action);
        const emptyKinds = ["reply", "private_reply"].includes(selectedKind)
          ? [selectedKind] : ["reply", "private_reply"];
        emptyKinds.filter((kind) => !rows.some((row) => row.kind === kind)).forEach((kind) =>
          rows.push(make({}, -1, kind, {status: "NO_ACTION", outcome: "本轮没有待回复消息。"})));
      }
      if (!collecting && !rows.length) {
        const kinds = ["home-engagement", "keyword-engagement"].includes(capability)
          ? ["browse", "like"] : [capabilityKind(capability, request)];
        const running = ["RUNNING", "PENDING", "QUEUED", "DISPATCHED", "TASK_ACK"]
          .includes(result.state);
        kinds.forEach((kind) => rows.push(make({...result, ...body}, kinds.length === 1 ? 0 : -1, kind, {
          status: kinds.length > 1 && !running ? "UNKNOWN"
            : behaviorStatus({...result, ...body}, kind),
          outcome: kinds.length > 1 && !running ? "任务已结束，该行为的逐项结果未回传。"
            : businessText(result.message || result.result_summary || result.summary),
        })));
      }
      return rows;
    }).sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  }

  function publishedNoteUrl(item, result) {
    const platform = result.platform || result.task_request?.platform || "xiaohongshu";
    const sources = [item, item.platform_result, item.result, result.business_result,
      result.business_result?.platform_result, result.business_result?.result, result];
    for (const source of sources) {
      const url = source?.note_url || source?.feed_url;
      if (/^https?:\/\//i.test(url || "")) return url;
    }
    for (const source of sources) {
      const id = source?.note_id || source?.noteId || source?.feed_id;
      if (/^[a-f0-9]{24}$/i.test(id || "")) {
        const host = platform === "rednote" ? "www.rednote.com" : "www.xiaohongshu.com";
        const route = platform === "rednote" ? "discovery/item" : "explore";
        return `https://${host}/${route}/${id}`;
      }
    }
    return "";
  }

  const behaviorLabels = {browse: "浏览笔记", like: "点赞笔记", favorite: "收藏笔记",
    comment: "发表评论", friend_request: "关注博主", reply: "回复评论",
    private_reply: "回复私信", private_message: "发送私信", publish: "发布笔记",
    collection: "巡检采集", search: "搜索笔记", other: "其他任务"};
  const collectionMetrics = [["new_followers", "新增粉丝"], ["received_likes", "收获点赞"],
    ["received_comments", "收到评论"], ["received_private_messages", "收到私信"]];
  function behaviorKind(value) {
    const aliases = {follow: "friend_request", "follow-user": "friend_request",
      "inbound-comment-reply": "reply", "private-message-reply": "private_reply",
      "post-comment": "comment", "like-feed": "like", "favorite-feed": "favorite"};
    if (String(value || "").startsWith("collect-") || value === "reply-inbox-collect") {
      return "collection";
    }
    return aliases[value] || (behaviorLabels[value] ? value : null);
  }
  function channelKind(value) {
    return {comment: "reply", COMMENT_REPLY: "reply", private_message: "private_reply",
      PRIVATE_MESSAGE_REPLY: "private_reply"}[value] || null;
  }
  function capabilityKind(capability, request) {
    const kinds = {"favorite-feed": "favorite", "reply-inbox": "reply",
      "random-comment": "comment", "reply-comment": "reply", "get-feed-detail": "browse",
      "search-feeds": "search", "send-private-messages": "private_message",
      "click-publish": "publish", "manual-publish": "publish", "fill-publish": "publish",
      "fill-publish-video": "publish", "long-article": "publish"};
    return behaviorKind(request.parameters?.acceptance_action) || behaviorKind(capability)
      || kinds[capability] || "other";
  }
  function behaviorStatus(item, kind) {
    const status = String(item.send_state || item.state || item.status || "").toUpperCase();
    if (["RESULT_UNKNOWN", "RECONCILIATION_REQUIRED", "FAILED", "BLOCKED", "SKIPPED",
      "SKIP", "NOT_SENT", "NOT_ATTEMPTED", "PENDING_MANUAL", "PENDING_LOCAL",
      "RUNNING", "PENDING", "QUEUED", "DISPATCHED", "TASK_ACK", "CANCELLED"].includes(status)) {
      return status;
    }
    if (item.success === false) return "FAILED";
    const success = item.success === true || ["SUCCESS", "COMPLETED", "CONFIRMED"].includes(status);
    if (!success) return "UNKNOWN";
    if (["browse", "search", "collection", "other"].includes(kind)) return "SUCCESS";
    if (item.platform_readback !== true && item.verified !== true && status !== "CONFIRMED") {
      return "UNKNOWN";
    }
    return item.changed === false ? "NO_CHANGE" : "SUCCESS";
  }
  function receiptObservations(value) {
    if (Array.isArray(value)) return value.flatMap(receiptObservations);
    if (!value || typeof value !== "object") return [];
    if (value.event_type) return [value];
    return ["events", "inbound_events", "created_events", "existing_events", "incremental_data",
      "reconciled_outbound", "collection_observations"].flatMap((name) =>
      receiptObservations(value[name]));
  }
  function behaviorResult(action) {
    if (["SKIP", "SKIPPED"].includes(action.status)
      && /无待回复|没有待回复/.test(action.outcome || "")) return "无需执行";
    const labels = {SUCCESS: "成功", FAILED: "失败", BLOCKED: "未执行", NOT_SENT: "未发送",
      SKIPPED: "已跳过", SKIP: "已跳过", NOT_ATTEMPTED: "未执行", NO_ACTION: "无需执行",
      NO_CHANGE: "已满足", UNKNOWN: "待核对", RESULT_UNKNOWN: "待核对",
      RECONCILIATION_REQUIRED: "待核对", PENDING_MANUAL: "待处理", PENDING_LOCAL: "待处理",
      PENDING: "等待执行", QUEUED: "等待执行", DISPATCHED: "等待执行", TASK_ACK: "等待执行",
      RUNNING: "执行中", CANCELLED: "已取消", PARTIAL_SUCCESS: "部分完成",
      NOT_COLLECTED: "未采集到结果"};
    if (action.kind === "collection" && action.status === "SUCCESS") return "已完成";
    return labels[action.status] || "待核对";
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
      ${["账号 / 所属机器", "压力", ...overviewFields.map(([, label]) => label), "外部动作", "完成率", "结果"].map((label) => `<th>${label}</th>`).join("")}
      </tr></thead><tbody>${rows.map((row) => {const metrics = dailyMetrics(row, yesterday).filter((metric) => overviewKinds.includes(metric.kind)); return `<tr>
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
    const actual = metrics.filter((item) => overviewKinds.includes(item.kind) && item.actual != null);
    return `<div class="workspace-overview"><div class="workspace-heading"><div><p class="eyebrow">经理机管理面板</p>
      <h2>全部账号</h2><p class="plain-help">每个账号独立展示运营计划与执行结果，机器标明账号所属设备。</p></div>${badge("实时只读")}</div>
      <div class="overview-summary">${facts([["账号单元", state.accounts.length, "每个账号一个运营单元"],
        ["今日已记录动作", actual.length ? actual.reduce((sum, r) => sum + r.actual, 0) : null, "来自员工机回执"],
        ["所属机器", state.machines.length, `${online} 台设备连接正常`], ["需要查看", alerts.length, "告警和待处理项"]])}</div>
      <section class="overview-node-panel" data-panel="accounts"><div class="overview-section-head"><div><strong>账号单元</strong>
        <span>状态颜色：绿色正常，黄色受限，红色需要人工处理</span></div><span>${state.accounts.length} 个账号</span></div>
        <div class="overview-node-grid">${state.accounts.map(accountCard).join("") || '<p class="empty-state">等待员工机账号同步</p>'}</div></section>
      ${operationsOverview()}${todayPlanning()}${dailyReport()}
      <div class="overview-lower-grid"><section class="panel plain-panel" data-panel="actual-summary">
        <div class="section-heading compact"><div><p class="eyebrow">今日进度</p><h2>实际动作汇总</h2></div><span>${state.accounts.length} 个账号</span></div>
        <div class="result-grid">${facts([["物理员工机", state.machines.length, `${online} 台连接正常`],
          ["需要人工查看", alerts.length, "员工机上报后仍未处理的异常"],
          ["尚未绑定账号", state.machines.filter((machine) => !state.accounts.some((a) => a.machine_id === machine.machine_id)).length, "员工机账号绑定情况"]]).replaceAll("<div>", '<div class="result-item">')}</div></section>
      <section class="panel plain-panel" data-panel="alerts"><div class="section-heading compact"><div><p class="eyebrow">需要查看</p><h2>告警与提醒</h2></div><span>${alerts.length} 条</span></div>
        <div class="alert-list">${alerts.slice(0, 6).map((alert) => `<article class="alert-row"><span>${esc(labelFor(alert.state))}</span><div><strong>${esc(alert.title)}</strong><small>${esc(alert.object)} · ${esc(alert.message)}</small></div></article>`).join("")
          || '<p class="empty-state">现在没有未关闭告警</p>'}</div></section></div></div>`;
  }

  function planMetricStatus(metric) {
    if (metric.target == null) return metric.actual == null ? "待回传" : "已回传";
    if (metric.actual == null || metric.incomplete) return "等待回执";
    return metric.actual >= metric.target ? "顺利完成" : `未完成 · 差 ${metric.target - metric.actual}`;
  }

  function planHandling(account, metrics, paused, today) {
    if (paused) return {status: "今日不参与", tone: "muted", reason: "账号今日计划不参与。", method: "按经理 Agent 的运营安排执行。"};
    const targets = metrics.filter((metric) => metric.target != null);
    const pending = targets.filter((metric) => metric.actual == null || metric.incomplete || metric.actual < metric.target);
    if (targets.length && !pending.length) return {status: "顺利完成", tone: "good",
      reason: "已确认的实际数量达到全部已设计划数量。", method: "今日计划已完成，持续接收员工机回执。"};
    const tasks = state.tasks.filter((task) => task.machine_id === account.machine_id
      && task.request?.account_slot === account.account_slot && workerTaskDate(task) === today);
    const active = tasks.some((task) => ["RUNNING", "ACCEPTED", "ACKNOWLEDGED"].includes(task.business_state || task.state || task.dispatch_state));
    const issues = actions(account, today).filter((action) =>
      ["FAILED", "BLOCKED", "RESULT_UNKNOWN", "UNKNOWN", "PENDING_MANUAL"].includes(action.status)
      && (!targets.length || pending.some((metric) => metric.kind === action.kind)));
    const reasons = [...new Set(issues.map((action) => `${action.label}：${action.outcome || executionStatus(action.status)}`))];
    const failed = tasks.filter((task) => ["FAILED", "BLOCKED", "PAUSED", "RESULT_UNKNOWN"].includes(task.business_state || task.state));
    failed.forEach((task) => {
      const reason = task.blocked_reason || task.error?.message || task.result_summary || task.message;
      if (reason && !reasons.includes(reason)) reasons.push(reason);
    });
    const unknown = issues.some((action) => ["RESULT_UNKNOWN", "UNKNOWN"].includes(action.status))
      || failed.some((task) => (task.business_state || task.state) === "RESULT_UNKNOWN");
    const recommendation = failed.find((task) => task.recommended_action)?.recommended_action;
    return {status: reasons.length ? "未完成" : active ? "进行中" : targets.length ? "未完成" : "待规划", tone: reasons.length ? "warn" : "muted",
      reason: reasons.join("；") || (active ? "员工机正在执行，等待后续回执。" : targets.length
        ? "尚未收到完整达标回执，具体阻塞原因未回传。" : "尚未设置今日目标。"),
      method: recommendation || (unknown ? "先核对原执行结果，由经理 Agent 安排后续处理。"
        : reasons.length ? "由经理 Agent 根据阻塞原因安排处理，员工机回传处理结果。"
        : active ? "员工机继续执行并回传进度。" : targets.length ? "等待员工机回传进度与未完成原因。" : "等待经理 Agent 下发今日计划。")};
  }

  function operationsDays() {
    const today = localDateValue(new Date());
    const dates = new Set([today, ...(state.dailyPlans || []).map((p) => p.plan_date),
      ...(state.employeeSummary?.daily_metrics || []).map((r) => r.date),
      ...state.results.map(resultDate), ...state.tasks.map(workerTaskDate),
      ...state.goals.filter((g) => g.period_start === g.period_end).map((g) => g.period_start)]);
    return [...dates].filter((date) => /^\d{4}-\d{2}-\d{2}$/.test(date || ""))
      .sort().reverse().map((date) => {
        const plan = (state.dailyPlans || []).find((p) => p.plan_date === date);
        const accounts = new Map(state.accounts.map((a) => [key(a), a]));
        for (const a of [...(plan?.accounts || []),
          ...(state.employeeSummary?.daily_metrics || []).filter((r) => r.date === date),
          ...state.results.filter((r) => resultDate(r) === date)
            .map((r) => ({machine_id: r.machine_id, account_slot: resultAccount(r)}))]) {
          if (a.machine_id && a.account_slot && !accounts.has(key(a))) accounts.set(key(a), a);
        }
        const compared = [...accounts.values()].map((account) => {
          const row = plan?.accounts.find((a) => key(a) === key(account));
          return dailyMetrics(account, date).filter((m) => overviewKinds.includes(m.kind))
            .map((m) => ({...m, target: row
              ? (row.participating === false ? null : row.metrics?.[m.kind] ?? null) : m.target}));
        });
        const metrics = overviewFields.map(([kind, label]) => {
          const rows = compared.flat().filter((m) => m.kind === kind);
          const targets = rows.filter((m) => m.target != null);
          const actuals = rows.filter((m) => m.actual != null);
          return {kind, label,
            target: targets.length ? targets.reduce((n, m) => n + Number(m.target), 0) : null,
            actual: actuals.length ? actuals.reduce((n, m) => n + Number(m.actual), 0) : null,
            incomplete: rows.some((m) => m.incomplete || (m.target != null && m.actual == null)),
          };
        });
        const targets = compared.flat().filter((m) => m.target != null);
        const complete = targets.length && targets.every((m) => !m.incomplete
          && m.actual != null && m.actual >= m.target);
        const missing = targets.some((m) => m.actual == null || m.incomplete);
        return {date, metrics, status: complete ? "顺利完成" : missing ? "等待回执"
          : targets.length ? "未完成" : "未设计划", tone: complete ? "good" : "muted"};
      });
  }

  function operationsOverview() {
    const days = operationsDays();
    return `<section class="panel" data-panel="operations-overview">
      <div class="section-heading compact"><div><h2>运营总览</h2>
        <p class="plain-help">每日运营计划总览 · 汇总全部账号，数量按计划 / 实际展示</p></div>
        <span>${days.length} 天</span></div>
      <div class="daily-report-table-wrap operations-days-scroll"><table class="daily-report-table">
        <thead><tr><th>运营日期</th>${overviewFields.map(([, label]) => `<th>${label}<small>计划 / 实际</small></th>`).join("")}
          <th>完成情况</th></tr></thead><tbody>${days.map((day) => `<tr data-operation-date="${esc(day.date)}">
          <th>${esc(day.date)}</th>${day.metrics.map((m) => `<td>${number(m.target)} / ${number(m.actual)}${m.incomplete ? '<small>部分数据待回传</small>' : ''}</td>`).join("")}
          <td>${badge(day.status, day.tone)}</td></tr>`).join("")}</tbody></table></div></section>`;
  }

  function todayPlanning() {
    const today = localDateValue(new Date());
    const plan = (state.dailyPlans || []).find((item) => item.plan_date === today);
    return `<section class="panel" data-panel="today-planning">
      <div class="section-heading compact"><h2>今日运营规划</h2><span>${esc(today)}</span></div>
      ${state.accounts.map((account) => {
        const row = plan?.accounts.find((item) => key(item) === key(account));
        const metrics = dailyMetrics(account, today);
        const paused = row?.participating === false;
        const compared = metrics.filter((metric) => overviewKinds.includes(metric.kind)).map((metric) => ({...metric,
          target: row ? (paused ? null : row.metrics?.[metric.kind] ?? null) : metric.target}));
        const handling = planHandling(account, compared, paused, today);
        return `<article class="panel node-strategy-card" data-today-plan-account="${esc(key(account))}">
          <div class="node-strategy-head"><div><h3>${esc(accountName(account))}</h3>
            <p class="eyebrow">${platformName(account)} · 所属机器 ${esc(account.machine_id)} · ${esc(account.account_slot)}</p></div>
            ${badge(handling.status, handling.tone)}</div>
          <div class="node-strategy-plan">${compared.map((metric) => `<div>
            <span>${esc(metric.label)}</span><small>计划 / 实际</small>
            <strong>${number(metric.target)} / ${metricActual(metric)}</strong>
            <small>${esc(planMetricStatus(metric))}</small></div>`).join("")}</div>
          <div class="today-plan-handling"><p><strong>${handling.status === "顺利完成" ? "完成情况" : "执行情况 / 阻塞原因"}：</strong>${esc(handling.reason)}</p>
            <p><strong>处理方式：</strong>${esc(handling.method)}</p></div>
          ${row?.notes ? `<p>计划说明：${esc(row.notes)}</p>` : ""}
        </article>`;
      }).join("") || '<p class="empty-state">等待员工机账号同步。</p>'}</section>`;
  }

  function todayTable(account, date) {
    const uid = account.current_uid || account.uid || "待同步";
    const taskSummary = account.current_task_title || (receipts(account, date).length
      ? "今日执行回执已回传" : "今日执行回执待回传");
    return `<div class="daily-targets" data-panel="today-targets"><div class="daily-targets-head"><strong>今日目标 / 实际</strong><small>${esc(date)} · ${esc(taskSummary)}</small></div>
      <div class="daily-account-snapshot"><span>今日执行账号</span><strong>${esc(account.nickname || account.account_slot)}</strong><small>Profile ${esc(uid)}</small></div>
      <div class="daily-targets-table"><div class="daily-target-row daily-target-header"><span>指标</span><span>目标</span><span>实际</span><span>状态</span></div>
      ${dailyMetrics(account, date).map((metric) => `${metric.kind === "browse" ? '<div class="daily-metric-group">主动动作</div>' : metric.kind === "reply" ? '<div class="daily-metric-group" title="员工机定时巡检执行或采集，经 Outbox 回传后展示">被动动作与信息</div>' : ""}<div class="daily-target-row"><span${metric.execution_source === "worker_polling" ? ' title="员工机定时巡检执行或采集，经 Outbox 回传后展示"' : ""}>${metric.label}</span><span>${number(metric.target)}</span>
        <span class="daily-actual-cell"><strong>${metricActual(metric)}</strong></span>
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
      <small class="history-note">每格为“目标 / 实际”。四项被动信息为北京时间每日新增，零点切换新一天；历史日报和累计量保留。历史“至少”为已确认数量，“采集缺口”为尚未采齐。鼠标悬停查看采集时间与账号总量。</small></details>`;
  }

  function actionList(account, accountActions) {
    const groups = Object.entries(behaviorLabels).map(([kind, label]) => ({kind, label,
      rows: accountActions.filter((action) => action.kind === kind)})).filter((group) => group.rows.length);
    const totals = (rows) => {
      const counts = new Map();
      rows.forEach((row) => {
        const label = behaviorResult(row);
        counts.set(label, (counts.get(label) || 0) + 1);
      });
      return [...counts].map(([label, count]) => `${label} ${count}`).join(" · ");
    };
    const detail = (action) => `<article class="behavior-record">
      <div class="behavior-record-head"><strong>${esc(action.target || action.label)}</strong>
      ${badge(behaviorResult(action), tone(action.status))}<time>${stamp(action.at)}</time></div>
      ${action.outcome ? `<p>${esc(action.outcome)}</p>` : `<p>${esc(
        action.status === "SUCCESS" ? "该行为已完成。" : action.status === "NO_CHANGE"
          ? "目标状态已满足，本次未产生新增操作。" : "该行为的具体结果说明未回传。")}</p>`}
      ${action.content ? `<p>${esc(action.contentLabel)}：${esc(action.content)}</p>` : ""}
      ${action.incoming ? `<p>收到的内容：${esc(action.incoming)}</p>` : ""}
      ${action.steps ? `<p>${esc(action.steps)}</p>` : ""}
      ${action.channels ? `<div class="collection-channels">${action.channels.map((channel) =>
        `<div><strong>${esc(channel.label)}</strong><span>${esc(behaviorResult({
          kind: "collection", status: channel.status}))}</span><small>${channel.count === null
            ? "数量未确认" : `本次采集记录 ${channel.count} 条`}</small>
        ${channel.reason ? `<small>${esc(channel.reason)}</small>` : ""}
        ${channel.at ? `<small>采集截至 ${stamp(channel.at)}</small>` : ""}</div>`).join("")}</div>
        <small class="behavior-note">本次采集记录可能包含历史数据；每日新增见上方“今日目标 / 实际”。</small>` : ""}
      ${action.url ? link(action.url, action.kind === "publish" ? "打开笔记 · 人工核对" : "查看对应笔记或会话")
        : action.kind === "publish" ? '<p class="behavior-note">笔记链接待回传</p>' : ""}
      <details class="behavior-technical"><summary>技术信息与执行归属</summary>
        <p>任务：${esc(action.taskId || "未记录")} · 命令：${esc(action.capability || "未记录")}</p>
        ${action.roundId ? `<p>轮次：${esc(action.roundId)}</p>` : ""}
        ${action.recordId ? `<div class="action-management-controls"><label><span>执行者</span>
        <select data-action-record="${esc(action.recordId)}" data-action-item="${action.itemIndex}"
          data-action-source="${esc(action.source)}">${options([["machine", "机器"],
          ["human", "人工"]], action.source)}</select></label>
          <small data-attribution-message role="status"></small></div>`
          : `<p>执行者：${action.source === "human" ? "人工" : "机器"}</p>`}
      </details></article>`;
    return `<details class="action-details" data-panel="action-links" data-expanded="actions:${esc(key(account))}"${open(`actions:${key(account)}`, true)}>
      <summary>执行记录 · 按行为查看（${groups.length} 类）</summary>
      <p>账号：${esc(accountName(account))} · 按行为记录汇总，展开查看对象、结果及每轮明细。</p>
      <div class="behavior-groups">${groups.map((group) => {
        const rounds = new Map();
        group.rows.forEach((row) => {
          const id = row.taskId || row.id;
          if (!rounds.has(id)) rounds.set(id, []);
          rounds.get(id).push(row);
        });
        const groupKey = `behavior:${key(account)}:${group.kind}`;
        return `<details class="behavior-group" data-behavior="${group.kind}"
          data-expanded="${esc(groupKey)}"${open(groupKey)}>
          <summary><strong>${esc(group.label)}</strong><span>${esc(totals(group.rows))}</span>
          <small>${group.rows.length} 条记录 · ${rounds.size} 轮</small></summary>
          <div class="behavior-rounds">${[...rounds.values()].map((rows, index) => {
            const roundKey = `${groupKey}:${rows[0].taskId || rows[0].id}`;
            return `<details class="behavior-round" data-expanded="${esc(roundKey)}"${open(roundKey, index === 0)}>
              <summary><time>${stamp(rows[0].at)}</time><span>${esc(totals(rows))}</span>
              <small>查看明细</small></summary>${rows.map(detail).join("")}</details>`;
          }).join("")}</div></details>`;
      }).join("")
        || '<p>当前账号暂无执行记录。</p>'}</div></details>`;
  }

  function privateRule(rule, basis) {
    if (!rule) return "规则记录未回传";
    const style = ({natural: "自然交流", friendly: "友好", professional: "专业", concise: "简洁"})[rule.reply_style] || rule.reply_style || "未设置";
    return `${basis === "current" ? "当前规则" : "处理时规则"} · 版本 ${rule.rule_version || "未记录"} · 自动回复${rule.enabled && rule.private_message_enabled ? "开启" : "关闭"} · 风格：${style}${rule.account_profile ? ` · 账号人设：${rule.account_profile}` : ""}${rule.knowledge ? ` · 回复知识：${rule.knowledge}` : ""}`;
  }

  function privateMessages(item) {
    const replies = item.reply_history || [];
    const decision = item.processing_decision || {};
    return `<small>收到的私信原文</small>
      <time>收到时间：${stamp(item.occurred_at)}</time>
      <p>${esc(item.content || item.message_preview || item.reply_text || "原文未回传")}</p>
      <small>回复原文</small>
      ${replies.length ? replies.map((reply) => `
        <small>${esc(({SUCCESS: "已发送", FAILED: "发送失败", RESULT_UNKNOWN: "发送结果待核对", RUNNING: "发送中"})[reply.result_state] || labelFor(reply.result_state || "UNKNOWN"))}</small>
        <time>回复时间：${reply.sent_at || reply.confirmed_at ? stamp(reply.sent_at || reply.confirmed_at) : "尚无确认发送时间"}</time>
        <p>${esc(reply.reply_text || "回复原文未回传")}</p>
        <small>处理规则：${esc(privateRule(reply.processing_rule, "applied"))}</small>
      `).join("") : `<small>${item.handling_state === "REPLIED" ? "回复记录未回传" : "暂无已发送回复"}</small>`}
      <small>处理规则：${esc(privateRule(item.processing_rule, item.processing_rule_basis))}</small>
      ${decision.reason ? `<small>处理说明：${esc(decision.reason)}</small>` : ""}`;
  }

  function moduleDetails(account) {
    const streams = [["comment", "收到的评论与回复", account.comment_reply_alerts, "当前快照没有收到的评论或回复。"],
      ["friend", "新增关注明细", (account.friend_events || []).filter((item) => item.event_type === "new_follower"), "当前快照没有新增关注记录。"],
      ["private", "收到的私信与回复", account.private_message_events, "当前快照没有收到的私信与回复。"]];
    return `<div class="module-details-grid">${streams.map(([kind, title, items, empty]) => {
      const records = Array.isArray(items) ? items : [];
      return `<details class="module-details${kind === "comment" ? " comment-reply-details" : ""}" data-panel="${kind}-details"
        data-expanded="${kind}:${esc(key(account))}"${open(`${kind}:${key(account)}`, records.some((item) => item.requires_reply))}>
        <summary><span>${title}</span><strong>${records.length}</strong></summary>
        ${records.length ? `<div class="module-event-list">${records.map((item) => `<article class="module-event${item.requires_reply ? " needs-attention" : ""}">
          <div class="module-event-head"><strong>${esc(item.actor_name || item.peer_name || "用户信息待补全")}</strong>${kind === "friend" ? badge("已采集", "good") : badge(({NEW: "待处理", IGNORED: "已忽略", REPLIED: "已回复", PENDING_MANUAL: "待 Agent 处理", RESULT_UNKNOWN: "结果待核对"})[item.handling_state] || labelFor(item.handling_state || "UNKNOWN"))}</div>
          <small>${esc(({note_comment: "收到评论", comment_reply: "收到评论回复", private_message: "收到私信", new_follower: "新增关注"})[item.event_type] || "收到的事件")}${item.historical_backfill ? " · 历史补采" : ""}</small>
          ${kind === "private" ? privateMessages(item) : `<time>${stamp(item.occurred_at)}</time><p>${esc(item.reply_text || item.message_preview || item.note || "内容未回传")}</p>`}
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
      ${todayTable(account, date)}${historyTable(account)}
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
      <button type="button" data-employee-account-select="">返回全部账号</button></div>
      <div class="node-machine-list">${profileCard(account, machine)}</div></div>`;
  }

  function render(accountKey = selected) {
    selected = state.accounts.some((account) => key(account) === accountKey) ? accountKey : "";
    if (!host) return;
    host.querySelectorAll("details[data-expanded]").forEach((item) => expanded.set(item.dataset.expanded, item.open));
    if (renderedSelection === selected && host.contains(document.activeElement)
      && document.activeElement.matches("input, select, textarea")) return;
    const account = state.accounts.find((item) => key(item) === selected);
    host.innerHTML = account ? detail(account) : overview();
    renderedSelection = selected;
  }

  function init() {
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
    });
  }
  return {init, render, dailyMetrics, historyDates, actions, operationsDays};
};
