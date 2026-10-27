import { SESSION_COOKIE_NAME } from "@/lib/auth";
import { NextResponse } from "next/server";

export function POST() {
  const response = NextResponse.json({ user: { name: "Demo Learner" } });

  response.cookies.set(SESSION_COOKIE_NAME, "test-account", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
  });

  return response;
}

export function DELETE() {
  const response = NextResponse.json({ success: true });
  response.cookies.set(SESSION_COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });

  return response;
}
