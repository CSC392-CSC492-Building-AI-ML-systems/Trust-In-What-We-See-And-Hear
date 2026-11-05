"use client";

import { ArrowRight, CalendarDays, ShieldCheck, Tag } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

export function OnboardingForm() {
  const router = useRouter();
  const [isContinuing, setIsContinuing] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsContinuing(true);

    // Persistence will be connected to the Python profile endpoint later.
    router.replace("/dashboard");
    router.refresh();
  }

  return (
    <form className="onboarding-card" onSubmit={handleSubmit}>
      <div className="onboarding-fields">
        <label className="onboarding-field">
          <span>Age range</span>
          <div className="onboarding-control">
            <CalendarDays aria-hidden="true" />
            <select name="ageRange" defaultValue="" required>
              <option value="" disabled>Select your age range</option>
              <option value="under-18">Under 18</option>
              <option value="18-24">18–24</option>
              <option value="25-34">25–34</option>
              <option value="35-49">35–49</option>
              <option value="50-64">50–64</option>
              <option value="65-plus">65+</option>
            </select>
          </div>
        </label>

        <label className="onboarding-field">
          <span>Interests <small>Optional</small></span>
          <div className="onboarding-control">
            <Tag aria-hidden="true" />
            <input name="interests" type="text" placeholder="e.g. technology, travel, shopping" />
          </div>
        </label>
      </div>

      <p>
         Add more onboarding questions here once the final set has been decided.
      </p>

      <div className="onboarding-privacy">
        <ShieldCheck aria-hidden="true" />
        <div>
          <strong>Your privacy matters</strong>
          <p>This information is used only to personalize your training. You’ll be able to review and change it from your profile.</p>
        </div>
      </div>

      <div className="onboarding-actions">
        <button type="submit" disabled={isContinuing}>
          {isContinuing ? "Continuing…" : "Save & Continue"}
          <ArrowRight aria-hidden="true" />
        </button>
      </div>
    </form>
  );
}
