// Short interface sounds. Synthesised with Web Audio by default (no asset files needed);
// optional files can be registered in SOUND_FILES (see public/sounds/README.md).
export type SoundName = "open" | "tap" | "connect" | "purchase" | "error" | "complete";

const SOUND_FILES: Partial<Record<SoundName, string>> = {};

const NOTES: Record<SoundName, Array<[number, number, number]>> = {
  // [frequency Hz, start s, duration s]
  open: [[523.25, 0, 0.18], [783.99, 0.08, 0.26]],
  tap: [[880, 0, 0.05]],
  connect: [[440, 0, 0.12], [554.37, 0.1, 0.12], [659.25, 0.2, 0.3]],
  purchase: [[523.25, 0, 0.14], [659.25, 0.1, 0.14], [783.99, 0.2, 0.14], [1046.5, 0.3, 0.4]],
  error: [[311.13, 0, 0.16], [233.08, 0.12, 0.26]],
  complete: [[987.77, 0, 0.12], [1318.5, 0.08, 0.3]],
};

const KEY = "spiritt.sounds";
let ctx: AudioContext | null = null;
let unlocked = false;
let enabled = localStorage.getItem(KEY) !== "0";

export function soundsEnabled(): boolean {
  return enabled;
}

export function setSoundsEnabled(value: boolean): void {
  enabled = value;
  localStorage.setItem(KEY, value ? "1" : "0");
}

// Browsers and Telegram block audio until a user gesture; we only unlock on the first tap.
export function unlockAudioOnFirstGesture(onUnlocked?: () => void): void {
  const unlock = () => {
    if (unlocked) return;
    unlocked = true;
    try {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Ctor) ctx = new Ctor();
      void ctx?.resume();
    } catch {
      ctx = null;
    }
    onUnlocked?.();
  };
  window.addEventListener("pointerdown", unlock, { once: true, capture: true });
}

export function play(name: SoundName): void {
  if (!enabled || !unlocked) return;
  const file = SOUND_FILES[name];
  if (file) {
    const audio = new Audio(`${import.meta.env.BASE_URL}${file}`);
    audio.volume = 0.35;
    void audio.play().catch(() => undefined);
    return;
  }
  if (!ctx) return;
  const now = ctx.currentTime;
  for (const [freq, start, dur] of NOTES[name]) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = name === "error" ? "triangle" : "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + start);
    gain.gain.linearRampToValueAtTime(0.05, now + start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + start + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now + start);
    osc.stop(now + start + dur + 0.02);
  }
}
