# 小红书与 RedNote 网页端兼容性调研报告

> 现有运营控制台的适配可行性、差异边界与推荐改造路线
>
> 调研日期：2026 年 8 月 20 日
>
> 调研方式：公开网页、官方创作平台、前端资源与本地项目源码只读检查
>
> 执行边界：本次仅调研，未修改项目代码、账号配置或真实登录状态

## 一、结论

现有项目可以适配 RedNote，而且不需要推倒重写。

RedNote 与小红书网页端共享大量前端工程、接口和创作平台组件。搜索、笔记详情、用户主页、点赞、收藏、评论和内容发布等核心业务，大部分可以继续复用现有 Bridge、Chrome Profile、账号槽位、任务锁、身份保护和发布确认链。

但当前项目把“小红书域名 + 小红书路由 + 中文界面文案”直接写进了扩展和 Python 业务代码，因此现阶段无法在 RedNote 页面上直接稳定运行。

整体判断如下：

- 内容浏览、搜索和详情：差异较小，可通过平台配置和路由适配解决。
- 点赞、收藏和评论：核心接口同源，但必须用海外账号验证执行结果和页面回读。
- 图文发布：页面组件骨架高度一致，主要差异是域名、英文文案和结果验证。
- 登录身份、Cookie、退出和扩展注入：差异较大，必须单独适配。
- 私信、直播和商业功能：RedNote 海外网页端的开放范围尚不明确，不能提前承诺完全对等。

建议把项目目标定义为：

> 使用同一套运营控制台，同时支持 Xiaohongshu 与 RedNote 的核心内容运营能力。

不建议把目标定义为“未经验证就实现国内小红书全部网页功能的一比一复制”。

## 二、调研范围与证据等级

本报告使用三类证据：

1. **已验证**：能够从官方公开页面、当前前端资源或本地源码直接确认。
2. **工程判断**：根据两边页面结构、接口路径和项目实现作出的技术推断。
3. **待实测**：必须使用目标海外账号登录后才能确认，当前不作为已实现事实。

本次没有使用用户账号执行登录、互动、发布、退出或换号操作。

## 三、RedNote 与小红书的产品关系

### 3.1 已验证事实

- RedNote 官方网页提供 `Explore`、`Post`、`Notifications` 和 `Me` 等入口。
- RedNote 的 `Post` 会进入 `creator.rednote.com`。
- Google Play 上的 RedNote 仍使用应用 ID `com.xingin.xhs`，开发者为行吟信息科技（上海）有限公司。
- RedNote 与小红书当前公开网页资源均保留 `xhs-pc-web` 工程标识。
- 两边公开网页包含大量相同的核心接口路径，包括首页推荐、搜索、笔记详情、用户信息、点赞、收藏和评论。
- 两边创作平台加载相同的发布组件族，上传、标题、正文、话题、权限和定时发布等核心组件钩子一致。

### 3.2 工程判断

RedNote 不是完全独立重写的平台，更接近同一产品体系下的国际网页入口。它与国内小红书共用大量业务骨架，但在以下部分已经发生地区化分叉：

- 站点域名和 CDN；
- 登录界面与国际区号；
- 页面语言；
- 笔记详情路由；
- 部分导航和功能入口；
- 不同地区、年龄或账号状态下的能力开放范围。

因此，项目适配应采用“共享核心实现 + 平台差异配置”的方式，而不是复制一套完整的 RedNote 代码。

## 四、网页端操作差异

| 功能 | 小红书网页端 | RedNote 网页端 | 差异判断 | 当前证据 |
|---|---|---|---|---|
| 首页/发现 | `/explore` | `/`、`/explore` | 小 | 已验证 |
| 搜索 | `/search_result` | `/search_result` | 小 | 已验证 |
| 笔记详情 | `/explore/{id}` | `/discovery/item/{id}` | 中 | 已验证 |
| 用户主页 | `/user/profile/{id}` | `/user/profile/{id}` | 小 | 已验证 |
| 创作平台 | `creator.xiaohongshu.com` | `creator.rednote.com` | 中 | 已验证 |
| 登录界面 | 中文及国内号码逻辑 | 英文及国际区号 | 大 | 已验证界面差异 |
| 登录身份读取 | 当前项目支持中文页面 | 尚未适配英文页面 | 大 | 源码已验证 |
| 点赞/收藏/评论 | 核心接口可用 | 核心接口同源 | 中 | 接口已验证，真实操作待实测 |
| 图文发布 | 已有填写、预览、确认链 | 创作组件骨架一致 | 中 | 组件已验证，账号发布待实测 |
| 私信 | 当前公开构建包含 `/chat` | 当前公开构建未发现 `/chat` | 大 | 公开资源已验证，登录态待实测 |
| 直播/商业能力 | 国内入口较多 | 海外开放范围未知 | 大 | 待实测 |

### 4.1 内容浏览与搜索

两边首页、搜索和用户主页的核心结构高度接近，搜索接口和返回数据预计可以继续使用现有统一数据模型。

需要处理的主要差异是：

- 首页默认入口不同；
- 笔记详情链接的路由不同；
- RedNote 页面使用英文分类和筛选文案；
- 当前搜索代码仍使用“综合、最新、最多点赞”等中文文本定位筛选条件。

这部分属于低到中等改造量。

### 4.2 笔记详情和用户主页

用户主页路由基本一致，笔记详情路由存在明确分叉：

```text
Xiaohongshu: /explore/{note_id}
RedNote:     /discovery/item/{note_id}
```

RedNote 当前前端构建仍能识别两个路由族，说明底层详情页面复用度较高。但是项目自己的 URL 生成、Feed ID 识别、重定向诊断和详情页选择器仍需支持双路由。

### 4.3 点赞、收藏和评论

当前公开前端资源中，两边都包含以下核心接口族：

```text
/api/sns/web/v1/note/like
/api/sns/web/v1/note/collect
/api/sns/web/v1/comment/post
/api/sns/web/v2/comment/page
```

这说明互动能力不是重新开发一套接口的问题。但真正开放前仍要验证：

- 海外账号是否有相同操作权限；
- 请求参数和登录态信息是否完全一致；
- 操作后是否能从页面或接口读取最终状态；
- 地区限制或账号限制如何反馈。

因此这部分应标记为“核心接口同源，真实账号待验收”，不能只根据前端资源就认定已经兼容。

### 4.4 图文发布

RedNote 与小红书创作平台当前使用相同的主要发布组件钩子，包括：

```text
creator-tab
upload-content
upload-input
d-input
ql-editor
img-preview-area
publish-page-publish-btn
xhs-publish-btn
creator-editor-topic-container
permission-card-wrapper
post-time-wrapper
custom-switch-card
```

这意味着现有发布逻辑的大部分 CSS 和组件定位可以继续复用。

主要不兼容点是中文文字匹配。当前项目会查找：

- 上传图文；
- 上传视频；
- 写长文；
- 输入标题；
- 原创声明；
- 暂存离开；
- 发布成功。

在 RedNote 英文界面中，这些文字定位和结果判断可能失效。

发布适配后仍应保留当前产品规则：

```text
填写表单
→ 浏览器真实预览
→ 展示账号、内容和素材
→ 用户确认
→ 点击发布
→ 页面或平台结果回读
```

不能因为适配 RedNote 而改成一步直发。

### 4.5 私信、直播和商业能力

当前小红书公开网页构建中能发现 `/chat`，RedNote 当前公开构建中没有发现对应路由。

这只能说明“当前公开国际网页构建没有暴露同样的私信入口”，不能直接证明 RedNote 所有海外账号都没有私信能力。最终结论必须来自目标海外账号登录后的能力盘点。

在完成实测前，建议：

- RedNote 私信标记为 `Unknown`；
- WebUI 不展示为已支持；
- 不把小红书 `/chat` 地址强行用于 RedNote；
- 直播和商业功能分别进行能力发现，不纳入第一阶段核心适配承诺。

## 五、当前项目为什么不能直接操作 RedNote

项目当前至少存在 54 处域名或路由硬编码，分布在 9 个实现文件中。

### 5.1 扩展不会注入 RedNote 页面

`extension/manifest.json` 当前只声明：

```text
https://*.xiaohongshu.com/*
https://xiaohongshu.com/*
https://www.xiaohongshu.com/*
https://creator.xiaohongshu.com/*
```

没有 `rednote.com` 和 `creator.rednote.com`，因此内容脚本和页面拦截器不会在 RedNote 页面中工作。

### 5.2 扩展后台只认识小红书域名

`extension/background.js` 中存在以下固定逻辑：

- 只寻找 `xiaohongshu.com` 标签页；
- 默认读取 `xiaohongshu.com` Cookie；
- 退出时只删除小红书域 Cookie；
- 网络监听只覆盖小红书域名；
- 搜索和诊断探针固定访问小红书地址；
- 新建标签页默认打开小红书首页。

即使用户已经在 RedNote 中登录，Bridge 也可能识别不到目标页面。

### 5.3 网络拦截器主动忽略 RedNote

`extension/interceptor.js` 会忽略 URL 中不包含 `xiaohongshu.com` 的请求，因此 RedNote 的接口响应、风控状态和发布结果不会进入现有采集链。

### 5.4 Python URL 构造全部固定为小红书

`scripts/xhs/urls.py` 当前固定了：

```text
EXPLORE_URL = https://www.xiaohongshu.com/explore
HOME_URL = https://www.xiaohongshu.com
PUBLISH_URL = https://creator.xiaohongshu.com/publish/publish?source=official
```

笔记详情、搜索和用户主页也直接生成小红书 URL。

### 5.5 登录身份和退出依赖中文页面

`scripts/xhs/login.py` 当前依赖：

- 中文“我”；
- 中文“编辑资料”；
- 中文“退出登录”；
- 小红书个人主页域名；
- `.xiaohongshu.com` Cookie 域。

RedNote 英文页面可能导致项目无法读取当前 UID、昵称和登录状态，也无法正确完成退出后的状态核验。

### 5.6 发布和搜索依赖中文文案

`scripts/xhs/publish.py`、`scripts/xhs/publish_long_article.py` 和 `scripts/xhs/search.py` 中仍有大量中文界面匹配。

组件结构能够复用，但文字定位必须改成：

1. 优先使用稳定 CSS、组件属性或数据属性；
2. 中文和英文文字作为备用定位器；
3. 业务参数与页面显示文字分离。

### 5.7 任务和身份模型没有平台字段

当前账号槽位只记录槽位名、Bridge、Chrome User Data、Profile、扩展身份等信息，没有记录：

```text
platform = xiaohongshu | rednote
```

身份记录也只比较 UID，没有显式携带平台信息。适配后需要使用“平台 + UID”识别账号，避免跨平台身份判断产生歧义。

## 六、建议保留的现有架构

以下主干不需要推倒重做：

```text
WebUI / CLI
→ 账号槽位与任务锁
→ 身份匹配和换号保护
→ WebSocket Bridge
→ 通用 MV3 扩展
→ 固定 Chrome Profile
→ Xiaohongshu 或 RedNote 网页端
```

可以继续复用：

- 多账号槽位；
- Profile 固定绑定；
- Bridge 独立端口；
- 通用扩展配对；
- 同账号任务串行锁；
- UID 身份保护；
- 换号和 UID 覆盖流程；
- 任务、审批和审计记录；
- 发布填写、预览、确认和结果回读流程；
- 搜索、详情、用户和互动的统一数据模型。

RedNote 适配应增加在这些主干的下层，而不是另起一个独立项目。

## 七、推荐目标架构

### 7.1 账号槽位增加平台属性

账号配置新增：

```text
platform = xiaohongshu | rednote
```

旧账号配置在读取时默认使用 `xiaohongshu`，避免要求用户重新创建现有槽位。

WebUI 账号卡建议同时显示：

```text
平台：RedNote
固定 Profile：Profile 2 / 实际 Profile 名称
槽位账号：账号昵称
记录 UID：xxxx
当前登录账号：账号昵称
当前 UID：xxxx
身份状态：MATCH / MISMATCH / NOT_CHECKED
```

### 7.2 建立 PlatformSpec

建议建立统一平台配置：

```text
PlatformSpec
├─ platform_id
├─ web_origin
├─ creator_origin
├─ cookie_domains
├─ extension_hosts
├─ home_routes
├─ detail_routes
├─ localized_labels
├─ api_hosts
└─ supported_capabilities
```

示意配置：

```python
XIAOHONGSHU = PlatformSpec(
    platform_id="xiaohongshu",
    web_origin="https://www.xiaohongshu.com",
    creator_origin="https://creator.xiaohongshu.com",
    detail_routes=("/explore/{note_id}",),
)

REDNOTE = PlatformSpec(
    platform_id="rednote",
    web_origin="https://www.rednote.com",
    creator_origin="https://creator.rednote.com",
    detail_routes=("/discovery/item/{note_id}", "/explore/{note_id}"),
)
```

以上只是架构示意，不是本次实施代码。

### 7.3 扩展根据当前标签页识别平台

扩展需要：

- 增加 RedNote 主站和创作平台权限；
- 使用受支持主机集合，不再使用 `includes("xiaohongshu.com")`；
- 根据当前标签页解析平台；
- Cookie 操作必须使用当前槽位的平台配置；
- 网络监听覆盖对应站点和 API 域；
- 搜索、详情和诊断跳转使用平台 URL 构造器；
- 同时打开小红书和 RedNote 时，只操作当前槽位绑定的 Profile 和平台。

扩展 manifest 改动后，所有使用通用扩展的 Chrome Profile 都需要重新加载扩展。这个动作应在未来实施说明中明确提示用户。

### 7.4 身份记录携带平台

建议身份数据改为：

```json
{
  "platform": "rednote",
  "user_id": "...",
  "nickname": "...",
  "profile_url": "https://www.rednote.com/user/profile/..."
}
```

身份匹配规则改为：

```text
recorded.platform == observed.platform
and recorded.user_id == observed.user_id
```

现有换号逻辑保持不变：

```text
固定槽位和 Profile
→ 自动退出当前账号
→ 用户在当前 Profile 中手动登录新账号
→ 读取平台、UID 和昵称
→ 核验并完成切换
```

如果 UID 不一致，仍保留两种处理方式：

1. 用户登错账号：退出错误账号，重新登录槽位原账号；
2. 用户确认换成当前账号：明确覆盖槽位记录的 UID，Profile 和槽位绑定不变。

国外手机号和短信验证码仍由用户在浏览器中手动处理，项目只负责登录后的身份确认。

### 7.5 业务代码使用平台适配器

建议按职责拆分：

```text
shared
├─ 统一 Feed / User / Comment 数据模型
├─ Bridge 调用
├─ 任务锁、审批和审计
└─ 发布预览与确认流程

platforms/xiaohongshu
├─ URL 与路由
├─ 登录状态定位器
├─ 页面文字
└─ 特有诊断主机

platforms/rednote
├─ URL 与路由
├─ 英文登录状态定位器
├─ 页面文字
└─ 特有诊断主机
```

不建议把整个 `scripts/xhs` 复制成 `scripts/rednote`，否则两边共享逻辑会迅速分叉，后续维护成本过高。

### 7.6 平台能力注册

现有能力注册表应增加按平台覆盖的能力状态：

| 能力 | Xiaohongshu | RedNote 初始状态 | 开放条件 |
|---|---|---|---|
| 登录身份核验 | 已实现 | 待适配 | 英文身份和 UID 读取通过 |
| 搜索/详情/主页 | 已实现 | 优先适配 | 公开及登录态返回稳定 |
| 点赞/收藏/评论 | 已实现 | 待实测 | 动作后取得页面或接口回读 |
| 图文发布 | 已实现确认链 | 优先适配 | 填写、预览、确认、发布、回读闭环 |
| 私信 | 当前可用 | `Unknown` | RedNote 存在入口且发送结果可回读 |
| 直播/商业 | 部分入口 | `Unknown` | 完成能力盘点并单独立项 |

WebUI 应根据平台能力状态隐藏或禁用不支持的任务，不能出现“可以点击但必然执行失败”的入口。

## 八、建议实施阶段

### 阶段 0：海外账号只读能力盘点

目标：把公开网页推断变成真实登录态事实。

只读检查：

- 登录后的最终域名；
- 页面语言；
- 当前用户主页和 UID；
- 搜索、详情、通知、创作入口；
- 是否存在私信、直播和商业入口；
- 创作平台实际按钮、权限和草稿状态；
- 账号所在地区和可能的功能限制提示。

该阶段不执行点赞、收藏、评论、私信或发布。

### 阶段 1：平台基础设施

改造范围：

- `AccountConfig.platform`；
- `PlatformSpec`；
- 扩展 host permissions；
- 标签页平台识别；
- Cookie 域；
- 网络监听；
- URL 构造；
- 平台化身份记录；
- WebUI 平台标识。

阶段结果：RedNote 槽位能够完成 Bridge、扩展、Profile、平台和 UID 核验，稳定进入 `READY`。

### 阶段 2：核心内容运营

建议顺序：

1. 首页和搜索；
2. 笔记详情和评论读取；
3. 用户主页；
4. 点赞、收藏和评论；
5. 图文发布填写；
6. 浏览器预览和用户确认；
7. 点击发布和发布结果回读。

### 阶段 3：高级能力决策

分别验证：

- 通知；
- 私信；
- 直播；
- 商业和交易入口；
- 视频及长文发布的实际开放范围。

不存在或未开放的能力保持平台禁用，不使用猜测路由或国内小红书入口替代。

## 九、验收标准

适配完成后，至少应满足以下条件：

- [ ] 一个 Xiaohongshu 槽位和一个 RedNote 槽位可以同时连接。
- [ ] 两个槽位不会互相抢占标签页、Cookie、扩展实例或 Bridge。
- [ ] RedNote 槽位能够显示平台、固定 Profile、账号昵称、UID 和身份状态。
- [ ] RedNote 搜索、列表、详情、评论读取和用户主页可以稳定使用。
- [ ] 点赞、收藏和评论执行后取得页面或接口回读，不能只报告点击成功。
- [ ] 图文内容可以填写到 RedNote 创作页。
- [ ] 发布前必须展示真实浏览器预览，并由用户确认后才能点击发布。
- [ ] 发布完成后取得页面或平台回读；无法确认时返回 `RESULT_UNKNOWN`。
- [ ] RedNote 换号保持原 Profile 和槽位绑定不变。
- [ ] UID 不一致时提供“恢复原账号”和“明确覆盖 UID”两种流程。
- [ ] 未支持或未验证的能力在 WebUI 中明确标识。
- [ ] 旧有 Xiaohongshu 槽位默认迁移，不要求用户重建。

## 十、主要风险与待验证事项

| 事项 | 当前证据 | 处理原则 |
|---|---|---|
| UID 是否跨域一致 | 公开页面无法确认 | 身份比较携带 `platform`，不提前假定 |
| RedNote 私信 | 公开构建未发现 `/chat` | 保持 `Unknown`，登录态实测后决定 |
| 互动接口响应 | 核心路径同源 | 必须使用海外账号验证响应和结果回读 |
| 发布结果提示 | 组件骨架一致，文案可能不同 | 使用页面、接口和发布结果多证据判断 |
| 地区及账号权限 | 可能受地区、年龄和账号状态影响 | 能力注册按槽位真实结果展示 |
| 平台页面更新 | 官方网页和前端资源会变化 | 实施前重新做一次只读检查 |

## 十一、最终建议

项目适配 RedNote 具备明确可行性。

推荐第一轮只承诺以下 RedNote 核心能力：

```text
账号与 Profile 绑定
登录身份和 UID 核验
搜索与内容发现
笔记详情与评论读取
用户主页
点赞、收藏和评论
图文发布填写、预览、确认和回读
```

推荐第一轮暂不承诺：

```text
私信
直播
商业和交易入口
未实测的视频、长文或其他地区限定功能
```

工程上应优先完成平台配置、扩展域名、身份链和国际化定位器。完成这些基础层后，大部分核心业务能力可以在现有实现上继续复用。

## 附录 A：主要源码证据位置

| 模块 | 文件位置 | 当前发现 |
|---|---|---|
| 扩展权限 | `extension/manifest.json:15` | 只允许 `xiaohongshu.com` |
| Cookie 域 | `extension/background.js:866` | 默认域名固定为 `xiaohongshu.com` |
| 网络拦截 | `extension/interceptor.js:284` | 非小红书域名请求被忽略 |
| 业务 URL | `scripts/xhs/urls.py:6` | 首页、详情、搜索和发布域名固定 |
| 登录身份 | `scripts/xhs/login.py:111` | 主页和退出逻辑依赖小红书域名及中文文案 |
| 发布结果 | `scripts/xhs/publish.py:220` | 成功提示和操作入口依赖中文文字 |
| 私信入口 | `scripts/xhs/direct_message.py:13` | 固定为小红书 `/chat` |
| 账号模型 | `scripts/account_manager.py:29` | 当前没有 `platform` 字段 |
| 能力注册 | `scripts/capability_registry.py:36` | 当前没有按平台区分能力状态 |
| 身份记录 | `scripts/account_identity.py:54` | 当前身份标准化结果没有平台字段 |

## 附录 B：官方公开资料

1. [RedNote 官方网页](https://www.rednote.com/)
2. [RedNote 官方创作平台](https://creator.rednote.com/publish/publish?source=official)
3. [小红书官方网页](https://www.xiaohongshu.com/explore)
4. [Google Play - rednote 官方应用页面](https://play.google.com/store/apps/details?id=com.xingin.xhs&hl=en_US)

> 说明：网页和前端资源检查时间为 2026 年 8 月 20 日。平台页面、地区策略和账号能力可能发生更新；进入实际开发前，应使用目标海外账号重新执行一次只读能力盘点。
