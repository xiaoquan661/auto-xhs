/* Operator-facing result lists and receipt details. */
window.AutoXhsResultView = function (services) {
  "use strict";
  const {state, element: el, append, stateBadge, formatTime, resultAccount,
    resultCapability, actualResultCount, requestedResultCount, resultDetailCard,
    resultAuditCard, operationReportCard, factList, collectionStatus, resultMatchesFilters,
    resultDate, metricLabels = {}, publishedMetricHistory, publishedNoteMetrics,
    resultLinks, recoverySelection, selectRecoveryAccount} = services;
  const root = document.querySelector("#results");
  const $ = (selector) => root.querySelector(selector);
  const pageSize = 8;
  const pages = {results: 1, incremental: 1, reports: 1, audit: 1};
  let tab = "results";
  let scope = "all";
  let query = "";
  let collectionType = "all";
  let collectionQuery = "";
  let data = {results: [], incremental: [], audit: [], reports: []};
  const status = (record) => String(record.business_state || record.state || record.status || "UNKNOWN").toUpperCase();
  const complete = (record) => ["SUCCESS", "COMPLETED"].includes(status(record));
  const attention = (record) => Boolean(record.manual_action_required)
    || ["FAILED", "BLOCKED", "RESULT_UNKNOWN", "RECONCILIATION_REQUIRED", "PARTIAL_SUCCESS",
      "UNKNOWN", "DISPATCH_UNKNOWN"].includes(status(record));
  const timestamp = (record) => record.recorded_at || record.date || record.created_at || record.collected_at;
  const newest = (records) => [...records].sort((a, b) =>
    (Date.parse(timestamp(b)) || 0) - (Date.parse(timestamp(a)) || 0));
  const actionNames = {"post-comment": "发表评论", "random-comment": "发表评论",
    "reply-comment": "回复评论", "like-feed": "点赞", "list-feeds": "浏览首页",
    "search-feeds": "搜索笔记", "get-feed-detail": "阅读笔记", "home-engagement": "浏览与点赞",
    "collect-note-comments": "采集评论", "collect-private-messages": "采集私信",
    "collect-operations-metrics": "采集运营数据", "send-private-messages": "发送私信",
    "follow-user": "关注博主", "click-publish": "发布笔记", "publish": "发布笔记"};
  const actionName = (record) => actionNames[resultCapability(record)] || "运营任务";
  const accountName = (record) => {
    const slot = resultAccount(record);
    return slot || "账号未记录";
  };
  const labels = {...metricLabels, new_followers: "新增粉丝", received_likes: "收获点赞",
    received_comments: "收到评论", received_private_messages: "收到私信"};
  const growth = ["new_followers", "received_comments", "received_private_messages"];
  const body = (record) => record.payload || record.data || record;
  const kind = (record) => record.event_type || record.entity_type || record.record_type;
  const category = (record) => ({note_comment: "comment", comment_reply: "comment", comment: "comment",
    private_message: "message", private_message_item: "message", new_follower: "follower",
    received_like: "like", account: "metrics", note: "metrics", metrics: "metrics",
    collection_observation: "collection", outbound_reply: "collection"}[kind(record)] || "other");
  const incrementalName = (record) => ({note_comment: "收到评论", comment_reply: "收到评论回复",
    comment: "评论", private_message: "私信会话", private_message_item: "收到私信",
    new_follower: "新增粉丝", received_like: "收获点赞", account: "账号指标快照",
    note: "笔记指标快照", metrics: "指标快照", collection_observation: "采集结果",
    outbound_reply: "回复记录"}[kind(record)] || "其他采集记录");
  const metricFields = {views: "浏览量", likes: "点赞", favorites: "收藏", comments: "评论",
    shares: "转发", followers: "粉丝", notes: "笔记数"};
  const metricPairs = (record) => Object.entries(metricFields).filter(([key]) =>
    (record.metrics || body(record).metrics || record)[key] != null).map(([key, label]) =>
    [label, (record.metrics || body(record).metrics || record)[key]]);
  function collectionTitle(record) {
    if (record.title || record.actor_name) return record.title || record.actor_name;
    if (record.entity_type !== "note") return "";
    for (const result of state.results || []) {
      if (result.machine_id !== record.machine_id || resultAccount(result) !== resultAccount(record)) continue;
      const business = result.business_result || {};
      const note = [business, ...(result.item_results || [])].find((item) =>
        (item.note_id || item.feed_id) === record.entity_id && (item.title || item.note_title));
      if (note) return note.title || note.note_title;
    }
    return record.entity_id || "";
  }
  function collectionText(record) {
    const value = body(record);
    if (category(record) === "metrics") return metricPairs(record)
      .map(([label, count]) => `${label} ${count}`).join(" · ") || "指标值待回传";
    if (kind(record) === "collection_observation") return [labels[value.metric] || value.metric,
      (value.count ?? value.observed_count) == null ? "数量未取得" : `采集 ${value.count ?? value.observed_count} 条`,
      value.reason || (value.coverage === "complete" ? "采集完整" : "采集覆盖待补齐")]
      .filter(Boolean).join(" · ");
    return value.content || value.reply_text || record.message_preview || record.summary
      || value.summary || record.message || "内容待回传";
  }
  const handling = (record) => category(record) === "metrics" ? "累计快照" : ({NEW: "待处理", REPLIED: "已回复", CONFIRMED: "发送已确认",
    IGNORED: "已跳过", HUMAN_OWNED: "人工处理", FAILED: "失败", RESULT_UNKNOWN: "待核对",
    SENT: "已发送", NOT_SENT: "未发送"}[
    body(record).send_state || record.handling_state] || body(record).reason || "已采集");
  function collectionDetail(record) {
    const value = body(record); const content = el("div");
    append(content, el("h3", "", incrementalName(record)), el("p", "recovery-content-text", collectionText(record)),
      factList([["账号", accountName(record)], ["员工机", record.machine_id],
        ["用户", record.actor_name || value.nickname || value.actor_name],
        ["笔记", record.entity_type === "note" ? collectionTitle(record) : value.title || value.feed_id],
        ["处理情况", handling(record)], ["记录时间", formatTime(timestamp(record))],
        ["时间依据", record.time_basis === "platform_timestamp" || record.time_basis === "platform_date"
          ? "平台发生时间" : "采集时间；发生时间以平台记录为准"],
        ...metricPairs(record)]));
    const url = record.target_url || record.note_url || value.note_url || value.url;
    if (url && /^https?:\/\//i.test(url)) {
      const link = el("a", "recovery-detail-button", "打开来源");
      link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
      content.appendChild(link);
    }
    const technical = el("details", "recovery-detail-technical");
    append(technical, el("summary", "", "记录来源与完整数据"),
      el("pre", "recovery-content-text", JSON.stringify(record, null, 2)));
    content.appendChild(technical);
    return content;
  }
  function visibleCollections() {
    return newest(data.incremental).filter((record) =>
      kind(record) !== "received_like"
      && !(kind(record) === "collection_observation" && body(record).metric === "received_likes")
      && (collectionType === "all" || category(record) === collectionType)
      && (!collectionQuery || [collectionText(record), accountName(record), record.actor_name,
        body(record).nickname, collectionTitle(record), body(record).title, record.entity_id]
        .join(" ").toLowerCase().includes(collectionQuery)));
  }
  const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };

  function visibleResults() {
    return newest(data.results).filter((record) =>
      (scope !== "attention" || attention(record)) && (scope !== "success" || complete(record))
      && (!query || [record.result_summary, record.summary, record.task_id, record.machine_id,
        resultAccount(record), accountName(record), resultCapability(record), actionName(record),
        record.business_result?.title, ...(record.item_results || []).map((item) => item.title || item.note_title)]
        .join(" ").toLowerCase().includes(query)));
  }

  function openDetail(title, content) {
    setText("#recovery-detail-title", title);
    $("#recovery-detail-content").replaceChildren(content);
    $("#recovery-detail-dialog").showModal();
  }

  function detailButton(title, createContent) {
    const button = el("button", "recovery-detail-button", "查看详情");
    button.type = "button";
    button.setAttribute("aria-label", `查看${title}详情`);
    button.addEventListener("click", () => openDetail(title, createContent()));
    return button;
  }

  function copyCell(title, subtitle) {
    const cell = el("div", "recovery-record-copy");
    append(cell, el("strong", "", title), el("small", "", subtitle || ""));
    return cell;
  }

  function table(headers, rows) {
    const wrap = el("div", "recovery-table-wrap");
    const node = el("table", "recovery-table");
    const head = el("thead"); const header = el("tr");
    headers.forEach((title) => { const th = el("th", "", title); th.scope = "col"; header.appendChild(th); });
    head.appendChild(header); node.appendChild(head);
    const body = el("tbody");
    rows.forEach((values) => {
      const row = el("tr");
      values.forEach((value) => {
        const cell = el("td");
        cell.appendChild(typeof value === "string" ? el("span", "", value) : value);
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    node.appendChild(body); wrap.appendChild(node);
    return wrap;
  }

  function resultRows(records) {
    const list = el("div", "recovery-note-grid");
    records.forEach((record) => {
      const business = record.business_result || {};
      const items = record.item_results?.length ? record.item_results : [business];
      items.forEach((item) => {
        const id = item.note_id || item.feed_id || business.note_id || business.feed_id;
        const title = item.title || item.note_title || business.title || business.note_title || "已发布笔记";
        const latest = publishedMetricHistory(record, id)[0];
        const card = el("article", "recovery-note-card");
        const header = el("div", "recovery-note-header");
        append(header, el("span", "recovery-note-kind", "已发布内容"), stateBadge(status(record)));
        append(card, header, el("h3", "", title), el("p", "recovery-note-time",
          `发布于 ${formatTime(item.published_at || business.published_at
            || (record.work_source === "creator_manager" ? "" :
              business.completed_at || record.completed_at || timestamp(record)))}`));
        const metrics = el("div", "recovery-note-metrics");
        Object.entries(metricFields).slice(0, 5).forEach(([key, label]) => {
          const metric = el("div");
          append(metric, el("strong", "", String(latest?.metrics[key] ?? "—")), el("span", "", label));
          metrics.appendChild(metric);
        });
        const hasMetrics = latest && Object.values(latest.metrics).some((value) => value != null);
        append(card, metrics, el("p", "recovery-note-time", hasMetrics
          ? `累计数据 · 更新于 ${formatTime(latest.captured_at)}` : "等待首次采集运营数据"));
        const actions = el("div", "recovery-note-actions");
        const scoped = {...record, item_results: [item]};
        const links = resultLinks(scoped);
        const url = item.note_url || business.note_url || links[0]?.url;
        if (url && /^https?:\/\//i.test(url)) {
          const link = el("a", "recovery-note-link", "查看笔记 ↗");
          link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
          actions.appendChild(link);
        }
        const detail = detailButton(title, () => {
          const content = el("div");
          content.appendChild(publishedNoteMetrics(scoped));
          const technical = el("details", "recovery-detail-technical");
          append(technical, el("summary", "", "发布执行与回执历史"), resultWithHistory(scoped));
          content.appendChild(technical);
          return content;
        });
        detail.textContent = "数据详情";
        append(actions, detail); append(card, actions); list.appendChild(card);
      });
    });
    return list;
  }

  function renderNavigation() {
    const selection = recoverySelection();
    const navigation = $("#recovery-navigation");
    const machineSection = el("section", "recovery-choice-section");
    append(machineSection, el("h2", "", "1 · 选择员工机"));
    const machines = el("div", "recovery-machine-grid");
    state.machines.forEach((machine) => {
      const accounts = state.accounts.filter((account) => account.machine_id === machine.machine_id);
      const button = el("button", "recovery-choice"); button.type = "button";
      button.setAttribute("aria-pressed", String(selection.machine === machine.machine_id));
      append(button, el("strong", "", machine.machine_id),
        el("span", "", `${accounts.length} 个账号`));
      button.addEventListener("click", () => selectRecoveryAccount(machine.machine_id, ""));
      machines.appendChild(button);
    });
    append(machineSection, machines); navigation.replaceChildren(machineSection);
    if (selection.machine) {
      const accountSection = el("section", "recovery-choice-section");
      append(accountSection, el("h2", "", `2 · 选择 ${selection.machine} 的账号`));
      const accounts = state.accounts.filter((account) => account.machine_id === selection.machine);
      const choices = el("div", "recovery-account-grid");
      accounts.forEach((account) => {
        const button = el("button", "recovery-choice recovery-account-choice"); button.type = "button";
        button.setAttribute("aria-pressed", String(selection.account === account.account_slot));
        append(button, el("span", "recovery-avatar", (account.account_slot).slice(0, 1)),
          copyCell(account.account_slot, `${account.platform === "rednote" ? "RedNote" : "小红书"} · ${account.account_slot}`));
        button.addEventListener("click", () => selectRecoveryAccount(selection.machine, account.account_slot));
        choices.appendChild(button);
      });
      append(accountSection, accounts.length ? choices : el("p", "recovery-help", "这台员工机尚未绑定账号。"));
      navigation.appendChild(accountSection);
    } else navigation.appendChild(el("p", "recovery-step-hint", "先选择员工机，再查看账号的发布内容。"));
    $("#recovery-account-content").hidden = !(selection.machine && selection.account);
    setText("#recovery-account-title", selection.account
      ? `${accountName({machine_id: selection.machine, account_slot: selection.account})} · 发布内容` : "");
    setText("#recovery-note-count", `${data.results.length} 条发布记录`);
  }

  function resultWithHistory(record) {
    const content = el("div"); content.appendChild(resultDetailCard(record));
    const history = data.audit.filter((item) => item.machine_id === record.machine_id
      && item.task_id && item.task_id === record.task_id);
    if (history.length) {
      const details = el("details", "recovery-detail-technical");
      append(details, el("summary", "", `回执历史（${history.length} 个版本）`));
      history.forEach((item) => details.appendChild(resultAuditCard(item)));
      content.appendChild(details);
    }
    return content;
  }

  function incrementalRows(records) {
    return table(["采集内容", "员工机 / 账号", "处理情况", "记录时间", ""], records.map((record) => [
      copyCell([incrementalName(record), collectionTitle(record)].filter(Boolean).join(" · "),
        collectionText(record)),
      copyCell(accountName(record), record.machine_id || "—"), handling(record),
      formatTime(timestamp(record)), detailButton(incrementalName(record), () => collectionDetail(record)),
    ]));
  }

  function auditRows(records) {
    return table(["执行内容", "员工机", "版本", "回执状态", "回收时间", ""], records.map((record) => [
      copyCell(actionName(record), record.result_summary || record.task_id), record.machine_id || "—",
      `修订 ${record.result_revision || "初始"}`, stateBadge(record.is_current_revision ? "CURRENT" : status(record)),
      formatTime(record.collected_at), detailButton("回执审计", () => resultAuditCard(record)),
    ]));
  }

  function savedReportRows(records) {
    return table(["运营报告", "员工机 / 账号", "实际 / 目标", "数据状态", "生成时间", ""], records.map((record) => {
      const definition = record.definition || {}; const content = record.content || {};
      const title = definition.report_type === "weekly" ? "运营周报" : "运营日报";
      return [copyCell(title, `${formatTime(definition.start_at)} 至 ${formatTime(definition.end_at)}`),
        copyCell(definition.account_slot || "全部账号", definition.machine_id || "全部员工机"),
        `${content.actual_total ?? "—"} / ${content.target_total ?? "—"}`, stateBadge(record.data_status),
        formatTime(record.created_at), detailButton(title, () => operationReportCard(record))];
    }));
  }

  function metricText(metric) {
    if (metric?.passive_view) return metric.passive_view.count_display;
    if (!metric || metric.actual == null) return "未取得";
    return metric.actual_display || `${metric.actual}${metric.incomplete ? "（已确认，待补齐）" : ""}`;
  }

  function reportDetail(record) {
    const content = el("div");
    append(content, el("h3", "", `${record.date} · ${accountName(record)}`),
      el("p", "", "动作统计使用已确认回执；新增数据采用北京时间自然日口径。"),
      table(["指标", "目标", "已确认实际", "数据与执行状态"],
        Object.entries(record.metrics || {}).map(([key, metric]) => [labels[key] || key,
          String(metric.target ?? "—"), metricText(metric), metric.passive_view?.status || metric.status || "—"])));
    const related = (state.results || []).filter((item) => item.machine_id === record.machine_id
      && resultAccount(item) === record.account_slot && resultDate(item) === record.date);
    const collected = data.incremental.filter((item) => item.machine_id === record.machine_id
      && resultAccount(item) === record.account_slot && resultDate(item) === record.date);
    if (collected.length) {
      const records = el("details", "recovery-detail-technical");
      append(records, el("summary", "", `当日采集明细（${collected.length} 条）`));
      collected.forEach((item) => {
        const detail = el("details", "recovery-detail-technical");
        append(detail, el("summary", "", `${incrementalName(item)} · ${handling(item)}`), collectionDetail(item));
        records.appendChild(detail);
      });
      content.appendChild(records);
    }
    append(content, el("h3", "", `对应执行记录（${related.length}）`));
    related.forEach((item) => {
      const details = el("details", "recovery-detail-technical");
      append(details, el("summary", "", `${actionName(item)} · ${item.result_summary || item.summary || status(item)}`),
        resultWithHistory(item));
      content.appendChild(details);
    });
    return content;
  }

  function reportRows(records) {
    const metricLines = (record, keys) => {
      const node = el("div", "recovery-metric-lines");
      keys.forEach((key) => node.appendChild(el("span", "", `${labels[key] || key}：${metricText(record.metrics?.[key])}`)));
      return node;
    };
    return table(["日期 / 账号", "动作完成量", "当日新增", "需关注", ""], records.map((record) => {
      const concerns = Object.entries(record.metrics || {}).filter(([, metric]) => metric.incomplete
        || ["执行结果待核对", "已阻塞", "执行失败"].includes(metric.status));
      const pending = data.incremental.filter((item) => item.machine_id === record.machine_id
        && resultAccount(item) === record.account_slot && resultDate(item) === record.date
        && item.requires_reply).length;
      const messages = concerns.map(([key, value]) =>
        `${labels[key] || key}：${value.passive_view?.status || value.status || "数据待补齐"}`);
      if (pending) messages.unshift(`${pending} 条待回复`);
      return [copyCell(`${record.date} · ${accountName(record)}`, record.machine_id),
        metricLines(record, Object.keys(metricLabels)), metricLines(record, growth),
        messages.length ? messages.join("；") : "查看指标状态", detailButton("运营汇总", () => reportDetail(record))];
    }));
  }

  function renderAudit() {
    const records = newest(data.audit); const count = Math.max(1, Math.ceil(records.length / pageSize));
    pages.audit = Math.min(pages.audit, count);
    $("#manager-result-audit-list").replaceChildren(records.length
      ? auditRows(records.slice((pages.audit - 1) * pageSize, pages.audit * pageSize))
      : el("p", "recovery-help", "当前筛选范围内暂无回执历史。"));
    setText("#recovery-audit-page", `${pages.audit} / ${count} · ${records.length} 条回执`);
    $("#recovery-audit-prev").disabled = pages.audit === 1;
    $("#recovery-audit-next").disabled = pages.audit === count;
  }

  function renderList() {
    const records = tab === "results" ? visibleResults() : tab === "incremental" ? visibleCollections() : newest(data[tab]);
    const pageCount = Math.max(1, Math.ceil(records.length / pageSize));
    pages[tab] = Math.min(pages[tab], pageCount);
    const start = (pages[tab] - 1) * pageSize;
    const slice = records.slice(start, start + pageSize);
    const listIds = {results: "#manager-result-list", incremental: "#manager-incremental-list",
      reports: "#manager-operation-report-list", audit: "#manager-result-audit-list"};
    const renderers = {results: resultRows, incremental: incrementalRows, reports: reportRows, audit: auditRows};
    const empty = el("div", "recovery-empty");
    append(empty, el("strong", "", "当前账号在所选日期内暂无记录"),
      el("p", "", "可调整筛选条件；员工机数据回收后会自动更新。"));
    $(listIds[tab]).replaceChildren(slice.length ? renderers[tab](slice) : empty);
    setText("#recovery-page-info", records.length
      ? `${start + 1}–${start + slice.length} 条，共 ${records.length} 条` : "共 0 条");
    setText("#recovery-page-number", `${pages[tab]} / ${pageCount}`);
    $("#recovery-prev").disabled = pages[tab] === 1;
    $("#recovery-next").disabled = pages[tab] === pageCount;
  }

  function selectTab(value) {
    tab = value;
    root.querySelectorAll("[data-recovery-tab]").forEach((button) => {
      const active = button.dataset.recoveryTab === tab;
      button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
    });
    root.querySelectorAll("[data-recovery-pane]").forEach((pane) => { pane.hidden = pane.dataset.recoveryPane !== tab; });
    $(".recovery-overview").hidden = true;
    $(".recovery-action-strip").hidden = true;
    $(".manager-acceptance-panel").hidden = true;
    $("#recovery-list-tools").hidden = tab !== "results";
    $("#recovery-collection-tools").hidden = tab !== "incremental";
    setText("#recovery-view-help", {results: "查看发布成果与笔记运营数据。",
      incremental: "按类型查看采集内容；条数表示记录数量，新增统计请查看运营汇总。",
      reports: "按日期与账号汇总，随回收数据更新。未取得表示缺少数据，0 表示已确认零值。"}[tab]);
    renderList();
  }

  function render(values) {
    const notes = new Map();
    const inventories = new Map();
    (values.inventoryResults || values.collectionResults || []).filter(complete).forEach((record) => {
      function visit(value) {
        if (!value || typeof value !== "object") return;
        if (value.source === "creator_manager" && value.inventory_complete === true
            && value.captured_at && Array.isArray(value.notes)) {
          const key = JSON.stringify([record.machine_id, resultAccount(record)]);
          const time = Date.parse(value.captured_at);
          if (!inventories.has(key) || time > inventories.get(key).time) {
            inventories.set(key, {time,
              ids: new Set(value.notes.map((note) => note.note_id || note.entity_id))});
          }
        }
        Object.values(value).forEach(visit);
      }
      visit(record.business_result);
    });
    newest(values.results).forEach((record) => {
      const business = record.business_result || {};
      const items = record.item_results?.length ? record.item_results : [business];
      items.forEach((item, index) => {
        const itemState = String(item.state || item.status || "").toUpperCase();
        if (["FAILED", "BLOCKED", "RESULT_UNKNOWN"].includes(itemState)) return;
        const confirmed = ["SUCCESS", "COMPLETED"].includes(itemState)
          || item.published === true || Number(item.published_count) > 0
          || (complete(record) && item.success !== false)
          || (status(record) === "PARTIAL_SUCCESS" &&
            (business.published === true || Number(business.metrics?.publish) > 0));
        if (!confirmed) return;
        const id = item.note_id || item.feed_id || business.note_id || business.feed_id
          || item.note_url || business.note_url || `${record.task_id}:${index}`;
        const key = JSON.stringify([record.machine_id, resultAccount(record), id]);
        if (!notes.has(key)) notes.set(key, {...record, item_results: [item]});
      });
    });
    newest([...(values.collectionResults || []), ...(values.resultHistory || [])])
      .forEach((record) => {
        function visit(value) {
          if (!value || typeof value !== "object") return;
          if (value.source === "creator_manager" && value.captured_at
              && Array.isArray(value.notes)) {
            value.notes.forEach((note) => {
              const id = note.note_id || note.entity_id;
              if (!id) return;
              const key = JSON.stringify([record.machine_id, resultAccount(record), id]);
              if (notes.has(key)) return;
              const item = {...note, note_id: id,
                published_at: note.published_at || note.extra?.published_at || ""};
              notes.set(key, {...record, work_source: "creator_manager",
                state: "SUCCESS", business_state: "SUCCESS", manual_action_required: false,
                business_result: item, item_results: [item]});
            });
          }
          Object.values(value).forEach(visit);
        }
        visit(record.business_result);
      });
    const currentWorks = [...notes.values()].filter((record) => {
      const inventory = inventories.get(JSON.stringify([record.machine_id, resultAccount(record)]));
      const item = record.item_results[0];
      if (record.machine_id === "PC071" && resultAccount(record) === "3-3") {
        const publishedAt = item.published_at || item.extra?.published_at
          || record.business_result?.published_at || record.completed_at || timestamp(record);
        const publishedTime = Date.parse(publishedAt);
        if (!Number.isFinite(publishedTime)
            || publishedTime < Date.parse("2026-09-22T00:00:00+08:00")) return false;
      }
      const id = item.note_id || item.feed_id || record.business_result?.note_id;
      // A publication confirmed after the inventory will enter the next scan.
      return !inventory || inventory.ids.has(id)
        || (record.work_source !== "creator_manager"
          && Date.parse(record.completed_at || timestamp(record)) > inventory.time);
    });
    data = {...values, results: currentWorks, audit: values.resultHistory,
      reports: values.reports || []};
    renderNavigation();
    const saved = (state.operationReports || []).filter((report) => resultMatchesFilters({...report,
      machine_id: report.definition?.machine_id, account_slot: report.definition?.account_slot}));
    $("#recovery-saved-report-list").replaceChildren(saved.length ? savedReportRows(saved)
      : el("p", "recovery-help", "当前筛选范围内暂无已保存报告。"));
    renderAudit();
    setText("#recovery-success-total", data.results.filter(complete).length);
    setText("#recovery-attention-total", data.results.filter(attention).length);
    for (const kind of ["results", "incremental", "reports", "audit"]) setText(`[data-recovery-count="${kind}"]`, data[kind].length);
    const machines = state.machines;
    const pending = machines.reduce((sum, machine) => sum + Number(machine.outbox_collection?.pending_records || 0), 0);
    const delayed = machines.filter((machine) => collectionStatus(machine).cached).length;
    const updated = machines.map((machine) => machine.last_result_success_at).filter(Boolean).sort().at(-1);
    setText("#recovery-sync-label", !machines.length ? "等待员工机接入"
      : delayed ? `${delayed} 台员工机待同步` : pending ? `${pending} 份回执待回收` : "员工机数据已同步");
    $("#recovery-sync-bar").dataset.tone = delayed || !machines.length ? "warning" : "success";
    setText("#recovery-sync-time", updated ? `最近成功 ${formatTime(updated)}` : "等待首次回收");
    renderList();
  }

  function reset() {
    collectionType = "all"; collectionQuery = "";
    $("#recovery-collection-type").value = "all"; $("#recovery-collection-search").value = "";
    scope = "all"; query = ""; $("#recovery-search").value = "";
    Object.keys(pages).forEach((key) => { pages[key] = 1; });
    root.querySelectorAll("[data-recovery-scope]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.recoveryScope === scope));
    });
  }

  function init() {
    root.querySelectorAll("[data-recovery-tab]").forEach((button) => {
      button.addEventListener("click", () => selectTab(button.dataset.recoveryTab));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...root.querySelectorAll("[data-recovery-tab]")];
        const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
          : (tabs.indexOf(button) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        selectTab(tabs[index].dataset.recoveryTab); tabs[index].focus();
      });
    });
    root.querySelectorAll("[data-recovery-scope]").forEach((button) => button.addEventListener("click", () => {
      scope = button.dataset.recoveryScope; pages.results = 1;
      root.querySelectorAll("[data-recovery-scope]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      renderList();
    }));
    $("#recovery-search").addEventListener("input", (event) => {
      query = event.target.value.trim().toLowerCase(); pages.results = 1; renderList();
    });
    $("#recovery-prev").addEventListener("click", () => { pages[tab] -= 1; renderList(); });
    $("#recovery-next").addEventListener("click", () => { pages[tab] += 1; renderList(); });
    $("#recovery-detail-close").addEventListener("click", () => $("#recovery-detail-dialog").close());
    $("#recovery-sync-details").addEventListener("click", () => {
      renderAudit(); $(".recovery-technical").open = true;
      $("#recovery-sync-dialog").showModal();
    });
    $("#recovery-sync-close").addEventListener("click", () => $("#recovery-sync-dialog").close());
    $("#recovery-audit-prev").addEventListener("click", () => { pages.audit -= 1; renderAudit(); });
    $("#recovery-audit-next").addEventListener("click", () => { pages.audit += 1; renderAudit(); });
    $("#recovery-collection-type").addEventListener("change", (event) => {
      collectionType = event.target.value; pages.incremental = 1; renderList();
    });
    $("#recovery-collection-search").addEventListener("input", (event) => {
      collectionQuery = event.target.value.trim().toLowerCase(); pages.incremental = 1; renderList();
    });
    selectTab("results");
    $(".recovery-filters").addEventListener("change", () => {
      Object.keys(pages).forEach((key) => { pages[key] = 1; }); renderList();
    });
    $("#manager-result-reset").addEventListener("click", () => { reset(); renderList(); });
  }
  return {init, render, reset, visibleResults, selectTab};
};
