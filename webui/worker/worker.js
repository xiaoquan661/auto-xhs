(() => {
  "use strict";

  const REFRESH_INTERVAL_MS = 5_000;
  const VIEW_STORAGE_KEY = "auto-xhs:worker-view";
  const validViews = new Set(["overview", "current", "history", "outbox", "local"]);
  const labels = {
    ACCEPTED: "已接收",
    ACTION_REQUIRED: "仍需现场处理",
    BLOCKED: "已阻塞",
    CANCELLED: "已取消",
    COLLECTED: "经理机已回收",
    COMPLETED: "已完成",
    CONNECTED: "最近回执成功",
    DEGRADED: "需要关注",
    FAILED: "失败",
    IDLE: "空闲",
    IN_PROGRESS: "现场处理中",
    PAUSED: "已暂停",
    PAUSE_REQUESTED: "等待暂停",
    PENDING: "等待回传",
    READY: "可调度",
    RECONCILIATION_REQUIRED: "需要对账",
    RESULT_UNKNOWN: "结果待核对",
    RUNNING: "执行中",
    SUCCESS: "成功",
    UNKNOWN: "尚未确认",
    WARNING: "需要关注",
  };
  const eventLabels = {
    TASK_ACK: "员工机已接收任务",
    TASK_PROGRESS: "员工机正在执行",
    TASK_RESULT: "员工机已产生结果",
    RESULT_ACK: "经理机已收到结果",
  };
  const stageLabels = {
    starting: "开始执行",
    browsing: "浏览与点赞",
    reading_context: "读取评论上下文",
    comment_generated: "评论文本已生成",
    platform_submitted: "平台操作已提交",
    outbox_written: "结果已写入 outbox",
    result_collected: "经理机已回收结果",
  };
  const metricLabels = { browse: "浏览", like: "点赞", comment: "评论" };
  const actionLabels = {
    doctor: "运行诊断",
    "profile-start": "启动 Profile",
    "bridge-restart": "重启 Bridge",
    "identity-check": "核验身份",
  };

  let snapshot = null;
  let sessionToken = "";
  let refreshTimer = 0;
  let loadingPromise = null;

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const node = (tag, className = "", text = "") => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== "") item.textContent = String(text);
    return item;
  };
  const append = (parent, ...children) => {
    children.filter(Boolean).forEach((child) => parent.appendChild(child));
    return parent;
  };
  const setText = (selector, value) => {
    const item = $(selector);
    if (item) item.textContent = String(value ?? "—");
  };
  const label = (value) =>
    labels[String(value || "UNKNOWN").toUpperCase()] || value || "尚未确认";
  const formatTime = (value) => {
    if (!value) return "—";
    const time = new Date(value);
    return Number.isNaN(time.getTime())
      ? String(value)
      : time.toLocaleString("zh-CN", { hour12: false });
  };
  const elapsed = (execution) => {
    const started = new Date(execution.started_at || execution.accepted_at || "");
    if (Number.isNaN(started.getTime())) return "—";
    const ended = execution.finished_at ? new Date(execution.finished_at) : new Date();
    const seconds = Math.max(0, Math.floor((ended.getTime() - started.getTime()) / 1000));
    if (seconds < 60) return `${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    return minutes < 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
  };
  const tone = (value) => {
    const state = String(value || "").toUpperCase();
    if (["READY", "SUCCESS", "COMPLETED", "COLLECTED", "CONNECTED"].includes(state)) {
      return "good";
    }
    if (["FAILED", "BLOCKED", "RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"].includes(state)) {
      return "danger";
    }
    return "warn";
  };
  const badge = (value) => {
    const item = node("span", "badge", label(value));
    item.dataset.tone = tone(value);
    return item;
  };
  const facts = (values) => {
    const list = node("dl", "facts");
    values.forEach(([name, value]) => {
      const item = node("div");
      append(item, node("dt", "", name), node("dd", "", value ?? "—"));
      list.appendChild(item);
    });
    return list;
  };
  const empty = (message) => node("article", "empty-card", message);

  function executionCard(execution, current = false) {
    const task = execution.task || {};
    const persona = task.persona_snapshot || {};
    const progress = execution.progress || task.progress || {};
    const itemResults = Array.isArray(execution.item_results) ? execution.item_results : [];
    const recentOperation = itemResults[itemResults.length - 1] || {};
    const card = node("article", current ? "current-card" : "record-card");
    const head = node("div", "record-head");
    const copy = node("div");
    append(
      copy,
      node("strong", "", task.request_summary || task.title || execution.task_id),
      node("small", "", `${execution.task_id || "—"} · ${execution.remote_execution_id || "—"}`),
    );
    append(head, copy, badge(execution.state));
    append(
      card,
      head,
      facts([
        ["运营任务", task.operation_task_id],
        ["来源经理 Agent", task.manager_agent_id || task.manager_id],
        ["账号槽位", task.account_slot],
        ["人设快照", persona.display_name || persona.persona_id],
        ["能力", task.capability],
        ["当前阶段", execution.stage || progress.stage || label(execution.state)],
        ["已执行", elapsed(execution)],
        ["进度", progress.summary || progress.message || execution.message],
        ["目标 / 实际", `${progress.target_total ?? "—"} / ${progress.actual_total ?? "—"}`],
        ["最近操作", recentOperation.summary || recentOperation.message || recentOperation.action],
        ["暂停状态", execution.paused_by_machine ? "本机暂停" : label(execution.state)],
        ["取消请求", execution.cancel_requested ? "已收到" : "无"],
        ["outbox", execution.outbox_recorded_at ? `已写入 · ${label(execution.outbox_status)}` : "等待写入"],
        ["回传状态", label(execution.callback_status)],
        ["接收时间", formatTime(execution.accepted_at)],
        ["更新时间", formatTime(execution.updated_at || execution.finished_at)],
      ]),
    );
    if (execution.recommended_action) {
      card.appendChild(node("p", "record-note", `建议：${execution.recommended_action}`));
    }
    return card;
  }

  function timelineItems(messages, limit = 50) {
    if (!messages.length) return [empty("尚未产生本机 Agent 执行记录。")];
    return messages.slice(0, limit).map((message) => {
      const item = node("article", "timeline-item");
      const payload = message.payload && typeof message.payload === "object" ? message.payload : {};
      const stage = String(payload.stage || "");
      const metrics = payload.metrics && typeof payload.metrics === "object" ? payload.metrics : {};
      append(
        item,
        node("strong", "", eventLabels[message.message_type] || message.message_type),
        node(
          "p",
          "",
          `${message.from_agent || "—"} → ${message.to_agent || "—"} · ${message.summary || label(message.status)}`,
        ),
      );
      if (stage || Object.keys(metrics).length) {
        const progress = node("div", "timeline-progress");
        if (stage) progress.appendChild(node("span", "", stageLabels[stage] || stage));
        Object.entries(metrics).forEach(([name, value]) => {
          const metric = value && typeof value === "object" ? value : {};
          progress.appendChild(
            node(
              "span",
              "",
              `${metricLabels[name] || name} ${metric.actual ?? 0}/${metric.target ?? "—"}`,
            ),
          );
        });
        item.appendChild(progress);
      }
      if (stage === "comment_generated" && payload.content) {
        item.appendChild(node("blockquote", "timeline-comment", payload.content));
      }
      item.appendChild(node("small", "", formatTime(message.created_at)));
      return item;
    });
  }

  function accountCard(account) {
    const slot = account.account_slot || account.name || "—";
    const management = account.management || {};
    const fieldRequest = account.field_request || {};
    const card = node("article", "record-card");
    const head = node("div", "record-head");
    const copy = node("div");
    append(copy, node("strong", "", account.nickname || slot), node("small", "", slot));
    append(head, copy, badge(account.health_state || account.status));
    append(
      card,
      head,
      facts([
        ["UID", account.uid],
        ["Bridge", label(account.bridge_status || account.bridge?.status)],
        ["扩展", label(account.extension_status)],
        ["Profile", label(account.profile_status)],
        ["身份", account.identity?.nickname || label(account.identity?.status)],
        ["经理机备注", management.account_note || "尚未配置"],
        ["调度状态", management.scheduling_enabled === false ? "暂停调度" : "允许调度"],
        ["现场请求", fieldRequest.request_id ? label(fieldRequest.state) : "无"],
        ["更新时间", formatTime(account.checked_at || account.updated_at)],
      ]),
    );
    const actions = node("div", "action-row account-actions");
    ["doctor", "profile-start", "bridge-restart", "identity-check"].forEach((action) => {
      const button = node("button", "secondary-button", actionLabels[action]);
      button.type = "button";
      button.dataset.workerAccountAction = action;
      button.dataset.workerAccount = slot;
      actions.appendChild(button);
    });
    card.appendChild(actions);
    return card;
  }

  function alertCard(alert) {
    const card = node("article", "record-card alert-card");
    const head = node("div", "record-head");
    append(head, node("strong", "", alert.task_id || "本机异常"), badge(alert.state));
    card.appendChild(head);
    if (alert.message) card.appendChild(node("p", "record-note", alert.message));
    if (alert.recommended_action) {
      card.appendChild(node("p", "record-note", `建议：${alert.recommended_action}`));
    }
    return card;
  }

  function fieldRequestCard(fieldRequest) {
    const card = node("article", "record-card field-request-card");
    const head = node("div", "record-head");
    const copy = node("div");
    append(
      copy,
      node(
        "strong",
        "",
        fieldRequest.request_type === "LOGIN" ? "现场登录" : "身份核对",
      ),
      node("small", "", `${fieldRequest.account_slot} · ${fieldRequest.request_id}`),
    );
    append(head, copy, badge(fieldRequest.state));
    append(
      card,
      head,
      facts([
        ["处理说明", fieldRequest.instructions],
        ["核验结果", label(fieldRequest.result_status)],
        ["结果摘要", fieldRequest.result_summary],
        ["更新时间", formatTime(fieldRequest.updated_at)],
      ]),
    );
    const actions = node("div", "action-row account-actions");
    if (fieldRequest.state === "PENDING") {
      const start = node("button", "secondary-button", "开始现场处理");
      start.type = "button";
      start.dataset.workerFieldAction = "start";
      start.dataset.workerFieldRequest = fieldRequest.request_id;
      actions.appendChild(start);
    }
    if (["IN_PROGRESS", "ACTION_REQUIRED"].includes(fieldRequest.state)) {
      const complete = node("button", "", "完成并回读身份");
      complete.type = "button";
      complete.dataset.workerFieldAction = "complete";
      complete.dataset.workerFieldRequest = fieldRequest.request_id;
      actions.appendChild(complete);
    }
    if (actions.childElementCount) card.appendChild(actions);
    return card;
  }

  function updateFilterOptions(executions) {
    const accountSelect = $("#worker-filter-account");
    const stateSelect = $("#worker-filter-state");
    const selectedAccount = accountSelect.value;
    const selectedState = stateSelect.value;
    const accounts = [...new Set(executions.map((item) => item.task?.account_slot).filter(Boolean))];
    const states = [...new Set(executions.map((item) => item.state).filter(Boolean))];
    accountSelect.replaceChildren(new Option("全部账号", ""), ...accounts.map((value) => new Option(value, value)));
    stateSelect.replaceChildren(new Option("全部状态", ""), ...states.map((value) => new Option(label(value), value)));
    accountSelect.value = selectedAccount;
    stateSelect.value = selectedState;
  }

  function renderHistory() {
    const executions = snapshot?.executions || [];
    updateFilterOptions(executions);
    const account = $("#worker-filter-account").value;
    const state = $("#worker-filter-state").value;
    const capability = $("#worker-filter-capability").value.trim().toLowerCase();
    const date = $("#worker-filter-date").value;
    const filtered = executions.filter((execution) => {
      const task = execution.task || {};
      const occurredAt = String(execution.started_at || execution.accepted_at || "");
      return (
        (!account || task.account_slot === account) &&
        (!state || execution.state === state) &&
        (!capability || String(task.capability || "").toLowerCase().includes(capability)) &&
        (!date || occurredAt.slice(0, 10) === date)
      );
    });
    setText("#worker-history-summary", `显示 ${filtered.length} / ${executions.length} 条记录`);
    $("#worker-execution-list").replaceChildren(
      ...(filtered.length ? filtered.map((item) => executionCard(item)) : [empty("没有符合筛选条件的本机执行记录。")]),
    );
  }

  function renderOutbox(exchange, records) {
    setText(
      "#worker-outbox-summary",
      `待回传 ${exchange.pending_outbox || 0} · 已回收 ${exchange.collected_outbox || 0} · 高水位 ${exchange.high_watermark || 0}`,
    );
    const summary = $("#worker-outbox-facts");
    summary.replaceChildren(
      ...[
        ["最近写入", formatTime(exchange.last_written_at)],
        ["最近回收", formatTime(exchange.last_collected_at)],
        ["最近失败", exchange.last_failure || "无"],
        ["outbox 路径", exchange.outbox_root || "—"],
      ].map(([name, value]) => {
        const card = node("article");
        append(card, node("span", "", name), node("strong", "", value));
        return card;
      }),
    );
    $("#worker-outbox-list").replaceChildren(
      ...(records.length
        ? records.map((record) => {
            const card = node("article", "record-card");
            const head = node("div", "record-head");
            const copy = node("div");
            append(
              copy,
              node("strong", "", `${record.record_type} · ${record.task_id}`),
              node("small", "", record.relative_path),
            );
            append(head, copy, badge(record.delivery_state));
            append(
              card,
              head,
              facts([
                ["序号", record.sequence],
                ["执行编号", record.remote_execution_id],
                ["写入时间", formatTime(record.recorded_at)],
              ]),
            );
            return card;
          })
        : [empty("outbox 当前没有待展示记录。")]),
    );
  }

  function render(nextSnapshot) {
    snapshot = nextSnapshot;
    const machine = snapshot.machine || {};
    const exchange = snapshot.exchange || {};
    const current = snapshot.current_execution;
    const messages = snapshot.agent_messages || [];
    const accounts = machine.account_slots?.items || [];
    const alerts = snapshot.alerts || [];
    const fieldRequests = snapshot.account_requests || [];

    setText("#worker-machine-title", `${machine.machine_id || "员工机"} 执行台`);
    setText("#worker-generated-at", `本机快照 ${formatTime(snapshot.generated_at)}`);
    setText("#worker-health", label(machine.status));
    setText("#worker-version", `版本 ${machine.project_version || "—"}`);
    setText("#worker-manager", label(machine.manager_connection));
    setText("#worker-inbox-total", exchange.pending_inbox || 0);
    setText("#worker-inbox-path", exchange.inbox_root || "—");
    setText("#worker-outbox-total", exchange.pending_outbox || 0);
    setText("#worker-outbox-path", exchange.outbox_root || "—");
    setText("#worker-local-machine-state", `${machine.machine_id || "—"} · ${label(machine.status)}`);

    ["#worker-current-state", "#worker-overview-current-state"].forEach((selector) => {
      setText(selector, label(current?.state || "IDLE"));
      $(selector).dataset.tone = tone(current?.state || "IDLE");
    });
    $("#worker-current-task").replaceChildren(
      current ? executionCard(current, true) : empty("当前没有正在执行或等待处理的任务。"),
    );
    $("#worker-overview-current").replaceChildren(
      current ? executionCard(current, true) : empty("当前执行器空闲。"),
    );
    $("#worker-agent-list").replaceChildren(...timelineItems(messages));
    $("#worker-overview-agent-list").replaceChildren(...timelineItems(messages, 6));
    $("#worker-local-log-list").replaceChildren(...timelineItems(messages, 30));

    $("#worker-account-list").replaceChildren(
      ...(accounts.length ? accounts.map(accountCard) : [empty("当前没有可显示的本机账号槽位。")]),
    );
    $("#worker-field-request-panel").hidden = fieldRequests.length === 0;
    $("#worker-field-request-list").replaceChildren(
      ...fieldRequests.map(fieldRequestCard),
    );
    $("#worker-alert-panel").hidden = alerts.length === 0;
    setText("#worker-alert-total", `${alerts.length} 项`);
    $("#worker-alert-list").replaceChildren(...alerts.map(alertCard));
    $("#worker-local-alert-list").replaceChildren(
      ...(alerts.length ? alerts.map(alertCard) : [empty("本机当前没有需要处置的告警。")]),
    );
    renderHistory();
    renderOutbox(exchange, snapshot.outbox_records || []);
  }

  function selectView(view, updateHash = true) {
    const selected = validViews.has(view) ? view : "overview";
    $$('[data-worker-view]').forEach((section) => {
      section.hidden = section.dataset.workerView !== selected;
    });
    $$('[data-worker-view-link]').forEach((button) => {
      const active = button.dataset.workerViewLink === selected;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
    localStorage.setItem(VIEW_STORAGE_KEY, selected);
    if (updateHash) history.replaceState(null, "", `#${selected}`);
    document.querySelector("main")?.scrollTo({ top: 0 });
  }

  async function getSessionToken() {
    if (sessionToken) return sessionToken;
    const response = await fetch("/api/v1/session", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.session_token) throw new Error(payload.message || "会话建立失败");
    sessionToken = payload.session_token;
    return sessionToken;
  }

  async function mutate(path) {
    const token = await getSessionToken();
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Auto-XHS-Session": token },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok || payload.success === false) {
      if (response.status === 403) sessionToken = "";
      throw new Error(payload.message || payload.error?.message || "本机动作执行失败");
    }
    return payload;
  }

  function setActionMessage(message, status = "") {
    const item = $("#worker-action-message");
    item.textContent = message;
    item.dataset.status = status;
  }

  async function handleMachineAction(button) {
    const action = button.dataset.workerAction;
    if (action === "refresh") {
      await load();
      return;
    }
    const routes = {
      pause: ["/api/v1/worker/pause", "确认暂停本机接收新任务？正在执行的任务将按服务规则处理。"],
      resume: ["/api/v1/worker/resume", "确认恢复本机接收任务？"],
      restart: ["/api/v1/worker/executor/restart", "确认重启本机执行服务？当前不能存在执行中任务。"],
    };
    const [path, prompt] = routes[action] || [];
    if (!path || !window.confirm(prompt)) return;
    button.disabled = true;
    setActionMessage("正在执行本机控制动作…");
    try {
      await mutate(path);
      setActionMessage("本机控制动作已完成，状态已回读。", "success");
      await load();
    } catch (error) {
      setActionMessage(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function handleAccountAction(button) {
    const action = button.dataset.workerAccountAction;
    const account = button.dataset.workerAccount;
    const routes = {
      doctor: "status/doctor",
      "profile-start": "profile/start",
      "bridge-restart": "bridge/restart",
      "identity-check": "identity/check",
    };
    if (!routes[action]) return;
    if (!window.confirm(`确认在本机账号槽位“${account}”执行“${actionLabels[action]}”？`)) return;
    button.disabled = true;
    setActionMessage(`正在处理本机账号 ${account}…`);
    try {
      await mutate(`/api/v1/worker/accounts/${encodeURIComponent(account)}/${routes[action]}`);
      setActionMessage(`${account}：${actionLabels[action]}已完成并回读状态。`, "success");
      await load();
    } catch (error) {
      setActionMessage(`${account}：${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function handleFieldAction(button) {
    const action = button.dataset.workerFieldAction;
    const requestId = button.dataset.workerFieldRequest;
    const prompt = action === "start"
      ? "确认开始处理这个账号现场请求？"
      : "确认已完成现场操作，并立即回读账号昵称与 UID？";
    if (!window.confirm(prompt)) return;
    button.disabled = true;
    setActionMessage(action === "start" ? "正在记录现场处理状态…" : "正在回读账号身份…");
    try {
      const payload = await mutate(
        `/api/v1/worker/account-requests/${encodeURIComponent(requestId)}/${action}`,
      );
      setActionMessage(payload.message || "现场处理状态已更新。", "success");
      await load();
    } catch (error) {
      setActionMessage(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    if (!document.hidden) refreshTimer = window.setTimeout(load, REFRESH_INTERVAL_MS);
  }

  function load() {
    if (loadingPromise) return loadingPromise;
    const button = $("#worker-refresh");
    window.clearTimeout(refreshTimer);
    button.disabled = true;
    loadingPromise = fetch("/api/v1/worker/snapshot", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || payload.success === false) {
          throw new Error(payload.message || "状态读取失败");
        }
        render(payload);
      })
      .catch((error) => setText("#worker-generated-at", error.message))
      .finally(() => {
        button.disabled = false;
        loadingPromise = null;
        scheduleRefresh();
      });
    return loadingPromise;
  }

  function bindEvents() {
    $("#worker-refresh").addEventListener("click", load);
    $$('[data-worker-view-link]').forEach((button) => {
      button.addEventListener("click", () => selectView(button.dataset.workerViewLink));
    });
    $("#worker-history-filter").addEventListener("input", renderHistory);
    $("#worker-history-filter").addEventListener("reset", () => window.setTimeout(renderHistory));
    document.addEventListener("click", (event) => {
      const machineAction = event.target.closest("[data-worker-action]");
      if (machineAction) {
        handleMachineAction(machineAction);
        return;
      }
      const accountAction = event.target.closest("[data-worker-account-action]");
      if (accountAction) {
        handleAccountAction(accountAction);
        return;
      }
      const fieldAction = event.target.closest("[data-worker-field-action]");
      if (fieldAction) handleFieldAction(fieldAction);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) window.clearTimeout(refreshTimer);
      else load();
    });
    window.addEventListener("hashchange", () => selectView(location.hash.slice(1), false));
  }

  bindEvents();
  selectView(location.hash.slice(1) || localStorage.getItem(VIEW_STORAGE_KEY) || "overview", false);
  load();
})();
