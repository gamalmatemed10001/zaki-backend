import { forwardRef } from "react";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

const BAR_COUNT = 5;

interface VoiceOrbProps {
  state: OrbState;
  /** Transient true right after the wake word fires — triggers a one-shot
   * bright pulse (globals.css `orb-wake-flash`) distinct from the ongoing
   * state animations, so waking up hands-free has its own visible beat. */
  justWoke?: boolean;
}

/**
 * Multi-layered "reactive energy sphere" — pure CSS/GPU-driven, no
 * animation library. Layers back-to-front:
 *   1. two blurred ambient-wave blobs (slow, independent drift)
 *   2. a rotating particle ring (12 dots)
 *   3. the glow + conic energy ring (existing idle/thinking loop)
 *   4. a 5-bar frequency visualizer arced under the core
 *   5. the glass core
 *
 * Every continuous loop is a @keyframes rule in globals.css keyed off
 * [data-state]; the live listening/speaking reactivity reads `--level` and
 * `--band-0..4`, which lib/useAudioAnalyser.ts writes directly onto this
 * element every animation frame (no React re-render involved).
 */
const VoiceOrb = forwardRef<HTMLDivElement, VoiceOrbProps>(function VoiceOrb({ state, justWoke }, ref) {
  return (
    <div ref={ref} className="voice-orb" data-state={state} data-wake={justWoke ? "true" : undefined}>
      <div className="orb-ambient-wave orb-ambient-wave-1" />
      <div className="orb-ambient-wave orb-ambient-wave-2" />

      <div className="orb-particle-ring">
        {Array.from({ length: 12 }, (_, i) => (
          <span key={i} className="orb-particle" style={{ ["--i" as string]: i }} />
        ))}
      </div>

      <div className="orb-glow" />
      <div className="orb-ring" />

      <div className="orb-bars" aria-hidden>
        {Array.from({ length: BAR_COUNT }, (_, i) => (
          <span key={i} className="orb-bar" style={{ ["--band" as string]: `var(--band-${i}, 0)` }} />
        ))}
      </div>

      <div className="orb-core glass-strong">
        <span className="text-2xl font-bold tracking-wide select-none">زكي</span>
      </div>
    </div>
  );
});

export default VoiceOrb;
