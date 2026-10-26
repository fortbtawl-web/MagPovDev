import { useState, type ReactNode } from "react";
import { api, type Device, type Order } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Icon, Row, SectionTitle, Sheet, SkeletonCards, Toggle, useAction, useApp, useLoad } from "../ui";
import { fmtDate, fmtShort } from "../format";

type Section = { id: string; label: string; perm: string; icon: string };
const SECTIONS: Section[] = [
  { id: "overview", label: "Обзор", perm: "stats.view", icon: "bolt" },
  { id: "users", label: "Пользователи", perm: "users.view", icon: "user" },
  { id: "plans", label: "Тарифы", perm: "plans.manage", icon: "card" },
  { id: "promos", label: "Промокоды", perm: "promos.manage", icon: "ticket" },
  { id: "refs", label: "Рефералы", perm: "referrals.manage", icon: "users" },
  { id: "nodes", label: "Серверы", perm: "nodes.view", icon: "server" },
  { id: "tickets", label: "Обращения", perm: "tickets", icon: "chat" },
  { id: "audit", label: "Аудит", perm: "audit.view", icon: "doc" },
  { id: "staff", label: "Персонал", perm: "staff.manage", icon: "admin" },
  { id: "settings", label: "Настройки", perm: "settings.manage", icon: "wrench" },
];

const SUB: Record<string, string> = { active: "активна", expired: "истекла", suspended: "отключена" };

export default function Admin() {
  const { me } = useApp();
  const perms = new Set(me?.staff?.perms ?? []);
  const available = SECTIONS.filter((s) => perms.has(s.perm));
  const [section, setSection] = useState(available[0]?.id ?? "");
  if (!me?.staff) return <Empty icon="admin" title="Нет доступа" />;
  return (
    <div className="stack">
      <div className="admin-head">
        <h2>Админ-панель</h2>
        <Badge tone="accent">{me.staff.title}</Badge>
      </div>
      <div className="chips chips-scroll">
        {available.map((s) => (
          <button key={s.id} className={`chip${section === s.id ? " chip-active" : ""}`} onClick={() => setSection(s.id)}>
            <Icon name={s.icon} size={14} /> {s.label}
          </button>
        ))}
      </div>
      <div key={section} className="fade-in">
        {section === "overview" && <Overview />}
        {section === "users" && <Users perms={perms} />}
        {section === "plans" && <Plans />}
        {section === "promos" && <Promos />}
        {section === "refs" && <Refs />}
        {section === "nodes" && <Nodes canCheck={perms.has("nodes.manage")} />}
        {section === "tickets" && <TicketsView />}
        {section === "audit" && <Audit />}
        {section === "staff" && <Staff />}
        {section === "settings" && <SettingsView />}
      </div>
    </div>
  );
}

function Loader<T>({ state, children }: { state: { data: T | null; error: string | null; loading: boolean; reload: () => Promise<void> }; children: (d: T) => ReactNode }) {
  if (state.loading && !state.data) return <SkeletonCards n={2} />;
  if (state.error || !state.data) return <ErrorBox text={state.error ?? "Нет данных"} onRetry={() => void state.reload()} />;
  return <>{children(state.data)}</>;
}

function Field(props: { label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string }) {
  return (
    <label className="field-label">
      <span>{props.label}</span>
      <input className="field" type={props.type ?? "text"} value={props.value} placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)} />
    </label>
  );
}

// ---------- overview ----------

type Stats = {
  users_total: number; users_day: number; users_month: number; active_subs: number; expired_subs: number; active_trials: number;
  trials_used: number; paid_orders: number; new_purchases_30d: number; renewals_30d: number; active_promos: number;
  devices_live: number; nodes_total: number; nodes_online: number; vpn_errors_24h: number; vpn_provider: string; vpn_ready: boolean;
  vpn_test: boolean; revenue_text: Record<string, string>;
  referrals: { invites: number; trials: number; paid: number; days_granted: number };
};

function Overview() {
  const st = useLoad(() => api<Stats>("/admin/stats"));
  return (
    <Loader state={st}>
      {(s) => (
        <div className="stack">
          <div className="grid3">
            <Card><span className="stat">{s.users_total}</span><span className="muted tiny">пользователей</span></Card>
            <Card><span className="stat">{s.active_subs}</span><span className="muted tiny">активных подписок</span></Card>
            <Card><span className="stat">{s.devices_live}</span><span className="muted tiny">устройств</span></Card>
          </div>
          <Card>
            <SectionTitle>Выручка (подтверждённая)</SectionTitle>
            <Row label="Сегодня" value={s.revenue_text.day} />
            <Row label="7 дней" value={s.revenue_text.week} />
            <Row label="30 дней" value={s.revenue_text.month} />
            <Row label="Новые покупки / продления (30 дн.)" value={`${s.new_purchases_30d} / ${s.renewals_30d}`} />
            <Row label="Оплаченных заказов всего" value={s.paid_orders} />
          </Card>
          <Card>
            <Row label="Новые пользователи (24 ч / 30 дн.)" value={`+${s.users_day} / +${s.users_month}`} />
            <Row label="Истёкших подписок" value={s.expired_subs} />
            <Row label="Активных пробных / всего выдано" value={`${s.active_trials} / ${s.trials_used}`} />
            <Row label="Действующих промокодов" value={s.active_promos} />
            <Row label="Рефералы: приглашено / пробный / оплата" value={`${s.referrals.invites} / ${s.referrals.trials} / ${s.referrals.paid}`} />
            <Row label="Бонусных дней начислено" value={s.referrals.days_granted} />
          </Card>
          <Card>
            <Row label="VPN-провайдер" value={<>{s.vpn_provider} {s.vpn_test ? <Badge tone="warn">mock</Badge> : !s.vpn_ready ? <Badge tone="muted">не подключён</Badge> : null}</>} />
            <Row label="Ноды онлайн" value={`${s.nodes_online} из ${s.nodes_total}`} />
            <Row label="Ошибки VPN-панели за 24 ч" value={<Badge tone={s.vpn_errors_24h ? "bad" : "ok"}>{s.vpn_errors_24h}</Badge>} />
          </Card>
        </div>
      )}
    </Loader>
  );
}

// ---------- users ----------

type UserCard = {
  user: { telegram_id: number; username: string | null; first_name: string | null; group: string | null; blocked: boolean; created_at: string; trial_used: boolean };
  subscription: { status: string; plan: string | null; lifetime: boolean; expires_at: string | null; device_limit: number; vpn_sync_needed: boolean } | null;
  devices: Device[];
  orders: Order[];
  promos: { code: string; kind: string; value: number; status: string }[];
  bonuses: { source: string; days: number; description: string; created_at: string }[];
  referral: { invited: number; bonus_days: number; pending: number };
};

function Users({ perms }: { perms: Set<string> }) {
  const { toast, fail, confirm } = useApp();
  const [query, setQuery] = useState("");
  const [card, setCard] = useState<UserCard | null>(null);
  const [form, setForm] = useState<{ kind: "extend" | "test" | "block"; days: string; reason: string } | null>(null);
  const action = useAction();

  const load = (tg = query.trim()) =>
    action.run(async () => {
      try {
        setCard(await api<UserCard>(`/admin/users/${tg}`));
      } catch (e) {
        setCard(null);
        fail(e);
      }
    });

  const submit = () =>
    action.run(async () => {
      if (!card || !form) return;
      const tg = card.user.telegram_id;
      try {
        if (form.kind === "block") {
          await api(`/admin/users/${tg}/block`, { method: "POST", body: { blocked: !card.user.blocked, reason: form.reason } });
        } else {
          await api(`/admin/users/${tg}/extend`, { method: "POST", body: { days: Number(form.days), reason: form.reason, test_access: form.kind === "test" } });
        }
        toast("Готово, действие записано в аудит", "ok");
        setForm(null);
        setCard(await api<UserCard>(`/admin/users/${tg}`));
      } catch (e) {
        fail(e);
      }
    });

  const revoke = async (d: Device) => {
    const reason = window.prompt(`Причина отзыва «${d.name}»`);
    if (!reason) return;
    try {
      await api(`/admin/devices/${d.id}/revoke`, { method: "POST", body: { reason } });
      toast("Устройство отозвано", "ok");
      void load(String(card?.user.telegram_id));
    } catch (e) {
      fail(e);
    }
  };

  const refund = async (o: Order) => {
    if (!(await confirm({ title: "Возврат платежа", text: `Заказ ${o.id.slice(0, 8)} · ${o.amount_text}. Для Telegram Stars возврат выполнит Telegram; для других провайдеров сначала верни деньги в кабинете провайдера.`, ok: "Оформить возврат", danger: true }))) return;
    const reason = window.prompt("Причина возврата");
    if (!reason) return;
    try {
      await api(`/admin/orders/${o.id}/refund`, { method: "POST", body: { reason } });
      toast("Возврат оформлен", "ok");
      void load(String(card?.user.telegram_id));
    } catch (e) {
      fail(e);
    }
  };

  return (
    <div className="stack">
      <div className="inline-form">
        <input inputMode="numeric" placeholder="Telegram ID" value={query} onChange={(e) => setQuery(e.target.value.replace(/\D/g, ""))} aria-label="Telegram ID" />
        <Button busy={action.busy} disabled={!query} onClick={() => void load()}>Найти</Button>
      </div>
      {card && (
        <>
          <Card glow>
            <div className="card-head"><b>{card.user.first_name ?? "—"} {card.user.username ? `@${card.user.username}` : ""}</b>{card.user.blocked && <Badge tone="bad">заблокирован</Badge>}</div>
            <Row label="Telegram ID" value={card.user.telegram_id} mono />
            <Row label="Регистрация" value={fmtDate(card.user.created_at)} />
            <Row label="Подписка" value={card.subscription ? `${SUB[card.subscription.status] ?? card.subscription.status} · ${card.subscription.plan ?? "—"}` : "нет"} />
            <Row label="Окончание" value={card.subscription ? (card.subscription.lifetime ? "Бессрочная" : fmtDate(card.subscription.expires_at)) : "—"} />
            <Row label="Устройства" value={card.subscription ? `${card.devices.length} из ${card.subscription.device_limit}` : card.devices.length} />
            <Row label="Пробный" value={card.user.trial_used ? "использован" : "нет"} />
            <Row label="Рефералы" value={`${card.referral.invited} · +${card.referral.bonus_days} дн.`} />
            {perms.has("users.manage") && (
              <div className="actions wrap">
                <Button kind="soft" icon="plus" onClick={() => setForm({ kind: "extend", days: "30", reason: "" })}>Продлить</Button>
                <Button kind="soft" icon="gift" onClick={() => setForm({ kind: "test", days: "3", reason: "" })}>Тестовый доступ</Button>
                <Button kind={card.user.blocked ? "soft" : "danger"} icon="admin" onClick={() => setForm({ kind: "block", days: "0", reason: "" })}>
                  {card.user.blocked ? "Разблокировать" : "Заблокировать"}
                </Button>
              </div>
            )}
          </Card>
          <SectionTitle>Устройства</SectionTitle>
          {card.devices.length === 0 ? <p className="muted small">Нет устройств</p> : card.devices.map((d) => (
            <Card key={d.id}><div className="card-head"><b>{d.name}</b><span className="muted tiny">{d.status} · клиент {d.client_id ?? "—"}</span>
              {perms.has("devices.manage") && <button className="icon-btn icon-danger" aria-label="Отозвать" onClick={() => void revoke(d)}><Icon name="trash" /></button>}</div></Card>
          ))}
          <SectionTitle>Платежи</SectionTitle>
          {card.orders.length === 0 ? <p className="muted small">Нет платежей</p> : (
            <div className="list">{card.orders.map((o) => (
              <div className="list-item" key={o.id}>
                <div className="grow"><b>{o.plan}{o.is_test ? " · ТЕСТ" : ""}</b><span className="muted tiny">{fmtShort(o.created_at)} · {o.amount_text} · {o.provider}</span></div>
                <Badge tone={o.status === "paid" ? "ok" : "muted"}>{o.status}</Badge>
                {o.status === "paid" && perms.has("payments.manage") && <button className="icon-btn" aria-label="Возврат" onClick={() => void refund(o)}><Icon name="refresh" /></button>}
              </div>))}
            </div>
          )}
          <SectionTitle>Бонусы и промокоды</SectionTitle>
          <div className="list">
            {card.bonuses.map((b, i) => <div className="list-item" key={`b${i}`}><div className="grow"><b>{b.description || b.source}</b><span className="muted tiny">{fmtShort(b.created_at)}</span></div><Badge tone="ok">{b.days > 0 ? "+" : ""}{b.days} дн.</Badge></div>)}
            {card.promos.map((p, i) => <div className="list-item" key={`p${i}`}><div className="grow"><b>{p.code}</b><span className="muted tiny">{p.kind} {p.value}</span></div><Badge tone="muted">{p.status}</Badge></div>)}
            {!card.bonuses.length && !card.promos.length && <p className="muted small">Нет</p>}
          </div>
        </>
      )}
      <Sheet open={!!form} onClose={() => setForm(null)} title={form?.kind === "block" ? (card?.user.blocked ? "Разблокировать" : "Заблокировать") : form?.kind === "test" ? "Тестовый доступ" : "Продлить подписку"}>
        {form && (
          <>
            {form.kind !== "block" && <Field label="Дней" type="number" value={form.days} onChange={(v) => setForm({ ...form, days: v })} />}
            <Field label="Причина (обязательно, попадёт в аудит)" value={form.reason} onChange={(v) => setForm({ ...form, reason: v })} />
            {form.kind === "block" && !card?.user.blocked && <p className="small muted">Блокировка также временно отключит VPN-доступ пользователя.</p>}
            <Button block busy={action.busy} disabled={!form.reason.trim()} onClick={() => void submit()}>Подтвердить</Button>
          </>
        )}
      </Sheet>
    </div>
  );
}

// ---------- plans ----------

type AdminPlan = { code: string; title: string; description: string; days: number; devices: number; lifetime: boolean; price: number; currency: string; price_text: string; active: boolean; sort: number };

const toMinor = (v: string, cur: string) => Math.round(Number(v.replace(",", ".")) * (cur === "XTR" ? 1 : 100));
const fromMinor = (v: number, cur: string) => String(cur === "XTR" ? v : v / 100);

function Plans() {
  const { toast, fail } = useApp();
  const plans = useLoad(() => api<{ plans: AdminPlan[] }>("/admin/plans"));
  const [edit, setEdit] = useState<(AdminPlan & { priceInput: string; isNew?: boolean }) | null>(null);
  const action = useAction();

  const save = () =>
    action.run(async () => {
      if (!edit) return;
      try {
        const price = toMinor(edit.priceInput, edit.currency);
        if (edit.isNew) {
          await api("/admin/plans", { method: "POST", body: { code: edit.code, title: edit.title, days: edit.days, devices: edit.devices, price, lifetime: edit.lifetime } });
        } else {
          await api(`/admin/plans/${edit.code}`, { method: "PATCH", body: { title: edit.title, description: edit.description, days: edit.days, devices: edit.devices, price, active: edit.active, sort: edit.sort } });
        }
        toast("Тариф сохранён. Оплаченные заказы не меняются.", "ok");
        setEdit(null);
        void plans.reload();
      } catch (e) {
        fail(e);
      }
    });

  return (
    <Loader state={plans}>
      {(d) => (
        <div className="stack">
          <Button kind="soft" icon="plus" onClick={() => setEdit({ code: "", title: "", description: "", days: 30, devices: 1, lifetime: false, price: 0, currency: d.plans[0]?.currency ?? "RUB", price_text: "", active: false, sort: 100, priceInput: "", isNew: true })}>Новый тариф</Button>
          {d.plans.map((p) => (
            <Card key={p.code} onClick={() => setEdit({ ...p, priceInput: fromMinor(p.price, p.currency) })}>
              <div className="card-head"><b>{p.title}</b><Badge tone={p.active ? "ok" : "muted"}>{p.active ? "вкл" : "выкл"}</Badge></div>
              <span className="muted small">{p.code} · {p.lifetime ? "Lifetime" : `${p.days} дн.`} · {p.devices} устр. · {p.price_text}</span>
            </Card>
          ))}
          <Sheet open={!!edit} onClose={() => setEdit(null)} title={edit?.isNew ? "Новый тариф" : `Тариф ${edit?.code}`}>
            {edit && (
              <>
                {edit.isNew && <Field label="Код (латиница)" value={edit.code} onChange={(v) => setEdit({ ...edit, code: v })} />}
                <Field label="Название" value={edit.title} onChange={(v) => setEdit({ ...edit, title: v })} />
                {!edit.isNew && <Field label="Описание" value={edit.description} onChange={(v) => setEdit({ ...edit, description: v })} />}
                <Field label={`Цена, ${edit.currency === "XTR" ? "⭐" : "₽"}`} type="number" value={edit.priceInput} onChange={(v) => setEdit({ ...edit, priceInput: v })} />
                {!edit.lifetime && <Field label="Срок, дней" type="number" value={String(edit.days)} onChange={(v) => setEdit({ ...edit, days: Number(v) })} />}
                <Field label="Устройств" type="number" value={String(edit.devices)} onChange={(v) => setEdit({ ...edit, devices: Number(v) })} />
                {edit.isNew ? <Toggle label="Lifetime" checked={edit.lifetime} onChange={(v) => setEdit({ ...edit, lifetime: v })} />
                  : <Toggle label="Доступен для покупки" checked={edit.active} onChange={(v) => setEdit({ ...edit, active: v })} />}
                <p className="tiny muted">Изменения действуют для новых заказов; уже оплаченные условия сохраняются.</p>
                <Button block busy={action.busy} onClick={() => void save()}>Сохранить</Button>
              </>
            )}
          </Sheet>
        </div>
      )}
    </Loader>
  );
}

// ---------- promos ----------

type Promo = { code: string; kind: string; value: number; description: string; active: boolean; archived: boolean; valid_until: string | null; max_activations: number | null; activations: number; group: string | null; plans: string | null; new_users_only: boolean };

function Promos() {
  const { toast, fail } = useApp();
  const promos = useLoad(() => api<{ promos: Promo[] }>("/admin/promos"));
  const [form, setForm] = useState<{ code: string; kind: string; value: string; max: string; until: string; plans: string; group: string; description: string } | null>(null);
  const action = useAction();

  const create = () =>
    action.run(async () => {
      if (!form) return;
      try {
        await api("/admin/promos", { method: "POST", body: { code: form.code, kind: form.kind, value: Number(form.value), max_activations: form.max || null, valid_until: form.until || null, plans: form.plans || null, group: form.group || null, description: form.description } });
        toast("Промокод создан", "ok");
        setForm(null);
        void promos.reload();
      } catch (e) {
        fail(e);
      }
    });

  const patch = (code: string, body: object, path = "") =>
    action.run(async () => {
      try {
        await api(`/admin/promos/${code}${path}`, { method: path ? "POST" : "PATCH", body });
        void promos.reload();
      } catch (e) {
        fail(e);
      }
    });

  const KIND: Record<string, string> = { days: "дни", percent: "%", fixed: "скидка" };
  return (
    <Loader state={promos}>
      {(d) => (
        <div className="stack">
          <Button kind="soft" icon="plus" onClick={() => setForm({ code: "", kind: "days", value: "7", max: "", until: "", plans: "", group: "", description: "" })}>Новый промокод</Button>
          {d.promos.length === 0 && <Empty icon="ticket" title="Промокодов нет" />}
          {d.promos.map((p) => (
            <Card key={p.code}>
              <div className="card-head"><b className="mono">{p.code}</b><Badge tone={p.active ? "ok" : "muted"}>{p.active ? "активен" : "выкл"}</Badge></div>
              <span className="muted small">{p.value} {KIND[p.kind]} · {p.activations}/{p.max_activations ?? "∞"} активаций{p.valid_until ? ` · до ${fmtShort(p.valid_until)}` : ""}{p.plans ? ` · тарифы: ${p.plans}` : ""}{p.group ? ` · группа ${p.group}` : ""}</span>
              <div className="actions">
                <Button kind="soft" sound={false} onClick={() => void patch(p.code, { active: !p.active })}>{p.active ? "Выключить" : "Включить"}</Button>
                <Button kind="ghost" sound={false} icon="trash" onClick={() => void patch(p.code, {}, "/archive")}>В архив</Button>
              </div>
            </Card>
          ))}
          <Sheet open={!!form} onClose={() => setForm(null)} title="Новый промокод">
            {form && (
              <>
                <Field label="Код" value={form.code} onChange={(v) => setForm({ ...form, code: v.toUpperCase() })} />
                <div className="chips">{(["days", "percent", "fixed"] as const).map((k) => (
                  <button key={k} className={`chip${form.kind === k ? " chip-active" : ""}`} onClick={() => setForm({ ...form, kind: k })}>{k === "days" ? "Дни подписки" : k === "percent" ? "Скидка %" : "Скидка, коп."}</button>))}
                </div>
                <Field label="Значение" type="number" value={form.value} onChange={(v) => setForm({ ...form, value: v })} />
                <Field label="Лимит активаций (пусто — без лимита)" type="number" value={form.max} onChange={(v) => setForm({ ...form, max: v })} />
                <Field label="Действует до (ГГГГ-ММ-ДД)" value={form.until} onChange={(v) => setForm({ ...form, until: v })} placeholder="2026-12-31" />
                <Field label="Тарифы (коды через запятую, для скидок)" value={form.plans} onChange={(v) => setForm({ ...form, plans: v })} placeholder="y1,life" />
                <Field label="Группа пользователей" value={form.group} onChange={(v) => setForm({ ...form, group: v })} />
                <Field label="Описание" value={form.description} onChange={(v) => setForm({ ...form, description: v })} />
                <Button block busy={action.busy} disabled={form.code.length < 3} onClick={() => void create()}>Создать</Button>
              </>
            )}
          </Sheet>
        </div>
      )}
    </Loader>
  );
}

// ---------- referrals ----------

type RefAdmin = { invites: number; trials: number; paid: number; days_granted: number; enabled: boolean; trial_days: number; payment_days: number;
  history: { user: number | null; invitee: number | null; source: string; days: number; description: string; created_at: string }[] };

function Refs() {
  const { toast, fail } = useApp();
  const data = useLoad(() => api<RefAdmin>("/admin/referrals"));
  const [corr, setCorr] = useState({ tg: "", days: "", reason: "" });
  const action = useAction();
  const update = (body: object) =>
    action.run(async () => {
      try {
        await api("/admin/referrals", { method: "PATCH", body });
        void data.reload();
      } catch (e) {
        fail(e);
      }
    });
  const correct = () =>
    action.run(async () => {
      try {
        await api("/admin/referrals/correction", { method: "POST", body: { telegram_id: corr.tg, days: Number(corr.days), reason: corr.reason } });
        toast("Корректировка проведена и записана в аудит", "ok");
        setCorr({ tg: "", days: "", reason: "" });
        void data.reload();
      } catch (e) {
        fail(e);
      }
    });
  return (
    <Loader state={data}>
      {(r) => (
        <div className="stack">
          <div className="grid3">
            <Card><span className="stat">{r.invites}</span><span className="muted tiny">приглашений</span></Card>
            <Card><span className="stat">{r.trials}</span><span className="muted tiny">пробных</span></Card>
            <Card><span className="stat">{r.paid}</span><span className="muted tiny">первых оплат</span></Card>
          </div>
          <Card>
            <Row label="Начислено бонусных дней" value={r.days_granted} />
            <Toggle label="Программа включена" checked={r.enabled} onChange={(v) => void update({ enabled: v })} />
            <div className="grid2">
              <Field label="Дней за пробный" type="number" value={String(r.trial_days)} onChange={(v) => data.setData({ ...r, trial_days: Number(v) })} />
              <Field label="Дней за оплату" type="number" value={String(r.payment_days)} onChange={(v) => data.setData({ ...r, payment_days: Number(v) })} />
            </div>
            <Button kind="soft" busy={action.busy} onClick={() => void update({ trial_days: r.trial_days, payment_days: r.payment_days })}>Сохранить размеры бонусов</Button>
          </Card>
          <Card>
            <SectionTitle>Ручная корректировка</SectionTitle>
            <Field label="Telegram ID" value={corr.tg} onChange={(v) => setCorr({ ...corr, tg: v.replace(/\D/g, "") })} />
            <Field label="Дней (можно отрицательное)" type="number" value={corr.days} onChange={(v) => setCorr({ ...corr, days: v })} />
            <Field label="Причина (обязательно)" value={corr.reason} onChange={(v) => setCorr({ ...corr, reason: v })} />
            <Button block busy={action.busy} disabled={!corr.tg || !corr.days || !corr.reason.trim()} onClick={() => void correct()}>Провести</Button>
          </Card>
          <SectionTitle>История начислений</SectionTitle>
          <div className="list">
            {r.history.length === 0 && <p className="muted small">Пусто</p>}
            {r.history.map((h, i) => (
              <div className="list-item" key={i}>
                <div className="grow"><b>{h.user}</b><span className="muted tiny">{h.description} {h.invitee ? `· друг ${h.invitee}` : ""} · {fmtShort(h.created_at)}</span></div>
                <Badge tone={h.days > 0 ? "ok" : "bad"}>{h.days > 0 ? "+" : ""}{h.days}</Badge>
              </div>
            ))}
          </div>
        </div>
      )}
    </Loader>
  );
}

// ---------- nodes ----------

type NodesData = { provider: string; configured: boolean; test_mode: boolean;
  nodes: { id: string; name: string; address: string; country: string; online: boolean; disabled: boolean; users_online: number; message: string | null; test: boolean; last_checked_at: string | null; last_seen_ok_at: string | null }[];
  errors: { operation: string; error: string; created_at: string }[] };

function Nodes({ canCheck }: { canCheck: boolean }) {
  const { toast, fail } = useApp();
  const data = useLoad(() => api<NodesData>("/admin/nodes"));
  const action = useAction();
  const check = () =>
    action.run(async () => {
      try {
        const r = await api<{ total: number; online: number }>("/admin/nodes/check", { method: "POST" });
        toast(`Проверено: ${r.online} из ${r.total} онлайн`, "ok");
        void data.reload();
      } catch (e) {
        fail(e);
      }
    });
  return (
    <Loader state={data}>
      {(n) => (
        <div className="stack">
          <Card>
            <Row label="Провайдер" value={<>{n.provider} {n.test_mode && <Badge tone="warn">mock</Badge>}</>} />
            {!n.configured && <p className="small muted">VPN-панель не подключена. Укажи REMNAWAVE_URL и REMNAWAVE_TOKEN в .env на сервере — токены в интерфейсе не отображаются.</p>}
            {canCheck && <Button kind="soft" icon="refresh" busy={action.busy} onClick={() => void check()}>Проверить сейчас</Button>}
          </Card>
          {n.nodes.length === 0 ? <Empty icon="server" title="Нод нет" text="Ноды появятся после подключения панели и первой проверки." /> : n.nodes.map((node) => (
            <Card key={node.id}>
              <div className="card-head"><b>{node.country ? `${node.country} · ` : ""}{node.name}</b><Badge tone={node.online ? "ok" : "bad"}>{node.online ? "онлайн" : node.disabled ? "выключена" : "недоступна"}</Badge></div>
              <span className="muted small">{node.address} · онлайн {node.users_online} · проверка {fmtShort(node.last_checked_at)}{node.test ? " · тест" : ""}</span>
              {node.message && <p className="tiny muted">{node.message}</p>}
            </Card>
          ))}
          <SectionTitle>Ошибки API панели</SectionTitle>
          <div className="list">
            {n.errors.length === 0 && <p className="muted small">Ошибок нет</p>}
            {n.errors.map((e, i) => <div className="list-item" key={i}><div className="grow"><b>{e.operation}</b><span className="muted tiny">{e.error} · {fmtDate(e.created_at)}</span></div></div>)}
          </div>
        </div>
      )}
    </Loader>
  );
}

// ---------- tickets ----------

type TicketList = { tickets: { id: number; category: string; status: string; created_at: string; telegram_id: number; username: string | null }[] };
type TicketFull = { id: number; status: string; category: string; messages: { from_admin: boolean; text: string; created_at: string }[] };

function TicketsView() {
  const { toast, fail } = useApp();
  const list = useLoad(() => api<TicketList>("/admin/tickets"));
  const [open, setOpen] = useState<TicketFull | null>(null);
  const [reply, setReply] = useState("");
  const action = useAction();
  const show = async (id: number) => {
    try { setOpen(await api<TicketFull>(`/admin/tickets/${id}`)); } catch (e) { fail(e); }
  };
  const send = (close = false) =>
    action.run(async () => {
      if (!open) return;
      try {
        if (reply.trim()) await api(`/admin/tickets/${open.id}/reply`, { method: "POST", body: { text: reply } });
        if (close) await api(`/admin/tickets/${open.id}/close`, { method: "POST" });
        toast(close ? "Обращение закрыто" : "Ответ отправлен", "ok");
        setReply("");
        setOpen(null);
        void list.reload();
      } catch (e) {
        fail(e);
      }
    });
  return (
    <Loader state={list}>
      {(d) => (
        <div className="stack">
          {d.tickets.length === 0 ? <Empty icon="chat" title="Открытых обращений нет" /> : d.tickets.map((t) => (
            <Card key={t.id} onClick={() => void show(t.id)}>
              <div className="card-head"><b>#{t.id} · {t.category}</b><Badge tone={t.status === "open" ? "warn" : "ok"}>{t.status}</Badge></div>
              <span className="muted small">{t.telegram_id}{t.username ? ` @${t.username}` : ""} · {fmtDate(t.created_at)}</span>
            </Card>
          ))}
          <Sheet open={!!open} onClose={() => setOpen(null)} title={`Обращение #${open?.id ?? ""}`}>
            {open?.messages.map((m, i) => <p key={i} className={`msg${m.from_admin ? " msg-admin" : ""}`}>{m.text}</p>)}
            <textarea className="field" rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Ответ пользователю" aria-label="Ответ" />
            <div className="actions">
              <Button busy={action.busy} disabled={!reply.trim()} onClick={() => void send(false)}>Ответить</Button>
              <Button kind="ghost" sound={false} busy={action.busy} onClick={() => void send(true)}>Закрыть</Button>
            </div>
          </Sheet>
        </div>
      )}
    </Loader>
  );
}

// ---------- audit / staff / settings ----------

function Audit() {
  const data = useLoad(() => api<{ entries: { admin: number; action: string; target: string | null; details: string | null; created_at: string }[] }>("/admin/audit"));
  return (
    <Loader state={data}>
      {(d) => (
        <div className="list">
          {d.entries.length === 0 && <p className="muted small">Пусто</p>}
          {d.entries.map((e, i) => (
            <div className="list-item" key={i}>
              <div className="grow"><b>{e.action}</b><span className="muted tiny">{e.admin} → {e.target ?? "—"} · {fmtDate(e.created_at)}</span>{e.details && <span className="tiny mono muted clip">{e.details}</span>}</div>
            </div>
          ))}
        </div>
      )}
    </Loader>
  );
}

function Staff() {
  const { toast, fail } = useApp();
  const data = useLoad(() => api<{ staff: { telegram_id: number; role: string; title: string; source: string }[]; roles: Record<string, string> }>("/admin/staff"));
  const [tg, setTg] = useState("");
  const [role, setRole] = useState("support");
  const action = useAction();
  const set = (id: string, r: string | null) =>
    action.run(async () => {
      try {
        await api(`/admin/staff/${id}`, { method: "PUT", body: { role: r } });
        toast(r ? "Роль назначена" : "Доступ снят", "ok");
        setTg("");
        void data.reload();
      } catch (e) {
        fail(e);
      }
    });
  return (
    <Loader state={data}>
      {(d) => (
        <div className="stack">
          <div className="list">
            {d.staff.map((s) => (
              <div className="list-item" key={`${s.telegram_id}${s.source}`}>
                <div className="grow"><b className="mono">{s.telegram_id}</b><span className="muted tiny">{s.title}{s.source === "env" ? " · из .env" : ""}</span></div>
                {s.source === "panel" && <button className="icon-btn icon-danger" aria-label="Снять доступ" onClick={() => void set(String(s.telegram_id), null)}><Icon name="trash" /></button>}
              </div>
            ))}
          </div>
          <Card>
            <Field label="Telegram ID" value={tg} onChange={(v) => setTg(v.replace(/\D/g, ""))} />
            <div className="chips">{Object.entries(d.roles).map(([k, v]) => <button key={k} className={`chip${role === k ? " chip-active" : ""}`} onClick={() => setRole(k)}>{v}</button>)}</div>
            <p className="tiny muted">Поддержка — обращения и просмотр пользователей. Технический специалист — серверы и устройства. Администратор — всё, кроме персонала, возвратов и управления инфраструктурой.</p>
            <Button block busy={action.busy} disabled={!tg} onClick={() => void set(tg, role)}>Назначить</Button>
          </Card>
        </div>
      )}
    </Loader>
  );
}

const SETTING_LABELS: Record<string, string> = {
  welcome_text: "Приветствие в боте (HTML)", trial_enabled: "Пробный период", trial_days: "Дней пробного периода",
  notifications_enabled: "Уведомления об окончании", maintenance: "Режим обслуживания", maintenance_text: "Текст режима обслуживания",
  support_extra_url: "Доп. ссылка поддержки (https://)",
};

function SettingsView() {
  const { toast, fail } = useApp();
  const data = useLoad(() => api<{ settings: Record<string, string> }>("/admin/settings"));
  const action = useAction();
  const save = (key: string, value: string | boolean) =>
    action.run(async () => {
      try {
        data.setData(await api<{ settings: Record<string, string> }>("/admin/settings", { method: "PATCH", body: { [key]: value } }));
        toast("Сохранено", "ok");
      } catch (e) {
        fail(e);
      }
    });
  const BOOL = new Set(["trial_enabled", "notifications_enabled", "maintenance"]);
  return (
    <Loader state={data}>
      {(d) => (
        <div className="stack">
          {Object.entries(d.settings).map(([k, v]) => BOOL.has(k) ? (
            <Card key={k}><Toggle label={SETTING_LABELS[k] ?? k} checked={v === "1"} onChange={(val) => void save(k, val)} /></Card>
          ) : (
            <Card key={k}>
              <label className="field-label"><span>{SETTING_LABELS[k] ?? k}</span>
                {k.endsWith("_text") ? <textarea className="field" rows={4} defaultValue={v} onBlur={(e) => e.target.value !== v && void save(k, e.target.value)} />
                  : <input className="field" defaultValue={v} onBlur={(e) => e.target.value !== v && void save(k, e.target.value)} />}
              </label>
            </Card>
          ))}
          <p className="tiny muted">Ключи платёжных систем и VPN-панели задаются только в переменных окружения сервера и здесь не отображаются.</p>
        </div>
      )}
    </Loader>
  );
}
