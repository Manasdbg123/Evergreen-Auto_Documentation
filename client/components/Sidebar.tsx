"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useDocumentsList } from "@/hooks/useDocumentsList";
import { bricolageGrotesque } from "@/app/fonts";
import { House } from "lucide-react";

export default function Sidebar() {
  const { documents } = useDocumentsList();
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 h-[calc(100vh-16px)] overflow-hidden rounded-xl my-2 ml-2 border border-neutral-200 flex flex-col bg-white">
      <div className="px-4 py-4 border-b border-neutral-100">
        <Link
          href="/"
          className={`text-md flex flex-row items-center gap-1 font-semibold text-neutral-900 hover:text-neutral-600 ${bricolageGrotesque.className}`}
        >
          <House color="#da5cc7" strokeWidth={2.8} size={16} /> Home
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {documents.length === 0 && (
          <p className="px-4 py-6 text-xs text-neutral-400">No documents yet</p>
        )}

        {documents.map((doc) => {
          const isActive = pathname === `/document/${doc.id}`;
          const isProcessing = doc.latestVersion.status === "processing";

          return (
            <Link
              key={doc.id}
              href={`/document/${doc.id}`}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm mx-2 rounded-lg transition-colors ${
                isActive
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-700 hover:bg-neutral-100"
              }`}
            >
              {isProcessing && (
                <span
                  className={`w-1.5 h-1.5 rounded-full shrink-0 animate-pulse ${
                    isActive ? "bg-white" : "bg-amber-500"
                  }`}
                />
              )}
              <span className="truncate flex-1">{doc.title}</span>
              {doc.latestVersion.versionNumber > 1 && (
                <span
                  className={`text-[10px] shrink-0 ${isActive ? "text-neutral-300" : "text-neutral-400"}`}
                >
                  v{doc.latestVersion.versionNumber}
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </aside>
  );
}
