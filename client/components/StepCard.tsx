import Image from "next/image";
import type { Step } from "@/lib/types";

export default function StepCard({ step }: { step: Step }) {
  return (
    <div className="border border-neutral-200 rounded-xl overflow-hidden bg-white">
      <div className="px-5 py-3 border-b border-neutral-100 flex items-center gap-2">
        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-neutral-900 text-white text-xs font-medium">
          {step.orderIndex + 1}
        </span>
        <h3 className="text-sm font-semibold text-neutral-900">{step.title}</h3>
      </div>

      <div className="relative w-full aspect-video bg-neutral-100">
        {step.screenshotUrl ? (
          <Image
            src={step.screenshotUrl}
            alt={step.title || ""}
            fill
            className="object-contain"
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = "none";
              const fallback = document.createElement("div");
              fallback.className =
                "absolute top-0 left-0 w-full h-full flex items-center justify-center bg-neutral-100";
              fallback.textContent = "Image not available";
              target.parentNode?.appendChild(fallback);
            }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-neutral-100 text-neutral-400 text-xs">
            Image not available
          </div>
        )}
      </div>

      <p className="px-5 py-3 text-sm text-neutral-600">{step.bodyText}</p>
    </div>
  );
}
