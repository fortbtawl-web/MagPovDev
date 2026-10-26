import { initData } from "./tg";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  if (!navigator.onLine) throw new ApiError(0, "offline", "Нет подключения к интернету.");
  let resp: Response;
  try {
    resp = await fetch(`/api/v1${path}`, {
      method: opts.method ?? "GET",
      headers: { Authorization: `tma ${initData}`, "Content-Type": "application/json" },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    throw new ApiError(0, "network", "Не удалось связаться с сервером. Проверь интернет.");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(resp.status, data.error ?? "error", data.message || "Что-то пошло не так. Попробуй ещё раз.");
  }
  return data as T;
}

export type SubStatus = "none" | "active" | "soon" | "expired" | "suspended" | "pending";

export type Me = {
  user: { telegram_id: number; first_name: string | null; username: string | null; blocked: boolean };
  subscription: {
    status: SubStatus;
    plan: string | null;
    is_trial: boolean;
    lifetime: boolean;
    starts_at: string | null;
    expires_at: string | null;
    remaining_seconds: number | null;
    device_limit: number;
    devices_used: number;
  };
  trial: { used: boolean; available: boolean; days: number };
  vpn: { ready: boolean; test_mode: boolean; provider: string };
  payments: { enabled: boolean; test_mode: boolean };
  maintenance: boolean;
  terms: { version: string; accepted_at: string | null };
  settings: { notifications: boolean; sounds: boolean };
  referral: { invited: number; bonus_days: number; pending: number; enabled: boolean };
  staff: { role: string; title: string; perms: string[] } | null;
  links: { privacy: string; offer: string; owner: string; developer: string; support: string[]; support_extra: string | null };
};

export type Plan = {
  code: string;
  title: string;
  description: string;
  days: number;
  lifetime: boolean;
  devices: number;
  price: number;
  discount: number;
  total: number;
  currency: string;
  price_text: string;
  total_text: string;
  discount_text: string;
  promo: string | null;
};

export type Order = {
  id: string;
  plan: string;
  status: "created" | "pending" | "paid" | "cancelled" | "failed" | "refunded";
  amount: number;
  currency: string;
  amount_text: string;
  discount: number;
  is_test: boolean;
  lifetime: boolean;
  days: number;
  devices: number;
  created_at: string;
  paid_at: string | null;
  provider?: string;
};

export type Device = {
  id: string;
  name: string;
  status: "pending" | "active" | "suspended" | "revoked";
  created_at: string;
  expires_at: string | null;
  lifetime: boolean;
  client_id: string | null;
  has_error: boolean;
};

export type DevicesPayload = {
  devices: Device[];
  used: number;
  limit: number;
  can_add: boolean;
  vpn_ready: boolean;
  test_mode: boolean;
};

export type Connection = { link: string; qr_svg: string; is_test: boolean };

export type Referrals = {
  link: string | null;
  share_url: string | null;
  invited: number;
  bonus_days: number;
  pending: number;
  enabled: boolean;
  trial_days: number;
  payment_days: number;
  invites: { name: string; invited_at: string; trial_done: boolean; payment_done: boolean; bonus_days: number }[];
};

export type Bonuses = {
  entries: { source: string; days: number; description: string; created_at: string }[];
  promos: { code: string; description: string; status: string; created_at: string }[];
  pending_discount: { code: string; description: string } | null;
};

export type Tickets = {
  categories: Record<string, string>;
  tickets: {
    id: number;
    category: string;
    status: string;
    created_at: string;
    messages: { from_admin: boolean; text: string; created_at: string }[];
  }[];
};
