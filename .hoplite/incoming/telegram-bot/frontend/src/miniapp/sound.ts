import { getPrefs } from "./prefs";

type ACtor = typeof AudioContext;
let ctx: AudioContext | null = null;
let unlocked = false;

// AudioContext создаётся только внутри пользовательского жеста.
export function unlockAudio() {
  if (unlocked) return;
  unlocked = true;
  try {
    const AC: ACtor | undefined = window.AudioContext ?? (window as unknown as { webkitAudioContext?: ACtor }).webkitAudioContext;
    if (AC) ctx = new AC();
    void ctx?.resume();
  } catch { ctx = null; }
}

export type SoundKind = "tap" | "ok" | "err" | "reward" | "complete" | "appear";
// [frequency Hz, start offset s, length s]
const NOTES: Record<SoundKind, [number, number, number][]> = {
  tap: [[520, 0, 0.06]],
  appear: [[440, 0, 0.12], [660, 0.09, 0.16]],
  ok: [[660, 0, 0.09], [880, 0.07, 0.1]],
  err: [[300, 0, 0.12], [220, 0.1, 0.16]],
  complete: [[523, 0, 0.1], [659, 0.08, 0.1], [784, 0.16, 0.16]],
  reward: [[659, 0, 0.09], [784, 0.07, 0.09], [988, 0.14, 0.12], [1319, 0.22, 0.22]],
};
let lastTap = 0;

export function playSound(kind: SoundKind = "tap") {
  const { sound, volume } = getPrefs();
  if (!unlocked || !sound || !ctx || volume <= 0) return;
  const now = performance.now();
  if (kind === "tap") { if (now - lastTap < 90) return; lastTap = now; } // no machine-gun clicks
  try {
    if (ctx.state === "suspended") void ctx.resume();
    const t = ctx.currentTime;
    const peak = (kind === "tap" ? 0.03 : 0.05) * (volume / 0.6);
    for (const [f, d, len] of NOTES[kind]) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = kind === "err" ? "triangle" : "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + d);
      g.gain.exponentialRampToValueAtTime(peak, t + d + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + d + len);
      o.connect(g).connect(ctx.destination);
      o.start(t + d);
      o.stop(t + d + len + 0.02);
    }
  } catch { /* WebView may block audio */ }
}
