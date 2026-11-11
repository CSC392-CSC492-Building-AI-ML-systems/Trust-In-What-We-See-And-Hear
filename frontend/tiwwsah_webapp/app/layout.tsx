import { AuthGuard } from "@/components/auth-guard";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SafeCall",
  description: "Practice recognizing scams in a safe, educational simulation.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AuthGuard />
        {children}
      </body>
    </html>
  );
}

