// Minimal typed wrapper around the official Telegram WebApp SDK (telegram-web-app.js).
type Haptic = {
  impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
  notificationOccurred(type: "error" | "success" | "warning"): void;
  selectionChanged(): void;
};

type BackButton = { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };

export type TgWebApp = {
  initData: string;
  initDataUnsafe: { user?: { id: number; first_name?: string }; start_param?: string };
  colorScheme: "light" | "dark";
  platform: string;
  version: string;
  ready(): void;
  expand(): void;
  isVersionAtLeast(v: string): boolean;
  setHeaderColor(c: string): void;
  setBackgroundColor(c: string): void;
  setBottomBarColor?(c: string): void;
  disableVerticalSwipes?(): void;
  openLink(url: string, opts?: { try_instant_view?: boolean }): void;
  openTelegramLink(url: string): void;
  openInvoice(url: string, cb?: (status: "paid" | "cancelled" | "failed" | "pending") => void): void;
  onEvent(event: string, cb: () => void): void;
  offEvent(event: string, cb: () => void): void;
  HapticFeedback?: Haptic;
  BackButton?: BackButton;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

export const tg: TgWebApp | undefined = window.Telegram?.WebApp;
export const initData = tg?.initData ?? "";
export const inTelegram = Boolean(initData);

const at = (v: string) => !!tg && typeof tg.isVersionAtLeast === "function" && tg.isVersionAtLeast(v);

export function setupTelegram(): void {
  if (!tg) return;
  tg.ready();
  tg.expand();
  if (at("6.1")) {
    tg.setHeaderColor("#07070d");
    tg.setBackgroundColor("#07070d");
  }
  if (at("7.10")) tg.setBottomBarColor?.("#07070d");
  if (at("7.7")) tg.disableVerticalSwipes?.();
  document.documentElement.dataset.tgScheme = tg.colorScheme;
}

export const haptic = {
  tap: () => at("6.1") && tg?.HapticFeedback?.impactOccurred("light"),
  select: () => at("6.1") && tg?.HapticFeedback?.selectionChanged(),
  success: () => at("6.1") && tg?.HapticFeedback?.notificationOccurred("success"),
  error: () => at("6.1") && tg?.HapticFeedback?.notificationOccurred("error"),
  warning: () => at("6.1") && tg?.HapticFeedback?.notificationOccurred("warning"),
};

export function openLink(url: string): void {
  if (tg && url.startsWith("https://t.me/")) tg.openTelegramLink(url);
  else if (tg) tg.openLink(url);
  else window.open(url, "_blank", "noopener");
}

export function openInvoice(url: string): Promise<string> {
  return new Promise((resolve) => {
    if (!tg || !at("6.1")) {
      openLink(url);
      resolve("pending");
      return;
    }
    tg.openInvoice(url, (status) => resolve(status));
  });
}

export function useBackButton(handler: (() => void) | null): void {
  // Imperative hook-free helper used from effects.
  const bb = at("6.1") ? tg?.BackButton : undefined;
  if (!bb) return;
  if (handler) {
    bb.onClick(handler);
    bb.show();
  } else {
    bb.hide();
  }
}

export function clearBackButton(handler: () => void): void {
  const bb = at("6.1") ? tg?.BackButton : undefined;
  bb?.offClick(handler);
}
