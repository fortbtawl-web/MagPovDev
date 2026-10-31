export interface TgWebApp {
  initData: string;
  initDataUnsafe: { user?: { id: number; first_name?: string } };
  version: string;
  platform: string;
  colorScheme: "light" | "dark";
  themeParams: Record<string, string | undefined>;
  safeAreaInset?: { top: number; bottom: number; left: number; right: number };
  contentSafeAreaInset?: { top: number; bottom: number; left: number; right: number };
  ready(): void;
  expand(): void;
  onEvent(name: string, cb: () => void): void;
  offEvent?(name: string, cb: () => void): void;
  openLink(url: string): void;
  openTelegramLink(url: string): void;
  showConfirm(message: string, cb: (ok: boolean) => void): void;
  showAlert(message: string, cb?: () => void): void;
  HapticFeedback?: { notificationOccurred(t: "error" | "success" | "warning"): void; impactOccurred?(s: "light" | "medium"): void };
  setHeaderColor?(c: string): void;
  setBackgroundColor?(c: string): void;
  setBottomBarColor?(c: string): void;
  disableVerticalSwipes?(): void;
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
}

declare global {
  interface Window { Telegram?: { WebApp?: TgWebApp } }
}

export const tg: TgWebApp | undefined = window.Telegram?.WebApp;

export function haptic(t: "error" | "success" | "warning") {
  try { tg?.HapticFeedback?.notificationOccurred(t); } catch { /* ignore */ }
}

export function openExternal(url: string) {
  if (tg) tg.openLink(url); else window.open(url, "_blank", "noopener");
}

export function openTg(url: string) {
  if (tg) tg.openTelegramLink(url); else window.open(url, "_blank", "noopener");
}

export function confirmDialog(msg: string): Promise<boolean> {
  return new Promise((res) => {
    if (tg) tg.showConfirm(msg, res); else res(window.confirm(msg));
  });
}

export function applySafeArea() {
  if (!tg) return;
  const root = document.documentElement.style;
  const set = (p: string, v: { top: number; bottom: number; left: number; right: number } | undefined) => {
    if (!v) return;
    root.setProperty(`--${p}-top`, `${v.top}px`);
    root.setProperty(`--${p}-bottom`, `${v.bottom}px`);
    root.setProperty(`--${p}-left`, `${v.left}px`);
    root.setProperty(`--${p}-right`, `${v.right}px`);
  };
  set("sa", tg.safeAreaInset);
  set("csa", tg.contentSafeAreaInset);
}

export function applyBrand() {
  if (!tg) return;
  try { tg.setHeaderColor?.("#070A12"); } catch { /* old client */ }
  try { tg.setBackgroundColor?.("#070A12"); } catch { /* old client */ }
  try { tg.setBottomBarColor?.("#070A12"); } catch { /* old client */ }
  try { tg.disableVerticalSwipes?.(); } catch { /* old client */ }
}

export function tapHaptic() {
  try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch { /* ignore */ }
}
