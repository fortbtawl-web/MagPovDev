import { Icon } from "../Icon";
import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, ErrorBox, Spinner, Title, statusTone } from "../ui";
import { fmtDateTime, fmtMoney } from "../../shared/format";
import { errText } from "../../shared/api";
import { haptic, openExternal } from "../telegram";
import type { Order } from "../../shared/types";

export function OrderStatus({ id }: { id: number }) {
  const { reloadMe, toast } = useApp();
  const res = useLoad<Order>(`/api/orders/${id}`);
  const [checking, setChecking] = useState(false);

  if (res.loading && !res.data) return <Spinner />;
  if (res.error || !res.data) return <ErrorBox text={res.error ?? "Ошибка"} onRetry={() => void res.reload()} />;
  const o = res.data;

  async function check() {
    setChecking(true);
    try {
      const n = await api<Order>("GET", `/api/orders/${id}`);
      res.setData(n);
      if (n.status === "paid") { haptic("success"); await reloadMe(); }
    } catch (e) { toast(errText(e), "err"); }
    setChecking(false);
  }

  return (
    <>
      <Title>Заказ №{o.id}</Title>
      <Card>
        <div className="row-between"><b>{o.plan_title}</b><Badge tone={statusTone(o.status)}>{o.status_label}</Badge></div>
        <div className="kv"><span>Сумма</span><b>{o.amount_label}</b></div>
        {o.discount_minor > 0 && (
          <div className="kv"><span>Скидка по промокоду {o.promo_code ?? ""}</span><b>−{fmtMoney(o.discount_minor)}</b></div>
        )}
        <div className="kv"><span>Создан</span><b>{fmtDateTime(o.created_at)}</b></div>
        {o.paid_at && <div className="kv"><span>Оплачен</span><b>{fmtDateTime(o.paid_at)}</b></div>}
        {o.payment?.is_test && <p className="hint small">Тестовый платёж</p>}
        {o.payment?.error && <p className="hint err-text">{o.payment.error}</p>}
        {o.status === "paid" && <p className="ok-text">Оплата подтверждена сервером. {o.fulfillment_status === "done" ? "Доступ выдан — загляните в «Устройства»." : "Выдаём доступ…"}</p>}
        {o.fulfillment_error && <p className="hint err-text">{o.fulfillment_error}</p>}
      </Card>
      <div className="stack">
        {o.status !== "paid" && o.payment?.confirmation_url && (
          <button className="btn" onClick={() => openExternal(o.payment!.confirmation_url!)}><Icon name="card" size={18} /><span>Перейти к оплате</span></button>
        )}
        <button className="btn btn-secondary" disabled={checking} onClick={() => void check()}>
          {checking ? "Проверяем…" : "Проверить оплату"}
        </button>
      </div>
      <p className="hint small">Доступ выдаётся только после подтверждения оплаты сервером, а не после возврата со страницы оплаты.</p>
    </>
  );
}
