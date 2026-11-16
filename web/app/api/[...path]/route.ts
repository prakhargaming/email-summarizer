import { getServerSession } from "next-auth";
import type { NextRequest } from "next/server";
import { apiToken, authOptions } from "@/lib/auth";

// Same-origin /api/* -> FastAPI, server-to-server, with a short-lived signed token for the session's email.
async function forward(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  // Only the signIn callback stores Gmail tokens; the browser never does.
  if (pathname === "/api/gmail/connect") return Response.json({ detail: "not_found" }, { status: 404 });
  const email = (await getServerSession(authOptions))?.user?.email;
  if (!email) return Response.json({ detail: "unauthenticated" }, { status: 401 });
  const res = await fetch(`${process.env.API_URL}${pathname}${search}`, {
    method: req.method,
    headers: {
      Authorization: `Bearer ${apiToken(email)}`,
      "Content-Type": req.headers.get("content-type") ?? "application/json",
    },
    body: req.method === "GET" ? undefined : await req.text(),
    cache: "no-store",
  });
  return new Response(res.body, {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
