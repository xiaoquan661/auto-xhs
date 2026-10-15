(() => {
  "use strict";
  const open = document.getElementById("manager-memory-open");
  if (!open) return;
  const { api, fetchJson, mutateJson } = window.AutoXhsApi;
  const dialog = document.createElement("dialog");
  dialog.className = "manager-persona-editor";
  dialog.setAttribute("aria-label", "账号记忆");
  dialog.innerHTML = `
    <div class="manager-persona-editor-heading"><div><h2>账号记忆</h2>
      <p>普通经验由经理 Agent 审核；核心人格和共享治理规则由你确认。</p></div>
      <button type="button" data-close class="secondary-button">关闭</button></div>
    <form data-search><div class="manager-persona-editor-grid">
      <label>账号<select name="account" aria-label="记忆账号"></select></label>
      <label>层次<select name="layer" aria-label="记忆层次">
        <option value="shared_core">共享核心</option><option value="account_long_term">账号长期经验</option>
        <option value="handoff">交接摘要</option><option value="runtime_fact">运行事实</option>
      </select></label>
      <label>关键词<input name="query" /></label><label>任务编号<input name="task_id" /></label>
      <label>会话编号<input name="session_id" /></label>
      <label><input name="include_inactive" type="checkbox" checked />包含待审核、停用及过期记录</label>
      </div><button type="submit" class="secondary-button">查询记忆</button>
      <button type="button" data-agent-review class="secondary-button">经理 Agent 审核经验</button>
      <button type="button" data-usage class="secondary-button">查看采用来源</button></form>
    <p data-status role="status"></p><div data-records class="record-list"></div>
    <details data-editor><summary>编写与维护知识</summary>
      <form data-save><input name="memory_id" type="hidden" />
        <div class="manager-persona-editor-grid">
          <label>类别<select name="category"><option value="brand_identity">品牌身份</option>
            <option value="knowledge">通用知识</option><option value="expression">表达原则</option>
            <option value="boundaries">运营边界</option><option value="experience">专业与互动经验</option>
            <option value="governance">共享治理规则</option><option value="persona">核心人格</option>
          </select></label><label>来源<input name="source" required /></label>
          <label>知识主题标识<input name="canonical_key" placeholder="同一事项使用相同标识" /></label>
          <label>变更原因<input name="change_reason" /></label>
          <label>有效至<input name="expires_at" placeholder="可选：2026-09-30T00:00:00+08:00" /></label>
          <label class="manager-persona-knowledge">正文<textarea name="content" rows="5" required></textarea></label>
          <label class="manager-persona-knowledge">摘要<textarea name="summary" rows="2"></textarea></label>
        </div><button class="primary-button" type="submit">保存为待审核</button>
        <button type="reset" class="secondary-button">新建记忆</button></form>
    </details><label>审核人<input data-reviewer aria-label="审核人" /></label>
    <label>审核或过期原因<input data-review-reason aria-label="审核或过期原因" /></label>
    <label>批准范围<select data-review-target aria-label="批准范围"><option value="">原记忆范围</option>
      <option value="shared_core">共享知识</option></select></label>`;
  document.body.appendChild(dialog);
  const searchForm = dialog.querySelector("[data-search]");
  const saveForm = dialog.querySelector("[data-save]");
  const status = dialog.querySelector("[data-status]");
  const records = dialog.querySelector("[data-records]");
  const accountSelect = searchForm.elements.account;
  let accounts = [];
  const categories = {brand_identity: "品牌身份", knowledge: "通用知识", expression: "表达原则", boundaries: "运营边界", experience: "专业与互动经验", general: "一般记录", governance: "共享治理规则", persona: "核心人格"};
  const stages = { task_prepared: "任务准备", execution_context: "执行上下文", execution_result: "执行结果", comment_generation: "评论生成", reply_generation: "回复生成" };
  const labels = { ACTIVE: "已批准", PENDING_REVIEW: "待审核", REJECTED: "已拒绝", RETIRED: "已停用",
    DUPLICATE: "重复", CONFLICT: "存在冲突", EXPIRED: "已过期", PROMOTED: "已批准并导入共享知识" };
  const request = (action, body) => mutateJson(`${api}/manager/memory/${action}`, "POST", body);
  function scope() {
    const account = accounts[Number(accountSelect.value)] || {};
    return { machine_id: account.machine_id || "", account_slot: account.account_slot || "",
      layer: searchForm.elements.layer.value };
  }
  async function run(action) {
    try { await action(); } catch (error) { status.textContent = error.message; }
  }
  function button(label, action) {
    const node = document.createElement("button");
    node.type = "button"; node.className = "secondary-button"; node.textContent = label;
    node.addEventListener("click", () => run(async () => {
      node.disabled = true;
      try { await action(); } finally { node.disabled = false; }
    }));
    return node;
  }
  async function search() {
    records.replaceChildren();
    const query = Object.fromEntries(new FormData(searchForm));
    query.include_inactive = searchForm.elements.include_inactive.checked;
    const payload = await request("search", { ...query, ...scope() });
    records.replaceChildren();
    for (const row of payload.memories) {
      const article = document.createElement("article");
      article.className = "subpage-panel";
      const title = document.createElement("strong");
      const expired = row.expires_at && Date.parse(row.expires_at) <= Date.now();
      title.textContent = `${expired ? "已过期" : labels[row.state] || row.state} · v${row.version} · ${categories[row.category] || row.category}`;
      const content = document.createElement("p"); content.textContent = row.content;
      const meta = document.createElement("p");
      meta.textContent = `来源：${row.source}；UID：${row.layer === "shared_core" ? "共享" : row.identity.uid || "—"}；任务：${row.task_id || "—"}；`
        + `会话：${row.session_id || "—"}；记录：${row.occurred_at}；有效至：${row.expires_at || "持续有效"}`;
      article.append(title, content, meta);
      const review = document.createElement("p");
      review.textContent = `审核方式：${row.review_authority === "human" ? "用户确认" : "经理 Agent"}；`
        + `审核：${row.reviewed_by || "—"}；审核说明：${row.review_reason || "—"}；变更原因：${row.change_reason || "—"}；`
        + `关联记忆：${row.related_memory_id || "—"}`;
      article.append(review);
      article.append(button("版本记录", async () => {
        const history = await request("history", { memory_id: row.memory_id });
        let detail = article.querySelector("pre");
        if (!detail) { detail = document.createElement("pre"); detail.style.whiteSpace = "pre-wrap"; article.append(detail); }
        detail.textContent = history.versions.map(v =>
          `v${v.version} ${labels[v.state] || v.state} ${v.updated_at}\n审核：${v.reviewed_by || "—"}`
          + `；生效：${v.effective_at || "—"}；版本截止：${v.valid_until || "当前版本"}`
          + `；审核说明：${v.review_reason || "—"}；变更原因：${v.change_reason || "—"}\n${v.content}`).join("\n\n");
      }));
      if (["shared_core", "account_long_term"].includes(row.layer)) {
        article.append(button("编辑", async () => {
          for (const name of ["memory_id", "category", "source", "content", "summary", "canonical_key", "change_reason", "expires_at"]) {
            saveForm.elements[name].value = row[name] || "";
          }
          dialog.querySelector("[data-editor]").open = true;
          saveForm.scrollIntoView({ block: "nearest" });
        }));
        for (const [decision, label] of row.state === "PENDING_REVIEW"
          ? [["approve", "批准"], ["reject", "拒绝"]] : row.state === "ACTIVE" ? [["retire", "停用"]] : []) {
          article.append(button(label, async () => {
            await request("review", { memory_id: row.memory_id, decision,
              reviewer: dialog.querySelector("[data-reviewer]").value,
              reason: dialog.querySelector("[data-review-reason]").value, expected_version: row.version,
              target_layer: decision === "approve" ? dialog.querySelector("[data-review-target]").value : "" });
            await search();
          }));
        }
      }
      if (row.state === "ACTIVE" && !expired) article.append(button("设为过期", async () => {
        await request("update", { memory_id: row.memory_id, change: "expire",
          reason: dialog.querySelector("[data-review-reason]").value });
        await search();
      }));
      if (row.state === "CONFLICT" && row.related_memory_id) article.append(button("替换关联知识版本", async () => {
        await request("update", { memory_id: row.related_memory_id, change: "replace",
          content: row.content, summary: row.summary, source: row.source,
          reason: dialog.querySelector("[data-review-reason]").value });
        await search();
      }));
      records.append(article);
    }
    status.textContent = `查到 ${payload.memories.length} 条记忆`;
    dialog.querySelector("[data-editor]").hidden = !["shared_core", "account_long_term"].includes(scope().layer);
  }
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  dialog.querySelector("[data-agent-review]").addEventListener("click", () => run(async () => {
    const result = await request("review-pending", {});
    await search();
    status.textContent = result.status === "WAITING_MODEL" ? "请在模型设置中配置审核使用的模型。"
      : `经理 Agent 本轮处理 ${(result.reviews || []).length} 条经验建议`;
  }));
  dialog.querySelector("[data-usage]").addEventListener("click", () => run(async () => {
    const result = await request("usage", { ...scope(), task_id: searchForm.elements.task_id.value });
    records.replaceChildren();
    for (const usage of result.usages) {
      const detail = document.createElement("pre"); detail.style.whiteSpace = "pre-wrap";
      detail.textContent = `任务：${usage.task_id}；阶段：${stages[usage.stage] || usage.stage}；结果：${usage.outcome || "—"}\n`
        + `采用回报：${usage.adoption_reported ? "已回报" : "待回报"}\n`
        + usage.sources.map(source => `${usage.adopted_refs.includes(source.ref) ? "已采用" : "已加载"} ${source.ref} 来源：${source.source}`).join("\n");
      records.append(detail);
    }
    status.textContent = `查到 ${result.usages.length} 条使用记录`;
  }));
  searchForm.addEventListener("submit", event => { event.preventDefault(); run(search); });
  for (const name of ["account", "layer"]) searchForm.elements[name].addEventListener("change", () => {
    saveForm.reset(); run(search);
  });
  saveForm.addEventListener("submit", event => {
    event.preventDefault(); run(async () => {
      const submit = saveForm.querySelector("[type=submit]");
      submit.disabled = true;
      try {
        await request("save", { ...Object.fromEntries(new FormData(saveForm)), ...scope() });
        saveForm.reset(); await search();
      } finally { submit.disabled = false; }
    });
  });
  open.addEventListener("click", () => run(async () => {
    dialog.showModal();
    status.textContent = "正在读取账号与记忆…";
    accounts = (await fetchJson(`${api}/manager/accounts`)).accounts;
    accountSelect.replaceChildren();
    accounts.forEach((account, index) => {
      const option = document.createElement("option"); option.value = String(index);
      option.textContent = `${account.machine_id} / ${account.account_slot} · ${account.uid || "待核验 UID"}`;
      accountSelect.append(option);
    });
    await search();
  }));
})();
