"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Shared AnalyserNode plumbing for both the mic (listening state) and the
 * <audio> playback element (speaking state). One AudioContext is reused for
 * the whole app since browsers cap how many can be created, and
 * createMediaElementSource() may only be called once per <audio> element.
 *
 * The live level is written straight to a bound DOM element's `--level`
 * CSS custom property (overall average) and `--band-0`..`--band-4` (5
 * frequency-bucket averages, for the bar visualizer) on every animation
 * frame — never through React state — so the 60fps loop never triggers a
 * React re-render. globals.css reads these vars to drive the orb's
 * transform/opacity via pure CSS.
 */
const BAND_COUNT = 5;

export function useAudioAnalyser() {
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const mediaSourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const targetRef = useRef<HTMLElement | null>(null);
  const dataRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const rafRef = useRef<number | null>(null);

  const ensureContext = useCallback(() => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext();
    }
    if (audioCtxRef.current.state === "suspended") {
      void audioCtxRef.current.resume();
    }
    return audioCtxRef.current;
  }, []);

  const bindTarget = useCallback((el: HTMLElement | null) => {
    targetRef.current = el;
  }, []);

  const tick = useCallback(() => {
    const analyser = analyserRef.current;
    const target = targetRef.current;
    if (analyser && target) {
      if (!dataRef.current || dataRef.current.length !== analyser.frequencyBinCount) {
        dataRef.current = new Uint8Array(analyser.frequencyBinCount);
      }
      const data = dataRef.current;
      analyser.getByteFrequencyData(data);

      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i];
      target.style.setProperty("--level", (sum / data.length / 255).toFixed(3));

      const bucketSize = Math.max(1, Math.floor(data.length / BAND_COUNT));
      for (let b = 0; b < BAND_COUNT; b++) {
        let bandSum = 0;
        const start = b * bucketSize;
        const end = Math.min(start + bucketSize, data.length);
        for (let i = start; i < end; i++) bandSum += data[i];
        const bandLevel = end > start ? bandSum / (end - start) / 255 : 0;
        target.style.setProperty(`--band-${b}`, bandLevel.toFixed(3));
      }
    }
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const startLoop = useCallback(() => {
    if (rafRef.current === null) tick();
  }, [tick]);

  const stopLoop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const target = targetRef.current;
    target?.style.setProperty("--level", "0");
    for (let b = 0; b < BAND_COUNT; b++) target?.style.setProperty(`--band-${b}`, "0");
  }, []);

  const attachMic = useCallback(async () => {
    const ctx = ensureContext();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    micStreamRef.current = stream;
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    analyserRef.current = analyser;
    startLoop();
  }, [ensureContext, startLoop]);

  const detachMic = useCallback(() => {
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    analyserRef.current = null;
    stopLoop();
  }, [stopLoop]);

  const attachAudioElement = useCallback(
    (el: HTMLAudioElement) => {
      const ctx = ensureContext();
      if (!mediaSourceRef.current) {
        mediaSourceRef.current = ctx.createMediaElementSource(el);
      }
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      mediaSourceRef.current.connect(analyser);
      analyser.connect(ctx.destination);
      analyserRef.current = analyser;
      startLoop();
    },
    [ensureContext, startLoop],
  );

  const detachAnalyser = useCallback(() => {
    analyserRef.current = null;
    stopLoop();
  }, [stopLoop]);

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  return { ensureContext, bindTarget, attachMic, detachMic, attachAudioElement, detachAnalyser };
}
