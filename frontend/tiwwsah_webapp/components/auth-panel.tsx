"use client";

import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

type AuthMode = "login" | "register";

export function AuthPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("login");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("email_verified") !== "true") return;

    async function finishEmailVerification() {
      const hash = new URLSearchParams(window.location.hash.slice(1));
      const callbackError = query.get("error_description") ?? hash.get("error_description");

      if (callbackError) {
        setError(callbackError);
      } else {
        const supabase = createClient();
        await supabase.auth.getSession();
        await supabase.auth.signOut({ scope: "local" });
        setSuccess("Email verified. You can now log in to your account.");
      }

      window.history.replaceState({}, "", window.location.pathname);
    }

    finishEmailVerification();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSuccess("");
    setIsSubmitting(true);

    try {
      const formData = new FormData(event.currentTarget);
      const email = String(formData.get("email"));
      const password = String(formData.get("password"));
      const supabase = createClient();

      if (mode === "register") {
        const firstName = String(formData.get("firstName")).trim();
        const lastName = String(formData.get("lastName")).trim();
        const confirmPassword = String(formData.get("confirmPassword"));

        if (password !== confirmPassword) {
          setError("Passwords do not match.");
          setIsSubmitting(false);
          return;
        }

        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/?email_verified=true`,
            data: {
              display_name: {
                first_name: firstName,
                last_name: lastName,
              },
            },
          },
        });

        if (signUpError) {
          setError(signUpError.message);
          setIsSubmitting(false);
          return;
        }

        setMode("login");
        setSuccess(`We sent a verification link to ${email}. Verify your email, then log in.`);
        setIsSubmitting(false);
        return;
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password,
        });

        if (signInError) {
          setError(signInError.message);
          setIsSubmitting(false);
          return;
        }
      }

      router.replace("/verify-identity");
    } catch {
      setError("Unable to reach the authentication service. Please try again.");
      setIsSubmitting(false);
    }
  }

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError("");
    setSuccess("");
  }

  return (
    <div className="auth-card">
      <div className="auth-card-heading">
        <span className="auth-heading-icon"><LockKeyhole aria-hidden="true" /></span>
        <div>
          <p>{mode === "login" ? "Welcome back" : "Create your account"}</p>
          <h2>{mode === "login" ? "Sign in to ScamAI" : "Start practising safely"}</h2>
        </div>
      </div>

      <div className="auth-tabs" role="tablist" aria-label="Account options">
        <button type="button" role="tab" aria-selected={mode === "login"} className={mode === "login" ? "auth-tab-active" : ""} onClick={() => changeMode("login")}>Log in</button>
        <button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "auth-tab-active" : ""} onClick={() => changeMode("register")}>Register</button>
      </div>

      <form className="auth-form" key={mode} onSubmit={handleSubmit}>
        {mode === "register" && (
          <div className="auth-name-row">
            <label>
              First name
              <input name="firstName" type="text" autoComplete="given-name" placeholder="Alex" required />
            </label>
            <label>
              Last name
              <input name="lastName" type="text" autoComplete="family-name" placeholder="Morgan" required />
            </label>
          </div>
        )}

        <label>
          Email address
          <input name="email" type="email" autoComplete="email" placeholder="you@example.com" required />
        </label>

        <label>
          Password
          <input name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="Enter your password" minLength={8} required />
        </label>

        {mode === "register" && (
          <label>
            Confirm password
            <input name="confirmPassword" type="password" autoComplete="new-password" placeholder="Enter it again" minLength={8} required />
          </label>
        )}

        {mode === "register" && (
          <label className="auth-checkbox auth-terms">
            <input type="checkbox" name="terms" required />
            <span>I agree to the terms and understand that ScamAI provides educational simulations.</span>
          </label>
        )}

        {error && <p className="auth-error" role="alert">{error}</p>}
        {success && <p className="auth-success" role="status">{success}</p>}

        <button className="auth-submit" type="submit" disabled={isSubmitting}>
          {isSubmitting ? (mode === "login" ? "Logging in…" : "Creating account…") : (mode === "login" ? "Log in" : "Create account")}
          <ArrowRight aria-hidden="true" />
        </button>
      </form>

      <p className="auth-privacy"><ShieldCheck aria-hidden="true" /> Your practice history and feedback stay private to your account.</p>
    </div>
  );
}
