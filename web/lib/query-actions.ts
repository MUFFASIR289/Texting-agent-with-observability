"use server";

import { redirect } from "next/navigation";
import { api, ApiError } from "./api";
import type { QueryAnswer } from "./types";

/* The answer is carried back through the URL so the page stays a server
   component with no client-side state. The question is the operator's own
   words and the answer contains no customer data, so neither is a secret. */

export async function ask(formData: FormData) {
  const account_id = String(formData.get("account_id") ?? "").trim();
  const query = String(formData.get("query") ?? "").trim();
  if (!account_id || !query) redirect("/console/query?error=Ask%20something%2C%20and%20name%20an%20account.");

  let answer: QueryAnswer;
  try {
    answer = await api<QueryAnswer>("/agent/query", {
      method: "POST",
      body: { account_id, query },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      redirect(`/console/query?error=${encodeURIComponent(`${error.code}: ${error.message}`)}&q=${encodeURIComponent(query)}&a=${encodeURIComponent(account_id)}`);
    }
    throw error;
  }

  const params = new URLSearchParams({
    q: query,
    a: account_id,
    answer: answer.answer,
    tools: answer.tools_called.join(","),
    grounded: answer.grounded_in.join(","),
    tokens: String(answer.tokens_used),
    truncated: String(answer.truncated),
  });
  redirect(`/console/query?${params}`);
}
