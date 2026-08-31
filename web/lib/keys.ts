import { readFileSync } from "node:fs";
import { join } from "node:path";

/* The keys the console uses, read from the same .env the service reads.
 *
 * One source of truth: you put the keys in .env once, `uv run texting-agent`
 * uses them, and the console uses them too. Nothing to configure twice and no
 * sign-in to get past.
 *
 * .env is gitignored and this file only ever runs on the server, so a key
 * never reaches the browser.
 *
 * The trade: the console can now both create and approve, because it holds
 * both keys. The backend still enforces the split - it is the console that no
 * longer represents two different people. That is right for one operator on a
 * laptop and wrong for a shared deployment; a shared one should go back to
 * asking each person for their own key.
 */

type Entry = { key_id: string; secret: string; role: string };

function fromEnvFile(): string | null {
  // Next runs with cwd = web/, so the repository .env is one level up.
  for (const path of [join(process.cwd(), "..", ".env"), join(process.cwd(), ".env")]) {
    try {
      const match = /^API_KEYS=(.*)$/m.exec(readFileSync(path, "utf8"));
      if (match) return match[1].trim();
    } catch {
      // Not there — try the next location.
    }
  }
  return null;
}

let cached: Record<string, string> | null = null;

function keys(): Record<string, string> {
  if (cached) return cached;

  const raw = process.env.API_KEYS ?? fromEnvFile();
  if (!raw) throw new Error("No API_KEYS found. Expected them in the repository .env, the same file the service reads.");

  let entries: Entry[];
  try {
    entries = JSON.parse(raw);
  } catch {
    throw new Error("API_KEYS is not valid JSON.");
  }

  cached = Object.fromEntries(entries.map((e) => [e.role, e.secret]));
  return cached;
}

export type Role = "operator" | "approver";

export function keyFor(role: Role): string {
  const secret = keys()[role];
  if (!secret) throw new Error(`No ${role} key in API_KEYS. The console needs both roles.`);
  return secret;
}
