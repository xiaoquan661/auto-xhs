(() => {
  const taskCapabilities = {
    "browse-feeds": {
      label: "自动浏览首页",
      help: "在一个首页会话中自动滚动、点开并阅读笔记；达到时间或数量任一上限即停止。",
      targetLabel: "",
      placeholder: "",
      targetRequired: false,
      targetVisible: false,
      tokenRequired: false,
      browseSettings: true,
    },
    "list-feeds": {
      label: "获取首页推荐",
      help: "读取当前首页推荐列表一次，不会自动滚动或点开笔记。",
      targetLabel: "任务备注（可选）",
      placeholder: "例如：上午选题采集",
      targetRequired: false,
      targetVisible: false,
      tokenRequired: false,
    },
    "search-feeds": {
      label: "搜索笔记",
      help: "按关键词搜索笔记，并返回标题、作者、互动数据和笔记 ID。",
      targetLabel: "搜索关键词",
      placeholder: "输入要搜索的关键词",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: false,
    },
    "get-feed-detail": {
      label: "查看笔记详情",
      help: "读取指定笔记的正文、作者、互动数据和评论。可从搜索结果一键带入参数。",
      targetLabel: "笔记 ID",
      placeholder: "输入目标笔记 ID",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: true,
    },
    "user-profile": {
      label: "查看用户主页",
      help: "读取指定用户的主页资料和公开笔记。可从搜索结果一键带入参数。",
      targetLabel: "用户 ID",
      placeholder: "输入目标用户 ID",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: true,
    },
    "like-feed": {
      label: "点赞 / 取消点赞",
      help: "修改指定笔记的点赞状态，执行前会核验账号身份并受操作配额限制。",
      targetLabel: "笔记 ID",
      placeholder: "输入目标笔记 ID",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: true,
    },
    "favorite-feed": {
      label: "收藏 / 取消收藏",
      help: "修改指定笔记的收藏状态，执行前会核验账号身份并受操作配额限制。",
      targetLabel: "笔记 ID",
      placeholder: "输入目标笔记 ID",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: true,
    },
    "keyword-engagement": {
      label: "关键词随机点赞收藏",
      help: "按关键词搜索后模拟向下滑动，边滑边去重搜集候选；达到候选池、时间上限或连续无新增时停止，再随机抽取执行。",
      targetLabel: "笔记筛选关键词",
      placeholder: "例如：露营装备、AI 视频",
      targetRequired: true,
      targetVisible: true,
      tokenRequired: false,
      engagementSettings: true,
    },
    "random-comment": {
      label: "首页随机评论",
      help: "从首页推荐中搜集候选笔记，随机抽取并读取正文，生成相关评论后直接发送。",
      targetLabel: "",
      placeholder: "",
      targetRequired: false,
      targetVisible: false,
      tokenRequired: false,
      commentSettings: true,
    },
    "home-engagement": {
      label: "首页浏览 + 点赞 + 评论",
      help: "一次会话点开推荐笔记，并只在这些已浏览笔记中完成点赞和评论；单篇失败会记录后继续。",
      targetLabel: "",
      placeholder: "",
      targetRequired: false,
      targetVisible: false,
      tokenRequired: false,
      homeEngagementSettings: true,
    },
    "fill-publish": { label: "图文发布" },
    "fill-publish-video": { label: "视频发布" },
    "long-article": { label: "长文发布" },
    "send-private-messages": { label: "个性化私信" },
  };

  const publishMonitorCapabilities = new Set(["fill-publish", "fill-publish-video", "long-article"]);
  const agentMonitorCapabilities = new Set([...publishMonitorCapabilities, "send-private-messages"]);
  const taskTemplates = {
    browse: ["browse-feeds"],
    search: ["search-feeds"],
    analysis: ["get-feed-detail", "user-profile"],
    engagement: ["home-engagement", "keyword-engagement"],
    comment: ["random-comment"],
  };
  const draftModes = {
    "post-comment": {
      label: "评论笔记",
      targetLabel: "目标笔记 ID",
      targetPlaceholder: "填写要评论的笔记 ID",
      summaryLabel: "笔记内容说明",
      summaryPlaceholder: "例如：露营装备清单与避坑建议",
      contentLabel: "评论内容",
      contentPlaceholder: "填写评论，或先补充笔记内容说明后生成草稿",
      help: "对目标笔记发表一条新评论，不需要选择已有评论。",
    },
    "reply-comment": {
      label: "回复评论",
      targetLabel: "目标评论 ID",
      targetPlaceholder: "填写要回复的评论 ID",
      summaryLabel: "原评论内容说明",
      summaryPlaceholder: "摘录对方评论，方便确认回复对象",
      contentLabel: "回复内容",
      contentPlaceholder: "结合对方原话填写最终回复",
      help: "仅在需要回应某条已有评论时使用；执行前还要填写该评论所属的笔记 ID。",
    },
    "send-private-messages": {
      label: "回复私信",
      targetLabel: "目标用户 ID",
      targetPlaceholder: "由私信收件箱自动带入",
      summaryLabel: "对方私信",
      summaryPlaceholder: "由私信收件箱自动带入",
      contentLabel: "回复内容",
      contentPlaceholder: "结合近期会话填写最终回复",
      help: "只用于已采集的一对一私信；人工确认后回到对应会话发送并回读。",
    },
  };
  const capabilityTemplate = Object.entries(taskTemplates).reduce((mapping, [template, capabilities]) => {
    capabilities.forEach((capability) => mapping.set(capability, template));
    return mapping;
  }, new Map());

  window.AutoXhsTaskCatalog = {
    taskCapabilities,
    publishMonitorCapabilities,
    agentMonitorCapabilities,
    taskTemplates,
    draftModes,
    capabilityTemplate,
  };
})();
