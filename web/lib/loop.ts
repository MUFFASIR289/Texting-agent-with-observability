/* The twelve stages, traced from the real campaign state machine in
   src/texting_agent/schemas/campaign.py plus the measure/optimize close
   (PRD G5). The hero ring and the U2 diagram both read from here, so the
   two are one continuous idea rather than two decorated screens. */

export type Stage = {
  n: number;
  name: string;
  /** The backend state this stage corresponds to, where there is one. */
  state?: string;
  note: string;
};

export const STAGES: Stage[] = [
  { n: 1,  name: "Detect",   state: "ANALYZING",         note: "Score every customer on churn risk. Deterministic, not the model." },
  { n: 2,  name: "Analyze",  state: "ANALYZING",         note: "The agent reads aggregates and names the patterns it can see." },
  { n: 3,  name: "Segment",  state: "SEGMENTED",         note: "Predicates, not lists. Code evaluates them in priority order." },
  { n: 4,  name: "Plan",     state: "PLANNED",           note: "A playbook, an offer and channels, justified by measured engagement." },
  { n: 5,  name: "Generate", state: "CONTENT_READY",     note: "Templates with placeholders. Never a real name or address." },
  { n: 6,  name: "Validate", state: "VALIDATED",         note: "Policy caps, length, footers, banned phrases. Fails loudly." },
  { n: 7,  name: "Review",   state: "AWAITING_APPROVAL", note: "A human sees the exact content and the exact audience." },
  { n: 8,  name: "Approve",  state: "APPROVED",          note: "The decision is bound to a content hash. Change it and it voids." },
  { n: 9,  name: "Render",   state: "SENDING",           note: "Code fills the placeholders. The model never sees the values." },
  { n: 10, name: "Send",     state: "SENT",              note: "Consent, suppression and frequency re-checked at the last moment." },
  { n: 11, name: "Measure",                              note: "Delivered, opened, clicked, converted. Counted, not estimated." },
  { n: 12, name: "Optimize",                             note: "What worked becomes the input to the next campaign." },
];
