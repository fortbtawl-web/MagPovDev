import { Icon } from "../Icon";
import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Card, ErrorBox, Skeleton, Title } from "../ui";
import { copyText, fmtDate } from "../../shared/format";
import { errText } from "../../shared/api";
import { haptic, openTg } from "../telegram";
import { PromoBox } from "./PromoBox";
import type { BonusHistory, ReferralsResp } from "../../shared/types";

export function Bonus() {
  const { toast, reloadMe, me, open } = useApp();
  const ref = useLoad<ReferralsResp>("/api/referrals");
  const hist = useLoad<BonusHistory>("/api/bonus/history");
  const [busy, setBusy] = useState(false);

  if ((ref.loading && !ref.data) || (hist.loading && !hist.data)) return <Skeleton />;
  if (ref.error || !ref.data) return <ErrorBox text={ref.error ?? "Ошибка"} onRetry={() => { void ref.reload(); void hist.reload(); }} />;
  const r = ref.data;
  const s = r.summary;
  const balance = hist.data?.balance ?? s.balance_days;

  async function share() {
    if (!r.link) return;
    openTg("https://t.me/share/url?url=" + encodeURIComponent(r.link) + "&text=" + encodeURIComponent("Попробуй SpirittVPN — твоя свобода в сети!"));
  }
  async function copy() {
    if (r.link && (await copyText(r.link))) { haptic("success"); toast("Ссылка скопирована"); } else toast("Не удалось скопировать", "err");
  }
  async function apply() {
    setBusy(true);
    try {
      await api("POST", "/api/bonus/apply", {});
      haptic("success");
      toast("Бонусные дни применены к подписке");
      await Promise.all([hist.reload(), ref.reload(), reloadMe()]);
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  return (
    <>
      <Title sub="Приглашайте друзей и получайте дни">Бонусы</Title>
      <button className="card task-entry clickable" onClick={() => open({ type: "tasks" })}>
        <span className="mi"><Icon name="sparkle" size={20} /></span>
        <span><b>Задания и SpirCoin</b><small>Выполняйте задания и оплачивайте подписку монетами</small></span>
        <Icon name="chevron" size={18} className="chev" />
      </button>
      <Card>
        <h2>Ваша ссылка</h2>
        {r.link ? (
          <>
            <div className="linkbox">{r.link}</div>
            <div className="stack">
              <button className="btn" onClick={() => void copy()}><Icon name="copy" size={18} /><span>Копировать</span></button>
              <button className="btn btn-secondary" onClick={() => void share()}><Icon name="share" size={18} /><span>Пригласить друга</span></button>
            </div>
          </>
        ) : <p className="hint">Ссылка появится после настройки бота.</p>}
      </Card>

      <Card>
        <h2>Промокод</h2>
        <PromoBox onDone={() => { void hist.reload(); }} />
      </Card>

      <Card>
        <h2>Баланс: {balance} дн.</h2>
        <button className="btn" disabled={busy || balance <= 0 || !me?.subscription} onClick={() => void apply()}>
          {busy ? "Применяем…" : "Применить бонусные дни"}
        </button>
        {!me?.subscription && <p className="hint small">Для применения бонусов нужна подписка.</p>}
      </Card>

      <Card>
        <div className="grid">
          <div className="stat"><b>{s.invited}</b><span>Приглашено</span></div>
          <div className="stat"><b>{s.with_trial}</b><span>С пробным</span></div>
          <div className="stat"><b>{s.with_payment}</b><span>С оплатой</span></div>
          <div className="stat"><b>{s.earned_days}</b><span>Получено дней</span></div>
          <div className="stat"><b>{s.pending}</b><span>Ожидают</span></div>
        </div>
      </Card>

      <Card>
        <h2>Правила</h2>
        {r.rules.enabled ? (
          <ul className="rules">
            <li>+{r.rules.trial_bonus_days} дн. вам, когда друг активирует пробный период ({r.rules.trial_days} дн.)</li>
            <li>+{r.rules.payment_bonus_days} дн. вам после первой подтверждённой оплаты друга</li>
          </ul>
        ) : <p className="hint">Реферальная программа сейчас отключена.</p>}
      </Card>

      <Card>
        <h2>Приглашённые</h2>
        {r.invites.length === 0 && <p className="hint">Пока никого нет.</p>}
        {r.invites.map((i, k) => (
          <div className="kv" key={k}>
            <span>{i.name}<br /><small>{fmtDate(i.registered_at)} · пробный: {i.trial ? "да" : "нет"} · оплата: {i.paid ? "да" : "нет"}</small></span>
            <b>+{i.bonus_days} дн.</b>
          </div>
        ))}
      </Card>

      <Card>
        <h2>История бонусов</h2>
        {hist.error && <p className="err-text">{hist.error}</p>}
        {hist.data?.items.length === 0 && <p className="hint">Начислений пока нет.</p>}
        {hist.data?.items.map((b) => (
          <div className="kv" key={b.id}>
            <span>{b.label}<br /><small>{fmtDate(b.created_at)}{b.comment ? ` · ${b.comment}` : ""}</small></span>
            <b>{b.days > 0 ? "+" : ""}{b.days} дн.</b>
          </div>
        ))}
      </Card>
    </>
  );
}
