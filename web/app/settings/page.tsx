"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, json, KIND_LABEL, RULE_MAX, SIGN_OUT, type ProposedRule, type Rule, type Settings } from "@/lib/api";
import { KindSelect } from "@/lib/kind-select";

// The morning run reads only the newest this-many active rules.
const PROMPT_RULE_CAP = 50;
const HOURS = Array.from({ length: 24 }, (_, h) => ({ h, label: `${h % 12 || 12}:00 ${h < 12 ? "AM" : "PM"}` }));

export default function SettingsPage() {
  const [form, setForm] = useState<Settings | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error" | "load-error">("idle");

  useEffect(() => {
    let live = true;
    json<Settings>("/api/settings").then((f) => live && setForm(f), () => live && setState("load-error"));
    return () => {
      live = false;
    };
  }, []);

  const edit = (patch: Partial<Settings>) => {
    setForm((f) => f && { ...f, ...patch });
    setState("idle");
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setState("saving");
    try {
      setForm(await json<Settings>("/api/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(form),
      }));
      setState("saved");
    } catch {
      setState("error");
    }
  }

  const zones = Intl.supportedValuesOf("timeZone");
  if (form && !zones.includes(form.timezone)) zones.unshift(form.timezone);

  return (
    <>
      <header className="topbar">
        <h1><Link href="/" className="back">‹ Triage</Link></h1>
        <nav><a href={SIGN_OUT}>Sign out</a></nav>
      </header>
      <main className="page">
        <h2>Settings</h2>
        {!form && state !== "load-error" && <p className="muted">Loading…</p>}
        {state === "load-error" && <p className="error" role="alert">Couldn&apos;t load settings. Try again in a bit.</p>}
        {form && (
          <form className="settings" onSubmit={save}>
            <div className="field">
              <label htmlFor="focus">What do you care about?</label>
              <p className="hint" id="focus-hint">
                Read every morning when picking what needs you. Plain words are fine.
              </p>
              <textarea
                id="focus"
                aria-describedby="focus-hint"
                rows={5}
                maxLength={2000}
                placeholder="e.g. Anything from the kids' school, bills and deadlines. Newsletters can wait."
                value={form.focus_text}
                onChange={(e) => edit({ focus_text: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="tz">Time zone</label>
              <select id="tz" value={form.timezone} onChange={(e) => edit({ timezone: e.target.value })}>
                {zones.map((z) => <option key={z} value={z}>{z.replaceAll("_", " ")}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="hour">Morning summary at</label>
              <select id="hour" value={form.notify_hour_local} onChange={(e) => edit({ notify_hour_local: Number(e.target.value) })}>
                {HOURS.map(({ h, label }) => <option key={h} value={h}>{label}</option>)}
              </select>
            </div>
            <div className="actions">
              <button className="primary" type="submit" disabled={state === "saving"}>
                {state === "saving" ? "Saving…" : "Save"}
              </button>
              <span aria-live="polite" className={state === "error" ? "error" : "saved"}>
                {state === "saved" && "Saved."}
                {state === "error" && "Couldn't save. Try again."}
              </span>
            </div>
          </form>
        )}
        <Rules />
      </main>
    </>
  );
}

const JSON_HEADERS = { "content-type": "application/json" };

function Rules() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<{ id: string; draft: ProposedRule } | null>(null);
  const [merge, setMerge] = useState<ProposedRule[] | "loading" | null>(null);

  const reload = () => json<Rule[]>("/api/rules").then(setRules);
  useEffect(() => {
    let live = true;
    json<Rule[]>("/api/rules").then((r) => live && setRules(r), () => live && setError("Couldn't load rules. Try again in a bit."));
    return () => {
      live = false;
    };
  }, []);

  async function run(what: string, fn: () => Promise<unknown>): Promise<boolean> {
    setError("");
    let ok = true;
    try {
      await fn();
    } catch {
      setError(`Couldn't ${what}. Try again.`);
      ok = false;
    }
    await reload().catch(() => {});
    return ok;
  }

  const patch = (r: Rule, body: Partial<Rule>) => {
    setRules((rs) => rs && rs.map((x) => (x.id === r.id ? { ...x, ...body } : x)));
    return run("save that change", () => json(`/api/rules/${r.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body) }));
  };
  const remove = (r: Rule) =>
    confirm(`Delete this rule?\n\n“${r.rule_text}”`) && run("delete the rule", async () => {
      if (!(await api(`/api/rules/${r.id}`, { method: "DELETE" })).ok) throw new Error("delete");
    });

  async function proposeMerge() {
    setError("");
    setMerge("loading");
    try {
      setMerge((await json<{ rules: ProposedRule[] }>("/api/rules/consolidate", { method: "POST" })).rules);
    } catch {
      setMerge(null);
      setError("Couldn't merge rules this time. Try again later.");
    }
  }

  const active = rules?.filter((r) => r.active).length ?? 0;

  return (
    <section aria-labelledby="rules-h" className="rules">
      <h2 id="rules-h">What the assistant has learned <span className="count">{active} active</span></h2>
      <p className="hint">Rules you saved with “Remember this for the future”. Turn one off to stop using it without losing it.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!rules && !error && <p className="muted">Loading…</p>}
      {rules?.length === 0 && <p className="empty">No rules yet. Tick “Remember this for the future” when you accept or reject an item.</p>}

      {active > PROMPT_RULE_CAP && !merge && (
        <div className="banner">
          <p>You have {active} active rules. Only the newest {PROMPT_RULE_CAP} are used. Merge similar ones to keep them all in play.</p>
          <button className="primary" onClick={proposeMerge}>Consolidate rules</button>
        </div>
      )}

      {merge === "loading" && <p className="muted" aria-live="polite">Merging similar rules…</p>}
      {Array.isArray(merge) && (
        <div className="card merge" role="region" aria-labelledby="merge-h">
          <h3 id="merge-h">Review merged rules</h3>
          <p className="hint">{active} active rules become {merge.length}. Your current rules are kept, switched off.</p>
          <ul className="rule-list">
            {merge.map((r, n) => (
              <li key={n}><span className="badge p3">{KIND_LABEL[r.kind]}</span> {r.rule_text}</li>
            ))}
          </ul>
          <div className="actions">
            <button className="primary" onClick={() => run("apply the merged rules", () =>
              json("/api/rules/active", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ rules: merge }) }),
            ).then((ok) => ok && setMerge(null))}>Apply</button>
            <button onClick={() => setMerge(null)}>Cancel</button>
          </div>
        </div>
      )}

      {rules && rules.length > 0 && (
        <ul className="rule-list">
          {rules.map((r) => (
            <li key={r.id} className={r.active ? "rule" : "rule off"}>
              {editing?.id === r.id ? (
                <form
                  className="rule-edit"
                  onSubmit={(e) => {
                    e.preventDefault();
                    patch(r, editing.draft).then((ok) => ok && setEditing(null));
                  }}
                >
                  <label htmlFor={`e-${r.id}`}>Rule</label>
                  <textarea
                    id={`e-${r.id}`}
                    rows={3}
                    maxLength={RULE_MAX}
                    required
                    value={editing.draft.rule_text}
                    onChange={(e) => setEditing({ id: r.id, draft: { ...editing.draft, rule_text: e.target.value } })}
                  />
                  <label htmlFor={`k-${r.id}`}>What it does</label>
                  <KindSelect id={`k-${r.id}`} value={editing.draft.kind} onChange={(kind) => setEditing({ id: r.id, draft: { ...editing.draft, kind } })} />
                  <div className="actions">
                    <button className="primary" type="submit" disabled={!editing.draft.rule_text.trim()}>Save</button>
                    <button type="button" onClick={() => setEditing(null)}>Cancel</button>
                  </div>
                </form>
              ) : (
                <>
                  <input
                    type="checkbox"
                    id={`a-${r.id}`}
                    checked={r.active}
                    onChange={() => patch(r, { active: !r.active })}
                    aria-describedby={`t-${r.id}`}
                  />
                  <div className="rule-body">
                    <label htmlFor={`a-${r.id}`} className="sr-only">Use this rule</label>
                    <span id={`t-${r.id}`}>{r.rule_text}</span>
                    <span className="muted">{KIND_LABEL[r.kind]}{!r.active && " · Off"}</span>
                  </div>
                  <div className="rule-actions">
                    <button className="quiet" onClick={() => setEditing({ id: r.id, draft: { rule_text: r.rule_text, kind: r.kind } })} aria-label={`Edit rule: ${r.rule_text}`}>Edit</button>
                    <button className="quiet" onClick={() => remove(r)} aria-label={`Delete rule: ${r.rule_text}`}>Delete</button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {active >= 2 && active <= PROMPT_RULE_CAP && !merge && (
        <button className="link" onClick={proposeMerge}>Merge similar rules</button>
      )}
    </section>
  );
}
