/* Dated account quantities shared by the manager console and Agent. */
window.AutoXhsPlanning = function ({state, mutateJson, managerApi, localDateValue,
  workerDayMetrics, metricLabels, formatTime}) {
  const $ = (selector) => document.querySelector(selector);
  const drafts = new Map();
  const fields = ["browse", "like", "comment", "publish"];
  const key = (row) => `${row.machine_id}:${row.account_slot}`;
  const node = (tag, cls, text = "") => {
    const item = document.createElement(tag);
    item.className = cls;
    item.textContent = text;
    return item;
  };
  function draft() {
    const date = $("#daily-plan-date").value;
    const saved = (state.dailyPlans || []).find((plan) => plan.plan_date === date);
    let current = drafts.get(date);
    if (!current || (!current.dirty && current.version !== saved?.version)) {
      current = {dirty: false, version: saved?.version, accounts: structuredClone(saved?.accounts || [])};
      drafts.set(date, current);
    }
    for (const account of state.accounts) {
      if (!current.accounts.some((item) => key(item) === key(account))) {
        current.accounts.push({machine_id: account.machine_id, account_slot: account.account_slot,
          participating: account.scheduling_enabled !== false && account.health_state === "READY",
          metrics: Object.fromEntries(fields.map((kind) => [kind, 0])), notes: ""});
      }
    }
    return current;
  }
  function summary() {
    const current = draft();
    const rows = current.accounts.filter((row) => row.participating);
    const facts = [["参与账号", rows.length],
      ["所属机器", new Set(rows.map((row) => row.machine_id)).size], ...fields.map((kind) => [metricLabels[kind],
        rows.reduce((sum, row) => sum + Number(row.metrics[kind] || 0), 0)])];
    $("#daily-plan-summary").replaceChildren(...facts.map(([label, value]) => {
      const card = node("article", "daily-plan-summary-card");
      card.append(node("span", "", label), node("strong", "", String(value)));
      return card;
    }));
    const saved = (state.dailyPlans || []).find((plan) => plan.plan_date === $("#daily-plan-date").value);
    $("#daily-plan-state").textContent = current.dirty ? "已修改，待保存"
      : saved ? `已保存 · v${saved.version} · ${formatTime(saved.updated_at)}` : "待制定计划";
  }
  function render() {
    const current = draft();
    if (current.dirty && $("#daily-plan-cards").dataset.date === $("#daily-plan-date").value) return;
    $("#daily-plan-cards").dataset.date = $("#daily-plan-date").value;
    const cards = current.accounts.map((row) => {
      const account = state.accounts.find((item) => key(item) === key(row));
      const persona = state.personas.find((item) => key(item) === key(row));
      const card = node("article", "daily-plan-card");
      const head = node("div", "daily-plan-card-head");
      const title = node("div", "");
      title.append(node("h2", "", account?.nickname || row.account_slot),
        node("p", "", `所属机器 ${row.machine_id} · ${row.account_slot}`));
      const status = node("span", "daily-plan-participation",
        row.participating ? "参与规划" : "暂不参与");
      head.append(title, status);
      const preview = node("div", "daily-plan-numbers");
      const numbers = {};
      fields.forEach((kind) => {
        const cell = node("div", "");
        numbers[kind] = node("strong", "", String(row.metrics[kind]));
        cell.append(node("span", "", metricLabels[kind]), numbers[kind]);
        preview.append(cell);
      });
      const body = node("div", "daily-plan-card-body");
      const participation = node("label", "daily-plan-participation-field");
      participation.append(node("span", "", "是否参与"));
      const select = node("select", "");
      for (const [value, label] of [["yes", "参与计划"], ["no", "暂不参与"]]) {
        const option = node("option", "", label); option.value = value; select.append(option);
      }
      select.value = row.participating ? "yes" : "no";
      select.addEventListener("change", () => {
        row.participating = select.value === "yes";
        status.textContent = row.participating ? "参与规划" : "暂不参与";
        current.dirty = true; summary();
      });
      participation.append(select);
      const context = node("div", "daily-plan-context");
      context.append(participation, node("p", "", `账号人设：${persona?.display_name || "待配置"}`));
      const inputs = node("div", "daily-plan-inputs");
      fields.forEach((kind) => {
        const label = node("label", "");
        const input = node("input", "");
        input.type = "number"; input.min = "0"; input.step = "1"; input.required = true;
        input.value = row.metrics[kind];
        input.setAttribute("aria-label", `${row.machine_id} ${row.account_slot} ${metricLabels[kind]}`);
        input.addEventListener("input", () => {
          row.metrics[kind] = input.value === "" ? null : Number(input.value);
          numbers[kind].textContent = input.value || "—";
          current.dirty = true; summary();
        });
        label.append(node("span", "", metricLabels[kind]), input); inputs.append(label);
      });
      const notes = node("label", "daily-plan-notes");
      const text = node("textarea", ""); text.rows = 2; text.value = row.notes;
      text.placeholder = "填写内容方向、执行要求或补充说明";
      text.addEventListener("input", () => {row.notes = text.value; current.dirty = true; summary();});
      notes.append(node("span", "", "计划说明"), text);
      body.append(context, inputs, notes);
      card.append(head, preview, body);
      return card;
    });
    $("#daily-plan-cards").replaceChildren(...cards);
    $("#daily-plan-empty").hidden = cards.length > 0;
    summary();
  }
  function fillRecent() {
    const current = draft();
    const date = $("#daily-plan-date").value;
    let matched = 0;
    for (const row of current.accounts) {
      const previous = [...(state.dailyPlans || [])].filter((plan) => plan.plan_date < date)
        .sort((a, b) => b.plan_date.localeCompare(a.plan_date))
        .flatMap((plan) => plan.accounts.map((item) => ({...item, date: plan.plan_date})))
        .find((item) => key(item) === key(row));
      const goal = [...state.goals].filter((item) => item.period_start < date
        && item.period_start === item.period_end
        && item.state !== "DRAFT" && item.targets?.length === 1 && key(item.targets[0]) === key(row))
        .sort((a, b) => b.period_start.localeCompare(a.period_start))[0];
      if (previous || goal) {
        const metrics = previous?.metrics || goal.target_metrics || {};
        row.metrics = Object.fromEntries(fields.map((kind) => [kind, Number(metrics[kind] || 0)]));
        row.notes = previous?.notes || `参考 ${goal.period_start} 的运营目标：${goal.title || ""}`;
        matched += 1;
      } else {
        const metrics = workerDayMetrics(row.machine_id, row.account_slot, localDateValue(new Date()));
        if (metrics.some((item) => item.target !== null)) {
          row.metrics = Object.fromEntries(metrics.filter((item) => fields.includes(item.kind))
            .map((item) => [item.kind, item.target ?? 0]));
          row.notes = "参考今日已下发的操作数量"; matched += 1;
        }
      }
    }
    $("#daily-plan-cards").dataset.date = "";
    current.dirty = true; render();
    $("#daily-plan-message").textContent = `已为 ${matched} 个账号填入参考数量，可调整后保存。`;
  }
  async function save(event) {
    event.preventDefault();
    const date = $("#daily-plan-date").value;
    const current = draft();
    const button = $("#daily-plan-save");
    button.disabled = true;
    try {
      const payload = await mutateJson(`${managerApi}/daily-plans`, "POST",
        {plan_date: date, accounts: current.accounts.map((row) => ({...row,
          metrics: Object.fromEntries(fields.map((kind) => [kind, row.metrics[kind]]))}))});
      state.dailyPlans = [...(state.dailyPlans || []).filter((plan) => plan.plan_date !== date), payload.plan];
      current.dirty = false; current.version = payload.plan.version;
      summary();
      $("#daily-plan-message").textContent = `${date} 计划已保存，经理 Agent 可读取并安排执行。`;
    } catch (error) { $("#daily-plan-message").textContent = error.message; }
    finally {button.disabled = false;}
  }
  function init() {
    const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
    $("#daily-plan-date").value = localDateValue(tomorrow);
    $("#daily-plan-date").addEventListener("change", () => {
      $("#daily-plan-message").textContent = ""; render();
    });
    $("#daily-plan-fill").addEventListener("click", fillRecent);
    $("#daily-plan-form").addEventListener("submit", save);
  }
  function selectDate(date) {
    $("#daily-plan-date").value = date;
    render();
  }
  return {init, render, selectDate};
};
