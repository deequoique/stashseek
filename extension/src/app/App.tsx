import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  HashRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router";

import { getSession, logout, setUnauthorizedHandler, streamConversationMessage } from "../api/client";
import type { Capabilities, SessionInfo } from "../api/contracts";
import { capabilitiesQueryOptions, preloadConversationHistory } from "../chat/conversationQueries";
import { AppShell } from "./AppShell";
import { BrandLogo } from "./BrandLogo";
import { useRouteNavigate } from "./RouteTransition";

const AccountLinkPage = lazy(() => import("../account/AccountLinkPage").then((module) => ({ default: module.AccountLinkPage })));
const BrowserCompanionPage = lazy(() => import("../account/BrowserCompanionPage").then((module) => ({ default: module.BrowserCompanionPage })));
const LoginPage = lazy(() => import("../auth/LoginPage").then((module) => ({ default: module.LoginPage })));
const ChatPage = lazy(() => import("../chat/ChatPage").then((module) => ({ default: module.ChatPage })));
const LibraryPage = lazy(() => import("../library/LibraryPage").then((module) => ({ default: module.LibraryPage })));
const ShowcasePage = lazy(() => import("../showcase/ShowcasePage").then((module) => ({ default: module.ShowcasePage })));
const VideoDetailPage = lazy(() => import("../videos/VideoDetailPage").then((module) => ({ default: module.VideoDetailPage })));

interface PublicCapabilitiesSnapshot {
  data: Capabilities;
  updatedAt: number;
}

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
  return <AccountLinkPage onLinked={onLinked} />;
}

function LoginRoute({ activateSession }: { activateSession: (session: SessionInfo, capabilities?: PublicCapabilitiesSnapshot) => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  const returnTo = loginReturnTo(location.state);
  return (
    <LoginPage
      onAuthenticated={(session) => {
        const capabilities = client.getQueryState<Capabilities>(["capabilities"]);
        const publicSnapshot = capabilities?.status === "success" && capabilities.data
          ? { data: capabilities.data, updatedAt: capabilities.dataUpdatedAt }
          : undefined;
        client.clear();
        activateSession(session, publicSnapshot);
        navigate(returnTo, { replace: true });
      }}
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
    <Routes>
      <Route path="/" element={<LazyPage><ShowcasePage /></LazyPage>} />
      <Route path="/showcase" element={<Navigate to="/" replace />} />
      <Route path="/login" element={<LazyPage><LoginRoute activateSession={rotateClient} /></LazyPage>} />
      <Route element={<ProtectedLayout rotateClient={rotateClient} />}>
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/chat" element={<ChatPage sendStream={streamConversationMessage} />} />
        <Route path="/videos/:id" element={<VideoDetailPage />} />
        <Route path="/account/link" element={<AccountLinkRoute rotateClient={rotateClient} />} />
        <Route path="/account/browser-companion" element={<BrowserCompanionPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
