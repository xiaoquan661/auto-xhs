(() => {
  "use strict";

  const { api, fetchJson, mutateJson } = window.AutoXhsApi;
  const managerApi = `${api}/manager`;
  const state = {
    available: true,
    machines: [],
    tasks: [],
    transfers: [],
    results: [],
    incrementalRecords: [],
    resultSummary: {},
    versions: [],
  };
  const pendingActions = new Set();

  const terminalTaskStates = new Set([
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "RESULT_UNKNOWN",
    "CANCELLED",
    "COMPLETED",
  ]);
  const reconciliationStates = new Set([
    "DISPATCH_UNKNOWN",
    "RECONCILIATION_REQUIRED",
    "RESULT_UNKNOWN",
  ]);
  const stateLabels = {
    ACCEPTED: "已接收",
    BLOCKED: "已阻塞",
    CANCELLED: "已取消",
    CHECK_UNKNOWN: "检查结果待核对",
    COMPLETED: "已结束",
    CONTACT_LOST: "连接中断",
    DISABLED: "已停用",
    DISPATCH_UNKNOWN: "派发结果待核对",
    FAILED: "失败",
    FORMAL: "正式",
    IDLE: "空闲",
    OFFLINE: "离线",
    ONLINE: "在线",
    PARTIAL_SUCCESS: "部分完成",
    PAUSED: "已暂停",
    PAUSE_PENDING: "暂停待同步",
    PAUSE_REQUESTED: "暂停处理中",
    PENDING_DISPATCH: "等待派发",
    PREPARING: "准备中",
    READY: "可调度",
    RECONCILIATION_REQUIRED: "需要对账",
    REGISTERED: "已登记",
    RESULT_UNKNOWN: "结果待核对",
    RESUME_PENDING: "恢复待同步",
    RUNNING: "执行中",
    SUCCESS: "成功",
    TRIAL: "试用",
    UNVERIFIED: "待检查",
    UPLOADING: "传输中",
  };

  function $(selector) {
    return document.querySelector(selector);
  }

  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== "") node.textContent = String(text);
    return node;
  }

  function append(parent, ...children) {
    children.filter(Boolean).forEach((child) => parent.appendChild(child));
    return parent;
  }

  function setText(selector, value) {
    const node = $(selector);
    if (node) node.textContent = String(value ?? "—");
  }

  function splitList(value) {
    return String(value || "")
      .split(/[，,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function parseObject(value, label) {
    let parsed;
    try {
      parsed = JSON.parse(String(value || "{}"));
    } catch (_error) {
      throw new Error(`${label}必须是有效的 JSON 对象`);
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(`${label}必须是 JSON 对象`);
    }
    return parsed;
  }

  function generatedId(prefix) {
    const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
    return `${prefix}-${stamp}-${String(Date.now()).slice(-4)}`;
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function labelFor(value) {
    const normalized = String(value || "UNKNOWN").toUpperCase();
    return stateLabels[normalized] || normalized;
  }

  function toneFor(value) {
    const normalized = String(value || "").toUpperCase();
    if (["READY", "SUCCESS", "ONLINE", "FORMAL"].includes(normalized)) return "success";
    if (["RUNNING", "ACCEPTED", "UPLOADING", "TRIAL"].includes(normalized)) return "info";
    if (
      [
        "REGISTERED",
        "UNVERIFIED",
        "PAUSED",
        "PENDING_DISPATCH",
        "PREPARING",
        "CHECK_UNKNOWN",
        "DISPATCH_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "RESULT_UNKNOWN",
      ].includes(normalized)
    ) {
      return "warning";
    }
    if (["OFFLINE", "BLOCKED", "FAILED"].includes(normalized)) return "danger";
    return "neutral";
  }

  function stateBadge(value) {
    const badge = element("span", "manager-state-badge", labelFor(value));
    badge.dataset.tone = toneFor(value);
    return badge;
  }

  function emptyCard(title, detail) {
    const card = element("article", "empty-card");
    const copy = element("div");
    append(copy, element("strong", "", title), element("span", "", detail));
    return append(card, copy);
  }

  function errorCard(message) {
    return element("article", "manager-error-card", message);
  }

  function factList(values, className = "manager-record-facts") {
    const list = element("dl", className);
    values.forEach(([label, value]) => {
      const item = element("div");
      append(item, element("dt", "", label), element("dd", "", value || "—"));
      list.appendChild(item);
    });
    return list;
  }

  function actionButton(label, action, identity, kind = "") {
    const button = element("button", `manager-action-button ${kind}`.trim(), label);
    button.type = "button";
    button.dataset.managerAction = action;
    button.dataset.managerIdentity = identity;
    if (pendingActions.has(`${action}:${identity}`)) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    return button;
  }

  function setMessage(selector, message, tone = "") {
    const node = $(selector);
    if (!node) return;
    node.textContent = message;
    node.dataset.tone = tone;
  }

  function populateMachineSelects() {
    const selectors = [
      "#manager-task-machine",
      "#manager-result-machine",
      "#manager-transfer-machine",
      "#manager-version-machine",
    ];
    selectors.forEach((selector) => {
      const select = $(selector);
      if (!select) return;
      const previous = select.value;
      const allOption = selector === "#manager-result-machine";
      const placeholder = element("option", "", allOption ? "全部执行机" : "请选择执行机");
      placeholder.value = "";
      select.replaceChildren(placeholder);
      state.machines.forEach((machine) => {
        const option = element(
          "option",
          "",
          `${machine.host_alias || machine.machine_id} · ${labelFor(machine.status)}`,
        );
        option.value = machine.machine_id;
        select.appendChild(option);
      });
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    });
  }

  function machineActionButtons(machine) {
    const actions = element("div", "manager-card-actions");
    const status = String(machine.status || "REGISTERED").toUpperCase();
    if (["REGISTERED", "OFFLINE", "BLOCKED", "CHECK_UNKNOWN"].includes(status)) {
      actions.appendChild(actionButton("连接检查", "machine-check", machine.machine_id, "primary"));
    }
    if (status === "READY") {
      actions.appendChild(actionButton("暂停调度", "machine-pause", machine.machine_id, "warning"));
    }
    if (status === "PAUSED") {
      actions.appendChild(actionButton("恢复调度", "machine-resume", machine.machine_id, "primary"));
    }
    if (["READY", "PAUSED"].includes(status)) {
      actions.appendChild(actionButton("回收结果", "machine-collect", machine.machine_id));
    }
    return actions;
  }

  function renderMachines() {
    const list = $("#manager-machine-list");
    if (!list) return;
    setText("#manager-machine-total", state.machines.length);
    setText(
      "#manager-ready-total",
      state.machines.filter((machine) => machine.status === "READY").length,
    );
    setText("#node-total", 1 + state.machines.length);
    populateMachineSelects();
    if (!state.machines.length) {
      list.replaceChildren(
        emptyCard("尚未登记执行机", "前往系统设置填写 SSH 地址、项目目录和允许版本。"),
      );
      return;
    }
    const cards = state.machines.map((machine) => {
      const card = element("article", "manager-machine-card");
      const head = element("div", "manager-card-head");
      const title = element("div", "manager-card-title");
      const icon = element("span", "manager-machine-icon", "EX");
      const copy = element("div");
      append(
        copy,
        element("strong", "", machine.host_alias || machine.machine_id),
        element("small", "", machine.machine_id),
      );
      append(title, icon, copy);
      append(head, title, stateBadge(machine.status));
      const facts = factList(
        [
          ["SSH", `${machine.ssh_user || "—"}@${machine.ssh_host || "—"}:${machine.ssh_port || 22}`],
          ["连接", labelFor(machine.ssh_state)],
          ["项目版本", machine.installed_version || "尚未读取"],
          ["最近检查", formatTime(machine.last_check_at)],
        ],
        "manager-machine-facts",
      );
      append(card, head, facts);
      if (Boolean(machine.desired_paused) !== Boolean(machine.remote_paused)) {
        card.appendChild(
          element("p", "manager-inline-message", "暂停状态正在等待执行机回读同步。"),
        );
      }
      if (machine.last_error) {
        card.appendChild(element("p", "manager-inline-message", machine.last_error));
      }
      card.appendChild(machineActionButtons(machine));
      return card;
    });
    list.replaceChildren(...cards);
  }

  function taskPrimaryState(task) {
    return task.business_state || task.state || task.dispatch_state || "PENDING_DISPATCH";
  }

  function taskNeedsReconciliation(task) {
    return Boolean(task.reconciliation_required) || reconciliationStates.has(taskPrimaryState(task));
  }

  function taskIsActive(task) {
    return !terminalTaskStates.has(taskPrimaryState(task));
  }

  function taskActions(task) {
    const actions = element("div", "manager-card-actions");
    const dispatchState = String(task.dispatch_state || "").toUpperCase();
    if (taskNeedsReconciliation(task)) {
      actions.appendChild(actionButton("读取并对账", "task-reconcile", task.task_id, "primary"));
      return actions;
    }
    if (dispatchState === "PENDING_DISPATCH") {
      actions.appendChild(actionButton("继续派发", "task-dispatch", task.task_id, "primary"));
      actions.appendChild(actionButton("暂停任务", "task-pause", task.task_id, "warning"));
    } else if (["ACCEPTED", "RUNNING", "PAUSE_REQUESTED"].includes(dispatchState)) {
      actions.appendChild(actionButton("读取状态", "task-reconcile", task.task_id));
      actions.appendChild(actionButton("暂停任务", "task-pause", task.task_id, "warning"));
    } else if (dispatchState === "PAUSED") {
      actions.appendChild(actionButton("恢复任务", "task-resume", task.task_id, "primary"));
    }
    return actions;
  }

  function renderTasks() {
    const list = $("#manager-task-list");
    if (!list) return;
    const active = state.tasks.filter(taskIsActive).length;
    const reconcile = state.tasks.filter(taskNeedsReconciliation).length;
    setText("#manager-active-task-total", active);
    setText("#manager-reconcile-total", reconcile);
    if (!state.tasks.length) {
      list.replaceChildren(
        emptyCard("暂无远程任务", "从左侧表单选择执行机并派发后，状态会显示在这里。"),
      );
      return;
    }
    const cards = state.tasks.map((task) => {
      const request = task.request || {};
      const card = element("article", "manager-task-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", request.request_summary || task.task_id),
        element("small", "", `${task.task_id} · ${task.machine_id || "—"}`),
      );
      append(head, copy, stateBadge(taskPrimaryState(task)));
      const facts = factList([
        ["账号", request.account_slot],
        ["能力", request.capability],
        ["派发", labelFor(task.dispatch_state)],
        ["连接", labelFor(task.connectivity)],
        ["功能版本", [task.function_id, task.actual_version].filter(Boolean).join(" ")],
        ["更新时间", formatTime(task.updated_at)],
      ]);
      append(card, head, facts);
      const detail = task.result_summary || task.message || task.recommended_action;
      if (detail) card.appendChild(element("p", "manager-inline-message", detail));
      const actions = taskActions(task);
      if (actions.childElementCount) card.appendChild(actions);
      return card;
    });
    list.replaceChildren(...cards);
  }

  function transferState(transfer) {
    return transfer.state || transfer.status || "PREPARING";
  }

  function renderTransfers() {
    const list = $("#manager-transfer-list");
    if (!list) return;
    if (!state.transfers.length) {
      list.replaceChildren(emptyCard("暂无传输记录", "功能包传输完成后会保留版本和目标路径。"));
      return;
    }
    const cards = state.transfers.map((transfer) => {
      const request = transfer.request || {};
      const card = element("article", "manager-transfer-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", transfer.transfer_id || "未命名传输"),
        element("small", "", `${transfer.machine_id || "—"} · ${request.transfer_type || transfer.transfer_type || "—"}`),
      );
      append(head, copy, stateBadge(transferState(transfer)));
      append(
        card,
        head,
        factList([
          ["功能", transfer.function_id],
          ["实际版本", transfer.actual_version],
          ["目标路径", transfer.installed_path || transfer.remote_path],
          ["更新时间", formatTime(transfer.updated_at)],
        ]),
      );
      if (transfer.message) card.appendChild(element("p", "manager-inline-message", transfer.message));
      return card;
    });
    list.replaceChildren(...cards);
  }

  function filteredResults() {
    const machineId = $("#manager-result-machine")?.value || "";
    return machineId
      ? state.results.filter((result) => result.machine_id === machineId)
      : state.results;
  }

  function filteredIncrementalRecords() {
    const machineId = $("#manager-result-machine")?.value || "";
    return machineId
      ? state.incrementalRecords.filter((record) => record.machine_id === machineId)
      : state.incrementalRecords;
  }

  function resultDataStatus(result, field) {
    return result[`${field}_status`] || (field in result ? "observed" : "missing");
  }

  function renderBusinessResult(result) {
    const status = resultDataStatus(result, "business_result");
    const business = result.business_result;
    if (status !== "observed" || !business || typeof business !== "object") {
      const reason = result.business_result_missing_reason;
      return reason
        ? element("p", "manager-inline-message", `业务数据：${reason}`)
        : null;
    }

    const feeds = Array.isArray(business.feeds) ? business.feeds : [];
    const reportedCount = Number(business.count);
    const count = Number.isFinite(reportedCount) ? reportedCount : feeds.length;
    const panel = element("details", "manager-business-result");
    panel.appendChild(element("summary", "", `业务数据 · ${count} 条`));

    if (!feeds.length) {
      panel.appendChild(element("p", "manager-inline-message", "业务结果已回收。"));
      return panel;
    }

    const list = element("div", "manager-business-list");
    feeds.forEach((feed, index) => {
      const card = element("article", "manager-business-item");
      const title = feed.displayTitle || feed.title || `内容 ${index + 1}`;
      const author = feed.user?.nickname || feed.author?.nickname || "作者未记录";
      const identity = [author, feed.type, feed.id].filter(Boolean).join(" · ");
      append(card, element("strong", "", title), element("small", "", identity));

      const interaction = feed.interactInfo || feed.interact_info;
      if (interaction && typeof interaction === "object") {
        const metrics = [
          ["赞", interaction.likedCount || interaction.liked_count],
          ["藏", interaction.collectedCount || interaction.collected_count],
          ["评", interaction.commentCount || interaction.comment_count],
        ]
          .filter(([, value]) => value !== undefined && value !== null && value !== "")
          .map(([label, value]) => `${label} ${value}`)
          .join(" · ");
        if (metrics) card.appendChild(element("span", "", metrics));
      }
      list.appendChild(card);
    });
    panel.appendChild(list);
    return panel;
  }

  function renderResults() {
    const resultList = $("#manager-result-list");
    const incrementalList = $("#manager-incremental-list");
    if (!resultList || !incrementalList) return;
    const results = filteredResults();
    const incremental = filteredIncrementalRecords();
    const allMachines = !($("#manager-result-machine")?.value || "");
    const manualActions = allMachines
      ? Number(state.resultSummary.manual_action_count || 0)
      : results.filter((item) => item.manual_action_required).length;
    const gaps = allMachines
      ? (state.resultSummary.data_gaps || []).length
      : results.reduce((count, item) => {
          const itemStatus = resultDataStatus(item, "item_results");
          const businessStatus = resultDataStatus(item, "business_result");
          const incrementalStatus = resultDataStatus(item, "incremental_data");
          return count
            + Number(itemStatus !== "observed")
            + Number(businessStatus !== "observed")
            + Number(incrementalStatus !== "observed");
        }, 0);
    setText("#manager-result-total", results.length);
    setText("#manager-incremental-total", incremental.length);
    setText("#manager-manual-total", manualActions);
    setText("#manager-data-gap-total", gaps);

    const resultCards = results.map((result) => {
      const card = element("article", "manager-result-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", result.result_summary || result.task_id || "远程结果"),
        element("small", "", `${result.machine_id || "—"} · ${result.task_id || result.remote_execution_id || "—"}`),
      );
      append(head, copy, stateBadge(result.state || result.status));
      append(
        card,
        head,
        factList([
          ["功能", result.function_id],
          ["版本", result.actual_version || result.project_version],
          ["序号", result.sequence],
          ["业务数据", resultDataStatus(result, "business_result")],
          ["记录时间", formatTime(result.recorded_at || result.collected_at)],
        ]),
      );
      const businessResult = renderBusinessResult(result);
      if (businessResult) card.appendChild(businessResult);
      if (result.recommended_action) {
        card.appendChild(
          element("p", "manager-inline-message", `建议动作：${result.recommended_action}`),
        );
      }
      return card;
    });
    resultList.replaceChildren(
      ...(resultCards.length
        ? resultCards
        : [emptyCard("暂无远程结果", "点击“立即回收所选执行机”读取最新结果。")]),
    );

    const incrementalCards = incremental.map((record) => {
      const card = element("article", "manager-incremental-card");
      const title =
        record.record_type || record.event_type || record.metric_type || record.record_id || "增量记录";
      append(
        card,
        element("strong", "", title),
        element(
          "small",
          "manager-inline-message",
          `${record.machine_id || "—"} · ${formatTime(record.created_at || record.collected_at)}`,
        ),
      );
      return card;
    });
    incrementalList.replaceChildren(
      ...(incrementalCards.length
        ? incrementalCards
        : [emptyCard("暂无增量数据", "采集到事件、指标或诊断后会显示在这里。")]),
    );
  }

  function renderVersions() {
    const list = $("#manager-version-list");
    if (!list) return;
    if (!state.versions.length) {
      list.replaceChildren(emptyCard("尚未读取执行机版本", "选择执行机后点击“读取执行机版本”。"));
      return;
    }
    const cards = state.versions.map((version) => {
      const card = element("article", "manager-version-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", `${version.function_id || "未标识功能"} ${version.version || ""}`.trim()),
        element("small", "", version.machine_id || "—"),
      );
      append(head, copy, stateBadge(version.status || version.version_status));
      append(
        card,
        head,
        factList([
          ["稳定版本", version.stable ? "是" : version.stable_version || "否"],
          ["安装路径", version.installed_path],
        ]),
      );
      return card;
    });
    list.replaceChildren(...cards);
  }

  function renderUnavailable(error) {
    state.available = false;
    const message = error?.code === "MANAGER_SERVICE_UNAVAILABLE"
      ? "当前 WebUI 未启用集群管理服务。"
      : `暂时无法读取集群状态：${error?.message || "请求失败"}`;
    const machineList = $("#manager-machine-list");
    const taskList = $("#manager-task-list");
    if (machineList) machineList.replaceChildren(errorCard(message));
    if (taskList) taskList.replaceChildren(errorCard(message));
    setMessage("#manager-cluster-message", message, "error");
  }

  function renderAll() {
    renderMachines();
    renderTasks();
    renderTransfers();
    renderResults();
    renderVersions();
  }

  async function load() {
    try {
      const [machinesPayload, tasksPayload, transfersPayload, resultsPayload] = await Promise.all([
        fetchJson(`${managerApi}/machines`),
        fetchJson(`${managerApi}/tasks`),
        fetchJson(`${managerApi}/transfers`),
        fetchJson(`${managerApi}/results`),
      ]);
      state.available = true;
      state.machines = machinesPayload.machines || [];
      state.tasks = tasksPayload.tasks || [];
      state.transfers = transfersPayload.transfers || [];
      state.results = resultsPayload.results || [];
      state.incrementalRecords = resultsPayload.incremental_records || [];
      state.resultSummary = resultsPayload.summary || {};
      renderAll();
      setMessage(
        "#manager-cluster-message",
        `集群状态已更新 · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`,
        "success",
      );
      return true;
    } catch (error) {
      renderUnavailable(error);
      return false;
    }
  }

  function businessPayload(error) {
    return error?.payload && !error.payload.error ? error.payload : null;
  }

  async function runMutation(button, path, body, messageSelector, pendingMessage) {
    const pendingKey = button.dataset.managerAction
      ? `${button.dataset.managerAction}:${button.dataset.managerIdentity}`
      : "";
    if (pendingKey) pendingActions.add(pendingKey);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage(messageSelector, pendingMessage);
    let payload;
    let requestError = null;
    try {
      payload = await mutateJson(path, "POST", body);
    } catch (error) {
      payload = businessPayload(error);
      if (!payload) {
        setMessage(messageSelector, error.message, "error");
        requestError = error;
      }
    }
    await load();
    if (pendingKey) {
      pendingActions.delete(pendingKey);
      renderAll();
    }
    if (requestError) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      throw requestError;
    }
    const status = payload?.status ? labelFor(payload.status) : "操作已完成";
    const message = payload?.message || payload?.machine?.last_error || status;
    setMessage(messageSelector, message, payload?.success === false ? "warning" : "success");
    button.disabled = false;
    button.removeAttribute("aria-busy");
    return payload;
  }

  async function handleMachineAction(button) {
    const machineId = button.dataset.managerIdentity;
    const action = button.dataset.managerAction.replace("machine-", "");
    const labels = { check: "连接检查", pause: "暂停调度", resume: "恢复调度", collect: "结果回收" };
    if (!window.confirm(`确认对 ${machineId} 执行“${labels[action]}”？`)) return;
    const path = action === "collect"
      ? `${managerApi}/machines/${encodeURIComponent(machineId)}/collect`
      : `${managerApi}/machines/${encodeURIComponent(machineId)}/${action}`;
    const body = action === "collect" ? { download_evidence: true } : {};
    await runMutation(button, path, body, "#manager-cluster-message", `正在执行${labels[action]}…`);
  }

  async function handleTaskAction(button) {
    const taskId = button.dataset.managerIdentity;
    const action = button.dataset.managerAction.replace("task-", "");
    const labels = {
      dispatch: "继续派发",
      pause: "暂停任务",
      resume: "恢复任务",
      reconcile: "读取远程状态",
    };
    if (!window.confirm(`确认对任务 ${taskId} 执行“${labels[action]}”？`)) return;
    const task = state.tasks.find((item) => item.task_id === taskId);
    const path = action === "dispatch"
      ? `${managerApi}/tasks`
      : `${managerApi}/tasks/${encodeURIComponent(taskId)}/${action}`;
    const body = action === "dispatch" ? task?.request || {} : {};
    await runMutation(
      button,
      path,
      body,
      "#manager-task-message",
      `正在${labels[action]}…`,
    );
  }

  async function submitMachine(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const versions = splitList($("#manager-machine-versions").value);
    if (!versions.length) {
      setMessage("#manager-machine-message", "请至少填写一个允许项目版本。", "error");
      return;
    }
    const body = {
      manager_id: $("#manager-machine-manager-id").value.trim() || "MILA-MANAGER",
      machine_id: $("#manager-machine-id").value.trim(),
      host_alias: $("#manager-machine-alias").value.trim(),
      ssh_host: $("#manager-machine-host").value.trim(),
      ssh_port: Number($("#manager-machine-port").value || 22),
      ssh_user: $("#manager-machine-user").value.trim(),
      working_directory: $("#manager-machine-workdir").value.trim(),
      python_executable: $("#manager-machine-python").value.trim(),
      identity_file: $("#manager-machine-identity").value.trim(),
      allowed_versions: versions,
      expected_role: "executor",
      connect_timeout_seconds: Number($("#manager-machine-connect-timeout").value || 10),
    };
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/machines`,
        body,
        "#manager-machine-message",
        "正在保存执行机…",
      );
      setMessage(
        "#manager-machine-message",
        `${payload.machine?.machine_id || body.machine_id} 已保存，可前往工作台执行连接检查。`,
        "success",
      );
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function submitTask(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    try {
      const functionId = $("#manager-task-function-id").value.trim();
      const functionVersions = splitList($("#manager-task-function-versions").value);
      if (functionId && !functionVersions.length) {
        throw new Error("填写功能编号后，必须同时填写允许的功能版本。");
      }
      const writeIntent = $("#manager-task-write-intent").checked;
      const machineId = $("#manager-task-machine").value;
      if (!window.confirm(`确认向 ${machineId} 派发这一个远程任务？`)) return;
      const body = {
        task_id: $("#manager-task-id").value.trim(),
        machine_id: machineId,
        account_slot: $("#manager-task-account").value.trim(),
        capability: $("#manager-task-capability").value.trim(),
        operation: null,
        platform: "xiaohongshu",
        request_summary: $("#manager-task-summary").value.trim(),
        source_type: "webui",
        target_type: $("#manager-task-target-type").value.trim(),
        target_id: $("#manager-task-target-id").value.trim(),
        function_id: functionId,
        allowed_function_versions: functionVersions,
        allowed_versions: splitList($("#manager-task-project-versions").value),
        parameters: parseObject($("#manager-task-parameters").value, "任务参数"),
        authorization: {
          authorization_id: generatedId("WEBUI-AUTH"),
          scope: writeIntent ? "explicit-write" : "read-only",
        },
        write_intent: writeIntent,
        timeout_seconds: Number($("#manager-task-timeout").value || 300),
      };
      const payload = await runMutation(
        button,
        `${managerApi}/tasks`,
        body,
        "#manager-task-message",
        "正在派发远程任务…",
      );
      if (payload.success === true) $("#manager-task-id").value = generatedId("WEB-TASK");
      setMessage(
        "#manager-task-message",
        `${body.task_id}：${labelFor(payload.status || payload.task?.state)}`,
        payload.success === false ? "warning" : "success",
      );
    } catch (error) {
      setMessage("#manager-task-message", error.message, "error");
    }
  }

  async function collectSelectedMachine(event) {
    const button = event.currentTarget;
    const machineId = $("#manager-result-machine").value;
    if (!machineId) {
      setMessage("#manager-collect-message", "请先选择需要回收结果的执行机。", "error");
      return;
    }
    if (!window.confirm(`确认立即连接 ${machineId} 并回收结果？`)) return;
    try {
      await runMutation(
        button,
        `${managerApi}/machines/${encodeURIComponent(machineId)}/collect`,
        { download_evidence: $("#manager-collect-evidence").checked },
        "#manager-collect-message",
        "正在回收结果…",
      );
      renderResults();
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function submitTransfer(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const machineId = $("#manager-transfer-machine").value;
    const body = {
      transfer_id: $("#manager-transfer-id").value.trim(),
      machine_id: machineId,
      transfer_type: $("#manager-transfer-type").value,
      source_path: $("#manager-transfer-source").value.trim(),
      allowed_versions: splitList($("#manager-transfer-versions").value),
      timeout_seconds: Number($("#manager-transfer-timeout").value || 600),
    };
    const existing = state.transfers.find((item) => item.transfer_id === body.transfer_id);
    if (
      existing &&
      ["PREPARING", "UPLOADING", "RESULT_UNKNOWN"].includes(transferState(existing))
    ) {
      setMessage(
        "#manager-transfer-message",
        transferState(existing) === "RESULT_UNKNOWN"
          ? "该传输结果仍待核对，当前页面不会用新编号重复传输。"
          : "该传输仍在进行，请等待状态回读。",
        "warning",
      );
      return;
    }
    if (!window.confirm(`确认把这个包传输到 ${machineId}？`)) return;
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/transfers`,
        body,
        "#manager-transfer-message",
        "正在准备并传输功能包…",
      );
      if (payload.success === true) $("#manager-transfer-id").value = generatedId("TRANSFER");
      setMessage(
        "#manager-transfer-message",
        `${body.transfer_id}：${labelFor(payload.status || payload.transfer?.state)}`,
        payload.success === false ? "warning" : "success",
      );
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function submitVersionPolicy(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const machineId = $("#manager-version-machine").value;
    const status = $("#manager-version-status").value;
    const machine = state.machines.find((item) => item.machine_id === machineId);
    const accounts = splitList($("#manager-version-accounts").value);
    const actions = splitList($("#manager-version-actions").value);
    if (status !== "DISABLED" && (!accounts.length || !actions.length)) {
      setMessage("#manager-version-message", "正式版和试用版必须填写账号范围与允许动作。", "error");
      return;
    }
    if (!window.confirm(`确认在 ${machineId} 保存这条功能版本策略？`)) return;
    const body = {
      machine_id: machineId,
      function_id: $("#manager-version-function").value.trim(),
      version: $("#manager-version-value").value.trim(),
      status,
      scope: status === "DISABLED"
        ? null
        : {
            manager_ids: [machine?.manager_id || "MILA-MANAGER"],
            machine_ids: [machineId],
            account_slots: accounts,
            allowed_actions: actions,
          },
      set_as_stable: status === "FORMAL" && $("#manager-version-stable").checked,
    };
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/version-policies`,
        body,
        "#manager-version-message",
        "正在保存版本策略…",
      );
      if (payload.version) {
        state.versions = [
          payload.version,
          ...state.versions.filter(
            (version) =>
              version.function_id !== payload.version.function_id ||
              version.version !== payload.version.version,
          ),
        ];
        renderVersions();
      }
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function loadVersions(event) {
    const button = event.currentTarget;
    const machineId = $("#manager-version-machine").value;
    if (!machineId) {
      setMessage("#manager-version-message", "请先选择执行机。", "error");
      return;
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage("#manager-version-message", "正在连接执行机读取版本…");
    try {
      const functionId = $("#manager-version-function").value.trim();
      const suffix = functionId ? `/${encodeURIComponent(functionId)}` : "";
      const payload = await fetchJson(
        `${managerApi}/machines/${encodeURIComponent(machineId)}/versions${suffix}`,
      );
      state.versions = payload.versions || [];
      renderVersions();
      setMessage(
        "#manager-version-message",
        `已读取 ${state.versions.length} 条版本记录。`,
        "success",
      );
    } catch (error) {
      const payload = businessPayload(error);
      if (payload?.versions) {
        state.versions = payload.versions;
        renderVersions();
      }
      setMessage(
        "#manager-version-message",
        payload?.message || error.message,
        payload ? "warning" : "error",
      );
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function bindEvents() {
    $("#manager-refresh")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      await load();
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
    $("#manager-machine-form")?.addEventListener("submit", submitMachine);
    $("#manager-task-form")?.addEventListener("submit", submitTask);
    $("#manager-collect-results")?.addEventListener("click", collectSelectedMachine);
    $("#manager-transfer-form")?.addEventListener("submit", submitTransfer);
    $("#manager-version-form")?.addEventListener("submit", submitVersionPolicy);
    $("#manager-load-versions")?.addEventListener("click", loadVersions);
    $("#manager-result-machine")?.addEventListener("change", renderResults);
    $("#manager-version-status")?.addEventListener("change", updateVersionStableControl);
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-manager-action]");
      if (!button) return;
      const action = button.dataset.managerAction || "";
      const operation = action.startsWith("machine-")
        ? handleMachineAction(button)
        : handleTaskAction(button);
      operation.catch((error) => {
        const selector = action.startsWith("machine-")
          ? "#manager-cluster-message"
          : "#manager-task-message";
        setMessage(selector, error.message, "error");
      });
    });
  }

  function initialize() {
    const taskId = $("#manager-task-id");
    const transferId = $("#manager-transfer-id");
    if (taskId && !taskId.value) taskId.value = generatedId("WEB-TASK");
    if (transferId && !transferId.value) transferId.value = generatedId("TRANSFER");
    updateVersionStableControl();
    bindEvents();
    renderVersions();
  }

  function updateVersionStableControl() {
    const status = $("#manager-version-status")?.value || "FORMAL";
    const stable = $("#manager-version-stable");
    if (!stable) return;
    stable.disabled = status !== "FORMAL";
    stable.checked = status === "FORMAL";
  }

  initialize();
  window.AutoXhsManagerConsole = { load, render: renderAll, state };
})();
