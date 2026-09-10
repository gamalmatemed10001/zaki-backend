"use client";

import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";

export default function ErrorToast({
  message,
  onDismiss,
}: {
  message: string | null;
  onDismiss: () => void;
}) {
  return (
    <AnimatePresence>
      {message && (
        <motion.div
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -16 }}
          className="fixed top-4 inset-x-4 z-50 mx-auto max-w-md glass-strong rounded-xl px-4 py-3 flex items-center justify-between gap-3"
          style={{ borderColor: "rgba(255, 84, 112, 0.4)" }}
        >
          <p className="text-sm text-[color:var(--danger)]">{message}</p>
          <button
            onClick={onDismiss}
            className="shrink-0 rounded-full p-1 hover:bg-white/10 transition-colors"
            aria-label="إغلاق"
          >
            <X size={16} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
