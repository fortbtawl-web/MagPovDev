export interface User { public_id: string; id: number; username: string | null; first_name: string | null; last_name: string | null; created_at: string; is_banned: boolean }
export interface Sub {
  id: number; status: string; status_label: string; plan_code: string | null; is_lifetime: boolean;
  device_limit: number; devices_used?: number; source: string; starts_at: string | null;
  expires_at: string | null; created_at: string; user_id: number; remaining_seconds?: number | null;
}
export interface NodeRef { code: string; name: string; flag: string }
export interface Device {
  id: number; user_id: number; label: string; status: string; status_label: string; protocol: string;
  node: NodeRef | null; created_at: string; expires_at: string | null; last_error: string | null;
  client_name: string; subscription_url?: string | null; link?: string | null;
}
export interface Payment {
  provider: string; provider_payment_id: string; status: string; confirmation_url: string | null;
  is_test: boolean; confirmed_at: string | null; error: string | null;
}
export interface Order {
  id: number; user_id: number; plan_code: string; plan_title: string; amount_minor: number; amount_label: string;
  status: string; status_label: string; fulfillment_status: string; fulfillment_error: string | null;
  created_at: string; paid_at: string | null; payment: Payment | null;
  discount_minor: number; promo_code: string | null;
}
export interface Plan {
  id: number; code: string; title: string; description: string; price_minor: number; price_label: string;
  currency: string; duration_days: number | null; is_lifetime: boolean; device_limit: number;
  is_active: boolean; sort_order: number; terms: string; price_coins?: number | null;
}
export interface RefSummary { invited: number; with_trial: number; with_payment: number; earned_days: number; pending: number; balance_days: number }
export interface Gate {
  terms_accepted: boolean; terms_version: string; privacy_url: string; terms_url: string;
  channel_required: boolean; channel_subscribed: boolean | null; channel_url: string | null;
  channel_error: string | null; allowed: boolean;
}
export interface PromoResult { kind: "days" | "percent" | "fixed"; message: string; days: number | null; discount_label: string | null }
export interface PromoActivateResp { result: PromoResult; subscription: Sub | null }
export interface PromoActive { discount: null | { code: string; kind: "percent" | "fixed"; label: string; expires_at: string | null } }
export type PromoKind = "percent" | "fixed" | "days";
export interface Promo {
  id: number; code: string; kind: PromoKind; value: number; value_label: string; max_activations: number | null;
  activations: number; expires_at: string | null; is_active: boolean; note: string | null; created_at: string; created_by: number;
}
export interface PromoActivation {
  id: number; user_id: number; user_public_id: string | number; status: "reserved" | "used" | "applied";
  order_id: number | null; created_at: string; used_at: string | null;
}
export interface Me {
  user: User; subscription: Sub | null; devices_used: number; devices_active: number; bonus_balance: number;
  trial: { available: boolean; days: number; reason: string | null };
  support_url: string | null; referral_link: string | null;
  account_status: "active" | "banned";
  access_state: "none" | "no_devices" | "pending" | "ready" | "inactive";
  support_open_tickets: number;
  gate: Gate;
}
export type AdminRole = "owner" | "admin";
export interface AuthResponse {
  token: string; expires_in: number; is_admin: boolean; role: AdminRole | null; admin_token: string | null; user: User;
}
export interface SubHistoryItem {
  id: number; action: string; action_label: string; old_expires_at: string | null; new_expires_at: string | null;
  days: number | null; note: string | null; created_at: string;
}
export type TicketStatus = "new" | "in_progress" | "waiting_user" | "resolved" | "closed";
export interface Ticket {
  id: number; number: string; category: string; category_label: string; subject: string; status: TicketStatus;
  status_label: string; created_at: string; updated_at: string; last_message_preview: string | null;
}
export interface TicketMessage { id: number; author: "user" | "support" | "system"; text: string; has_attachment: boolean; created_at: string }
export interface KeyLabel { key: string; label: string }
export interface TicketsResp { tickets: Ticket[]; categories: KeyLabel[] }
export interface TicketThread { ticket: Ticket; messages: TicketMessage[] }
export interface DevicesResp { devices: Device[]; used: number; limit: number; subscription: Sub | null }
export interface NodePub { code: string; name: string; flag: string; online: boolean | null }
export interface DeviceMutation { confirmed: boolean; error: string | null; device: Device }
export interface LinkResp { live: boolean; subscription_url: string | null; link: string | null; device: Device }
export interface PlansResp { plans: Plan[]; payments_available: boolean; payment_methods: string[] }
export interface ReferralsResp {
  link: string | null; code: string; summary: RefSummary;
  invites: { name: string; registered_at: string; trial: boolean; paid: boolean; bonus_days: number }[];
  rules: { enabled: boolean; trial_bonus_days: number; payment_bonus_days: number; trial_days: number };
}
export interface Bonus { id: number; days: number; kind: string; label: string; comment: string | null; created_at: string }
export interface BonusHistory { balance: number; items: Bonus[] }

// admin
export interface AdminNode {
  id: number; code: string; name: string; country_code: string; flag: string; is_active: boolean;
  health_ok: boolean | null; health_at: string | null; last_error: string | null;
  remote_clients: number | null; active_devices: number;
}
export interface VpnOp {
  id: number; device_id: number | null; node_id: number | null; op_type: string; status: string; attempts: number;
  last_error: string | null; next_retry_at: string | null; actor: string | null; created_at: string; updated_at: string;
}
export interface Audit {
  id: number; admin_id: number | null; source: string; action: string; entity_type: string; entity_id: string | null;
  reason: string | null; old_value: unknown; new_value: unknown; created_at: string;
}
export interface AdminStats {
  users_total: number; users_new_24h: number; users_new_7d: number; subs_active: number; subs_expired: number;
  subs_suspended: number; subs_paid_active: number; subs_lifetime: number; revenue_minor: number; orders_paid: number;
  trials_total: number; referrals_total: number; referrals_with_trial: number; referrals_with_payment: number;
  bonus_days_granted: number; devices_active: number; vpn_ops_pending: number; vpn_ops_failed_24h: number;
  support_new?: number; support_open?: number; users_banned?: number;
}
export interface AdminDashboard { stats: AdminStats; nodes: AdminNode[]; recent_problems: VpnOp[]; recent_audit: Audit[] }
export interface AdminUser {
  id: number; username: string | null; first_name: string | null; last_name: string | null; created_at: string;
  is_banned: boolean; public_id: string; role: AdminRole | null;
}
export interface AdminMe { admin_id: number; role: AdminRole; is_owner: boolean; permissions?: string[] }
export interface AdminTicket extends Ticket { user_id: number; user_public_id: string; user_name: string; assigned_admin_id: number | null }
export interface AdminSubHistory {
  id: number; action: string; action_label: string; old_status: string | null; new_status: string | null;
  old_expires_at: string | null; new_expires_at: string | null; days: number | null; actor: string | null;
  comment: string | null; note: string | null; created_at: string;
}
export interface AdminTicketMessage {
  id: number; author_type: "user" | "admin" | "system"; author_id: number | null; text: string;
  is_internal: boolean; has_attachment: boolean; created_at: string;
}
export interface AdminThread { ticket: AdminTicket; messages: AdminTicketMessage[]; admins: { user_id: number; name: string }[] }
export interface AdminEntry {
  user_id: number; role: AdminRole; source: "env" | "db"; username: string | null; first_name: string | null;
  public_id: string | null; granted_by: number | null; granted_at: string | null;
}
export interface GrantResult {
  subscription: Sub | null; vpn: { synced: number; failed: number; revoked: number; provisioned: boolean | null; pending: number };
  message: string;
}
export interface AdminUserDetail {
  user: AdminUser; subscription_history: AdminSubHistory[]; support_tickets: AdminTicket[]; subscription: Sub | null; devices: Device[]; orders: Order[]; bonus: Bonus[];
  referrals: RefSummary; referrer_id: number | null; trial: { status: string; activated_at: string | null } | null;
  coins?: number;
}
export interface RewardRow {
  id: number; referrer_id: number; referral_id: number; event_type: string; days: number; status: string;
  created_at: string; reversed_at: string | null; reverse_reason: string | null;
}
export interface SettingSchema { key: string; label: string; type: "bool" | "int"; default: boolean | number; bounds: [number, number] | null }
export interface Integrations {
  bot: { configured: boolean; username: string | null }; webapp_url: string | null; public_base_url: string | null;
  payments: { provider: string | null; webhook_url: string | null; verify_ip: boolean };
  vpn_nodes: { code: string; name: string; api_url_configured: boolean; token_configured: boolean }[];
  app_env: string;
}

export type TaskStatus = "available" | "pending" | "rejected" | "rewarded" | "unavailable";
export interface TaskItem { id: number; title: string; description: string; url: string; reward: number; verify: "telegram" | "manual" | string; status: TaskStatus; note: string | null }
export interface TasksResp { tasks: TaskItem[] }
export interface ClaimResp { status: "rewarded" | "pending" | "rejected" | "not_subscribed"; message: string; balance: number }
export interface CoinTx { id: number; amount: number; balance_after: number; kind: string; kind_label: string; reason: string | null; order_id: number | null; created_at: string }
export interface CoinsResp { balance: number; enabled: boolean; history: CoinTx[] }
