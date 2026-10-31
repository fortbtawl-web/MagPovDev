import { API_BASE, ApiError, createApi, type Api } from "../shared/api";
import type { AuthResponse } from "../shared/types";
import { tg } from "./telegram";

let token: string | null = null;
export let session: AuthResponse | null = null;

export async function authenticate(): Promise<AuthResponse> {
  const raw = createApi({ getToken: () => null });
  const r = await raw<AuthResponse>("POST", "/api/auth/telegram", { init_data: tg?.initData ?? "" });
  token = r.token;
  session = r;
  return r;
}

const baseApi = createApi({
  getToken: () => token,
  onUnauthorized: async () => {
    try { await authenticate(); return true; } catch { return false; }
  },
});

let gateListener: (() => void) | null = null;
export function setGateListener(fn: (() => void) | null) { gateListener = fn; }

/** 403 с кодом terms_required / channel_required → приложение перезагружает /api/me и показывает экран доступа. */
export const api: Api = async <T,>(method: string, path: string, body?: unknown): Promise<T> => {
  try {
    return await baseApi<T>(method, path, body);
  } catch (e) {
    if (e instanceof ApiError && e.status === 403 && (e.code === "terms_required" || e.code === "channel_required")) gateListener?.();
    throw e;
  }
};

/** Открывает админ-панель внутри webview Telegram; токен передаётся в hash и сразу удаляется страницей. */
export function openAdminPanel() {
  if (!session?.is_admin || !session.admin_token) return;
  window.location.href = `${API_BASE}/admin#admin_token=${encodeURIComponent(session.admin_token)}&from=miniapp`;
}
export const canOpenAdmin = () => !!(session?.is_admin && session.admin_token);
