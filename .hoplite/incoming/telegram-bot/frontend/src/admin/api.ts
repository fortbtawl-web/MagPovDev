import { createApi } from "../shared/api";

const KEY = "spiritt_admin_token";
export const getToken = () => sessionStorage.getItem(KEY);
export const setToken = (t: string | null) => { if (t) sessionStorage.setItem(KEY, t); else sessionStorage.removeItem(KEY); };

let onLogout: () => void = () => {};
export const setLogoutHandler = (f: () => void) => { onLogout = f; };

export const api = createApi({ getToken });
export const publicApi = createApi({ getToken: () => null });

/** Запрос с автоматическим выходом при 401/403. */
export async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  try {
    return await api<T>(method, path, body);
  } catch (e) {
    const s = (e as { status?: number }).status;
    if (s === 401 || s === 403) { setToken(null); onLogout(); }
    throw e;
  }
}
