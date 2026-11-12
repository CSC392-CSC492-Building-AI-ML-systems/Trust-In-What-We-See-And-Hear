"use client";

import {
  clearLocalSession,
  getValidAccessToken,
} from "@/lib/supabase/auth-session";

async function signOutAndRedirect() {
  await clearLocalSession();
  window.location.replace("/");
}

/**
 * Sends an authenticated JSON request to the Python API.
 * Signs the user out when their session is missing or the API rejects it.
 */
export async function apiFetch(
  url: string,
  method: string,
  body?: unknown,
): Promise<Response> {
  const accessToken = await getValidAccessToken();

  if (!accessToken) {
    await signOutAndRedirect();
    throw new Error("Authentication required");
  }

  const response = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 || response.status === 403) {
    await signOutAndRedirect();
    throw new Error("Session expired");
  }

  return response;
}
