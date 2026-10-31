import { Icon } from "../Icon";
import { useState } from "react";
import { useAdmin } from "../ctx";
import { copyText } from "../../shared/format";
import { GrantModal } from "./GrantModal";
import { call } from "../api";
import { Badge, Load, Panel, Stat, Table, tone, useData, useMutate } from "../components";
import { fmtDate, fmtDateTime, toLocalInput } from "../../shared/format";
import type { AdminNode, AdminUser, AdminUserDetail, Plan } from "../../shared/types";
import { OrdersTable } from "./parts";
import { CoinTable, type CoinRow } from "./Coins";

const coinHistory = (d: AdminUserDetail) => ((d as unknown as { coin_history?: CoinRow[] }).coin_history ?? []);

export function Users({ param }: { param: string }) {
  return param ? <Detail id={param} /> : <Search />;
}

function Search() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const res = useData<{ users: AdminUser[] }>(`/api/admin/users?q=${encodeURIComponent(applied)}`);
  return (
    <Panel title="Пользователи">
      <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
        <input placeholder="Telegram ID, @username или SV-ID" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn primary">Найти</button>
      </form>
      <Load res={res}>
        {(d) => (
          <Table head={["SV-ID", "Telegram ID", "Имя", "Username", "Регистрация", "Статус"]} empty={d.users.length === 0}>
            {d.users.map((u) => (
              <tr key={u.id} className="click" onClick={() => { window.location.hash = `#/users/${u.id}`; }}>
                <td className="idtag">{u.public_id}</td><td>{u.id}</td><td>{[u.first_name, u.last_name].filter(Boolean).join(" ")}</td>
                <td>{u.username ? `@${u.username}` : "—"}</td><td>{fmtDateTime(u.created_at)}</td>
                <td>{u.is_banned ? <Badge tone="err">заблокирован</Badge> : <Badge tone="ok">ок</Badge>}{u.role && <> <Badge tone="neutral">{u.role === "owner" ? "владелец" : "админ"}</Badge></>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}

function Detail({ id }: { id: string }) {
  const res = useData<AdminUserDetail>(`/api/admin/users/${id}`);
  const nodes = useData<{ nodes: AdminNode[] }>("/api/admin/nodes");
  const plans = useData<{ plans: Plan[] }>("/api/admin/plans");
  const { notify, me } = useAdmin();
  const [grant, setGrant] = useState(false);
  const mutate = useMutate();
  const base = `/api/admin/users/${id}`;
  const reload = async () => { await res.reload(); };

  return (
    <>
      <p><a href="#/users">← К списку</a></p>
      <Load res={res}>
        {(d) => {
          const sub = d.subscription;
          return (
            <>
              {grant && <GrantModal telegramId={d.user.id} sub={sub} plans={plans.data?.plans ?? []} onClose={(changed) => { setGrant(false); if (changed) void reload(); }} />}
              <Panel title={`${[d.user.first_name, d.user.last_name].filter(Boolean).join(" ") || "Пользователь"} · ${d.user.id}`} actions={
                d.user.is_banned ? (
                  <button className="btn primary" onClick={() => void mutate("Разблокировать пользователя", [], (v) => call("POST", `${base}/unblock`, { reason: v.reason }), reload)}>Разблокировать</button>
                ) : (
                  <button className="btn danger" onClick={() => void mutate("Заблокировать пользователя", [], (v) => call("POST", `${base}/block`, { reason: v.reason }), reload)}>Заблокировать</button>
                )
              }>
                <div className="kvs">
                  <div><span>SpirittVPN ID</span><b className="idtag">{d.user.public_id}{" "}
                    <button className="btn" onClick={() => void copyText(d.user.public_id).then((ok) => notify(ok ? "ID скопирован" : "Не удалось скопировать", ok ? "ok" : "err"))} aria-label="Копировать ID" title="Копировать ID"><Icon name="copy" size={16} /></button></b></div>
                  <div><span>Статус</span><b>{d.user.is_banned ? <Badge tone="err">заблокирован</Badge> : <Badge tone="ok">активен</Badge>}{d.user.role && <> <Badge>{d.user.role === "owner" ? "владелец" : "админ"}</Badge></>}</b></div>
                  <div><span>Username</span><b>{d.user.username ? `@${d.user.username}` : "—"}</b></div>
                  <div><span>Регистрация</span><b>{fmtDateTime(d.user.created_at)}</b></div>
                  <div><span>Пригласил</span><b>{d.referrer_id ? <a href={`#/users/${d.referrer_id}`}>{d.referrer_id}</a> : "—"}</b></div>
                  <div><span>Пробный период</span><b>{d.trial ? `${d.trial.status} (${fmtDateTime(d.trial.activated_at)})` : "не использован"}</b></div>
                </div>
              </Panel>

              <Panel title="Подписка" actions={
                <>
                  <button className="btn primary" onClick={() => setGrant(true)}><Icon name="gift" size={16} />Выдать / изменить подписку</button>
                  {sub && <>
                  <button className="btn" onClick={() => void mutate("Продлить / добавить дни", [
                    { name: "days", label: "Дней", type: "number", required: true, value: "30" },
                    { name: "device_limit", label: "Лимит устройств (необязательно)", type: "number" },
                  ], (v) => call("POST", `${base}/add-days`, { days: Math.trunc(Number(v.days)), ...(v.device_limit ? { device_limit: Math.trunc(Number(v.device_limit)) } : {}), reason: v.reason }), reload)}>＋ Добавить дни</button>
                  <button className="btn" onClick={() => void mutate("Установить дату окончания", [
                    { name: "expires_at", label: "Дата окончания (ваше локальное время)", type: "datetime-local", required: true, value: toLocalInput(sub.expires_at) },
                  ], (v) => call("POST", `${base}/set-expiry`, { expires_at: new Date(v.expires_at).toISOString(), reason: v.reason }), reload)}>Изменить срок</button>
                  <button className="btn" onClick={() => void mutate("Изменить лимит устройств", [
                    { name: "limit", label: "Лимит устройств", type: "number", required: true, value: String(sub.device_limit) },
                  ], (v) => call("POST", `${base}/device-limit`, { limit: Math.trunc(Number(v.limit)), reason: v.reason }), reload)}>Лимит устройств</button>
                  {sub.status === "suspended" ? (
                    <button className="btn primary" onClick={() => void mutate("Восстановить доступ", [], (v) => call<{ confirmed: number; failed: number }>("POST", `${base}/resume`, { reason: v.reason }), reload)}>Восстановить</button>
                  ) : (
                    <button className="btn danger" onClick={() => void mutate("Приостановить доступ", [], (v) => call<{ confirmed: number; failed: number }>("POST", `${base}/suspend`, { reason: v.reason }), reload)}>Приостановить</button>
                  )}
                  <button className="btn" onClick={() => void mutate("Пересинхронизировать подписку с VPN", [], (v) => call<{ ok: number; failed: number }>("POST", `/api/admin/subscriptions/${sub.id}/sync`, { reason: v.reason }), reload)}>Синхронизировать</button>
                  </>}
                </>
              }>
                {sub ? (
                  <div className="kvs">
                    <div><span>Статус</span><b><Badge tone={tone(sub.status)}>{sub.status_label}</Badge></b></div>
                    <div><span>Тариф</span><b>{sub.plan_code ?? "—"}</b></div>
                    <div><span>Окончание</span><b>{sub.is_lifetime ? "Бессрочно" : fmtDate(sub.expires_at)}</b></div>
                    <div><span>Устройства</span><b>{d.devices.filter((x) => x.status === "active").length} из {sub.device_limit}</b></div>
                    <div><span>Источник</span><b>{sub.source}</b></div>
                  </div>
                ) : <p className="muted">Подписки нет.</p>}
              </Panel>

              <Panel title="Устройства" actions={
                <button className="btn primary" onClick={() => void mutate("Создать устройство", [
                  { name: "node_code", label: "Сервер", type: "select", value: "", options: [{ value: "", label: "Любой" }, ...(nodes.data?.nodes ?? []).map((n) => ({ value: n.code, label: `${n.flag} ${n.name}` }))] },
                ], (v) => call("POST", `${base}/devices`, { ...(v.node_code ? { node_code: v.node_code } : {}), reason: v.reason }), reload)}><Icon name="plus" size={16} />Создать устройство</button>
              }>
                <Table head={["ID", "Название", "Сервер", "Протокол", "Статус", "Создано", "Ошибка", ""]} empty={d.devices.length === 0}>
                  {d.devices.map((x) => (
                    <tr key={x.id}>
                      <td>{x.id}</td><td>{x.label}</td><td>{x.node ? `${x.node.flag} ${x.node.name}` : "—"}</td><td>{x.protocol}</td>
                      <td><Badge tone={tone(x.status)}>{x.status_label}</Badge></td><td>{fmtDateTime(x.created_at)}</td>
                      <td className="wrap err">{x.last_error ?? ""}</td>
                      <td>{x.status !== "revoked" && (
                        <button className="btn danger" onClick={() => void mutate(`Отозвать устройство «${x.label}»`, [],
                          (v) => call<{ confirmed: boolean; error: string | null }>("POST", `/api/admin/devices/${x.id}/revoke`, { reason: v.reason }).then((r) => {
                            if (!r.confirmed) throw new Error(`Сервер не подтвердил отзыв: ${r.error ?? "ошибка"}. Операция сохранена для повтора.`);
                            return r;
                          }), reload)}>Отозвать</button>
                      )}</td>
                    </tr>
                  ))}
                </Table>
              </Panel>

              <Panel title="Рефералы">
                <div className="stats">
                  <Stat label="Приглашено" value={d.referrals.invited} /><Stat label="С пробным" value={d.referrals.with_trial} />
                  <Stat label="С оплатой" value={d.referrals.with_payment} /><Stat label="Получено дней" value={d.referrals.earned_days} />
                  <Stat label="Ожидают" value={d.referrals.pending} /><Stat label="Баланс, дн." value={d.referrals.balance_days} />
                </div>
              </Panel>

              <Panel title="SpirCoin" actions={
                (me?.is_owner || me?.permissions?.includes("coins.manage")) && (
                  <button className="btn" onClick={() => void mutate("Начислить / списать SpirCoin", [
                    { name: "amount", label: "Сумма (отрицательная — списание)", type: "number", required: true, value: "10" },
                  ], (v) => {
                    const n = Number(v.amount);
                    if (!Number.isInteger(n) || n === 0) throw new Error("Укажите целое число, не равное нулю");
                    return call("POST", `${base}/coins`, { amount: n, reason: v.reason });
                  }, reload, "Баланс обновлён")}>Начислить / списать SpirCoin</button>
                )
              }>
                <div className="kvs"><div><span>Баланс</span><b>{d.coins ?? 0} SpirCoin</b></div></div>
                <CoinTable items={coinHistory(d)} />
              </Panel>

              <Panel title="Бонусы" actions={
                <button className="btn" onClick={() => void mutate("Начислить / списать бонусные дни", [
                  { name: "days", label: "Дней (отрицательное — списание)", type: "number", required: true, value: "3" },
                ], (v) => call("POST", `${base}/bonus`, { days: Math.trunc(Number(v.days)), reason: v.reason }), reload)}>Ручная корректировка</button>
              }>
                <Table head={["ID", "Дни", "Тип", "Описание", "Комментарий", "Дата"]} empty={d.bonus.length === 0}>
                  {d.bonus.map((b) => <tr key={b.id}><td>{b.id}</td><td>{b.days}</td><td>{b.kind}</td><td>{b.label}</td><td className="wrap">{b.comment ?? ""}</td><td>{fmtDateTime(b.created_at)}</td></tr>)}
                </Table>
              </Panel>

              <Panel title="Платежи"><OrdersTable orders={d.orders} /></Panel>

              <Panel title="История подписки">
                <Table head={["Дата", "Действие", "Статус", "Окончание", "Дней", "Кто", "Причина", "Для пользователя"]} empty={d.subscription_history.length === 0}>
                  {d.subscription_history.map((h) => (
                    <tr key={h.id}>
                      <td>{fmtDateTime(h.created_at)}</td><td>{h.action_label}</td>
                      <td>{h.old_status ?? "—"} → {h.new_status ?? "—"}</td>
                      <td>{fmtDateTime(h.old_expires_at)} → {fmtDateTime(h.new_expires_at)}</td>
                      <td>{h.days ?? "—"}</td><td>{h.actor ?? ""}</td>
                      <td className="wrap">{h.comment ?? ""}</td><td className="wrap">{h.note ?? ""}</td>
                    </tr>
                  ))}
                </Table>
              </Panel>

              <Panel title="Обращения в поддержку">
                <Table head={["№", "Статус", "Категория", "Сообщение", "Обновлено"]} empty={d.support_tickets.length === 0}>
                  {d.support_tickets.map((t) => (
                    <tr key={t.id} className="click" onClick={() => { window.location.hash = `#/support/${t.id}`; }}>
                      <td><b>{t.number}</b></td><td><Badge>{t.status_label}</Badge></td><td>{t.category_label}</td>
                      <td className="wrap">{t.last_message_preview ?? t.subject}</td><td>{fmtDateTime(t.updated_at)}</td>
                    </tr>
                  ))}
                </Table>
              </Panel>
            </>
          );
        }}
      </Load>
    </>
  );
}
