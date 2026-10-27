import type { LucideIcon } from "lucide-react";

type PagePlaceholderProps = {
  title: string;
  description: string;
  icon: LucideIcon;
};

export function PagePlaceholder({ title, description, icon: Icon }: PagePlaceholderProps) {
  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      </header>
      <div className="placeholder-card">
        <div className="placeholder-icon"><Icon aria-hidden="true" /></div>
        <h2>{title} is ready to build</h2>
        <p>This route and the shared application layout are in place. Feature content can be added here next.</p>
      </div>
    </section>
  );
}

