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

// --- Day 5: scoring & progress ---

export interface HeadlineScores {
  fluency: number;
  vocabulary: number;
  clarity: number | null;
  relevance: number | null;
  argumentation: number | null;
  overall: number;
}

export interface ProgressPoint extends HeadlineScores {
  session_id: string;
  created_at: string;
  topic_difficulty: number;
  topic_category: string;
  fluency_ewma: number;
  vocabulary_ewma: number;
  clarity_ewma: number | null;
  relevance_ewma: number | null;
  argumentation_ewma: number | null;
  overall_ewma: number;
}

export interface ProgressResponse {
  points: ProgressPoint[];
  latest: ProgressPoint | null;
}

export interface RelevanceUtterance {
  text: string;
  start_s: number;
  end_s: number;
  similarity: number;
}

export interface PauseSpan {
  start_s: number;
  end_s: number;
  duration_s: number;
  is_hesitation: boolean;
}

export interface SlowSegment {
  start_s: number;
  end_s: number;
  wpm: number;
}

export interface TranscriptWord {
  word: string;
  start_s: number;
  end_s: number;
}

export interface FillerWordCount {
  word: string;
  count: number;
}

export interface WordIssue {
  word: string;
  offset_s: number;
  error_type: string;
  accuracy_score: number | null;
}

export interface FeedbackResponse {
  session_id: string;
  created_at: string;
  topic_text: string;
  topic_category: string;
  topic_difficulty: number;
  duration_s: number | null;
  headline: HeadlineScores;
  full_text: string;
  transcript_words: TranscriptWord[];
  pauses: PauseSpan[];
  slow_segments: SlowSegment[];
  top_filler_words: FillerWordCount[];
  relevance_drift_curve: RelevanceUtterance[] | null;
  improvement_points: string[] | null;
  argument_rationale: string | null;
  pronunciation_words_needing_attention: WordIssue[] | null;
}

export interface MessageResponse {
  message: string;
}

export const api = {
  signup: (email: string, password: string, name: string) =>
    request<MessageResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    }),
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) => request<UserResponse>("/auth/me", {}, token),
  verifyEmail: (token: string) =>
    request<MessageResponse>("/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  resendVerification: (email: string) =>
    request<MessageResponse>("/auth/resend-verification", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  forgotPassword: (email: string) =>
    request<MessageResponse>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  resetPassword: (token: string, newPassword: string) =>
    request<MessageResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
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
  getFeedback: (token: string, id: string) =>
    request<FeedbackResponse>(`/sessions/${id}/feedback`, {}, token),
  getProgress: (token: string) => request<ProgressResponse>("/me/progress", {}, token),
  getAudioObjectUrl: async (token: string, id: string): Promise<string> => {
    const res = await fetch(`${API_URL}/sessions/${id}/audio`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  },
};
