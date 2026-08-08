const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response wasn't JSON; fall back to statusText
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  email: string;
  name: string;
}

export interface TopicResponse {
  id: string;
  text: string;
  category: string;
  difficulty: number;
}

export type SessionStatus =
  | "recording"
  | "uploaded"
  | "transcribing"
  | "transcribed"
  | "scoring"
  | "scored"
  | "failed";

export interface SessionResponse {
  id: string;
  status: SessionStatus;
  topic: TopicResponse;
  duration_s: number | null;
  failure_reason: string | null;
  created_at: string;
}

export const api = {
  signup: (email: string, password: string, name: string) =>
    request<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    }),
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) => request<UserResponse>("/auth/me", {}, token),
  randomTopic: (token: string, difficulty?: number, category?: string) => {
    const params = new URLSearchParams();
    if (difficulty) params.set("difficulty", String(difficulty));
    if (category) params.set("category", category);
    const qs = params.toString();
    return request<TopicResponse>(`/topics/random${qs ? `?${qs}` : ""}`, {}, token);
  },
  createSession: (token: string, difficulty?: number, category?: string) =>
    request<SessionResponse>(
      "/sessions",
      { method: "POST", body: JSON.stringify({ difficulty, category }) },
      token
    ),
  getSession: (token: string, id: string) =>
    request<SessionResponse>(`/sessions/${id}`, {}, token),
  uploadAudio: async (
    token: string,
    id: string,
    blob: Blob,
    filename: string
  ): Promise<{ id: string; status: SessionStatus }> => {
    const form = new FormData();
    form.append("file", blob, filename);
    const res = await fetch(`${API_URL}/sessions/${id}/audio`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch {
        // ignore
      }
      throw new ApiError(res.status, detail);
    }
    return res.json();
  },
  getAudioObjectUrl: async (token: string, id: string): Promise<string> => {
    const res = await fetch(`${API_URL}/sessions/${id}/audio`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  },
};
