"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { api, ApiError } from "./api";
import type { Role } from "./keys";

/* Every mutation runs on the server, so the key stays server-side and the
 * browser ships no fetch code.
 *
 * The API's own refusals are the interesting output here - a 403 for the wrong
 * role, a 409 for a state that has moved on, a policy violation with rule ids -
 * so they are carried back to the page and shown verbatim rather than being
 * flattened into "something went wrong".
 */

async function run(path: string, body: unknown, back: string, role: Role = "operator") {
  try {
    await api(path, { method: "POST", body, role });
  } catch (error) {
    if (error instanceof ApiError) {
      redirect(`${back}?error=${encodeURIComponent(`${error.code}: ${error.message}`)}`);
    }
    throw error;
  }
  revalidatePath(back);
  redirect(back);
}

const note = (f: FormData) => {
  const value = String(f.get("note") ?? "").trim();
  return value ? { note: value } : {};
};

/* One action for all three decisions, dispatching on the button that was
 * pressed. Three <button formAction> in a single form cannot be expressed in
 * plain HTML, so React only wires them once it has hydrated - the buttons do
 * nothing with JS disabled. A single form action plus name/value is ordinary
 * HTML and works either way. */
export async function decide(id: string, formData: FormData) {
  const decision = String(formData.get("decision") ?? "");
  // FR-41: approve and reject are the approver's to make; revise is not.
  const paths: Record<string, [string, string, Role]> = {
    approve: [`/campaigns/${id}/approve`, `/console/c/${id}/approval`, "approver"],
    reject: [`/campaigns/${id}/reject`, `/console/c/${id}/approval`, "approver"],
    revise: [`/campaigns/${id}/revise`, `/console/c/${id}`, "operator"],
  };
  const target = paths[decision];
  if (!target) redirect(`/console/c/${id}/approval?error=Unknown%20decision.`);
  await run(target[0], note(formData), target[1], target[2]);
}

export async function cancel(id: string, formData: FormData) {
  await run(`/campaigns/${id}/cancel`, note(formData), `/console/c/${id}`);
}

export async function send(id: string) {
  await run(`/campaigns/${id}/send`, undefined, `/console/c/${id}`);
}

export async function simulateEvents(id: string) {
  await run(`/campaigns/${id}/simulate-events`, undefined, `/console/c/${id}`);
}

export async function createCampaign(formData: FormData) {
  const account_id = String(formData.get("account_id") ?? "").trim();
  const goal = String(formData.get("goal") ?? "").trim();
  if (!account_id || !goal) redirect("/console/new?error=Both%20fields%20are%20required.");

  let id: string;
  try {
    // One LLM run: minutes, not milliseconds. No timeout is imposed here
    // because the service already caps itself with a per-campaign token budget.
    const created = await api<{ campaign_id: string }>("/campaigns", {
      method: "POST",
      body: { account_id, goal },
    });
    id = created.campaign_id;
  } catch (error) {
    if (error instanceof ApiError) {
      redirect(`/console/new?error=${encodeURIComponent(`${error.code}: ${error.message}`)}`);
    }
    throw error;
  }
  revalidatePath("/console");
  redirect(`/console/c/${id}`);
}
