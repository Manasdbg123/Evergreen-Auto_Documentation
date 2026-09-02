"use client";

import { getCurrentUser, logout } from "@/lib/auth";
import { LogIn, LogOut } from "lucide-react";
import { useEffect, useState } from "react";

function isOnLoginPage(): boolean {
  if (typeof window === "undefined") return false;
  const pathname = window.location.pathname;
  return pathname.startsWith("/login");
}

export default function LogOutBox() {
  const [data, setData] = useState<{ email: string } | null>(null);
  const [onLoginPage, setOnLoginPage] = useState(false);

  useEffect(() => {
    async function hello() {
      const user = await getCurrentUser();
      setData(user);
    }
    hello();

    setOnLoginPage(isOnLoginPage());
  }, []);

  if (onLoginPage) {
    if (!data) {
      return null;
    }
    return (
      <div className="fixed top-3 right-2 p-1 rounded-lg bg-slate-100">
        <button
          onClick={logout}
          className="shrink-0 rounded-lg bg-white px-4 py-2 text-xs font-medium text-black transition hover:opacity-95 active:scale-95"
        >
          Logout <LogOut strokeWidth={2.8} size={13} color="black" />
        </button>
      </div>
    );
  }

  return (
    <div className="fixed top-5.5 right-2">
      {data ? (
        <button
          onClick={logout}
          className="shrink-0 rounded-lg cursor-pointer bg-neutral-50 px-4 py-2 text-xs font-medium text-black transition hover:opacity-95 active:scale-95"
        >
          Logout <LogOut strokeWidth={2.8} size={13} color="black" />
        </button>
      ) : (
        <button
          onClick={() => {
            const callbackUrl =
              window.location.pathname +
              window.location.search +
              window.location.hash;
            window.location.href = `/login?callbackUrl=${encodeURIComponent(callbackUrl)}`;
          }}
          className="shrink-0 px-3 py-1.5 mr-3 cursor-pointer rounded-lg bg-neutral-50 inline-flex items-center justify-center gap-1 border border-neutral-200 text-xs font-medium text-black transition hover:opacity-95 active:scale-95"
        >
          Login <LogIn strokeWidth={2.8} size={13} color="black" />
        </button>
      )}
    </div>
  );
}
