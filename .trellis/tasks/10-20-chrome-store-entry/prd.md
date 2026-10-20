# Chrome Web Store 插件成为网页主入口

## Goal

StashSeek Chat — 搜藏助手 已在 Chrome 应用商店上架
（`https://chromewebstore.google.com/detail/jeemllapbelpglmpnhjfamcnoibpeail`，
商店版本 0.2.2）。网页要把"安装插件"作为用户的首要入口：落地页的主行动从
"进入资料库"换成"添加到 Chrome"，登录后的网页也要在一级位置露出插件入口，
不再只藏在账户菜单（二级菜单）里。

## Background

- 线上 `stashseek.deequoique.tech` 由 `main` 的 `web/` 构建；`dev` 上同一套
  页面已并入 `extension/src/`（插件工作区）。按仓库规则先在 `dev` 实现，再从
  `origin/main` 拉 release 分支移植到 `web/src/` 并开 PR（同 740da84 的做法）。
- 旧入口：落地页导航一个下载 zip 的小链接；登录后只有账户菜单里的「浏览器伴侣」。
- 中国大陆用户可能无法访问 Chrome 应用商店，离线 zip 需要保留为次要兜底。

## Requirements

1. 新增商店地址常量（不带 `authuser`/`hl` 参数），所有"安装插件"入口都指向它，
   以新标签页打开（`target="_blank" rel="noreferrer"`）。
2. 落地页（`ShowcasePage`）以插件为主入口：
   - 导航：主按钮"添加到 Chrome"（醒目的品牌高亮色），"登录"降为次要文字链接；
     375px 宽度下品牌、登录、安装按钮都可见且不溢出。
   - 首屏：引导文案说明插件能力（YouTube/哔哩哔哩视频旁提问、答案带时间点、
     一键保存）；主按钮"添加到 Chrome"，次按钮跳到试用场景；下方一行说明
     Chrome 版本要求，并提供"下载离线安装包"兜底链接。
   - 使用流程四步改成以插件为起点（安装登录 → 视频旁提问 → 保存到资料库 → 跨收藏检索回源）。
   - 底部 CTA：主按钮"添加到 Chrome"，次要链接"已经安装？登录私人资料库"。
3. 登录后的顶栏（`AppShell`）在一级位置显示"Chrome 插件"安装按钮（窄屏只显示图标，
   保留无障碍标签）；账户菜单里的设备管理链接保留。
4. 浏览器伴侣页（`BrowserCompanionPage`）的安装卡片：主按钮改为前往 Chrome 应用商店，
   离线 zip 降为次要链接。
5. 在插件内部（`chrome-extension:` 协议，即 dev 的插件工作区）不显示"添加到 Chrome"类按钮，
   落地页主按钮回退为进入资料库。
6. 文案不夸大：不声称免费、评分、用户量；只描述插件已有能力。

## Acceptance Criteria

- [ ] 落地页导航、首屏、底部 CTA 各有一个指向商店地址的"添加到 Chrome"主按钮，新标签页打开。
- [ ] 离线 zip 仍可从落地页和浏览器伴侣页下载，但只作为次要链接。
- [ ] 登录后顶栏一级可见插件入口；插件工作区内不显示。
- [ ] 桌面 1440px、移动 375px 下落地页导航和首屏无横向溢出，按钮可点击区域 ≥ 44px。
- [ ] `extension/`：typecheck、lint、test、build、audit 通过（dev）。
- [ ] `web/`：typecheck、lint、test、build、check:api 通过（main 移植分支）。
- [ ] 改动提交到 `dev`，并从 `origin/main` 拉 release 分支移植后开 PR 到 `main`；
      合并和 Production 审批由仓库所有者完成。

## Out of Scope

- Chrome 应用商店 listing 文案修改。
- 检测用户是否已安装插件（需要 `externally_connectable`，另立任务）。
- dev 合并到 main 后网页 SPA 不再由服务端托管的问题（另行规划）。
