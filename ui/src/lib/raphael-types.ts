/**
 * TypeScript types for Raphael Conversational API.
 */

export interface RaphaelMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  client_message_id?: string | null;
  created_at: string;
}

export interface RaphaelConversationSummary {
  id: string;
  title: string;
  hermes_session_id?: string | null;
  status: string;
  last_activity_at?: string | null;
  summary_status?: string | null;
  summarized_at?: string | null;
  summary_capture_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RaphaelConversationDetail {
  id: string;
  title: string;
  hermes_session_id?: string | null;
  status: string;
  last_activity_at?: string | null;
  summary_status?: string | null;
  summarized_at?: string | null;
  summary_capture_id?: string | null;
  created_at: string;
  updated_at: string;
  messages: RaphaelMessage[];
}

export interface DiarySessionMemory {
  capture_id: string;
  conversation_id?: string | null;
  title: string;
  summary: string;
  topics: string[];
  decisions: string[];
  tasks: string[];
  next_steps: string[];
  created_at?: string | null;
}

export interface CreateConversationPayload {
  title?: string;
}

export interface SendMessagePayload {
  content: string;
  client_message_id?: string;
}

export interface SendMessageResponse {
  user_message: RaphaelMessage;
  assistant_message: RaphaelMessage;
}

export interface ProblemDetail {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  extensions?: Record<string, unknown>;
}
