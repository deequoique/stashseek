import { queryOptions } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { getCapabilities, getConversationTurns, listConversations } from "../api/client";
import type { Capabilities, ConversationHistoryPage, ConversationTurns, SessionInfo } from "../api/contracts";

export const CAPABILITIES_STALE_TIME = 5 * 60_000;
const HISTORY_STALE_TIME = 60_000;

export function capabilitiesQueryOptions(load?: () => Promise<Capabilities>) {
  return queryOptions({
    queryKey: ["capabilities"],
    queryFn: ({ signal }) => load ? load() : getCapabilities(signal),
    retry: false,
    staleTime: CAPABILITIES_STALE_TIME,
  });
}

export function conversationHistoryQueryOptions(load?: () => Promise<ConversationHistoryPage>) {
  return queryOptions({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => load ? load() : listConversations(undefined, signal),
    retry: 1,
    staleTime: HISTORY_STALE_TIME,
  });
}

export function conversationTurnsQueryOptions(
  threadId: string | null,
  load?: (threadId: string) => Promise<ConversationTurns>,
) {
  return queryOptions({
    queryKey: ["conversation", threadId],
    queryFn: ({ signal }) => {
      if (!threadId) throw new Error("请选择一段对话。");
      return load ? load(threadId) : getConversationTurns(threadId, signal);
    },
    retry: 1,
    staleTime: HISTORY_STALE_TIME,
  });
}

export async function preloadConversationHistory(client: QueryClient): Promise<void> {
  const session = client.getQueryData<SessionInfo>(["session"]);
  if (!session?.authenticated || !client.getQueryData<Capabilities>(["capabilities"])?.chat) return;

  // Prefetch retains failures in the query state for the owning page to handle;
  // it must never delay navigation or turn a failed history into an empty one.
  await client.prefetchQuery(conversationHistoryQueryOptions());
  if (
    client.getQueryData(["session"]) !== session
    || !client.getQueryData<Capabilities>(["capabilities"])?.chat
    || client.getQueryState(["conversations"])?.status !== "success"
  ) return;

  const latest = client.getQueryData<ConversationHistoryPage>(["conversations"])?.items?.[0];
  if (latest) await client.prefetchQuery(conversationTurnsQueryOptions(latest.thread_id));
}
