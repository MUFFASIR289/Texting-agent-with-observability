import type { CampaignState } from "./types";
import type { Tone } from "@/components/ui";

/* One place decides how a state looks, so a state added to the backend enum
   shows up as neutral rather than silently styled like something it is not. */

const TONES: Partial<Record<CampaignState, Tone>> = {
  AWAITING_APPROVAL: "warn",
  APPROVED: "ok",
  SENT: "ok",
  SENDING: "warn",
  FAILED: "error",
  REJECTED: "error",
  CANCELLED: "neutral",
};

export const stateTone = (s: CampaignState): Tone => TONES[s] ?? "neutral";

/** The states each action is legal from, mirroring orchestrator/transitions.py. */
export const canApprove = (s: CampaignState) => s === "AWAITING_APPROVAL";
export const canReject = (s: CampaignState) => s === "AWAITING_APPROVAL";
export const canSend = (s: CampaignState) => s === "APPROVED";
export const canCancel = (s: CampaignState) =>
  !["SENT", "FAILED", "REJECTED", "CANCELLED"].includes(s);

export const shortId = (id: string) => id.slice(0, 8);

export const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

export const num = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : n.toLocaleString();

/** The API sends these as JSON strings; a malformed one must not blank the page. */
export function parseJson<T>(raw: string | null): T | null {
  if (!raw) return null;
  try { return JSON.parse(raw) as T; } catch { return null; }
}

export const channelList = (raw: string) =>
  raw.split(",").map((c) => c.trim()).filter(Boolean);

export type Offer = { type: string; value: number | null; code: string | null };
export type Predicate = {
  risk_levels?: string[];
  value_tiers?: string[];
  required_reason_codes?: string[];
  [k: string]: unknown;
};
