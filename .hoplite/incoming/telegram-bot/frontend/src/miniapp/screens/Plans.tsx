import { Icon } from "../Icon";
import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, ErrorBox, Skeleton, Title, statusTone } from "../ui";
import { fmtDate, fmtRemaining } from "../../shared/format";
import { planName } from "./planName";
import { errText } from "../../shared/api";
import { haptic, openExternal } from "../telegram";
import { PromoBox } from "./PromoBox";
import type { Order, PlansResp, PromoActive } from "../../shared/types";

export function Plans() {
  const { toast, open, me } = useApp();
  const sub = me?.subscription ?? null;
  const res = useLoad<PlansResp>("/api/plans");
  const promo = useLoad<PromoActive>("/api/promo/active");
  const [busy, setBusy] = useState<string | null>(null);
  const [promoOpen, setPromoOpen] = useState(false);

  if (res.loading && !res.data) return <Skeleton />;
  if (res.error || !res.data) return <ErrorBox text={res.error ?? "Ошибка"} onRetry={() => void res.reload()} />;
  const { plans, payments_available, payment_methods } = res.data;

  async function buy(code: string) {
    setBusy(code);
    try {
      const o = await api<Order>("POST", "/api/orders", { plan_code: code });
      if (o.payment?.confirmation_url) openExternal(o.payment.confirmation_url);
      open({ type: "order", id: o.id });
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(null);
  }

  return (
    <>
      <Title sub="Выберите подходящий тариф">Тарифы</Title>
      <Card className="plan current">
        <div className="row-between">
          <h2>Ваш тариф</h2>
          {sub ? <Badge tone={statusTone(sub.status)}>{sub.status_label}</Badge> : <Badge>Нет подписки</Badge>}
        </div>
        {sub ? (
          <>
            <div className="kv"><span>Тариф</span><b>{plans.find((p) => p.code === sub.plan_code)?.title ?? planName(sub.plan_code)}</b></div>
            <div className="kv"><span>Срок</span><b>{sub.is_lifetime ? "Бессрочная подписка" : `до ${fmtDate(sub.expires_at)}`}</b></div>
            {!sub.is_lifetime && <div className="kv"><span>Осталось</span><b>{fmtRemaining(sub.remaining_seconds)}</b></div>}
            <p className="hint small">Покупка тарифа продлевает подписку после подтверждения оплаты сервером.</p>
          </>
        ) : <p className="hint">Активной подписки нет — выберите тариф ниже.</p>}
      </Card>
      {promo.data?.discount && (
        <Card className="ok-box"><Icon name="ticket" size={20} /><span>Промокод {promo.data.discount.code}: скидка {promo.data.discount.label} применится к следующему заказу</span></Card>
      )}
      <Card>
        <button className="btn btn-secondary" onClick={() => setPromoOpen(!promoOpen)}>{promoOpen ? "Скрыть" : "Есть промокод?"}</button>
        {promoOpen && <PromoBox onDone={() => { void promo.reload(); }} />}
      </Card>
      {!payments_available && <Card className="warn-box">Оплата временно недоступна: платёжная система подключается.</Card>}
      {payments_available && payment_methods.length > 0 && <p className="hint small">Способы оплаты: {payment_methods.join(", ")}</p>}
      {plans.map((p) => (
        <Card key={p.id} className={sub?.plan_code === p.code ? "plan current" : "plan"}>
          <div className="row-between">
            <h2>{p.title}</h2>
            {p.is_lifetime ? <Badge tone="ok">Навсегда</Badge> : sub?.plan_code === p.code ? <Badge tone="ok">Текущий</Badge> : null}
          </div>
          <div className="price">{p.price_label}</div>
          <div className="hint">{p.terms}</div>
          <div className="kv"><span>Устройств</span><b>{p.device_limit}</b></div>
          {p.description && <p className="hint">{p.description}</p>}
          {p.is_lifetime && <p className="hint small">Бессрочно — без регулярных продлений; лимит устройств указан в тарифе.</p>}
          <button className="btn" disabled={!payments_available || busy !== null} onClick={() => void buy(p.code)}>
            {busy === p.code ? "Создаём заказ…" : sub?.plan_code === p.code ? "Продлить" : "Купить"}
          </button>
        </Card>
      ))}
    </>
  );
}
