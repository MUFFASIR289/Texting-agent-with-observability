"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { KEY_COOKIE } from "./api";

/* The key goes straight into an httpOnly cookie, so it is never readable from
   browser JavaScript and never appears in a URL. */

export async function signIn(formData: FormData) {
  const value = String(formData.get("key") ?? "").trim();
  if (!value) redirect("/signin?error=empty");

  (await cookies()).set(KEY_COOKIE, value, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 12,
  });

  redirect("/console");
}

export async function signOut() {
  (await cookies()).delete(KEY_COOKIE);
  redirect("/signin");
}
