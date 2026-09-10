import type {
  HumanReviewDecisionPayload,
  ReviewDetailResponse,
  ReviewInboxResponse,
} from "./types";

async function readJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  // Bound every poll and submission so a stalled backend cannot freeze the control plane.
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(input, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
    const payload = (await response.json()) as T | { error?: string };
    if (!response.ok) {
      const message =
        typeof payload === "object" && payload !== null && "error" in payload
          ? payload.error
          : "Request failed.";
      throw new Error(message || "Request failed.");
    }
    return payload as T;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(
        init?.method === "POST"
          ? "Conferma di invio non ricevuta in tempo. La decisione potrebbe essere già registrata: verifica la coda prima di riprovare."
          : "La dashboard non ha risposto in tempo. I dati visualizzati potrebbero non essere aggiornati.",
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function fetchReviewInbox(): Promise<ReviewInboxResponse> {
  return readJson<ReviewInboxResponse>("/api/reviews");
}

export async function fetchReviewDetail(threadId: string): Promise<ReviewDetailResponse> {
  return readJson<ReviewDetailResponse>(`/api/reviews/${threadId}`);
}

export async function resumeReviewThread(
  threadId: string,
  payload: HumanReviewDecisionPayload,
): Promise<ReviewDetailResponse> {
  return readJson<ReviewDetailResponse>(`/api/reviews/${threadId}/resume`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}
