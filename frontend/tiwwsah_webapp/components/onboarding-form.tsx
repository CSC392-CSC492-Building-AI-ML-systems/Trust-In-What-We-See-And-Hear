"use client";

import { ArrowRight, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { apiFetch } from "@/lib/api-fetch";
import {
  buildOnboardingAnswers,
  buildProfileFields,
  ONBOARDING_QUESTIONS,
  type OnboardingQuestion,
  usernameFromName,
  withRandomSuffix,
} from "@/lib/onboarding";
import { createClient } from "@/lib/supabase/client";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
// Attempts at finding a free username before giving up.
const MAX_USERNAME_ATTEMPTS = 5;

type MaybeNames = { first_name?: unknown; last_name?: unknown };

/** Reads the names saved at sign-up, in either metadata shape. */
async function getSignUpNames() {
  const supabase = createClient();
  const { data: { user } } = await supabase.auth.getUser();
  const metadata = (user?.user_metadata ?? {}) as MaybeNames & { display_name?: MaybeNames };
  const names = metadata.display_name ?? metadata;
  return {
    firstName: typeof names.first_name === "string" ? names.first_name : "",
    lastName: typeof names.last_name === "string" ? names.last_name : "",
  };
}

async function errorCode(response: Response): Promise<string | undefined> {
  try {
    const body = await response.json();
    return body?.detail?.code;
  } catch {
    return undefined;
  }
}

export function OnboardingForm() {
  const router = useRouter();
  const [isContinuing, setIsContinuing] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsContinuing(true);

    try {
      if (!API_URL) throw new Error("NEXT_PUBLIC_API_URL is not configured.");

      const formData = new FormData(event.currentTarget);
      const answers = buildOnboardingAnswers(formData);
      const profileFields = buildProfileFields(formData);

      // 1. Create the profile row. Retry with a suffix if the username is taken.
      const { firstName, lastName } = await getSignUpNames();
      const baseUsername = usernameFromName(firstName, lastName);
      let username = baseUsername;
      let created = false;

      for (let attempt = 0; attempt < MAX_USERNAME_ATTEMPTS && !created; attempt++) {
        const response = await apiFetch(`${API_URL}/api/v1/users/me/onboarding`, "POST", {
          username,
          // Accepted with the terms checkbox at registration.
          accept_privacy_policy: true,
          answers,
        });

        if (response.ok) {
          created = true;
          break;
        }

        const code = await errorCode(response);
        if (code === "username_taken") {
          username = withRandomSuffix(baseUsername);
          continue;
        }
        if (code === "profile_exists") {
          // Already onboarded (e.g. an earlier attempt failed at step 2): just update the profile.
          created = true;
          break;
        }
        if (code === "invalid_names") {
          throw new Error("Your account is missing a first or last name. Please contact support.");
        }
        throw new Error("We couldn’t save your answers. Please try again.");
      }

      if (!created) throw new Error("We couldn’t create your username. Please try again.");

      // 2. Save answers that live in their own `users` columns (e.g. preferred_language).
      if (Object.keys(profileFields).length > 0) {
        const response = await apiFetch(`${API_URL}/api/v1/users/me`, "PATCH", profileFields);
        if (!response.ok) throw new Error("We couldn’t save your language. Please try again.");
      }

      router.replace("/dashboard");
      router.refresh();
    } catch (ex) {
      setError(ex instanceof Error && !(ex instanceof TypeError)
        ? ex.message
        : "Unable to reach the server. Please try again.");
      setIsContinuing(false);
    }
  }

  return (
    <form className="onboarding-card" onSubmit={handleSubmit}>
      <div className="onboarding-fields">
        {ONBOARDING_QUESTIONS.map((question) => (
          <QuestionField key={question.id} question={question} />
        ))}
      </div>

      <div className="onboarding-privacy">
        <ShieldCheck aria-hidden="true" />
        <div>
          <strong>Your privacy matters</strong>
          <p>This information is used only to personalize your training. You’ll be able to review and change it from your profile.</p>
        </div>
      </div>

      {error && <p className="onboarding-error" role="alert">{error}</p>}

      <div className="onboarding-actions">
        <button type="submit" disabled={isContinuing}>
          {isContinuing ? "Continuing…" : "Save & Continue"}
          <ArrowRight aria-hidden="true" />
        </button>
      </div>
    </form>
  );
}

function QuestionField({ question }: { question: OnboardingQuestion }) {
  const Icon = question.icon;
  const className = `onboarding-field${question.wide ? " onboarding-field-wide" : ""}`;
  const heading = (
    <>
      {question.label}
      {!question.required && <small>Optional</small>}
    </>
  );

  if (question.kind === "multiselect") {
    return (
      <fieldset className={className}>
        <legend>{heading}</legend>
        <div className="onboarding-options">
          {question.options.map((option) => (
            <label className="onboarding-option" key={option.value}>
              <input type="checkbox" name={question.id} value={option.value} />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  return (
    <label className={className}>
      <span>{heading}</span>
      <div className="onboarding-control">
        <Icon aria-hidden="true" />
        {question.kind === "select" ? (
          <select name={question.id} defaultValue="" required={question.required}>
            <option value="" disabled={question.required}>{question.placeholder}</option>
            {question.options.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        ) : (
          <input
            name={question.id}
            type="text"
            placeholder={question.placeholder}
            maxLength={question.maxLength}
            required={question.required}
          />
        )}
      </div>
    </label>
  );
}

/*

Age range:
Interests:
Technological Literacy:
Industry: The arts, tech, engineering, science, etc.
Language:
Family structure: list family members? mom, dad, sibling, dog, etc. a call can come about a
neice where they don't have one. it's a good space to tailor it.

get location -> prompt location
static page so user can read the privacy policy / terms and conditions.

*/
