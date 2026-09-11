"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import VoiceOrb, { OrbState } from "@/components/VoiceOrb";
import ChatFeed from "@/components/ChatFeed";
import { Message } from "@/components/MessageBubble";
import Composer from "@/components/Composer";
import ErrorToast from "@/components/ErrorToast";
import TasksWidget from "@/components/dashboard/TasksWidget";
import CalendarWidget from "@/components/dashboard/CalendarWidget";
import EmailWidget from "@/components/dashboard/EmailWidget";
import NotesSearchWidget from "@/components/dashboard/NotesSearchWidget";
import { ApiError, sendChat, speak } from "@/lib/api";
import { getSessionId } from "@/lib/session";
import { useSpeechRecognition } from "@/lib/useSpeechRecognition";
import { useWakeWord } from "@/lib/useWakeWord";
import { useAudioAnalyser } from "@/lib/useAudioAnalyser";
import { DashboardData, fetchDashboard } from "@/lib/dashboard";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [orbState, setOrbState] = useState<OrbState>("idle");
  const [isBusy, setIsBusy] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [justWoke, setJustWoke] = useState(false);

  const sessionIdRef = useRef<string>("default");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const orbRef = useRef<HTMLDivElement | null>(null);
  // Destructured once: each of these is a useCallback with stable deps
  // inside the hook, but the wrapper object useAudioAnalyser() returns is a
  // new literal every render — depending on that object directly would
  // re-fire every effect/callback below on every render.
  const { ensureContext, bindTarget, attachMic, detachMic, attachAudioElement, detachAnalyser } =
    useAudioAnalyser();

  const refreshDashboard = useCallback(() => {
    void fetchDashboard().then(({ data, error: dashboardError }) => {
      // On failure `data` is null -- leave any existing (possibly
      // stale-but-good) widget data as-is rather than wiping it, and
      // surface the failure as a toast instead of failing silently.
      if (data) setDashboardData(data);
      if (dashboardError) setError(dashboardError);
    });
  }, []);

  useEffect(() => {
    sessionIdRef.current = getSessionId();
    bindTarget(orbRef.current);
    refreshDashboard();
  }, [bindTarget, refreshDashboard]);

  const handleSend = useCallback(
    async (text: string) => {
      setError(null);
      setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text }]);
      setIsBusy(true);
      setOrbState("thinking");
      ensureContext();

      try {
        const result = await sendChat(text, sessionIdRef.current);
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", text: result.reply },
        ]);
        // Zaki may have just created/completed a task, saved a note, booked
        // an event, or drafted an email during this turn's tool loop — pull
        // a fresh snapshot so the side widgets reflect it without a manual
        // refresh.
        refreshDashboard();

        if (!isMuted) {
          setOrbState("speaking");
          const blob = await speak(result.reply);
          const url = URL.createObjectURL(blob);

          if (!audioRef.current) {
            audioRef.current = new Audio();
            attachAudioElement(audioRef.current);
          }
          const el = audioRef.current;
          el.src = url;
          el.onended = () => {
            setOrbState("idle");
            detachAnalyser();
            URL.revokeObjectURL(url);
          };
          await el.play();
        } else {
          setOrbState("idle");
        }
      } catch (err) {
        setOrbState("idle");
        setError(err instanceof ApiError ? err.message : "تعذر الاتصال بالخادم");
      } finally {
        setIsBusy(false);
      }
    },
    [ensureContext, attachAudioElement, detachAnalyser, isMuted, refreshDashboard],
  );

  // Same destructuring rule as useAudioAnalyser above: take the stable
  // functions out of the hook's return object immediately, never carry the
  // object itself into a dependency array.
  const {
    isSupported: isMicSupported,
    isListening: isCommandListening,
    start: startCommandRecognition,
    stop: stopCommandRecognition,
  } = useSpeechRecognition((text) => {
    void handleSend(text);
  });

  const startListening = useCallback(async () => {
    ensureContext();
    try {
      await attachMic();
    } catch {
      setError("تعذر الوصول إلى الميكروفون");
      return;
    }
    setOrbState("listening");
    startCommandRecognition();
  }, [ensureContext, attachMic, startCommandRecognition]);

  const handleMicToggle = useCallback(async () => {
    if (isCommandListening) {
      stopCommandRecognition();
      detachMic();
      setOrbState("idle");
      return;
    }
    await startListening();
  }, [isCommandListening, stopCommandRecognition, detachMic, startListening]);

  // "إصحى يا زكي" — hands-free wake word. Opt-in via the header toggle.
  // Only fires when Zaki is actually idle: a wake heard mid-conversation
  // (e.g. picked up from the speaker output) is ignored rather than
  // interrupting an in-flight turn. orbState/isBusy are read fresh here
  // because useWakeWord stores this callback in a ref and re-reads it on
  // every render, so this closure is never stale despite not being
  // memoized. The hook suspends its own recognizer internally the instant
  // it hears the phrase, before this even runs.
  const {
    isSupported: isWakeWordSupported,
    isArmed: isWakeWordArmed,
    enable: enableWakeWord,
    disable: disableWakeWord,
    resume: resumeWakeWord,
  } = useWakeWord(() => {
    if (orbState === "idle" && !isBusy) {
      // The chime is audio; this is the matching visual beat — a brief
      // bright pulse (globals.css orb-wake-flash) distinct from the
      // ongoing listening-state animation that follows right after.
      setJustWoke(true);
      setTimeout(() => setJustWoke(false), 650);
      void startListening();
    }
  });

  useEffect(() => {
    if (!isCommandListening && orbState === "listening") {
      detachMic();
      setOrbState("idle");
    }
  }, [isCommandListening, orbState, detachMic]);

  // Resume background wake-word listening once a full turn settles back to
  // idle — covers both the "heard the wake word" path and manual mic use.
  useEffect(() => {
    if (orbState === "idle" && isWakeWordArmed) {
      resumeWakeWord();
    }
  }, [orbState, isWakeWordArmed, resumeWakeWord]);

  return (
    <div className="dashboard-grid max-w-7xl mx-auto w-full p-4 flex-1">
      <ErrorToast message={error} onDismiss={() => setError(null)} />

      <aside className="flex flex-col gap-4">
        <p className="text-xs text-muted px-1">المهام والجدول</p>
        <TasksWidget tasks={dashboardData?.tasks ?? []} />
        <CalendarWidget
          events={dashboardData?.calendar ?? null}
          googleConnected={dashboardData?.google_connected ?? false}
        />
      </aside>

      <div className="flex flex-col h-[calc(100dvh-2rem)]">
        <header className="flex flex-col items-center gap-2 pt-4 pb-2 shrink-0">
          <VoiceOrb ref={orbRef} state={orbState} justWoke={justWoke} />
          {isWakeWordSupported && (
            <button
              onClick={() => (isWakeWordArmed ? disableWakeWord() : enableWakeWord())}
              className={`text-xs rounded-full px-3 py-1 transition-colors ${
                isWakeWordArmed
                  ? "bg-[color:var(--accent)]/25 text-[color:var(--accent-2)]"
                  : "glass text-muted hover:bg-white/10"
              }`}
            >
              {isWakeWordArmed ? "الاستماع التلقائي مُفعّل — قل «اصحى يا زكي»" : "تفعيل الاستماع التلقائي"}
            </button>
          )}
        </header>

        <ChatFeed messages={messages} />

        <Composer
          onSend={handleSend}
          disabled={isBusy}
          isListening={isCommandListening}
          isMicSupported={isMicSupported}
          onMicToggle={handleMicToggle}
          isMuted={isMuted}
          onMuteToggle={() => setIsMuted((m) => !m)}
        />
      </div>

      <aside className="flex flex-col gap-4">
        <p className="text-xs text-muted px-1">البريد والتواصل والنظام</p>
        <EmailWidget
          emails={dashboardData?.emails ?? null}
          googleConnected={dashboardData?.google_connected ?? false}
        />
        <NotesSearchWidget
          notes={dashboardData?.notes ?? []}
          onSearch={(q) => void handleSend(`ابحث في الإنترنت عن: ${q}`)}
        />
      </aside>
    </div>
  );
}
