/* global self */

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let payload;
    try {
      payload = event.data?.json();
    } catch {
      payload = null;
    }
    await self.registration.showNotification(
      typeof payload?.title === "string" ? payload.title : "Email triage",
      {
        body: typeof payload?.body === "string" ? payload.body : "Open Email triage to see what needs your attention.",
        icon: "/triage-192.png",
        data: { url: payload?.url },
      },
    );
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const home = new URL("/", self.location.origin);
    let url = home;
    try {
      const target = new URL(event.notification.data?.url ?? "/", home);
      if (target.origin === home.origin) url = target;
    } catch {
      url = home;
    }
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = windows.find((client) => client.url === url.href);
    if (existing) {
      const refreshed = await existing.navigate(url.href).catch(() => null);
      if (refreshed) return refreshed.focus();
    }
    return self.clients.openWindow(url.href);
  })());
});
