"use client";
import Link from "next/link";
import { bricolageGrotesque } from "./fonts";

export default function HomePage() {
  return (
    <main className="h-full flex items-center justify-center">
      <div className="text-center">
        <h1
          className={`text-2xl font-semibold text-neutral-900 mb-2 ${bricolageGrotesque.className}`}
        >
          Turn recordings into documentation
        </h1>
        <p className="text-sm text-neutral-500 mb-6 max-w-sm">
          Upload a rough screen recording and get a structured, step-by-step
          guide.
        </p>
        <Link
          href="/create/document"
          className="inline-block px-5 py-2.5 rounded-lg bg-neutral-900 text-white text-sm font-medium hover:bg-neutral-800"
        >
          Generate Document
        </Link>
      </div>
    </main>
  );
}
