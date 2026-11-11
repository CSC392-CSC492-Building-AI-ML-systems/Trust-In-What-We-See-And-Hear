"use client";

import { createClient } from "@/lib/supabase/client";

let pendingTokenCheck: Promise<string | null> | null = null;

export async function clearLocalSession() {
  const supabase = createClient();
  await supabase.auth.signOut({ scope: "local" });
}

async function resolveValidAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data: { session }, error } = await supabase.auth.getSession();

  if (error || !session) {
    await clearLocalSession();
    return null;
  }

  return session.access_token;
}

export function getValidAccessToken(): Promise<string | null> {
  if (!pendingTokenCheck) {
    pendingTokenCheck = resolveValidAccessToken().finally(() => {
      pendingTokenCheck = null;
    });
  }

  return pendingTokenCheck;
}
