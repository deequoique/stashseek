import { type ReactNode, useEffect, useRef, useState } from "react";

import type { LoginChannel } from "../api/contracts";
import { BrandLogo } from "./BrandLogo";
import { RouteLink } from "./RouteTransition";

interface AppShellProps {
  children: ReactNode;
  loginChannel: LoginChannel;
  onLogout: () => void;
  logoutPending?: boolean;
  logoutError?: string;
}

export const COMMUNITY_NOTICE_STORAGE_KEY = "stashseek:community-notice:v1:dismissed";
const GITHUB_REPOSITORY_URL = "https://github.com/deequoique/stashseek";

function communityNoticeWasDismissed(): boolean {
  try {
    return window.localStorage.getItem(COMMUNITY_NOTICE_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function AppShell({ children, loginChannel, onLogout, logoutPending = false, logoutError }: AppShellProps) {
  const loginChannelLabel = loginChannel === "telegram"
    ? "Telegram"
    : loginChannel === "email"
      ? "邮箱"
      : "微信";
  const accountMenuRef = useRef<HTMLDetailsElement>(null);
  const communityMenuRef = useRef<HTMLDetailsElement>(null);
  const [communityNoticeVisible, setCommunityNoticeVisible] = useState(
    () => !communityNoticeWasDismissed(),
  );

  useEffect(() => {
    function closeTopbarMenus(event: PointerEvent) {
      if (!(event.target instanceof Node)) return;
      for (const menu of [communityMenuRef.current, accountMenuRef.current]) {
        if (menu && !menu.contains(event.target)) menu.removeAttribute("open");
      }
    }

    document.addEventListener("pointerdown", closeTopbarMenus);
    return () => document.removeEventListener("pointerdown", closeTopbarMenus);
  }, []);

  function dismissCommunityNotice() {
    setCommunityNoticeVisible(false);
    try {
      window.localStorage.setItem(COMMUNITY_NOTICE_STORAGE_KEY, "true");
    } catch {
      // The in-memory dismissal still applies when browser storage is blocked.
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="topbar">
        <div className="topbar__inner">
          <RouteLink className="wordmark" to="/library" aria-label="搜藏助手资料库">
            <BrandLogo className="wordmark__sigil" />
            <span>StashSeek Chat</span>
          </RouteLink>
          <div className="topbar__actions">
            <details className="community-menu" ref={communityMenuRef}>
              <summary aria-label="查看用户交流群，QQ群 1107151131" title="QQ群 1107151131">
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path d="M21.395 15.035a40 40 0 0 0-.803-2.264l-1.079-2.695c.001-.032.014-.562.014-.836C19.526 4.632 17.351 0 12 0S4.474 4.632 4.474 9.241c0 .274.013.804.014.836l-1.08 2.695a39 39 0 0 0-.802 2.264c-1.021 3.283-.69 4.643-.438 4.673.54.065 2.103-2.472 2.103-2.472 0 1.469.756 3.387 2.394 4.771-.612.188-1.363.479-1.845.835-.434.32-.379.646-.301.778.343.578 5.883.369 7.482.189 1.6.18 7.14.389 7.483-.189.078-.132.132-.458-.301-.778-.483-.356-1.233-.646-1.846-.836 1.637-1.384 2.393-3.302 2.393-4.771 0 0 1.563 2.537 2.103 2.472.251-.03.581-1.39-.438-4.673" />
                </svg>
              </summary>
              <div className="community-popover">
                <p className="eyebrow">用户交流群</p>
                <strong>QQ群 1107151131</strong>
                <p>欢迎交流使用体验、反馈问题。</p>
              </div>
            </details>
            <a
              className="topbar__external-link"
              href={GITHUB_REPOSITORY_URL}
              target="_blank"
              rel="noreferrer"
              aria-label="在 GitHub 查看 StashSeek 源码，欢迎提交 Issue 与 PR"
              title="GitHub · 欢迎提 Issue / PR"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24">
                <path d="M12 .7a11.5 11.5 0 0 0-3.6 22.4c.6.1.8-.2.8-.5v-2c-3.4.7-4.1-1.4-4.1-1.4-.5-1.4-1.3-1.8-1.3-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.9 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.4-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.3 11.3 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.7-5.5 6 .4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5A11.5 11.5 0 0 0 12 .7Z" />
              </svg>
            </a>
            <details className="account-menu" ref={accountMenuRef}>
              <summary aria-label={`打开账户菜单，当前登录方式：${loginChannelLabel}`}>
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <circle cx="12" cy="8" r="3.25" />
                  <path d="M5.75 19c.7-3.3 2.8-5 6.25-5s5.55 1.7 6.25 5" />
                </svg>
              </summary>
              <div className="account-popover">
                <p className="eyebrow">登录方式</p>
                <strong>{loginChannelLabel}</strong>
                <RouteLink className="account-popover__link" to="/account/link">
                  绑定 Telegram
                </RouteLink>
                <RouteLink className="account-popover__link" to="/account/browser-companion">
                  浏览器伴侣
                </RouteLink>
                <button disabled={logoutPending} onClick={onLogout}>退出登录</button>
                {logoutError ? <p className="account-popover__error" role="alert">{logoutError}</p> : null}
              </div>
            </details>
          </div>
        </div>
      </header>
      {communityNoticeVisible ? (
        <aside className="community-notice" aria-label="StashSeek 用户交流群公告">
          <div className="community-notice__inner">
            <svg className="community-notice__icon" aria-hidden="true" viewBox="0 0 24 24">
              <path d="M4.5 5.5h15a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H12l-4.5 3v-3h-3a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" />
              <path d="M7 10h10M7 13.5h6" />
            </svg>
            <p>
              非常高兴看到您在使用这款产品。StashSeek 仍处于非常初期的阶段。为了更好地维护和迭代这款产品，诚邀您加入用户交流群（QQ群：<strong>1107151131</strong>）。同时，StashSeek 完全开源，也欢迎任何人在 GitHub 上提交 Issue 与 PR。
            </p>
            <button type="button" onClick={dismissCommunityNotice} aria-label="关闭用户交流群公告">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17" /></svg>
            </button>
          </div>
        </aside>
      ) : null}
      <main className="page-container" id="main-content" tabIndex={-1}>{children}</main>
    </div>
  );
}
