import { useState } from "react";
import { api, type Bonuses, type Referrals as RefData } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Icon, Sheet, SkeletonCards, copyText, useApp, useLoad } from "../ui";
import { fmtShort } from "../format";
import { openLink } from "../tg";

const SOURCE: Record<string, string> = {
  referral_trial: "Друг активировал пробный",
  referral_payment: "Друг оплатил подписку",
  promo: "Промокод",
  admin: "От администрации",
  correction: "Корректировка",
};

export default function Referrals() {
  const { toast, go } = useApp();
  const refs = useLoad(() => api<RefData>("/referrals"));
  const [view, setView] = useState<"invites" | "bonuses" | null>(null);
  const bonuses = useLoad(() => (view === "bonuses" ? api<Bonuses>("/bonuses") : Promise.resolve(null)), [view]);

  if (refs.loading && !refs.data) return <SkeletonCards n={2} />;
  if (refs.error || !refs.data) return <ErrorBox text={refs.error ?? ""} onRetry={() => void refs.reload()} />;
  const r = refs.data;

  return (
    <div className="stack">
      <Card glow delay={0} className="ref-hero">
        <div className="tile-icon tile-icon-violet"><Icon name="users" /></div>
        <h2>Реферальная программа SpirittVPN</h2>
        <p className="muted">Приглашай друзей и получай бонусные дни VPN!</p>
        {r.enabled ? (
          <div className="ref-rules">
            <div><b>+{r.trial_days} дн.</b><span>когда друг активирует пробный период</span></div>
            <div><b>+{r.payment_days} дн.</b><span>после его первой оплаты</span></div>
          </div>
        ) : (
          <Badge tone="muted">Программа приостановлена — новые бонусы не начисляются</Badge>
        )}
      </Card>

      <Card delay={1}>
        <span className="muted small">Твоя ссылка</span>
        {r.link ? (
          <>
            <div className="link-box mono small">{r.link}</div>
            <div className="actions">
              <Button icon="share" onClick={() => r.share_url && openLink(r.share_url)}>Поделиться ссылкой</Button>
              <Button kind="soft" icon="copy" sound={false} onClick={async () => toast((await copyText(r.link!)) ? "Ссылка скопирована" : "Не удалось скопировать", "ok")}>Копировать</Button>
            </div>
          </>
        ) : (
          <p className="muted small">Ссылка временно недоступна.</p>
        )}
      </Card>

      <div className="grid3">
        <Card delay={2}><span className="stat">{r.invited}</span><span className="muted tiny">Приглашено друзей</span></Card>
        <Card delay={3}><span className="stat">{r.bonus_days}</span><span className="muted tiny">Бонусных дней</span></Card>
        <Card delay={4}><span className="stat">{r.pending}</span><span className="muted tiny">Ожидают условий</span></Card>
      </div>

      <div className="actions">
        <Button kind="soft" icon="users" onClick={() => setView("invites")}>Мои приглашения</Button>
        <Button kind="soft" icon="gift" onClick={() => setView("bonuses")}>История бонусов</Button>
      </div>
      <Button kind="ghost" icon="home" sound={false} onClick={() => go("home")}>Главное меню</Button>

      <p className="tiny muted center">
        Бонус за каждое условие начисляется один раз на приглашённого. Самоприглашения и повторная смена пригласившего не засчитываются.
      </p>

      <Sheet open={view === "invites"} onClose={() => setView(null)} title="Мои приглашения">
        {r.invites.length === 0 ? (
          <Empty icon="users" title="Пока никого" text="Поделись ссылкой с друзьями." />
        ) : (
          <div className="list">
            {r.invites.map((i, idx) => (
              <div className="list-item" key={idx}>
                <div className="grow">
                  <b>{i.name}</b>
                  <span className="muted tiny">{fmtShort(i.invited_at)}</span>
                </div>
                <div className="checks tiny">
                  <span className={i.trial_done ? "on" : ""}>{i.trial_done ? "✓" : "○"} пробный</span>
                  <span className={i.payment_done ? "on" : ""}>{i.payment_done ? "✓" : "○"} оплата</span>
                </div>
                <Badge tone={i.bonus_days ? "ok" : "muted"}>+{i.bonus_days} дн.</Badge>
              </div>
            ))}
          </div>
        )}
      </Sheet>

      <Sheet open={view === "bonuses"} onClose={() => setView(null)} title="История бонусов">
        {bonuses.loading ? <SkeletonCards n={1} /> : !bonuses.data?.entries.length && !bonuses.data?.promos.length ? (
          <Empty icon="gift" title="Бонусов пока нет" />
        ) : (
          <div className="list">
            {bonuses.data?.pending_discount && (
              <div className="list-item"><div className="grow"><b>{bonuses.data.pending_discount.code}</b><span className="muted tiny">{bonuses.data.pending_discount.description}</span></div><Badge tone="accent">ждёт покупки</Badge></div>
            )}
            {bonuses.data?.entries.map((e, idx) => (
              <div className="list-item" key={`e${idx}`}>
                <div className="grow"><b>{SOURCE[e.source] ?? e.source}</b><span className="muted tiny">{e.description} · {fmtShort(e.created_at)}</span></div>
                <Badge tone={e.days > 0 ? "ok" : "bad"}>{e.days > 0 ? "+" : ""}{e.days} дн.</Badge>
              </div>
            ))}
          </div>
        )}
      </Sheet>
    </div>
  );
}
