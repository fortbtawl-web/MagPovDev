export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

export interface ApiOptions {
  getToken: () => string | null;
  /** Вызывается при 401; вернуть true, если токен обновлён и запрос стоит повторить. */
  onUnauthorized?: () => Promise<boolean>;
}

export type Api = <T>(method: string, path: string, body?: unknown) => Promise<T>;

export function createApi(opts: ApiOptions): Api {
  async function once<T>(method: string, path: string, body?: unknown): Promise<{ res: Response; data: T | null }> {
    const headers: Record<string, string> = {};
    const token = opts.getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res: Response;
    try {
      res = await fetch(API_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw new ApiError(0, "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.");
    }
    let data: T | null = null;
    try { data = (await res.json()) as T; } catch { data = null; }
    return { res, data };
  }
  return async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let r = await once<T>(method, path, body);
    if (r.res.status === 401 && opts.onUnauthorized && (await opts.onUnauthorized())) {
      r = await once<T>(method, path, body);
    }
    if (!r.res.ok) {
      const d = r.data as { detail?: unknown; code?: string } | null;
      const detail = typeof d?.detail === "string" ? d.detail
        : Array.isArray(d?.detail) ? "Проверьте введённые данные" : `Ошибка сервера (${r.res.status})`;
      throw new ApiError(r.res.status, detail, d?.code);
    }
    return r.data as T;
  };
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : "Неизвестная ошибка";
}
