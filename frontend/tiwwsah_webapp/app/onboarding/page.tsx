import { OnboardingForm } from "@/components/onboarding-form";
import { ShieldCheck } from "lucide-react";

export default function OnboardingPage() {
  return (
    <main className="onboarding-page">
      <header className="onboarding-header">
        <div className="onboarding-brand">
          <ShieldCheck aria-hidden="true" />
          <span>ScamAI</span>
        </div>
      </header>

      <section className="onboarding-content" aria-labelledby="onboarding-title">
        <div className="onboarding-intro">
          <h1 id="onboarding-title">Personalize your training</h1>
          <p>Tell us a little about yourself so future simulations can feel relevant without collecting more information than we need.</p>
        </div>
        <OnboardingForm />
      </section>
    </main>
  );
}
