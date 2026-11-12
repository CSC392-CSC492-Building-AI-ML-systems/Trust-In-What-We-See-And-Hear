"use client";

import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { usePathname, useRouter } from "next/navigation";
import {
  CircleUserRound,
  Clock3,
  LibraryBig,
  LogOut,
  Menu,
  Plus,
  Settings,
  ShieldCheck,
  UsersRound,
  X,
} from "lucide-react";
import { useState, useEffect } from "react";
import { getValidAccessToken } from "@/lib/supabase/auth-session";
import { apiFetch } from '../lib/api-fetch'

const navigation = [
  { href: "/simulations/new", label: "New Simulation", icon: Plus },
  { href: "/friends", label: "Friends", icon: UsersRound },
  { href: "/history", label: "History", icon: Clock3 },
  { href: "/scenarios", label: "Scenarios", icon: LibraryBig },
  { href: "/settings", label: "Settings", icon: Settings },
];


export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [firstName, setFirstName] = useState();
  const [lastName, setLastName] = useState();
  const [isSigningOut, setIsSigningOut] = useState(false);

  async function loadProfile() {
      try{
          const response = await apiFetch(`${process.env.NEXT_PUBLIC_API_URL}/api/v1/users/me`,"GET")
          if (!response.ok) {
            throw new Error("Unable to load profile");
          }
          const data = await response.json()
          setFirstName(data.first_name);
          setLastName(data.last_name);
      }
      catch(ex){
        console.error(ex);
      }
    }

  useEffect(() => {
    //pull profilename
    loadProfile();
  }, []);

  async function handleSignOut() {
    setIsSigningOut(true);

    try {
      const supabase = createClient();
      await supabase.auth.signOut();
    } finally {
      router.replace("/");
      router.refresh();
    }
  }

  return (
    <>
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <Link className="brand" href="/dashboard" onClick={() => setOpen(false)} aria-label="SafeCall dashboard">
            <span className="brand-mark"><ShieldCheck aria-hidden="true" /></span>
            <span className="sidebar-label">ScamAI</span>
          </Link>
          <button className="mobile-close" type="button" onClick={() => setOpen(false)} aria-label="Close navigation">
            <X aria-hidden="true" />
          </button>
        </div>

        <nav className="nav-list" aria-label="Main navigation">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(`${href}/`));
            return (
              <Link key={href} href={href} className={`nav-link ${active ? "nav-link-active" : ""}`} onClick={() => setOpen(false)}>
                <Icon aria-hidden="true" />
                <span className="sidebar-label">
                  {label === "New Simulation" ? <>New<br className="desktop-label-break" /> Simulation</> : label}
                </span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className={`account-card ${pathname === "/profile" ? "account-active" : ""}`}>
            <Link
              href="/profile"
              className="account"
              onClick={() => setOpen(false)}
              aria-label={`View ${firstName} ${lastName}'s profile`}
            >
              <CircleUserRound aria-hidden="true" />
              <div className="sidebar-label">
                <strong>{firstName}<br />{lastName}</strong>
                <span>Practice account</span>
              </div>
            </Link>
            <button className="account-sign-out" type="button" aria-label="Sign out" title="Sign out" onClick={handleSignOut} disabled={isSigningOut}>
              <LogOut aria-hidden="true" />
              <span>{isSigningOut ? "Signing out…" : "Sign out"}</span>
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
