/* Response shapes, transcribed from the live OpenAPI schema rather than guessed.
   Only the fields the console actually renders are listed. */

export type CampaignState =
  | "RECEIVED" | "ANALYZING" | "SEGMENTED" | "PLANNED" | "CONTENT_READY"
  | "VALIDATED" | "AWAITING_APPROVAL" | "APPROVED" | "SENDING" | "SENT"
  | "REJECTED" | "FAILED" | "CANCELLED";

export type Campaign = {
  campaign_id: string;
  account_id: string;
  state: CampaignState;
  goal: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  content_hash: string | null;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  llm_cost_usd: number | null;
  excluded_stale_count: number | null;
  excluded_unknown_count: number | null;
  revised_from: string | null;
  failure_code: string | null;
  failure_detail: string | null;
};

export type Segment = {
  segment_id: string;
  name: string;
  priority: number;
  predicate_json: string | Record<string, unknown>;
  playbook_id: string | null;
  offer_json: string | Record<string, unknown> | null;
  channels: string[] | string;
  customer_count: number;
  rationale: string | null;
};

export type AgentRun = {
  run_id: string;
  stage: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  status: string;
  error: string | null;
  created_at: string;
};

export type Variant = {
  variant_id: string;
  segment_name: string;
  channel: string;
  label: string;
  subject_template: string | null;
  body_template: string;
  preview: string | null;
  preview_unavailable: string | null;
};

export type Send = {
  customer_id: string;
  channel: string;
  variant_id: string | null;
  status: string;
  skip_reason: string | null;
  provider_message_id: string | null;
  attempted_at: string | null;
};

export type CampaignDetail = {
  campaign: Campaign;
  segments: Segment[];
  agent_runs: AgentRun[];
};

export type QueryAnswer = {
  answer: string;
  grounded_in: string[];
  tools_called: string[];
  truncated: boolean;
  tokens_used: number;
};
