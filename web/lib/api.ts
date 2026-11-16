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

export const SIGN_OUT = "/ngrok/logout?auth_id=triage";
const LOGIN = "/ngrok/login?auth_id=triage&redirect_path=";

// An expired ngrok session answers /api calls with a redirect to the Google login; our API never redirects.
// "manual" surfaces it as an opaque redirect instead of a CORS failure on idp.ngrok.com.
export async function api(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, { ...init, redirect: "manual" });
  if (res.type === "opaqueredirect") {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- /ngrok/* is served by the edge, not Next
    location.href = LOGIN + encodeURIComponent(location.pathname);
    throw new Error("session_expired");
  }
  return res;
}

export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await api(path, init);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

/** True when Gmail is usable. Stores the refresh token from the current ngrok login once if it is missing or revoked. */
export async function ensureGmail(): Promise<boolean> {
  const ok = (s: { connected: boolean; needs_reauth: boolean }) => s.connected && !s.needs_reauth;
  if (ok(await json("/api/gmail/status"))) return true;
  await api("/api/gmail/connect", { method: "POST" });
  return ok(await json("/api/gmail/status"));
}
