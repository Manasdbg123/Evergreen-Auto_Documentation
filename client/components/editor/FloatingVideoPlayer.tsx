"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Play, X } from "lucide-react";

export default function FloatingVideoPlayer({
  videoUrl,
}: {
  videoUrl: string;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            transition={{
              type: "spring",
              stiffness: 900, // higher = snappier, faster settle
              damping: 25, // lower = more overshoot/bounce before settling
              mass: 0.7,
            }}
            style={{ transformOrigin: "bottom right", width: "50vw" }}
            className="rounded-xl overflow-hidden shadow-2xl bg-black"
          >
            <video
              src={videoUrl}
              controls
              autoPlay
              className="w-full h-auto block"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <button
        onClick={() => setIsOpen((v) => !v)}
        aria-label={isOpen ? "Close source recording" : "Show source recording"}
        className="w-12 h-12 rounded-full bg-neutral-900 hover:bg-neutral-800 text-white flex items-center justify-center shadow-lg transition-colors shrink-0"
      >
        {isOpen ? <X size={20} /> : <Play size={18} fill="white" />}
      </button>
    </div>
  );
}
