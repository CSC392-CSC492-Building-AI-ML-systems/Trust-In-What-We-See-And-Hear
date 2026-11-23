import { PrivacyPolicyAcceptance } from "@/components/privacy-policy-acceptance";
import { ShieldCheck } from "lucide-react";

export default function PrivacyPolicyPage() {
  return (
    <main className="onboarding-page">
      <header className="onboarding-header">
        <div className="onboarding-brand">
          <ShieldCheck aria-hidden="true" />
          <span>ScamAI</span>
        </div>
      </header>

      <section className="onboarding-content" aria-labelledby="privacy-title">
        <div className="onboarding-intro">
          <h1 id="privacy-title">Privacy Policy</h1>
          <p>Please read how ScamAI handles your information. You need to accept it before setting up your training.</p>
        </div>
        <PrivacyPolicyAcceptance />
      </section>
    </main>
  );
}
