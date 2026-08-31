import { keyFor, type Role } from "./keys";

/* Server-side only. Every call to the service happens here, on the server,
 * which is why the console needs no CORS and no browser-facing proxy: the
 * browser never talks to the API and never sees a key.
 *
 * The role is chosen per call, not per session, so the backend's separation
 * still decides what each request may do - approve goes out under the approver
 * key, everything else under the operator key. See lib/keys.ts for what that
 * does and does not buy on a shared deployment.
 */

/* The service on the loopback. `texting-agent` sets this when it starts the UI;
   the fallback is the same internal port for when the UI is run on its own. */
const BASE = process.env.TEXTING_AGENT_URL
  ?? `http://127.0.0.1:${process.env.API_INTERNAL_PORT ?? 8001}`;

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

type Options = { method?: string; body?: unknown; role?: Role };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: options.method ?? "GET",
    headers: {
      "X-API-Key": keyFor(options.role ?? "operator"),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    // Campaign state changes under us, so nothing here is cacheable.
    cache: "no-store",
  });

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

/** Reachability and the boundary check, for the shell's status line. `/health` is public. */
export async function health(): Promise<{ status: string; boundary_intact: boolean } | null> {
  try {
    const response = await fetch(`${BASE}/health`, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** Whether the keys are readable at all — the shell says so rather than crashing. */
export function keysAvailable(): string | null {
  try {
    keyFor("operator");
    keyFor("approver");
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : "Keys unavailable.";
  }
}
