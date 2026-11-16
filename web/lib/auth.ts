import { createHmac } from "node:crypto";
import type { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";

// Server-only. The HS256 JWT FastAPI verifies in app/core/auth.py; it only has to survive one hop.
export function apiToken(email: string): string {
  const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const body = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: email, exp: Math.floor(Date.now() / 1000) + 60 })}`;
  return `${body}.${createHmac("sha256", process.env.API_SIGNING_SECRET!).update(body).digest("base64url")}`;
}

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          scope: "openid email profile https://www.googleapis.com/auth/gmail.readonly",
          access_type: "offline",
          prompt: "consent", // without it, repeat logins return no refresh token
        },
      },
    }),
  ],
  callbacks: {
    // FastAPI enforces the allowlist (403) and stores the Gmail token. Any failure refuses the sign-in,
    // so nobody is logged in without working Gmail. The token never enters the session cookie.
    async signIn({ account, profile }) {
      const email = profile?.email;
      if (!email || !(profile as { email_verified?: boolean }).email_verified || !account?.refresh_token) return false;
      const res = await fetch(`${process.env.API_URL}/api/gmail/connect`, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiToken(email)}`, "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: account.refresh_token }),
        signal: AbortSignal.timeout(10_000),
      });
      return res.ok;
    },
  },
};
