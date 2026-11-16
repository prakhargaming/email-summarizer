"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ensureGmail, json, RULE_MAX, SIGN_OUT, type Item, type ProposedRule, type Status } from "@/lib/api";
import { KindSelect } from "@/lib/kind-select";

const STATUSES: Status[] = ["pending", "accepted", "done", "rejected"];
const PRIORITY: Record<number, string> = { 1: "Urgent", 2: "Soon", 3: "Whenever" };
const MOVED: Record<Status, string> = {
  pending: "Moved back to Pending",
  accepted: "Added to To do",
  done: "Marked done",
  rejected: "Rejected",
};

const rank = (a: Item, b: Item) =>
  (a.priority ?? 9) - (b.priority ?? 9) || b.created_at.localeCompare(a.created_at);
const title = (i: Item) => i.subject || "(no subject)";
// "Jane Doe <jane@x.com>" reads better as "Jane Doe"; keep the address when there is no name.
const senderName = (s: string | null) => s?.replace(/\s*<[^>]*>$/, "").replace(/^"|"$/g, "") || s || "";

type Load = { items: Item[] } | { problem: "forbidden" | "error" } | null;
// undefined while the server is still suggesting, null when it couldn't.
type Proposal = { item: Item; rule: ProposedRule | null | undefined };
type Decided = Item & { proposed_rule?: ProposedRule | null; rule_error?: string | null };

export default function Home() {
  const [load, setLoad] = useState<Load>(null);
  const [gmailOk, setGmailOk] = useState(true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [announce, setAnnounce] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);

  useEffect(() => {
    let live = true;
    Promise.all(STATUSES.map((s) => json<Item[]>(`/api/items?status=${s}`))).then(
      (lists) => live && setLoad({ items: lists.flat() }),
      (e: Error) => live && setLoad({ problem: e.message === "403" ? "forbidden" : "error" }),
    );
    ensureGmail().then(setGmailOk, () => setGmailOk(false));
    return () => {
      live = false;
    };
  }, []);

  const update = (id: string, fn: (i: Item) => Item) =>
    setLoad((l) => (l && "items" in l ? { items: l.items.map((i) => (i.id === id ? fn(i) : i)) } : l));

  // Each item's decisions run in order: the server rejects "done" if it hasn't seen "accepted" yet.
  // The chain resolves to the last item the server confirmed, which is what a failure rolls back to.
  const chains = useRef<Record<string, Promise<Item>>>({});

  const propose = (p: Proposal) =>
    setProposals((ps) => (ps.some((x) => x.item.id === p.item.id) ? ps.map((x) => (x.item.id === p.item.id ? p : x)) : [...ps, p]));
  const dropProposal = (id: string) => setProposals((ps) => ps.filter((p) => p.item.id !== id));

  function decide(item: Item, decision: Status, note = "", remember = false) {
    const n = note.trim();
    setErrors((e) => ({ ...e, [item.id]: "" }));
    update(item.id, () => ({ ...item, status: decision, decision_note: decision === "pending" ? null : n || item.decision_note }));
    setAnnounce(`${MOVED[decision]}: ${title(item)}`);
    if (remember) propose({ item, rule: undefined });
    chains.current[item.id] = (chains.current[item.id] ?? Promise.resolve(item)).then(async (confirmed) => {
      try {
        const { proposed_rule, rule_error, ...saved } = await json<Decided>(`/api/items/${item.id}/decision`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision, ...(n && { note: n }), ...(remember && { remember }) }),
        });
        if (remember) {
          propose({ item, rule: proposed_rule ?? null });
          setAnnounce(rule_error ? "Couldn't suggest a rule this time." : "Suggested a rule. Review it above Pending.");
        }
        // A newer click already moved it on; let that one's response settle the UI.
        update(item.id, (i) => (i.status === decision ? saved : i));
        return saved;
      } catch {
        if (remember) dropProposal(item.id);
        update(item.id, () => confirmed);
        setAnnounce("");
        setErrors((e) => ({ ...e, [item.id]: "Couldn't save that. Check your connection and try again." }));
        return confirmed;
      }
    });
  }

  const items = load && "items" in load ? [...load.items].sort(rank) : [];
  const of = (...s: Status[]) => items.filter((i) => s.includes(i.status));
  const pending = of("pending");
  const todo = [...of("accepted"), ...of("done")];
  const rejected = of("rejected");

  return (
    <>
      <header className="topbar">
        <h1>Triage</h1>
        <nav>
          <Link href="/settings">Settings</Link>
          <a href={SIGN_OUT}>Sign out</a>
        </nav>
      </header>
      <main className="page">
        <p className="sr-only" aria-live="polite">{announce}</p>
        {!gmailOk && (
          <div className="banner" role="alert">
            <p>Gmail isn&apos;t connected, so new mail can&apos;t be checked.</p>
            <a className="button primary" href={SIGN_OUT}>Reconnect Gmail</a>
          </div>
        )}
        {!load && <p className="muted">Loading…</p>}
        {load && "problem" in load && (
          <p className="error" role="alert">
            {load.problem === "forbidden" ? "This account is not allowed." : "Couldn't load your items. Try again in a bit."}
          </p>
        )}
        {proposals.length > 0 && (
          <section aria-labelledby="proposals-h" className="proposals">
            <h2 id="proposals-h" className="sr-only">Suggested rules</h2>
            {proposals.map((p) => (
              <RuleProposal
                key={p.item.id}
                proposal={p}
                done={(saved) => {
                  dropProposal(p.item.id);
                  setAnnounce(saved ? "Rule saved. It applies from the next morning summary." : "Rule discarded.");
                }}
              />
            ))}
          </section>
        )}
        {load && "items" in load && (
          <>
            <section aria-labelledby="pending-h">
              <h2 id="pending-h">Pending <span className="count">{pending.length}</span></h2>
              {pending.length === 0 ? (
                <p className="empty">Nothing needs you right now.</p>
              ) : (
                pending.map((i) => <PendingCard key={i.id} item={i} error={errors[i.id]} decide={decide} />)
              )}
            </section>

            <section aria-labelledby="todo-h">
              <h2 id="todo-h">To do <span className="count">{of("accepted").length}</span></h2>
              {todo.length === 0 ? (
                <p className="empty">Accepted items show up here.</p>
              ) : (
                <ul className="todo-list">
                  {todo.map((i) => <TodoRow key={i.id} item={i} error={errors[i.id]} decide={decide} />)}
                </ul>
              )}
            </section>

            {rejected.length > 0 && (
              <details className="rejected">
                <summary>Rejected <span className="count">{rejected.length}</span></summary>
                <ul>
                  {rejected.map((i) => (
                    <li key={i.id}>
                      <div>
                        <span className="row-title">{title(i)}</span>
                        <span className="muted">{senderName(i.sender)}</span>
                        {errors[i.id] && <span className="error" role="alert">{errors[i.id]}</span>}
                      </div>
                      <button onClick={() => decide(i, "pending")} aria-label={`Restore ${title(i)}`}>Restore</button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </main>
    </>
  );
}

type RowProps = { item: Item; error?: string; decide: (i: Item, d: Status, note?: string, remember?: boolean) => void };

function GmailLink({ item }: { item: Item }) {
  return (
    <a href={item.gmail_url} target="_blank" rel="noopener noreferrer">
      Open in Gmail<span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

function PendingCard({ item, error, decide }: RowProps) {
  const [note, setNote] = useState("");
  const [remember, setRemember] = useState(false);
  const p = item.priority ?? 3;
  return (
    <article className="card" aria-labelledby={`t-${item.id}`}>
      <div className="card-meta">
        <span className={`badge p${p}`}>{PRIORITY[p] ?? "Whenever"}</span>
        {item.due_hint && <span className="due">Due {item.due_hint}</span>}
      </div>
      <h3 id={`t-${item.id}`}>{title(item)}</h3>
      <p className="sender">{senderName(item.sender)}</p>
      {item.summary && <p>{item.summary}</p>}
      {item.why_it_matters && <p className="why"><strong>Why it matters.</strong> {item.why_it_matters}</p>}
      {item.suggested_action && <p className="suggest"><strong>Suggested.</strong> {item.suggested_action}</p>}
      <GmailLink item={item} />
      <details className="note">
        <summary>Add note</summary>
        <label htmlFor={`n-${item.id}`} className="sr-only">Note for {title(item)}</label>
        <textarea
          id={`n-${item.id}`}
          maxLength={500}
          rows={2}
          placeholder="Optional, saved with your choice"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </details>
      <label className="check">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        Remember this for the future
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button className="primary" onClick={() => decide(item, "accepted", note, remember)}>Accept</button>
        <button onClick={() => decide(item, "rejected", note, remember)}>Reject</button>
      </div>
    </article>
  );
}

function TodoRow({ item, error, decide }: RowProps) {
  const done = item.status === "done";
  return (
    <li className={done ? "todo done" : "todo"}>
      <input
        type="checkbox"
        id={`d-${item.id}`}
        checked={done}
        onChange={() => decide(item, done ? "accepted" : "done")}
      />
      <div className="todo-body">
        <label htmlFor={`d-${item.id}`} className="row-title">{title(item)}</label>
        {item.suggested_action && <span>{item.suggested_action}</span>}
        <span className="muted">
          {senderName(item.sender)}
          {item.due_hint && !done && <> · Due {item.due_hint}</>}
        </span>
        {item.decision_note && <span className="muted note-text">Note: {item.decision_note}</span>}
        {!done && <GmailLink item={item} />}
        {error && <span className="error" role="alert">{error}</span>}
      </div>
      {!done && (
        <button className="quiet" onClick={() => decide(item, "pending")} aria-label={`Move ${title(item)} back to Pending`}>
          Undo
        </button>
      )}
    </li>
  );
}

function RuleProposal({ proposal: { item, rule }, done }: { proposal: Proposal; done: (saved: boolean) => void }) {
  const [draft, setDraft] = useState<ProposedRule | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "error">("idle");
  const value = draft ?? rule;
  const id = `r-${item.id}`;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!value) return;
    setState("saving");
    try {
      await json("/api/rules", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...value, source_item_id: item.id }),
      });
      done(true);
    } catch {
      setState("error");
    }
  }

  return (
    <article className="card proposal" aria-labelledby={`${id}-h`} aria-busy={rule === undefined}>
      <h3 id={`${id}-h`}>Remember for the future?</h3>
      <p className="sender">From your choice on “{title(item)}”</p>
      {rule === undefined && <p className="muted">Suggesting a rule…</p>}
      {rule === null && (
        <>
          <p className="muted">Couldn&apos;t suggest a rule this time. Your choice was still saved.</p>
          <div className="actions"><button onClick={() => done(false)}>Dismiss</button></div>
        </>
      )}
      {value && (
        <form onSubmit={save}>
          <label htmlFor={`${id}-text`}>Rule</label>
          <textarea
            id={`${id}-text`}
            rows={3}
            maxLength={RULE_MAX}
            required
            value={value.rule_text}
            onChange={(e) => setDraft({ ...value, rule_text: e.target.value })}
          />
          <label htmlFor={`${id}-kind`}>What it does</label>
          <KindSelect id={`${id}-kind`} value={value.kind} onChange={(kind) => setDraft({ ...value, kind })} />
          {state === "error" && <p className="error" role="alert">Couldn&apos;t save the rule. Try again.</p>}
          <div className="actions">
            <button className="primary" type="submit" disabled={state === "saving" || !value.rule_text.trim()}>
              {state === "saving" ? "Saving…" : "Save rule"}
            </button>
            <button type="button" onClick={() => done(false)}>Cancel</button>
          </div>
        </form>
      )}
    </article>
  );
}
