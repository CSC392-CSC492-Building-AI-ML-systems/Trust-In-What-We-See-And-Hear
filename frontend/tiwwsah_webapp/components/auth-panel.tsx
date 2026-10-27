"use client";

import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

type AuthMode = "login" | "register";

export function AuthPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("login");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (mode === "register") {
      setError("Registration will be available when authentication is connected.");
      return;
    }

    setError("");
    setIsSubmitting(true);

    try {
      const response = await fetch("/api/auth/test-session", {
        method: "POST",
      });

      if (!response.ok) {
        throw new Error("Unable to start the test session.");
      }

      router.replace("/verify-identity");
      router.refresh();
    } catch {
      setError("Unable to log in right now. Please try again.");
      setIsSubmitting(false);
    }
  }

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError("");
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

      <form className="auth-form" onSubmit={handleSubmit}>
        {mode === "register" && (
          <label>
            Full name
            <input name="name" type="text" autoComplete="name" placeholder="Alex Morgan" required />
          </label>
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

        {mode === "login" ? (
          <button className="auth-text-button auth-forgot" type="button">Forgot password?</button>
        ) : (
          <label className="auth-checkbox auth-terms">
            <input type="checkbox" name="terms" required />
            <span>I agree to the terms and understand that ScamAI provides educational simulations.</span>
          </label>
        )}

        {error && <p className="auth-error" role="alert">{error}</p>}

        <button className="auth-submit" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Logging in…" : mode === "login" ? "Log in" : "Create account"}
          <ArrowRight aria-hidden="true" />
        </button>
      </form>

      <p className="auth-privacy"><ShieldCheck aria-hidden="true" /> Your practice history and feedback stay private to your account.</p>
    </div>
  );
}
