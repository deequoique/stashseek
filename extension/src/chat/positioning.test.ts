import { describe, expect, it } from "vitest";

import type { ConversationTurns } from "../api/contracts";
import { durablePendingTurnIndex } from "./ChatPage";

function turn(user: string, assistant: string, createdAt: string) {
  return {
    user_text: user,
    assistant_text: assistant,
    status: "completed" as const,
    citations: [],
    action_results: [],
    created_at: createdAt,
  };
}

function transcript(...turns: ReturnType<typeof turn>[]): ConversationTurns {
  return { conversation_id: "conversation-1", thread_id: "thread-1", turns };
}

describe("durable pending turn positioning", () => {
  it("finds this tab's turn even when another tab appended first", () => {
    const history = transcript(
      turn("old", "old answer", "2026-10-20T08:00:00Z"),
      turn("other tab", "other answer", "2026-10-20T08:01:00Z"),
      turn("mine", "my answer", "2026-10-20T08:02:00Z"),
    );
    expect(durablePendingTurnIndex(history, "mine", "my answer", 1)).toBe(2);
    expect(durablePendingTurnIndex(history, "mine", "my answer", 3)).toBeNull();
  });

  it("accepts the durable answer with the appended source block", () => {
    const history = transcript(
      turn("mine", "my answer\n\n来源：\n[S1] https://example.test", "2026-10-20T08:02:00Z"),
    );
    expect(durablePendingTurnIndex(history, "mine", "my answer", 0)).toBe(0);
  });

  it("does not claim a turn with the same question but another answer", () => {
    const history = transcript(turn("mine", "different answer", "2026-10-20T08:02:00Z"));
    expect(durablePendingTurnIndex(history, "mine", "my answer", 0)).toBeNull();
  });
});
