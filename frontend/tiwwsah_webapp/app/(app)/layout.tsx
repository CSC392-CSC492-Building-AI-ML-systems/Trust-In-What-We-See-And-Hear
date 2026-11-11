import { AuthGuard } from "@/components/auth-guard";
import { Sidebar } from "@/components/sidebar";

export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="app-shell">
      <AuthGuard /> 
      <Sidebar />
      <main className="main-content">{children}</main>
    </div>
  );
}
