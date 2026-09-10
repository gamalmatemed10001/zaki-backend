"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getSpeechRecognitionCtor, normalizeArabic, SpeechRecognitionLike } from "./speechRecognition";

// "اصحى يا زكي" — matched as a prefix + name rather than one fixed string,
// because ز/ذ are acoustically close and Web Speech frequently mishears
// "زكي" as "ذكي" (reported in testing). Matching either keeps the wake
// word working without over-loosening it to any random phrase.
const WAKE_PREFIX = normalizeArabic("اصحى يا");
const WAKE_NAME_VARIANTS = ["زكي", "ذكي"].map(normalizeArabic);

function matchesWakePhrase(transcript: string): boolean {
  const norm = normalizeArabic(transcript);
  if (!norm.includes(WAKE_PREFIX)) return false;
  return WAKE_NAME_VARIANTS.some((name) => norm.includes(name));
}

/** Short two-tone chime via a throwaway AudioContext — no audio asset, no
 * dependency on the shared analyser/mic pipeline that's about to be used
 * for the actual command recording. Purely a "you're being heard now" cue. */
function playWakeChime() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const now = ctx.currentTime;

    [660, 990].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = now + i * 0.09;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.15, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.18);
    });

    setTimeout(() => void ctx.close(), 400);
  } catch {
    // Non-essential — never let the chime block waking up.
  }
}

/**
 * Hands-free wake-word listener — a SECOND, continuous SpeechRecognition
 * instance separate from the push-to-talk one in useSpeechRecognition.ts.
 * Browsers only allow one active recognizer at a time per mic, so on match
 * this hook stops itself and waits for that stop to actually complete
 * (onend) before notifying the caller — starting the command recognizer
 * while this one is still mid-teardown is a real race that can silently
 * swallow the first start() call.
 *
 * Opt-in only (enable()/disable()) — continuous background listening is
 * never started automatically, since that would mean silently capturing
 * audio without the user having asked for it.
 */
export function useWakeWord(onWake: () => void) {
  const [isSupported, setIsSupported] = useState(false);
  const [isArmed, setIsArmed] = useState(false);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const armedRef = useRef(false);
  const suspendedRef = useRef(false);
  const runningRef = useRef(false);
  const onWakeRef = useRef(onWake);
  onWakeRef.current = onWake;

  useEffect(() => {
    setIsSupported(getSpeechRecognitionCtor() !== null);
  }, []);

  const startRecognizer = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor || runningRef.current || suspendedRef.current || !armedRef.current) return;

    const recognition = new Ctor();
    recognition.lang = "ar-EG";
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      for (let i = event.resultIndex ?? 0; i < event.results.length; i++) {
        const transcript: string = event.results[i]?.[0]?.transcript ?? "";
        if (transcript && matchesWakePhrase(transcript)) {
          suspendedRef.current = true;
          playWakeChime();
          // Override onend just for this stop: wait for the recognizer to
          // actually finish tearing down before telling the caller it's
          // safe to start the command recognizer on the same mic.
          recognition.onend = () => {
            runningRef.current = false;
            onWakeRef.current();
          };
          recognition.stop();
          return;
        }
      }
    };
    // Browsers auto-stop continuous recognition after silence/timeout, and
    // may fire transient "no-speech"/"aborted" errors — both just end up
    // at onend, where we transparently restart as long as still armed.
    recognition.onerror = () => {
      runningRef.current = false;
    };
    recognition.onend = () => {
      runningRef.current = false;
      if (armedRef.current && !suspendedRef.current) {
        recognitionRef.current = null;
        startRecognizer();
      }
    };

    recognitionRef.current = recognition;
    runningRef.current = true;
    try {
      recognition.start();
    } catch {
      runningRef.current = false;
    }
  }, []);

  const enable = useCallback(() => {
    armedRef.current = true;
    setIsArmed(true);
    startRecognizer();
  }, [startRecognizer]);

  const disable = useCallback(() => {
    armedRef.current = false;
    setIsArmed(false);
    recognitionRef.current?.stop();
  }, []);

  const resume = useCallback(() => {
    suspendedRef.current = false;
    startRecognizer();
  }, [startRecognizer]);

  useEffect(() => {
    return () => {
      armedRef.current = false;
      recognitionRef.current?.stop();
    };
  }, []);

  return { isSupported, isArmed, enable, disable, resume };
}
