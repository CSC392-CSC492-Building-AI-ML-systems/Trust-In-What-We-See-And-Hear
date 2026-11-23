"use client";

import { ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { markPrivacyPolicyAccepted, PRIVACY_POLICY_VERSION } from "@/lib/privacy-policy";

// How close (px) to the bottom of the policy counts as "read to the end".
const SCROLL_END_TOLERANCE = 8;

// TBD: placeholder text. Replace with the reviewed policy and bump PRIVACY_POLICY_VERSION
// here and in the backend when it changes.
const POLICY_SECTIONS = [
  {
    title: "1. What we collect",
    body: "Placeholder. Your name and email from sign-up, your username, and the optional answers you give during onboarding (such as age range, interests and preferred language).",
  },
  {
    title: "2. Voice samples and simulations",
    body: "Placeholder. If you choose to train a voice, we store your voice samples and the resulting voice model. Simulated calls may be recorded so you can review them.",
  },
  {
    title: "3. Consent between friends",
    body: "Placeholder. Your voice and profile are only used in a friend's simulation after you accept their request. Each consent is single use and expires.",
  },
  {
    title: "4. How we use your information",
    body: "Placeholder. Only to run and personalize scam-awareness simulations. Friends never see your onboarding answers.",
  },
  {
    title: "5. Storage and retention",
    body: "Placeholder. Data is stored with our database and storage provider. Retention periods are to be decided.",
  },
  {
    title: "6. Your choices",
    body: "Placeholder. You can change or remove your onboarding answers, withdraw consents and delete friendships at any time.",
  },
  {
    title: "7. Contact",
    body: "Placeholder. Contact details to be added.",
  },
];

export function PrivacyPolicyAcceptance() {
  const router = useRouter();
  const policyRef = useRef<HTMLDivElement>(null);
  const [hasReadToEnd, setHasReadToEnd] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [isContinuing, setIsContinuing] = useState(false);

  function checkReadToEnd() {
    const policy = policyRef.current;
    if (!policy) return;
    if (policy.scrollTop + policy.clientHeight >= policy.scrollHeight - SCROLL_END_TOLERANCE) {
      setHasReadToEnd(true);
    }
  }

  // If the whole policy fits without scrolling, it has already been seen.
  useEffect(checkReadToEnd, []);

  function handleContinue() {
    setIsContinuing(true);
    markPrivacyPolicyAccepted();
    router.replace("/onboarding");
  }

  return (
    <div className="onboarding-card">
      <div
        className="privacy-policy-text"
        ref={policyRef}
        onScroll={checkReadToEnd}
        tabIndex={0}
        aria-label="Privacy policy text"
      >
        <p className="privacy-policy-version">Version {PRIVACY_POLICY_VERSION}</p>
        {POLICY_SECTIONS.map((section) => (
          <section key={section.title}>
            <h2>{section.title}</h2>
            <p>{section.body}</p>
          </section>
        ))}
      </div>

      {!hasReadToEnd && (
        <p className="privacy-policy-hint">Scroll to the end of the policy to accept it.</p>
      )}

      <label className="privacy-policy-accept">
        <input
          type="checkbox"
          checked={accepted}
          disabled={!hasReadToEnd}
          onChange={(event) => setAccepted(event.target.checked)}
        />
        <span>I have read and accept the Privacy Policy, and I understand that ScamAI provides educational simulations.</span>
      </label>

      <div className="onboarding-actions">
        <button type="button" onClick={handleContinue} disabled={!accepted || isContinuing}>
          {isContinuing ? "Continuing…" : "Accept & Continue"}
          <ArrowRight aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
