// components/UnauthorizedDialogContext.tsx
"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

const UnauthorizedDialogContext = createContext<{
  showUnauthorizedDialog: () => void;
  closeUnauthorizedDialog: () => void;
} | null>(null);

export function UnauthorizedDialogProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <UnauthorizedDialogContext.Provider
      value={{
        showUnauthorizedDialog: () => setIsOpen(true),
        closeUnauthorizedDialog: () => setIsOpen(false),
      }}
    >
      {children}

      {isOpen && (
        <div
          className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 p-4"
          onClick={() => setIsOpen(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl bg-white p-6 text-black shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-6">
              <div>
                <p className="text-sm font-semibold">Unauthorized</p>
                <p className="mt-1 text-xs text-neutral-500">
                  Please log in to continue.
                </p>
              </div>

              <button
                onClick={() => {
                  const callbackUrl =
                    window.location.pathname +
                    window.location.search +
                    window.location.hash;

                  window.location.href = `/login?callbackUrl=${encodeURIComponent(callbackUrl)}`;
                }}
                className="shrink-0 rounded-lg bg-black px-4 py-2 text-xs font-medium text-white transition hover:bg-neutral-800 active:scale-95"
              >
                Login
              </button>
            </div>
          </div>
        </div>
      )}
    </UnauthorizedDialogContext.Provider>
  );
}

export function useUnauthorizedDialog() {
  const context = useContext(UnauthorizedDialogContext);

  if (!context) {
    throw new Error(
      "useUnauthorizedDialog must be used inside UnauthorizedDialogProvider",
    );
  }

  return context;
}
