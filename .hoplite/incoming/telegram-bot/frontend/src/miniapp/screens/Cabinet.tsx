import { Icon } from "../Icon";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, BannedBanner, Card, Skeleton, Spinner, Title, statusTone } from "../ui";
import { copyText, fmtDate, fmtDateTime, fmtRemaining } from "../../shared/format";
import { canOpenAdmin, openAdminPanel } from "../api";
import { haptic } from "../telegram";
import type { Order, ReferralsResp, SubHistoryItem } from "../../shared/types";
import { planName } from "./planName";

export function Cabinet() {
  const { me, toast, open, goTab } = useApp();
  const orders = useLoad<{ orders: Order[] }>("/api/orders");
  const hist = useLoad<{ items: SubHistoryItem[] }>("/api/subscription/history");
  const refs = useLoad<ReferralsResp>("/api/referrals");
  if (!me) return <Skeleton />;
  const u = me.user;
  const sub = me.subscription;
  const name = [u.first_name, u.last_name].filter(Boolean).join(" ") || "Без имени";
  const banned = me.account_status === "banned";

  async function copyId() {
    if (await copyText(u.public_id)) { haptic("success"); toast("ID скопирован"); } else toast("Не удалось скопировать", "err");
  }
  async function copyRef() {
    if (me?.referral_link && (await copyText(me.referral_link))) { haptic("success"); toast("Ссылка скопирована"); } else toast("Не удалось скопировать", "err");
  }

  return (
    <>
      <div className="row-between">
        <Title>Личный кабинет</Title>
        <button className="icon-btn" aria-label="Поддержка и настройки" onClick={() => open({ type: "settings" })}><Icon name="settings" size={20} /></button>
      </div>
      {banned && <BannedBanner />}

      <Card>
        <div className="hint small">SpirittVPN ID</div>
        <div className="idbox">
          <code>{u.public_id}</code>
          <button className="btn btn-secondary" onClick={() => void copyId()}><Icon name="copy" size={18} /><span>Копировать</span></button>
        </div>
        <p className="hint small">Сообщите этот ID поддержке — по нему мы найдём ваш аккаунт</p>
        <div className="kv"><span>Статус аккаунта</span>{banned ? <Badge tone="err">Заблокирован</Badge> : <Badge tone="ok">Активен</Badge>}</div>
        <div className="kv"><span>Имя</span><b>{name}</b></div>
        <div className="kv"><span>Имя пользователя</span><b>{u.username ? `@${u.username}` : "—"}</b></div>
        <div className="kv"><span>Telegram ID</span><b>{u.id}</b></div>
      </Card>

      <Card>
        <div className="row-between">
          <h2>Подписка</h2>
          {sub ? <Badge tone={statusTone(sub.status)}>{sub.status_label}</Badge> : <Badge>Нет подписки</Badge>}
        </div>
        {sub ? (
          <>
            <div className="kv"><span>Тариф</span><b>{planName(sub.plan_code)}</b></div>
            <div className="kv"><span>Активирована</span><b>{fmtDate(sub.starts_at)}</b></div>
            <div className="kv"><span>Окончание</span><b>{sub.is_lifetime ? "Бессрочная подписка" : fmtDate(sub.expires_at)}</b></div>
            {!sub.is_lifetime && <div className="kv"><span>Осталось</span><b>{fmtRemaining(sub.remaining_seconds)}</b></div>}
            <div className="kv"><span>Устройства</span><b>{me.devices_used} из {sub.device_limit}</b></div>
          </>
        ) : <p className="hint">Активной подписки нет.</p>}
        <div className="kv"><span>Бонусных дней</span><b>{me.bonus_balance}</b></div>
      </Card>

      <div className="stack">
        <button className="btn" onClick={() => goTab("devices")}><Icon name="link" size={18} /><span>Ссылка подписки и QR</span></button>
        <button className="btn btn-secondary" onClick={() => open({ type: "support" })}>
          <Icon name="chat" size={18} /><span>Поддержка{me.support_open_tickets > 0 ? ` (${me.support_open_tickets})` : ""}</span>
        </button>
        {canOpenAdmin() && <button className="btn btn-secondary" onClick={openAdminPanel}><Icon name="admin" size={18} /><span>Админ-панель</span></button>}
      </div>

      <Card>
        <h2>Приглашения</h2>
        {me.referral_link ? (
          <>
            <div className="linkbox">{me.referral_link}</div>
            <button className="btn btn-secondary" onClick={() => void copyRef()}><Icon name="copy" size={18} /><span>Копировать ссылку</span></button>
          </>
        ) : <p className="hint">Ссылка появится после настройки бота.</p>}
        <div className="kv"><span>Приглашено друзей</span><b>{refs.data ? refs.data.summary.invited : "—"}</b></div>
      </Card>

      <Card>
        <h2>История платежей</h2>
        {orders.loading && <Spinner />}
        {orders.error && <p className="err-text">{orders.error}</p>}
        {orders.data?.orders.length === 0 && <p className="hint">Заказов пока нет.</p>}
        {orders.data?.orders.map((o) => (
          <div className="kv clickable" key={o.id} onClick={() => open({ type: "order", id: o.id })}>
            <span>{o.plan_title}<br /><small>{fmtDateTime(o.created_at)} · {o.amount_label}</small></span>
            <Badge tone={statusTone(o.status)}>{o.status_label}</Badge>
          </div>
        ))}
      </Card>

      <Card>
        <h2>История подписки</h2>
        {hist.loading && !hist.data && <Spinner />}
        {hist.error && <p className="err-text">{hist.error}</p>}
        {hist.data?.items.length === 0 && <p className="hint">Изменений пока нет.</p>}
        {hist.data?.items.map((h) => (
          <div className="kv hist-item" key={h.id}>
            <span>
              {h.action_label}
              <small>{fmtDateTime(h.created_at)}{h.new_expires_at ? ` · до ${fmtDate(h.new_expires_at)}` : ""}</small>
              {h.note && <small><i>{h.note}</i></small>}
            </span>
          </div>
        ))}
      </Card>
    </>
  );
}
