import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  HashRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router";

import {
  getSession,
  listExternalIdentities,
  logout,
  setUnauthorizedHandler,
  streamConversationMessage,
  unlinkExternalIdentity,
} from "../api/client";
import type { Capabilities, SessionInfo } from "../api/contracts";
import { capabilitiesQueryOptions, preloadConversationHistory } from "../chat/conversationQueries";
import { AppShell } from "./AppShell";
import { BrandLogo } from "./BrandLogo";
import { RouteErrorBoundary } from "./ErrorBoundary";
import { useRouteNavigate } from "./RouteTransition";

const AccountLinkPage = lazy(() => import("../account/AccountLinkPage").then((module) => ({ default: module.AccountLinkPage })));
const BrowserCompanionPage = lazy(() => import("../account/BrowserCompanionPage").then((module) => ({ default: module.BrowserCompanionPage })));
const LoginPage = lazy(() => import("../auth/LoginPage").then((module) => ({ default: module.LoginPage })));
const ExternalLoginPage = lazy(() => import("../auth/ExternalLoginPage").then((module) => ({ default: module.ExternalLoginPage })));
const ChatPage = lazy(() => import("../chat/ChatPage").then((module) => ({ default: module.ChatPage })));
const LibraryPage = lazy(() => import("../library/LibraryPage").then((module) => ({ default: module.LibraryPage })));
const ShowcasePage = lazy(() => import("../showcase/ShowcasePage").then((module) => ({ default: module.ShowcasePage })));
const VideoDetailPage = lazy(() => import("../videos/VideoDetailPage").then((module) => ({ default: module.VideoDetailPage })));

interface PublicCapabilitiesSnapshot {
  data: Capabilities;
  updatedAt: number;
}

// The external-login continuation depends on SameSite cookies and a 302 back to
// an http(s) page; inside the extension side panel neither is available.
const extensionWorkspace = typeof chrome !== "undefined"
  && Boolean(chrome.storage?.local)
  && globalThis.location?.protocol === "chrome-extension:";

function RouteLoading({ label = "正在打开页面" }: { label?: string }) {
  return <main className="route-loading" role="status" aria-label={label} aria-busy="true"><BrandLogo className="wordmark__sigil" /></main>;
}

function LazyPage({ children }: { children: ReactNode }) {
  return <Suspense fallback={<RouteLoading />}>{children}</Suspense>;
}

type NavigateFn = (
  path: string,
  options: { replace: boolean; state?: unknown },
) => void;

export interface SessionEndOptions {
  accountLinkSuccess?: boolean;
  returnTo?: string;
}

const BROWSER_COMPANION_PATH = "/account/browser-companion";
const PAIRING_SEARCH_RE = /^\?pairing=[a-f0-9]{32}$/;

export function browserCompanionReturnTo(pathname: string, search = ""): string | undefined {
  if (pathname !== BROWSER_COMPANION_PATH) return undefined;
  if (!search) return BROWSER_COMPANION_PATH;
  return PAIRING_SEARCH_RE.test(search) ? `${BROWSER_COMPANION_PATH}${search}` : BROWSER_COMPANION_PATH;
}

export function loginReturnTo(state: unknown): string {
  if (typeof state !== "object" || state === null || !("returnTo" in state)) return "/library";
  const returnTo = (state as { returnTo?: unknown }).returnTo;
  if (typeof returnTo !== "string") return "/library";
  const queryIndex = returnTo.indexOf("?");
  const pathname = queryIndex === -1 ? returnTo : returnTo.slice(0, queryIndex);
  const search = queryIndex === -1 ? "" : returnTo.slice(queryIndex);
  return browserCompanionReturnTo(pathname, search) ?? "/library";
}

export async function logoutAndClear(
  client: QueryClient,
  serverLogout: () => Promise<void>,
  navigate: NavigateFn,
  rotateClient: () => void = () => undefined,
): Promise<void> {
  await serverLogout();
  endPrivateSession(client, navigate, rotateClient);
}

export function endPrivateSession(
  client: QueryClient,
  navigate: NavigateFn,
  rotateClient: () => void,
  options: SessionEndOptions = {},
): void {
  client.clear();
  rotateClient();
  if (options.accountLinkSuccess) {
    navigate("/login", { replace: true, state: { accountLinkSuccess: true } });
    return;
  }
  if (options.returnTo) {
    navigate("/login", { replace: true, state: { returnTo: options.returnTo } });
    return;
  }
  navigate("/login", { replace: true });
}

export function createPrivateQueryClient(capabilities?: PublicCapabilitiesSnapshot): QueryClient {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) => !(error instanceof Error && "status" in error && error.status === 401) && count < 1,
        refetchOnWindowFocus: false,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  });
  if (capabilities) client.setQueryData(["capabilities"], capabilities.data, { updatedAt: capabilities.updatedAt });
  return client;
}

export function createSessionQueryClient(session: SessionInfo, capabilities?: PublicCapabilitiesSnapshot): QueryClient {
  const client = createPrivateQueryClient(capabilities);
  client.setQueryData(["session"], session);
  return client;
}

function ProtectedLayout({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: ({ signal }) => getSession(signal),
    retry: false,
    staleTime: 30_000,
  });
  // Public deployment capabilities can load alongside Session validation.
  // Private history starts only after the authoritative Session succeeds.
  const capabilities = useQuery(capabilitiesQueryOptions());
  useEffect(() => {
    if (session.isSuccess && capabilities.data?.chat) {
      void preloadConversationHistory(client);
    }
  }, [capabilities.data, client, session.data, session.isSuccess]);
  const logoutMutation = useMutation({
    mutationFn: () => logoutAndClear(client, logout, navigate, rotateClient),
  });

  if (session.isPending) {
    return <RouteLoading label="正在验证登录" />;
  }
  if (session.isError) {
    const returnTo = browserCompanionReturnTo(location.pathname, location.search);
    return <Navigate to="/login" replace state={returnTo ? { returnTo } : undefined} />;
  }
  return (
    <AppShell
      loginChannel={session.data.login_channel}
      logoutPending={logoutMutation.isPending}
      logoutError={logoutMutation.isError ? "服务器尚未确认退出，请检查网络后重试。" : undefined}
      onLogout={() => logoutMutation.mutate()}
    >
      <LazyPage><Outlet /></LazyPage>
    </AppShell>
  );
}

function AccountLinkRoute({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const onLinked = useCallback(() => {
    endPrivateSession(client, navigate, rotateClient, { accountLinkSuccess: true });
  }, [client, navigate, rotateClient]);
  return (
    <AccountLinkPage
      onLinked={onLinked}
      loadIdentities={listExternalIdentities}
      unlinkIdentity={unlinkExternalIdentity}
    />
  );
}

function LoginRoute({ activateSession, external = false }: { activateSession: (session?: SessionInfo, capabilities?: PublicCapabilitiesSnapshot) => void; external?: boolean }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  const returnTo = external ? "/library" : loginReturnTo(location.state);
  const Page = external ? ExternalLoginPage : LoginPage;
  const active = useRef(true);
  const previousPrivate = useMemo(() => ({
    queries: client.getQueryCache().getAll().filter((query) => {
      const key = query.queryKey[0];
      if (key === "capabilities" || key === "login-session-probe") return false;
      return !(key === "login-challenge" && query.queryKey[1] == null && query.state.data === undefined && query.state.fetchStatus === "idle");
    }),
    mutations: client.getMutationCache().getAll().filter((mutation) => mutation.state.status !== "idle"),
  }), [client, external]);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, [client, external]);
  const onAnonymous = useCallback(() => {
    if (!active.current) return;
    const hasOldPrivateState = previousPrivate.queries.some((query) => client.getQueryCache().get(query.queryHash) === query)
      || previousPrivate.mutations.some((mutation) => client.getMutationCache().getAll().includes(mutation));
    if (!hasOldPrivateState) return;
    const capabilities = client.getQueryState<Capabilities>(["capabilities"]);
    const publicSnapshot = capabilities?.status === "success" && capabilities.data
      ? { data: capabilities.data, updatedAt: capabilities.dataUpdatedAt }
      : undefined;
    active.current = false;
    client.clear();
    activateSession(undefined, publicSnapshot);
  }, [activateSession, client, previousPrivate]);
  return (
    <Page
      onAuthenticated={(session) => {
        if (!active.current) return;
        active.current = false;
        const capabilities = client.getQueryState<Capabilities>(["capabilities"]);
        const publicSnapshot = capabilities?.status === "success" && capabilities.data
          ? { data: capabilities.data, updatedAt: capabilities.dataUpdatedAt }
          : undefined;
        client.clear();
        activateSession(session, publicSnapshot);
        navigate(returnTo, { replace: true });
      }}
      onAnonymous={onAnonymous}
      onSignedOut={() => endPrivateSession(client, navigate, () => activateSession())}
    />
  );
}

function UnauthorizedBoundary({ rotateClient }: { rotateClient: (session?: SessionInfo, capabilities?: PublicCapabilitiesSnapshot) => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      endPrivateSession(client, navigate, rotateClient, {
        returnTo: browserCompanionReturnTo(location.pathname, location.search),
      });
    });
    return () => setUnauthorizedHandler(null);
  }, [client, location.pathname, location.search, navigate, rotateClient]);
  return (
    <RouteErrorBoundary>
      <Routes>
        <Route path="/" element={<LazyPage><ShowcasePage /></LazyPage>} />
        <Route path="/showcase" element={<Navigate to="/" replace />} />
        <Route path="/login" element={<LazyPage><LoginRoute activateSession={rotateClient} /></LazyPage>} />
        <Route path="/login/external" element={extensionWorkspace
          ? <Navigate to="/login" replace />
          : <LazyPage><LoginRoute activateSession={rotateClient} external /></LazyPage>} />
        <Route element={<ProtectedLayout rotateClient={rotateClient} />}>
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/chat" element={<ChatPage sendStream={streamConversationMessage} />} />
          <Route path="/videos/:id" element={<VideoDetailPage />} />
          <Route path="/account/link" element={<AccountLinkRoute rotateClient={rotateClient} />} />
          <Route path="/account/browser-companion" element={<BrowserCompanionPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </RouteErrorBoundary>
  );
}

export function App({ initialClient }: { initialClient?: QueryClient }) {
  const [{ client, generation }, setCache] = useState(() => ({
    client: initialClient ?? createPrivateQueryClient(), generation: 0,
  }));
  const rotateClient = useCallback((session?: SessionInfo, capabilities?: PublicCapabilitiesSnapshot) => {
    setCache((current) => ({
      client: session ? createSessionQueryClient(session, capabilities) : createPrivateQueryClient(capabilities),
      generation: current.generation + 1,
    }));
  }, []);
  return (
    <QueryClientProvider client={client}>
      <HashRouter>
        {/* Query observers retain their original client; remount them on rotation. */}
        <UnauthorizedBoundary key={generation} rotateClient={rotateClient} />
      </HashRouter>
    </QueryClientProvider>
  );
}
