export type ChatRole = "user" | "assistant" | "system";

export interface ChatHistoryItem {
  role: ChatRole;
  content: string;
  source?: string;
  created_at?: string;
}

export interface SessionResponse {
  session_id: string;
  show_context_in_history: boolean;
  created_at: string;
  history: ChatHistoryItem[];
}

export interface SessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  latest_preview: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  history: ChatHistoryItem[];
}

export type StreamEvent =
  | { type: "delta"; delta: string }
  | { type: "done"; session_id: string; reply: string; history: ChatHistoryItem[] }
  | { type: "error"; error: string };
