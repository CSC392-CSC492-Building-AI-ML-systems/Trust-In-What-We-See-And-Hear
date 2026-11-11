"use client";

import { createClient } from "@/lib/supabase/client";
import { getValidAccessToken } from "@/lib/supabase/auth-session";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthGuard() {
  const router = useRouter();
  

  useEffect(() => {
    const supabase = createClient();

    async function checkSession() {
      const accessToken = await getValidAccessToken();
      if (!accessToken) router.replace("/");
    }

    checkSession();

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        if (!session) router.replace("/");
      },
    );

    return () => subscription.unsubscribe();
  }, [router]);

  return null;
}
