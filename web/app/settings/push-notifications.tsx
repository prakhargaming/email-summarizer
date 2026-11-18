"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
const SUBSCRIPTION = "/api/push/subscription";

type PushState =
  | { kind: "loading" | "install" | "unavailable" | "error" }
  | { kind: "ready"; subscription: PushSubscription | null };

function request(path: string, method: "PUT" | "POST" | "DELETE", body: object) {
  return api(path, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function load(): Promise<PushState> {
  if (!PUBLIC_KEY) return { kind: "unavailable" };
  const onAppleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (onAppleMobile && !window.matchMedia("(display-mode: standalone)").matches) {
    return { kind: "install" };
  }
  if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
    return { kind: "unavailable" };
  }

  await navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" });
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return { kind: "ready", subscription: null };

  const res = await request("/api/push/status", "POST", { endpoint: subscription.endpoint });
  if (res.status === 404) return { kind: "ready", subscription: null };
  if (!res.ok) throw new Error("subscription_status");
  return { kind: "ready", subscription };
}

async function unsubscribe(subscription: PushSubscription) {
  if (!(await subscription.unsubscribe())) {
    const registration = await navigator.serviceWorker.ready;
    if ((await registration.pushManager.getSubscription())?.endpoint === subscription.endpoint) {
      throw new Error("unsubscribe");
    }
  }
}

export function PushNotifications() {
  const [state, setState] = useState<PushState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let live = true;
    load().then(
      (result) => live && setState(result),
      () => live && setState({ kind: "error" }),
    );
    return () => { live = false; };
  }, []);

  async function enable() {
    if (!PUBLIC_KEY) return;
    setBusy(true);
    setMessage("");
    try {
      if (await Notification.requestPermission() !== "granted") {
        setMessage("Allow notifications for Email triage in your device or browser settings, then try again.");
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const applicationServerKey = Uint8Array.from(
        atob(PUBLIC_KEY.replaceAll("-", "+").replaceAll("_", "/")),
        (char) => char.charCodeAt(0),
      );
      const subscription = await registration.pushManager.getSubscription() ??
        await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
      const res = await request(SUBSCRIPTION, "PUT", subscription.toJSON());
      if (res.status === 409) {
        await unsubscribe(subscription);
        setMessage("The previous account’s subscription was removed from this device. Tap Enable again to set up this account.");
        return;
      }
      if (res.status === 503) {
        setMessage("The server is not ready to send notifications yet.");
        return;
      }
      if (!res.ok) throw new Error("save_subscription");
      setState({ kind: "ready", subscription });
      setMessage("Notifications are on for this device.");
    } catch {
      setMessage("Could not enable notifications. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function manage(subscription: PushSubscription, action: "disable" | "test") {
    setBusy(true);
    setMessage("");
    try {
      const body = { endpoint: subscription.endpoint };
      if (action === "disable") {
        const res = await request(SUBSCRIPTION, "DELETE", body);
        if (!res.ok && res.status !== 404) throw new Error("delete_subscription");
        await unsubscribe(subscription);
        setState({ kind: "ready", subscription: null });
        setMessage("Notifications are off for this device.");
        return;
      }

      const res = await request("/api/push/test", "POST", body);
      if (res.status === 404 || res.status === 410) {
        await unsubscribe(subscription);
        setState({ kind: "ready", subscription: null });
        setMessage("This subscription expired. Enable notifications again.");
      } else if (res.ok) {
        setMessage("Test notification sent. Look for it on this device.");
      } else {
        throw new Error("send_test");
      }
    } catch {
      setMessage(action === "disable"
        ? "Could not turn off notifications. Try again."
        : "Could not send a test notification. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const subscription = state.kind === "ready" ? state.subscription : null;

  return (
    <section className="push-settings" aria-labelledby="push-h">
      <h2 id="push-h">Notifications</h2>
      {state.kind === "loading" && <p className="hint">Loading notification settings…</p>}
      {state.kind === "install" && (
        <p className="hint">
          On iPhone or iPad, use Share → Add to Home Screen. Open Email triage from the Home Screen, then enable notifications here.
        </p>
      )}
      {state.kind === "unavailable" && <p className="hint">Push notifications are unavailable here.</p>}
      {state.kind === "error" && <p className="error" role="alert">Could not load notification settings. Reload this page to try again.</p>}
      {state.kind === "ready" && (
        <>
          <p className="hint">Get an alert when the digest finds new action items or Gmail needs reconnection.</p>
          <div className="actions">
            {subscription ? (
              <>
                <button type="button" onClick={() => manage(subscription, "test")} disabled={busy}>
                  Send test notification
                </button>
                <button type="button" onClick={() => manage(subscription, "disable")} disabled={busy}>
                  Turn off
                </button>
              </>
            ) : (
              <button type="button" className="primary" onClick={enable} disabled={busy}>Enable notifications</button>
            )}
          </div>
          <p className="hint" role="status">{message}</p>
        </>
      )}
    </section>
  );
}
