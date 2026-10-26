import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Order, type Plan } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Icon, Row, SectionTitle, Sheet, SkeletonCards, useAction, useApp, useLoad } from "../ui";
import { devicesWord, fmtDate, fmtRemaining, term } from "../format";
import { haptic, openInvoice, openLink } from "../tg";
import { play } from "../sound";
import { statusInfo } from "./Home";

type PayState = { order: Order; phase: "waiting" | "paid" | "failed" | "cancelled" };

export default function Subscription() {
  const { me, refreshMe, toast, fail, go } = useApp();
  const plans = useLoad(() => api<{ plans: Plan[] }>("/plans"));
  const [selected, setSelected] = useState<Plan | null>(null);
  const [pay, setPay] = useState<PayState | null>(null);
  const [promo, setPromo] = useState("");
  const action = useAction();
  const poll = useRef<number | null>(null);

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  if (!me) return <SkeletonCards />;
  const s = me.subscription;
  const st = statusInfo(me);
  const termsOk = Boolean(me.terms.accepted_at);

  const watchOrder = (order: Order) => {
    setPay({ order, phase: "waiting" });
    let tries = 0;
    if (poll.current) window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      tries += 1;
      try {
        const { order: o } = await api<{ order: Order }>(`/orders/${order.id}`);
        if (o.status === "paid") {
          window.clearInterval(poll.current!);
          setPay({ order: o, phase: "paid" });
          haptic.success();
          play("purchase");
          setTimeout(() => play("complete"), 900);
          await refreshMe();
          void plans.reload();
        } else if (o.status === "failed" || o.status === "cancelled") {
          window.clearInterval(poll.current!);
          setPay({ order: o, phase: o.status === "failed" ? "failed" : "cancelled" });
          play("error");
        }
      } catch {
        /* keep polling; network hiccups are expected */
      }
      if (tries > 150) window.clearInterval(poll.current!);
    }, 2000);
  };

  const buy = (plan: Plan) =>
    action.run(async () => {
      try {
        if (!termsOk) {
          await api("/terms/accept", { method: "POST", body: { version: me.terms.version } });
          await refreshMe();
        }
        const res = await api<{ order: Order; kind: "invoice" | "url"; url: string | null }>("/orders", {
          method: "POST",
          body: { plan_code: plan.code },
        });
        setSelected(null);
        watchOrder(res.order);
        if (res.url && res.kind === "invoice") {
          const status = await openInvoice(res.url);
          if (status === "cancelled") toast("Оплата отменена", "info");
          if (status === "failed") toast("Платёж не прошёл", "error");
        } else if (res.url) {
          openLink(res.url);
        }
      } catch (e) {
        if (e instanceof ApiError && e.code === "need_terms") await refreshMe();
        fail(e);
      }
    });

  const activateTrial = () =>
    action.run(async () => {
      try {
        if (!termsOk) await api("/terms/accept", { method: "POST", body: { version: me.terms.version } });
        await api("/trial", { method: "POST" });
        haptic.success();
        play("complete");
        toast("Пробный период активирован", "ok");
        await refreshMe();
        go("devices");
      } catch (e) {
        fail(e);
        await refreshMe();
      }
    });

  const applyPromo = () =>
    action.run(async () => {
      try {
        const res = await api<{ description: string; days_added: number }>("/promo", { method: "POST", body: { code: promo } });
        toast(`Промокод активирован: ${res.description}`, "ok");
        play("complete");
        setPromo("");
        await refreshMe();
        void plans.reload();
      } catch (e) {
        fail(e);
      }
    });

  return (
    <div className="stack">
      <Card glow delay={0}>
        <div className="card-head">
          <h2>Моя подписка</h2>
          <Badge tone={st.tone}>{st.label}</Badge>
        </div>
        <Row label="Тариф" value={s.plan ?? "—"} />
        <Row label="Начало" value={fmtDate(s.starts_at)} />
        <Row label="Окончание" value={s.lifetime ? "Бессрочная" : fmtDate(s.expires_at)} />
        {s.remaining_seconds != null && <Row label="Осталось" value={fmtRemaining(s.remaining_seconds)} />}
        <Row label="Устройства" value={s.device_limit ? `${s.devices_used} из ${s.device_limit}` : "—"} />
      </Card>

      {!me.vpn.ready && (
        <Card className="card-accent" delay={1}>
          <b>VPN-серверы готовятся к запуску</b>
          <p className="muted small">Оплата откроется, когда подключение станет доступно. Мы не принимаем деньги, пока не можем выдать доступ.</p>
        </Card>
      )}

      {me.trial.available && (
        <Card delay={1} className="trial-card">
          <div className="tile-icon tile-icon-violet"><Icon name="gift" /></div>
          <div className="grow">
            <b>Пробный период — {me.trial.days} дня</b>
            <p className="muted small">Бесплатно, один раз на аккаунт. 1 устройство.</p>
          </div>
          <Button busy={action.busy} onClick={activateTrial}>Забрать</Button>
        </Card>
      )}

      <SectionTitle>Тарифы</SectionTitle>
      {plans.loading && !plans.data ? (
        <SkeletonCards n={3} />
      ) : plans.error ? (
        <ErrorBox text={plans.error} onRetry={() => void plans.reload()} />
      ) : !plans.data?.plans.length ? (
        <Empty icon="card" title="Тарифы скоро появятся" />
      ) : (
        <div className="plans">
          {plans.data.plans.map((p, i) => (
            <Card key={p.code} delay={i + 2} className={`plan${p.lifetime ? " plan-life" : ""}`} onClick={() => setSelected(p)}>
              <div className="plan-top">
                <b>{p.title}</b>
                {p.lifetime ? <Badge tone="accent">Lifetime</Badge> : null}
              </div>
              <div className="plan-price">
                {p.discount > 0 && <s className="muted">{p.price_text}</s>}
                <span>{p.total_text}</span>
              </div>
              <span className="muted small">{term(p)} · {devicesWord(p.devices)}</span>
            </Card>
          ))}
        </div>
      )}

      <Card delay={8}>
        <b>Промокод</b>
        <div className="inline-form">
          <input value={promo} onChange={(e) => setPromo(e.target.value.toUpperCase())} placeholder="Например, SPIRIT10"
            maxLength={32} autoCapitalize="characters" aria-label="Промокод" />
          <Button kind="soft" busy={action.busy} disabled={promo.trim().length < 3} onClick={applyPromo}>Применить</Button>
        </div>
      </Card>

      <Sheet open={!!selected} onClose={() => setSelected(null)} title={selected?.title ?? ""}>
        {selected && (
          <>
            <p className="muted">{selected.description}</p>
            <Row label="Срок" value={term(selected)} />
            <Row label="Устройства" value={devicesWord(selected.devices)} />
            <Row label="Стоимость" value={selected.price_text} />
            {selected.discount > 0 && <Row label={`Скидка ${selected.promo ?? ""}`} value={`−${selected.price_text === selected.total_text ? "" : ""}${(selected.discount / (selected.currency === "XTR" ? 1 : 100)).toLocaleString("ru-RU")}`} />}
            <div className="total"><span>Итого</span><b>{selected.total_text}</b></div>
            {selected.lifetime && (
              <p className="small muted">
                Lifetime — без регулярных продлений на всё время работы сервиса в рамках условий публичной оферты.
              </p>
            )}
            <ul className="terms-list small">
              <li>Доступ выдаётся только после подтверждения оплаты платёжной системой.</li>
              <li>Каждое устройство получает собственную ссылку подключения.</li>
              <li>Продление добавляется к текущему сроку.</li>
            </ul>
            <div className="doc-links">
              <button className="link" onClick={() => openLink(me.links.offer)}><Icon name="doc" size={16} /> Публичная оферта</button>
              <button className="link" onClick={() => openLink(me.links.privacy)}><Icon name="doc" size={16} /> Политика конфиденциальности</button>
            </div>
            {!termsOk && <p className="small muted">Нажимая «Оплатить», ты принимаешь условия публичной оферты и политики конфиденциальности.</p>}
            {me.payments.test_mode && <Badge tone="warn">Тестовый режим оплаты — деньги не списываются</Badge>}
            <Button block busy={action.busy} disabled={!me.vpn.ready || !me.payments.enabled} onClick={() => void buy(selected)} icon="card">
              {!me.vpn.ready ? "Скоро" : !me.payments.enabled ? "Оплата временно недоступна" : `Оплатить ${selected.total_text}`}
            </Button>
          </>
        )}
      </Sheet>

      <Sheet open={!!pay} onClose={() => setPay(null)} title="Оплата">
        {pay?.phase === "waiting" && (
          <div className="pay-state">
            <div className="pulse-ring"><Icon name="card" size={30} /></div>
            <b>Ожидаем подтверждение оплаты</b>
            <p className="muted small">Подписка включится автоматически, как только платёжная система подтвердит платёж. Нажимать «Я оплатил» не нужно.</p>
            <span className="mono small">Заказ {pay.order.id.slice(0, 8)}</span>
          </div>
        )}
        {pay?.phase === "paid" && (
          <div className="pay-state pay-success">
            <div className="success-burst"><Icon name="check" size={38} /></div>
            <b>Оплата подтверждена</b>
            <p className="muted small">{pay.order.plan} · {pay.order.amount_text}{pay.order.is_test ? " · ТЕСТ" : ""}</p>
            <Button block icon="devices" onClick={() => { setPay(null); go("devices"); }}>Подключить устройство</Button>
          </div>
        )}
        {(pay?.phase === "failed" || pay?.phase === "cancelled") && (
          <div className="pay-state">
            <div className="fail-mark"><Icon name="close" size={30} /></div>
            <b>{pay.phase === "failed" ? "Платёж отклонён" : "Заказ отменён"}</b>
            <p className="muted small">Деньги не списаны. Можно оформить новый заказ.</p>
            <Button kind="soft" block onClick={() => setPay(null)}>Понятно</Button>
          </div>
        )}
      </Sheet>
    </div>
  );
}
