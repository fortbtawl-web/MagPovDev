import { useSyncExternalStore } from "react";

export interface Prefs { sound: boolean; reduceFx: boolean; volume: number }
const clampVol = (v: unknown) => (typeof v === "number" && isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.6);
const KEY = "spiritt.prefs.v1";
const reducedOS = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function read(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) { const j = JSON.parse(raw) as Partial<Prefs>; return { sound: j.sound !== false, reduceFx: j.reduceFx ?? reducedOS, volume: clampVol(j.volume) }; }
  } catch { /* storage unavailable */ }
  return { sound: true, reduceFx: reducedOS, volume: 0.6 };
}

let state = read();
const subs = new Set<() => void>();

function apply() { document.documentElement.dataset.fx = state.reduceFx ? "low" : "full"; }
apply();

export function setPref<K extends keyof Prefs>(k: K, v: Prefs[K]) {
  state = { ...state, [k]: v };
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* ignore */ }
  apply();
  subs.forEach((f) => f());
}
export const getPrefs = () => state;
export function usePrefs(): Prefs {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => state);
}
