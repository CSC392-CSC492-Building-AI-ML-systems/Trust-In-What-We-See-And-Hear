"use client";

import { ArrowRight, Camera, FileCheck2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

const proposedChecks = [
  {
    icon: FileCheck2,
    title: "Government-issued ID",
    description: "A future verification provider may check an eligible identity document.",
  },
  {
    icon: Camera,
    title: "Selfie or liveness check",
    description: "A future flow may compare a live capture with the submitted document.",
  },
];

export function IdentityVerification() {
  const router = useRouter();
  const [isContinuing, setIsContinuing] = useState(false);

  function continueToOnboarding() {
    setIsContinuing(true);
    router.replace("/onboarding");
    router.refresh();
  }

  return (
    <div className="verification-card">
      <div className="verification-checks">
        {proposedChecks.map(({ icon: Icon, title, description }) => (
          <div className="verification-check" key={title}>
            <span><Icon aria-hidden="true" /></span>
            <div>
              <h2>{title}</h2>
              <p>{description}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="verification-privacy">
        <ShieldCheck aria-hidden="true" />
        <p><strong>Privacy note:</strong> The provider, required documents, retention policy, and consent process still need to be selected and reviewed.</p>
      </div>

      <button className="verification-continue" type="button" onClick={continueToOnboarding} disabled={isContinuing}>
        {isContinuing ? "Continuing…" : "Continue to onboarding"}
        <ArrowRight aria-hidden="true" />
      </button>
    </div>
  );
}

