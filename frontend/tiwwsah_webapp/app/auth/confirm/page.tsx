"use client";

import { createClient } from "@/lib/supabase/client";
import type { EmailOtpType } from "@supabase/supabase-js";
import Link from "next/link";
import { LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

export default function ConfirmEmailPage() {
  const [error, setError] = useState("");

  useEffect(() => {
    async function confirmEmail() {
      const query = new URLSearchParams(window.location.search);
      const tokenHash = query.get("token_hash");
      const type = query.get("type") as EmailOtpType | null;

      if (!tokenHash || !type) {
        setError("This verification link is incomplete or invalid.");
        return;
      }

      const supabase = createClient();
      const { error: verificationError } = await supabase.auth.verifyOtp({
        token_hash: tokenHash,
        type,
      });

      if (verificationError) {
        setError(verificationError.message);
        return;
      }

      window.location.replace("/?email_verified=true");
    }

    void confirmEmail();
  }, []);

  return (
    <main className="auth-callback-page">
      <section className="auth-callback-card" aria-live="polite">
        {error ? (
          <>
            <span className="auth-callback-icon auth-callback-error"><TriangleAlert aria-hidden="true" /></span>
            <h1>Unable to verify email</h1>
            <p>{error}</p>
            <Link href="/">Return to login</Link>
          </>
        ) : (
          <>
            <span className="auth-callback-icon"><ShieldCheck aria-hidden="true" /></span>
            <LoaderCircle className="spin auth-callback-loader" aria-hidden="true" />
            <h1>Verifying your email</h1>
            <p>Please wait while we finish confirming your account.</p>
          </>
        )}
      </section>
    </main>
  );
}
