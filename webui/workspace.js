(() => {
  const definitions = {
    overview: {
      breadcrumb: "节点工作台 · 当前节点",
      title: "工作台",
      description: "查看经理机、执行节点、账号健康状态和 Agent 任务概览。",
    },
    accounts: {
      breadcrumb: "节点工作台 · 账号",
      title: "账号管理",
      description: "管理当前节点上的账号槽位、Profile、扩展连接与身份状态。",
    },
    records: {
      breadcrumb: "节点工作台 · 执行",
      title: "执行记录",
      description: "统一查看 Agent 任务、历史结果、平台回读和异常原因。",
    },
    messages: {
      breadcrumb: "节点工作台 · 互动",
      title: "消息互动",
      description: "查看 Agent 采集的评论、私信、回复草稿和处理状态。",
    },
    publishing: {
      breadcrumb: "节点工作台 · 发布",
      title: "发布管理",
      description: "查看 Agent 发布任务、真实预览状态和最终平台回读。",
    },
    data: {
      breadcrumb: "节点工作台 · 数据",
      title: "数据分析",
      description: "查看运营台账、指标汇总，并生成可追溯的运营报告。",
    },
    system: {
      breadcrumb: "节点工作台 · 系统",
      title: "系统设置",
      description: "诊断当前节点，并管理并发、配额、模型和熔断规则。",
    },
  };

  const aliases = {
    top: "overview",
    work: "overview",
    tasks: "overview",
    setup: "accounts",
    approvals: "messages",
    diagnosis: "system",
  };

  function normalize(value) {
    const requested = String(value || "").replace(/^#/, "");
    const route = aliases[requested] || requested;
    return definitions[route] ? route : "overview";
  }

  function render(route, { focus = false } = {}) {
    const activeRoute = normalize(route);
    document.querySelectorAll(".workspace-view[data-workspace]").forEach((view) => {
      const active = view.dataset.workspace === activeRoute;
      view.hidden = !active;
      view.classList.toggle("active", active);
    });
    document.querySelectorAll("[data-workspace-link]").forEach((link) => {
      const active = link.dataset.workspaceLink === activeRoute;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    const meta = definitions[activeRoute];
    const breadcrumb = document.querySelector("#workspace-breadcrumb");
    const title = document.querySelector("#workspace-title");
    const description = document.querySelector("#workspace-description");
    if (breadcrumb) breadcrumb.textContent = meta.breadcrumb;
    if (title) title.textContent = meta.title;
    if (description) description.textContent = meta.description;
    document.title = `${meta.title} · auto-xhs`;
    document.body.dataset.workspace = activeRoute;
    if (focus && title) title.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "auto" });
    window.dispatchEvent(new CustomEvent("auto-xhs:workspace-change", { detail: { route: activeRoute } }));
    return activeRoute;
  }

  function navigate(route, { replace = false, focus = false } = {}) {
    const activeRoute = normalize(route);
    const targetHash = `#${activeRoute}`;
    if (window.location.hash !== targetHash) {
      if (replace) window.history.replaceState(null, "", targetHash);
      else window.history.pushState(null, "", targetHash);
    }
    return render(activeRoute, { focus });
  }

  function setup() {
    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-workspace-link]");
      if (!link) return;
      event.preventDefault();
      navigate(link.dataset.workspaceLink, { focus: true });
    });
    window.addEventListener("hashchange", () => render(window.location.hash, { focus: true }));
    render(window.location.hash, { focus: false });
  }

  window.AutoXhsWorkspace = { definitions, navigate, render, current: () => normalize(window.location.hash) };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
