import { IdentityVerification } from "@/components/identity-verification";
import { ShieldCheck } from "lucide-react";

export default function VerifyIdentityPage() {
  return (
    <main className="verification-page">
      <header className="onboarding-header">
        <div className="onboarding-brand">
          <ShieldCheck aria-hidden="true" />
          <span>ScamAI</span>
        </div>
      </header>

      <section className="verification-content" aria-labelledby="verification-title">
        <div className="verification-intro">
          <p className="verification-kicker">Identity and consent</p>
          <h1 id="verification-title">Verify your identity</h1>
          <p>Identity verification may help protect sensitive personalization and voice features from misuse.</p>
        </div>
        <IdentityVerification />
      </section>
    </main>
  );
}

