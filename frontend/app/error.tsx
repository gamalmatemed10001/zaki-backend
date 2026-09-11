"use client";

import { useEffect } from "react";

// Last-resort safety net: catches any exception thrown during render
// anywhere in this route tree that a component's own try/catch didn't
// already handle (handleSend and refreshDashboard in page.tsx already
// catch their own fetch/runtime errors and show ErrorToast instead of
// throwing — this only fires for something genuinely unexpected).
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="glass-strong rounded-2xl px-6 py-8 max-w-sm text-center flex flex-col items-center gap-4">
        <p className="text-[color:var(--danger)] text-sm">حدث خطأ غير متوقع في التطبيق.</p>
        <p className="text-xs text-muted">حاول تحديث الصفحة، أو اضغط الزر بالأسفل لإعادة المحاولة.</p>
        <button
          onClick={reset}
          className="text-xs rounded-full px-4 py-2 glass hover:bg-white/10 transition-colors"
        >
          إعادة المحاولة
        </button>
      </div>
    </div>
  );
}
