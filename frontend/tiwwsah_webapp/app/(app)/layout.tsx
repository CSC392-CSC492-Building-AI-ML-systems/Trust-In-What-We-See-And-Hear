import { Sidebar } from "@/components/sidebar";
import { getCurrentUser } from "@/lib/get-current-user";
import { redirect } from "next/navigation";

export default async function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const user = await getCurrentUser();

  if (!user) {
    redirect("/");
  }

  return (
    <div className="app-shell">
      <Sidebar firstName={user.first_name} lastName={user.last_name} />
      <main className="main-content">{children}</main>
    </div>
  );
}
