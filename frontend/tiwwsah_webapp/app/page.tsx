import { AuthPanel } from "@/components/auth-panel";
import { BrainCircuit, CheckCircle2, MessageCircleWarning, ShieldCheck } from "lucide-react";

const benefits = [
  { icon: MessageCircleWarning, text: "Practice realistic scam scenarios safely" },
  { icon: BrainCircuit, text: "Learn how to recognize manipulation tactics" },
  { icon: CheckCircle2, text: "Receive private feedback after every session" },
];

export default function Home() {
  return (
    <main className="home-page">
      <section className="home-hero" aria-labelledby="home-title">
        <a className="home-brand" href="#top" aria-label="ScamAI home">
          <ShieldCheck aria-hidden="true" />
          <span>ScamAI</span>
        </a>

        <div className="home-hero-content">
          <p className="home-kicker">AI-powered scam awareness training</p>
          <h1 id="home-title">Practice spotting scams. Build real confidence.</h1>
          <p className="home-intro">
            Learn to recognize social engineering through safe, clearly identified simulations—before the stakes are real.
          </p>

          <div className="home-benefits">
            {benefits.map(({ icon: Icon, text }) => (
              <div className="home-benefit" key={text}>
                <span><Icon aria-hidden="true" /></span>
                <p>{text}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="home-safety-note">
          <ShieldCheck aria-hidden="true" />
          Every conversation is a consensual, educational AI simulation.
        </p>
      </section>

      <section className="home-auth-section" aria-label="Account access">
        <AuthPanel />
      </section>
    </main>
  );
}

