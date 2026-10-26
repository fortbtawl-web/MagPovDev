import { useState } from "react";
import { api, type Order, type Tickets } from "../api";
import { Badge, Button, Card, Empty, Icon, Row, SectionTitle, Sheet, SkeletonCards, Toggle, useAction, useApp, useLoad } from "../ui";
import { fmtDate, fmtShort } from "../format";
import { openLink } from "../tg";
import { setSoundsEnabled, play } from "../sound";
import { statusInfo } from "./Home";

const ORDER: Record<Order["status"], { tone: "ok" | "warn" | "bad" | "muted"; label: string }> = {
  created: { tone: "muted", label: "Создан" },
  pending: { tone: "warn", label: "Ожидает оплаты" },
  paid: { tone: "ok", label: "Подтверждена" },
  failed: { tone: "bad", label: "Отклонена" },
  cancelled: { tone: "muted", label: "Отменена" },
  refunded: { tone: "muted", label: "Возвращена" },
};

export default function Profile() {
  const { me, refreshMe, toast, fail } = useApp();
  const [sheet, setSheet] = useState<"orders" | "support" | "about" | null>(null);
  const orders = useLoad(() => (sheet === "orders" ? api<{ orders: Order[] }>("/orders") : Promise.resolve(null)), [sheet]);
  const tickets = useLoad(() => (sheet === "support" ? api<Tickets>("/support") : Promise.resolve(null)), [sheet]);
  const [category, setCategory] = useState("connection");
  const [text, setText] = useState("");
  const action = useAction();

  if (!me) return <SkeletonCards />;
  const s = me.subscription;
  const st = statusInfo(me);

  const setPref = (key: "notifications" | "sounds", value: boolean) =>
    action.run(async () => {
      if (key === "sounds") {
        setSoundsEnabled(value);
        if (value) play("tap");
      }
      try {
        await api("/me/settings", { method: "PATCH", body: { [key]: value } });
        await refreshMe();
      } catch (e) {
        fail(e);
      }
    });

  const sendTicket = () =>
    action.run(async () => {
      try {
        await api("/support", { method: "POST", body: { category, text } });
        setText("");
        toast("Обращение отправлено — ответ придёт в бот", "ok");
        void tickets.reload();
      } catch (e) {
        fail(e);
      }
    });

  const paid = orders.data?.orders.filter((o) => o.status === "paid") ?? [];

  return (
    <div className="stack">
      <Card glow delay={0}>
        <div className="card-head">
          <h2>Твой аккаунт</h2>
          <Badge tone={st.tone}>{st.label}</Badge>
        </div>
        <Row label="Telegram ID" value={me.user.telegram_id} mono />
        <Row label="Тариф" value={s.plan ?? "—"} />
        <Row label="Начало подписки" value={fmtDate(s.starts_at)} />
        <Row label="Подписка до" value={s.lifetime ? "Бессрочная" : fmtDate(s.expires_at)} />
        <Row label="Устройства" value={`${s.devices_used} из ${s.device_limit || 0}`} />
        <Row label="Пробный период" value={me.trial.used ? "использован" : "доступен"} />
        <Row label="Рефералы" value={`${me.referral.invited} · +${me.referral.bonus_days} дн.`} />
        {!me.vpn.ready && <p className="small muted">🛠 VPN-серверы готовятся к запуску — настройки появятся, как только всё заработает.</p>}
      </Card>

      <div className="menu-list">
        <button className="menu-item" onClick={() => setSheet("orders")}><Icon name="card" /> <span>История покупок и продлений</span><Icon name="chevron" /></button>
        <button className="menu-item" onClick={() => setSheet("support")}><Icon name="chat" /> <span>Поддержка</span><Icon name="chevron" /></button>
        <button className="menu-item" onClick={() => openLink(me.links.privacy)}><Icon name="doc" /> <span>Политика конфиденциальности</span><Icon name="chevron" /></button>
        <button className="menu-item" onClick={() => openLink(me.links.offer)}><Icon name="doc" /> <span>Публичная оферта</span><Icon name="chevron" /></button>
        <button className="menu-item" onClick={() => setSheet("about")}><Icon name="help" /> <span>О SpirittVPN</span><Icon name="chevron" /></button>
      </div>
      {me.terms.accepted_at && <p className="tiny muted center">Условия приняты {fmtDate(me.terms.accepted_at)} (версия {me.terms.version})</p>}

      <SectionTitle>Настройки</SectionTitle>
      <Card delay={2}>
        <Toggle label="Уведомления" hint="Напоминания об окончании подписки в боте" checked={me.settings.notifications} onChange={(v) => void setPref("notifications", v)} />
        <Toggle label="Звуки интерфейса" hint="Короткие сигналы при действиях" checked={me.settings.sounds} onChange={(v) => void setPref("sounds", v)} />
      </Card>

      <Sheet open={sheet === "orders"} onClose={() => setSheet(null)} title="Покупки и продления">
        {orders.loading ? <SkeletonCards n={1} /> : !orders.data?.orders.length ? (
          <Empty icon="card" title="Платежей пока нет" />
        ) : (
          <div className="list">
            {orders.data.orders.map((o) => (
              <div className="list-item" key={o.id}>
                <div className="grow">
                  <b>{o.plan}{o.is_test ? " · ТЕСТ" : ""}</b>
                  <span className="muted tiny">
                    {fmtShort(o.paid_at ?? o.created_at)} · {o.amount_text}
                    {o.status === "paid" && paid.indexOf(o) < paid.length - 1 ? " · продление" : ""}
                  </span>
                </div>
                <Badge tone={ORDER[o.status].tone}>{ORDER[o.status].label}</Badge>
              </div>
            ))}
          </div>
        )}
      </Sheet>

      <Sheet open={sheet === "support"} onClose={() => setSheet(null)} title="Поддержка">
        <p className="muted small">Возникли вопросы по подключению, оплате или подписке? Напиши нашей команде поддержки — мы поможем разобраться.</p>
        <div className="actions">
          {me.links.support.map((u) => (
            <Button key={u} kind="soft" icon="chat" sound={false} onClick={() => openLink(`https://t.me/${u}`)}>@{u}</Button>
          ))}
        </div>
        <SectionTitle>Обращение через бот</SectionTitle>
        <div className="chips">
          {Object.entries(tickets.data?.categories ?? { connection: "Подключение", payment: "Оплата", subscription: "Подписка", other: "Другое" })
            .filter(([k]) => k !== "general")
            .map(([k, v]) => (
              <button key={k} className={`chip${category === k ? " chip-active" : ""}`} onClick={() => setCategory(k)}>{v}</button>
            ))}
        </div>
        <textarea className="field" rows={4} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} placeholder="Опиши ситуацию" aria-label="Текст обращения" />
        <Button block busy={action.busy} disabled={text.trim().length < 5} onClick={() => void sendTicket()}>Отправить</Button>
        {tickets.data?.tickets.map((t) => (
          <Card key={t.id}>
            <div className="card-head"><b>#{t.id} · {tickets.data!.categories[t.category] ?? t.category}</b><Badge tone={t.status === "answered" ? "ok" : t.status === "closed" ? "muted" : "warn"}>{t.status === "answered" ? "Есть ответ" : t.status === "closed" ? "Закрыто" : "Открыто"}</Badge></div>
            {t.messages.map((m, i) => <p key={i} className={`msg${m.from_admin ? " msg-admin" : ""}`}>{m.text}</p>)}
          </Card>
        ))}
      </Sheet>

      <Sheet open={sheet === "about"} onClose={() => setSheet(null)} title="SpirittVPN — VPN-сервис">
        <div className="about">
          <img src={`${import.meta.env.BASE_URL}logo-384.webp`} alt="SpirittVPN" />
          <Row label="👑 Овнер проекта" value={<button className="link" onClick={() => openLink(`https://t.me/${me.links.owner}`)}>@{me.links.owner}</button>} />
          <Row label="💻 Разработчик / кодер" value={<button className="link" onClick={() => openLink(`https://t.me/${me.links.developer}`)}>@{me.links.developer}</button>} />
          <Row label="🆘 Поддержка" value={me.links.support.map((u) => `@${u}`).join(" и ")} />
          <p className="muted small center">SpirittVPN — твоя свобода в сети.</p>
        </div>
      </Sheet>
    </div>
  );
}
