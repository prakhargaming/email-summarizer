"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { json, SIGN_OUT, type Settings } from "@/lib/api";

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
      </main>
    </>
  );
}
