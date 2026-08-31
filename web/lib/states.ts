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
