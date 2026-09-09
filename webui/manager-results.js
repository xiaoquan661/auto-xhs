/* Operator-facing result lists and receipt details. */
window.AutoXhsResultView = function (services) {
  "use strict";
  const {state, element: el, append, stateBadge, formatTime, resultAccount,
    resultCapability, actualResultCount, requestedResultCount, resultDetailCard,
    resultAuditCard, operationReportCard, factList, collectionStatus, resultMatchesFilters} = services;
  const root = document.querySelector("#results");
  const $ = (selector) => root.querySelector(selector);
  const pageSize = 8;
  const pages = {results: 1, incremental: 1, reports: 1, audit: 1};
  let tab = "results";
  let scope = "all";
  let query = "";
  let data = {results: [], incremental: [], audit: [], reports: []};
  const status = (record) => String(record.business_state || record.state || record.status || "UNKNOWN").toUpperCase();
  const complete = (record) => ["SUCCESS", "COMPLETED"].includes(status(record));
  const attention = (record) => Boolean(record.manual_action_required)
    || ["FAILED", "BLOCKED", "RESULT_UNKNOWN", "RECONCILIATION_REQUIRED", "PARTIAL_SUCCESS",
      "UNKNOWN", "DISPATCH_UNKNOWN"].includes(status(record));
  const timestamp = (record) => record.recorded_at || record.created_at || record.collected_at;
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
    return state.accounts.find((item) => item.machine_id === record.machine_id
      && item.account_slot === slot)?.nickname || slot || "账号未记录";
  };
  const incrementalName = (record) => ({record: "数据更新", manager04_acceptance: "验收记录",
    comment: "评论记录", private_message: "私信记录", metrics: "指标更新"}[
    record.record_type || record.event_type || record.metric_type] || "运营数据更新");
  const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };

  function visibleResults() {
    return newest(data.results).filter((record) =>
      (scope !== "attention" || attention(record)) && (scope !== "success" || complete(record))
      && (!query || [record.result_summary, record.summary, record.task_id, record.machine_id,
        resultAccount(record), accountName(record), resultCapability(record), actionName(record)]
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
    return table(["执行内容", "员工机 / 账号", "实际 / 目标", "结果", "完成时间", ""], records.map((record) => [
      copyCell(actionName(record), record.result_summary || record.summary || "查看详情了解执行情况"),
      copyCell(accountName(record), record.machine_id || "员工机未记录"),
      `${actualResultCount(record) ?? "—"} / ${requestedResultCount(record) ?? "—"}`,
      stateBadge(status(record)), formatTime(timestamp(record)),
      detailButton(actionName(record), () => resultDetailCard(record)),
    ]));
  }

  function incrementalRows(records) {
    return table(["记录内容", "员工机 / 账号", "记录时间", ""], records.map((record) => [
      copyCell(incrementalName(record), record.summary || record.message || "已收集运营数据"),
      copyCell(accountName(record), record.machine_id || "—"), formatTime(timestamp(record)),
      detailButton(incrementalName(record), () => {
        const content = el("div");
        append(content, el("p", "", record.summary || record.message || "已收集运营数据。"), factList([
          ["员工机", record.machine_id], ["账号", resultAccount(record)],
          ["记录时间", formatTime(timestamp(record))], ["记录类型", record.record_type || record.event_type],
          ["记录编号", record.record_id], ["关联任务", record.source_task_id || record.operation_task_id],
          ["关联目标", record.goal_id], ["人设", record.persona_id],
        ]));
        return content;
      }),
    ]));
  }

  function auditRows(records) {
    return table(["执行内容", "员工机", "版本", "回执状态", "回收时间", ""], records.map((record) => [
      copyCell(actionName(record), record.result_summary || record.task_id), record.machine_id || "—",
      `修订 ${record.result_revision || "初始"}`, stateBadge(record.is_current_revision ? "CURRENT" : status(record)),
      formatTime(record.collected_at), detailButton("回执审计", () => resultAuditCard(record)),
    ]));
  }

  function reportRows(records) {
    return table(["运营报告", "员工机 / 账号", "实际 / 目标", "数据状态", "生成时间", ""], records.map((record) => {
      const definition = record.definition || {}; const content = record.content || {};
      const title = definition.report_type === "weekly" ? "运营周报" : "运营日报";
      return [copyCell(title, `${formatTime(definition.start_at)} 至 ${formatTime(definition.end_at)}`),
        copyCell(definition.account_slot || "全部账号", definition.machine_id || "全部员工机"),
        `${content.actual_total ?? "—"} / ${content.target_total ?? "—"}`, stateBadge(record.data_status),
        formatTime(record.created_at), detailButton(title, () => operationReportCard(record))];
    }));
  }

  function renderList() {
    const records = tab === "results" ? visibleResults() : newest(data[tab]);
    const pageCount = Math.max(1, Math.ceil(records.length / pageSize));
    pages[tab] = Math.min(pages[tab], pageCount);
    const start = (pages[tab] - 1) * pageSize;
    const slice = records.slice(start, start + pageSize);
    const listIds = {results: "#manager-result-list", incremental: "#manager-incremental-list",
      reports: "#manager-operation-report-list", audit: "#manager-result-audit-list"};
    const renderers = {results: resultRows, incremental: incrementalRows, reports: reportRows, audit: auditRows};
    const empty = el("div", "recovery-empty");
    append(empty, el("strong", "", "暂无符合条件的记录"),
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
    $("#recovery-list-tools").hidden = tab !== "results";
    renderList();
  }

  function render(values) {
    data = {...values, audit: values.resultHistory, reports: (state.operationReports || []).filter((report) =>
      resultMatchesFilters({...report, machine_id: report.definition?.machine_id,
        account_slot: report.definition?.account_slot}))};
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
      selectTab("audit"); $(".recovery-technical").open = true;
    });
    $(".recovery-filters").addEventListener("change", () => {
      Object.keys(pages).forEach((key) => { pages[key] = 1; }); renderList();
    });
    $("#manager-result-reset").addEventListener("click", () => { reset(); renderList(); });
  }
  return {init, render, reset, visibleResults};
};
