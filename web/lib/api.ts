import { signIn } from "next-auth/react";

export type Status = "pending" | "accepted" | "rejected" | "done";

export type Item = {
  id: string;
  sender: string | null;
  subject: string | null;
  summary: string | null;
  why_it_matters: string | null;
  suggested_action: string | null;
  priority: number | null;
  due_hint: string | null;
  status: Status;
  decision_note: string | null;
  gmail_url: string;
  created_at: string;
};

export type RuleKind = "prioritize" | "ignore" | "note";
export type ProposedRule = { rule_text: string; kind: RuleKind };
export type Rule = ProposedRule & { id: string; active: boolean; source_item_id: string | null; created_at: string };
export const KIND_LABEL: Record<RuleKind, string> = {
  prioritize: "Prioritize",
  ignore: "Not an action item",
  note: "Keep in mind",
};
export const KINDS = Object.keys(KIND_LABEL) as RuleKind[];
export const RULE_MAX = 200;

export type Settings = { focus_text: string; timezone: string; notify_hour_local: number };

export const SIGN_OUT = "/api/auth/signout";

// The /api proxy answers 401 when the NextAuth session is missing or expired; FastAPI's own 401s mean the same.
export async function api(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, init);
  if (res.status === 401) {
    await signIn("google");
    throw new Error("session_expired");
  }
  return res;
}

export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await api(path, init);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

/** True when Gmail is usable. Otherwise signing in again (fresh consent) stores a new token. */
export async function gmailUsable(): Promise<boolean> {
  const s = await json<{ connected: boolean; needs_reauth: boolean }>("/api/gmail/status");
  return s.connected && !s.needs_reauth;
}
