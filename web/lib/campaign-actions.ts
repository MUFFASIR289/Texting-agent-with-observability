"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { api, ApiError } from "./api";

/* Every mutation runs on the server, so the key stays server-side and the
 * browser ships no fetch code.
 *
 * The API's own refusals are the interesting output here - a 403 for the wrong
 * role, a 409 for a state that has moved on, a policy violation with rule ids -
 * so they are carried back to the page and shown verbatim rather than being
 * flattened into "something went wrong".
 */

async function run(path: string, body: unknown, back: string) {
  try {
    await api(path, { method: "POST", body });
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

export async function approve(id: string, formData: FormData) {
  await run(`/campaigns/${id}/approve`, note(formData), `/console/c/${id}/approval`);
}

export async function reject(id: string, formData: FormData) {
  await run(`/campaigns/${id}/reject`, note(formData), `/console/c/${id}/approval`);
}

export async function revise(id: string, formData: FormData) {
  await run(`/campaigns/${id}/revise`, note(formData), `/console/c/${id}`);
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
