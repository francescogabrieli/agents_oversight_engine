import type {
  HumanReviewDecisionPayload,
  ReviewDetailResponse,
  ReviewInboxResponse,
} from "./types";

async function readJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
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
