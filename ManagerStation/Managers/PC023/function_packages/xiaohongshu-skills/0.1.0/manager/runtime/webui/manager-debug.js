(() => {
  "use strict";
  window.AutoXhsDebug = ({state, managerApi, mutateJson, load}) => {
    const list = document.querySelector("#manager-debug-list");
    const dialog = document.querySelector("#manager-debug-dialog");
    if (!list || !dialog) return {render() {}};
    const form = document.querySelector("#manager-debug-form");
    const message = document.querySelector("#manager-debug-message");
    const labels = {OPEN: "待诊断", DIAGNOSING: "诊断中", FIXING: "修复中", READY: "待下发",
      DELIVERED: "已送达", INSTALLING: "安装中", AWAITING_VERIFICATION: "待服务验证",
      AWAITING_ACCEPTANCE: "待验收", VERIFIED: "验证通过", CLOSED: "已关闭",
      ROLLED_BACK: "已回滚", RESULT_UNKNOWN: "等待回执核对", OFFLINE: "机器离线",
      DEFERRED: "等待员工任务结束", FAILED: "处理失败"};
    let selected = null;
    const node = (tag, text = "") => {
      const value = document.createElement(tag); value.textContent = text; return value;
    };
    const field = (name) => form.elements.namedItem(name);
    const data = () => state.snapshot.debug || {incidents: [], releases: []};
    const current = () => data().incidents.find((item) => item.incident_id === selected);
    const release = () => data().releases.find((item) => item.release_id === current()?.release_id);
    const outcomeText = (result) => {
      const status = result.release?.state || result.status || result.incident?.state;
      if (result.handoff_path) return `修复交接单：${result.handoff_path}`;
      if (status === "VALIDATION_FAILED") return `经理测试未通过，日志：${result.validation?.log_path || ""}`;
      const detail = result.release?.error?.message || result.release?.receipt?.error || result.error?.message;
      return `${labels[status] || (result.success ? "处理完成" : "处理失败")}${detail ? `：${detail}` : ""}`;
    };
    async function act(action, values) {
      message.textContent = "正在处理…";
      form.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      try {
        const result = await mutateJson(`${managerApi}/debug/${action}`, "POST", values);
        if (result.incident) selected = result.incident.incident_id;
        await load();
        message.textContent = outcomeText(result);
        updateButtons();
      } catch (error) {
        if (error.payload) { await load(); message.textContent = outcomeText(error.payload); }
        else message.textContent = error.message;
        updateButtons();
      }
      finally { form.querySelectorAll("button").forEach((button) => { button.disabled = false; }); }
    }
    function updateButtons() {
      const item = current();
      const build = release();
      form.querySelectorAll("[data-debug-action]").forEach((button) => {
        const action = button.dataset.debugAction;
        const availability = {
          report: !item, claim: item && item.state === "OPEN",
          diagnose: item && item.state !== "CLOSED", build: item?.diagnosis && item.state !== "CLOSED",
          dispatch: build && ["READY", "OFFLINE", "FAILED", "RESULT_UNKNOWN"].includes(build.state),
          apply: build && ["DELIVERED", "DEFERRED"].includes(build.state),
          reconcile: !!build, verify: build?.state === "AWAITING_VERIFICATION",
          accept: build?.state === "VERIFIED" && item?.state !== "CLOSED",
          rollback: build?.receipt && build.receipt.status !== "ROLLED_BACK",
          resolve: item && item.category !== "code" && !build && item.state !== "CLOSED",
          reopen: item?.state === "CLOSED",
        };
        button.hidden = !availability[action];
      });
    }
    function open(item) {
      selected = item?.incident_id || null;
      form.reset();
      document.querySelector("#manager-debug-title").textContent = item?.summary || "登记员工问题";
      field("machine_id").replaceChildren(...state.machines.map((machine) => {
        const option = node("option", machine.machine_id); option.value = machine.machine_id; return option;
      }));
      for (const key of ["machine_id", "account_slot", "summary", "category"]) {
        if (item?.[key]) field(key).value = item[key];
        field(key).disabled = !!item;
      }
      for (const key of ["reproduction", "root_cause", "resolution"]) field(key).value = item?.diagnosis?.[key] || "";
      const details = document.querySelector("#manager-debug-evidence");
      details.textContent = item ? [
        `工单 ${item.incident_id} · 任务 ${item.task_id || "—"} · 错误 ${item.error_code || "—"}`,
        `原任务状态 ${item.source_status || "—"}`,
        `证据位置 ${JSON.stringify(item.evidence_index || {})}`,
        ...(item.history || []).map((entry) => `${entry.at} ${labels[entry.state] || entry.state}：${entry.note}`),
      ].join("\n") : "";
      message.textContent = "";
      updateButtons();
      dialog.showModal();
    }
    function render() {
      const records = data().incidents;
      list.replaceChildren();
      if (!records.length) { list.append(node("p", "暂无故障工单，员工失败回执会在回收后显示。")); return; }
      for (const item of records) {
        const card = node("article"); card.className = "manager-transfer-card";
        const button = node("button", "查看与处理"); button.type = "button"; button.className = "secondary-button";
        button.addEventListener("click", () => open(item));
        const associated = data().releases.find((entry) => entry.release_id === item.release_id);
        card.append(node("strong", item.summary),
          node("p", `${item.machine_id} · ${item.account_slot || "机器"} · ${labels[item.state] || item.state}`));
        if (associated) card.append(node("small", `修复包：${labels[associated.state] || associated.state}`));
        card.append(button); list.append(card);
      }
    }
    document.querySelector("#manager-debug-report").addEventListener("click", () => open(null));
    document.querySelector("#manager-debug-close").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", (event) => event.preventDefault());
    form.querySelectorAll("[data-debug-action]").forEach((button) => button.addEventListener("click", () => {
      const action = button.dataset.debugAction;
      const values = Object.fromEntries(["machine_id", "account_slot", "summary", "category",
        "reproduction", "root_cause", "resolution"].map((key) => [key, field(key).value.trim()]));
      if (selected) values.incident_id = selected;
      if (current()?.release_id) values.release_id = current().release_id;
      if (action === "build") {
        values.files = field("files").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        values.tests = field("tests").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      }
      values.acceptance_note = field("acceptance_note").value.trim();
      values.reconciliation_note = field("reconciliation_note").value.trim();
      values.reason = values.acceptance_note;
      act(action, values);
    }));
    return {render};
  };
})();
