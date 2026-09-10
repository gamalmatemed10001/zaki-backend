"use client";

import { useState, KeyboardEvent } from "react";
import { Send, Mic, MicOff, Volume2, VolumeX } from "lucide-react";

interface ComposerProps {
  onSend: (text: string) => void;
  disabled: boolean;
  isListening: boolean;
  isMicSupported: boolean;
  onMicToggle: () => void;
  isMuted: boolean;
  onMuteToggle: () => void;
}

export default function Composer({
  onSend,
  disabled,
  isListening,
  isMicSupported,
  onMicToggle,
  isMuted,
  onMuteToggle,
}: ComposerProps) {
  const [value, setValue] = useState("");

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") submit();
  };

  return (
    <div className="p-4 flex items-center gap-2 glass-strong rounded-2xl mx-4 mb-4">
      <button
        onClick={onMuteToggle}
        className="shrink-0 rounded-full p-2.5 hover:bg-white/10 transition-colors text-muted"
        aria-label={isMuted ? "تفعيل الصوت" : "كتم الصوت"}
        title={isMuted ? "تفعيل الصوت" : "كتم الصوت"}
      >
        {isMuted ? <VolumeX size={20} /> : <Volume2 size={20} />}
      </button>

      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="اكتب رسالة..."
        dir="rtl"
        className="flex-1 bg-transparent outline-none placeholder:text-muted text-[15px]"
      />

      {isMicSupported ? (
        <button
          onClick={onMicToggle}
          className={`mic-button shrink-0 rounded-full p-2.5 transition-colors ${
            isListening
              ? "is-listening bg-[color:var(--accent)]/30 text-[color:var(--accent-2)]"
              : "hover:bg-white/10 text-muted"
          }`}
          aria-label={isListening ? "إيقاف الاستماع" : "بدء الاستماع"}
        >
          <Mic size={20} />
        </button>
      ) : (
        <span title="الميكروفون غير مدعوم في هذا المتصفح" className="shrink-0 rounded-full p-2.5 text-muted/40">
          <MicOff size={20} />
        </span>
      )}

      <button
        onClick={submit}
        disabled={disabled || !value.trim()}
        className="shrink-0 rounded-full p-2.5 bg-[color:var(--accent)] disabled:opacity-30 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        aria-label="إرسال"
      >
        <Send size={18} className="-scale-x-100" />
      </button>
    </div>
  );
}
