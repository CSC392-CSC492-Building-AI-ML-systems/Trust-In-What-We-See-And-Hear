"use client";

import { createClient } from "@/lib/supabase/client";
import { getValidAccessToken } from "@/lib/supabase/auth-session";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

const publicPaths = new Set(["/"]);

export function AuthGuard() {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (publicPaths.has(pathname)) return;

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
  }, [pathname, router]);

  return null;
}
