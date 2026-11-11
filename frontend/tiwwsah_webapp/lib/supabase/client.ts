import { createClient as createSupabaseClient } from "@supabase/supabase-js";

let browserClient: ReturnType<typeof createSupabaseClient> | undefined;

export function createClient() {
  browserClient ??= createSupabaseClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
  );

  return browserClient;
}
