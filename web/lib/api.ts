import { cookies } from "next/headers";
import { redirect } from "next/navigation";

/* Server-side only. Every call to the service happens here, on the server,
 * which is why the console needs no CORS and no browser-facing proxy: the
 * browser never talks to the API and never holds the key.
 *
 * The key comes from an httpOnly cookie holding whatever the operator signed
 * in with. It is deliberately *not* read from an env var shared by everyone:
 * the backend separates the operator and approver roles, and a console holding
 * both keys would hand every visitor both roles and quietly undo that.
 */

const BASE = process.env.TEXTING_AGENT_URL ?? "http://127.0.0.1:8000";

export const KEY_COOKIE = "ta_key";

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

async function key(): Promise<string> {
  const value = (await cookies()).get(KEY_COOKIE)?.value;
  if (!value) redirect("/signin");
  return value;
}

type Options = { method?: string; body?: unknown; cache?: RequestCache };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: options.method ?? "GET",
    headers: {
      "X-API-Key": await key(),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    // Campaign state changes under us, so nothing here is cacheable.
    cache: options.cache ?? "no-store",
  });

  if (response.status === 401) redirect("/signin?expired=1");

  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const error = payload.error ?? {};
    const details = (error.details ?? [])
      .map((d: { field?: string; problem?: string; rule_id?: string; message?: string }) =>
        d.rule_id ? `${d.rule_id}: ${d.message ?? ""}` : `${d.field ?? ""} ${d.problem ?? ""}`)
      .join("; ");
    throw new ApiError(
      response.status,
      error.code ?? String(response.status),
      [error.message, details].filter(Boolean).join(" — ") || "The request failed.",
    );
  }

  return payload as T;
}

/** True when a key is present — the console shell uses this, not the key itself. */
export async function signedIn(): Promise<boolean> {
  return Boolean((await cookies()).get(KEY_COOKIE)?.value);
}

/** Reachability and the boundary check, for the shell's status line. `/health` is public. */
export async function health(): Promise<{ status: string; boundary_intact: boolean } | null> {
  try {
    const response = await fetch(`${BASE}/health`, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}
