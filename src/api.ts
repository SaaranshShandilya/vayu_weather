import type { ChatResult, ConversationMessage, DataInfo, SessionState } from "./types";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 65_000);
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
      ...init,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(
        "The analysis took too long and was stopped safely. Please retry.",
        408,
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    let message = "The request could not be completed.";
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => {
            if (typeof item === "string") return item;
            if (
              item &&
              typeof item === "object" &&
              "msg" in item &&
              typeof item.msg === "string"
            ) {
              return item.msg;
            }
            return "The request was not valid.";
          })
          .join(" ");
      } else if (payload.detail) {
        message = "The analysis service could not complete this request.";
      }
    } catch {
      // Keep the safe fallback message.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  session: () => request<SessionState>("/api/session"),
  login: (password: string) =>
    request<{ authenticated: boolean }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () =>
    request<{ authenticated: boolean }>("/api/logout", {
      method: "POST",
      body: "{}",
    }),
  dataInfo: () => request<DataInfo>("/api/data-info"),
  chat: (message: string, history: ConversationMessage[]) =>
    request<ChatResult>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        history: history.slice(-10).map(({ role, content }) => ({ role, content })),
      }),
    }),
};
