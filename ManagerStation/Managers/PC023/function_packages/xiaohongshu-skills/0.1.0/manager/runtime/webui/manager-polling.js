(() => {
  "use strict";
  window.AutoXhsPolling = ({state, mutateJson, managerApi, load, formatTime}) => {
    const form = document.querySelector("#manager-polling-form");
    const fields = document.querySelector("#manager-polling-fields");
    const button = document.querySelector("#manager-polling-save");
    const message = document.querySelector("#manager-polling-message");
    const inputs = new Map();
    const summaries = new Map();
    let dirty = false;
    let saving = false;
    const descriptions = {
      health_accounts_seconds: "通过 SSH 读取机器连接和各账号状态，不重复核验平台登录。",
      results_seconds: "通过 SFTP 回收员工机 outbox 中的执行结果。",
      agent_seconds: "经理 Agent 检查待处理运营任务并推进执行。",
      memory_review_seconds: "检查待审核经验，在经理 Agent 轮询周期中执行。",
      dashboard_seconds: "刷新经理机缓存视图，不直接连接员工机；页面隐藏时暂停。",
      task_detail_seconds: "打开任务详情时使用的页面刷新间隔。",
    };
    const cycles = {health_accounts_seconds: "health_accounts", results_seconds: "results",
      agent_seconds: "agent"};
    const duration = (seconds) => seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`;
    const node = (tag, text = "") => {
      const item = document.createElement(tag);
      item.textContent = text;
      return item;
    };
    function render() {
      const config = state.snapshot.polling;
      if (!config?.fields) { button.disabled = true; return; }
      if (!inputs.size) {
        for (const field of config.fields) {
          const row = node("div");
          row.className = "manager-polling-field";
          const label = node("label", `${field.label}（秒）`);
          const input = node("input");
          input.type = "number";
          input.id = `polling-${field.key}`;
          input.name = field.key;
          input.min = field.min;
          input.max = field.max;
          input.step = 1;
          input.required = true;
          label.htmlFor = input.id;
          input.addEventListener("input", () => { dirty = true; });
          const summary = node("small");
          row.append(label, input, node("p", descriptions[field.key]), summary);
          fields.appendChild(row);
          inputs.set(field.key, input);
          summaries.set(field.key, summary);
        }
      }
      for (const [key, input] of inputs) {
        if (!dirty && !saving) input.value = config.intervals[key];
        input.disabled = saving;
        const seconds = config.intervals[key];
        const cycle = state.sync[cycles[key]];
        const next = cycle?.completed_at
          ? new Date(Date.parse(cycle.completed_at) + seconds * 1000).toISOString() : null;
        summaries.get(key).textContent = `当前每 ${duration(seconds)}` + (next
          ? ` · 最近 ${formatTime(cycle.completed_at)} · 预计下次 ${formatTime(next)}` : "");
      }
      document.querySelector("#manager-polling-freshness").textContent =
        `状态有效期随检查间隔调整为 ${duration(config.status_stale_seconds)}。` +
        "后台检查在服务启动时执行一轮，之后按间隔运行；已有操作完成后按新配置安排下一轮。";
      button.disabled = saving;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving || !form.reportValidity()) return;
      saving = true;
      render();
      message.textContent = "正在保存轮询配置…";
      try {
        const values = Object.fromEntries([...inputs].map(([key, input]) => [key, Number(input.value)]));
        const result = await mutateJson(`${managerApi}/polling-settings`, "POST", values);
        state.snapshot.polling = result;
        dirty = false;
        message.textContent = "轮询配置已保存并生效。";
        await load();
      } catch (error) {
        message.textContent = `保存失败：${error.message}`;
      } finally {
        saving = false;
        render();
      }
    });
    return {render};
  };
})();
