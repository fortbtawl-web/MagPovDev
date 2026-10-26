import { Badge, Button, Card, Icon, SkeletonCards, useApp } from "../ui";
import { devicesWord, fmtDate, fmtRemaining } from "../format";
import type { Me } from "../api";

export function statusInfo(me: Me): { tone: "ok" | "warn" | "bad" | "muted" | "accent"; label: string } {
  switch (me.subscription.status) {
    case "active": return { tone: "ok", label: me.subscription.lifetime ? "Бессрочная" : "Активна" };
    case "soon": return { tone: "warn", label: "Скоро закончится" };
    case "pending": return { tone: "accent", label: "Ожидает активации" };
    case "expired": return { tone: "bad", label: "Истекла" };
    case "suspended": return { tone: "bad", label: "Временно отключена" };
    default: return { tone: "muted", label: "Не активна" };
  }
}

export default function Home() {
  const { me, go } = useApp();
  if (!me) return <SkeletonCards n={3} />;
  const s = me.subscription;
  const active = ["active", "soon", "pending"].includes(s.status);
  const st = statusInfo(me);

  let hero;
  if (!me.vpn.ready) {
    hero = (
      <Card glow className="hero hero-idle" delay={0}>
        <div className="hero-orb hero-orb-idle"><Icon name="server" size={34} /></div>
        <h1>VPN-серверы готовятся к запуску</h1>
        <p className="muted">Подключение станет доступно после настройки инфраструктуры. Мы сообщим в боте, когда всё заработает.</p>
      </Card>
    );
  } else if (active) {
    hero = (
      <Card glow className="hero hero-on" delay={0}>
        <div className="hero-orb hero-orb-on"><Icon name="shield" size={34} /></div>
        <h1>VPN доступен</h1>
        <p className="muted">
          {s.plan} · {s.lifetime ? "бессрочно" : `до ${fmtDate(s.expires_at, false)}`} · {devicesWord(s.device_limit)}
        </p>
        {me.vpn.test_mode && <Badge tone="warn">Тестовый режим — доступ не настоящий</Badge>}
        <Button icon="devices" onClick={() => go("devices")} block>Управлять подключениями</Button>
      </Card>
    );
  } else if (s.status === "suspended") {
    hero = (
      <Card glow className="hero hero-off" delay={0}>
        <div className="hero-orb hero-orb-off"><Icon name="clock" size={34} /></div>
        <h1>Подписка временно отключена</h1>
        <p className="muted">Напиши в поддержку — поможем разобраться.</p>
        <Button kind="soft" icon="chat" onClick={() => go("profile")} block>Поддержка</Button>
      </Card>
    );
  } else {
    hero = (
      <Card glow className="hero hero-off" delay={0}>
        <div className="hero-orb hero-orb-off"><Icon name="shield" size={34} /></div>
        <h1>{s.status === "expired" ? "Подписка истекла" : "Подписка не активна"}</h1>
        <p className="muted">
          {me.trial.available ? `Начни с бесплатного пробного периода на ${me.trial.days} дня или выбери тариф.` : "Выбери тариф, чтобы подключить устройства."}
        </p>
        <Button icon="bolt" onClick={() => go("sub")} block>Выбрать тариф</Button>
      </Card>
    );
  }

  return (
    <div className="stack">
      <div className="status-strip">
        <Badge tone={st.tone}>{st.label}</Badge>
        {s.remaining_seconds != null && <span className="muted small">Осталось {fmtRemaining(s.remaining_seconds)}</span>}
      </div>
      {hero}

      <div className="grid2">
        <Card delay={1} onClick={() => go("sub")}>
          <div className="tile-icon"><Icon name="card" /></div>
          <span className="muted small">Подписка</span>
          <b>{s.plan ?? "Нет тарифа"}</b>
          <span className="small">{s.lifetime ? "Бессрочная" : s.expires_at ? `до ${fmtDate(s.expires_at, false)}` : "—"}</span>
        </Card>
        <Card delay={2} onClick={() => go("devices")}>
          <div className="tile-icon"><Icon name="devices" /></div>
          <span className="muted small">Устройства</span>
          <b>{s.devices_used} из {s.device_limit || "—"}</b>
          <div className="meter"><i style={{ width: `${s.device_limit ? (100 * s.devices_used) / s.device_limit : 0}%` }} /></div>
        </Card>
      </div>

      {(s.status === "soon" || s.status === "expired") && me.vpn.ready && (
        <Card delay={3} className="card-accent">
          <b>{s.status === "soon" ? "Подписка скоро закончится" : "Верни доступ к VPN"}</b>
          <p className="muted small">Новый срок добавится к текущему, устройства включатся автоматически.</p>
          <Button icon="refresh" onClick={() => go("sub")}>Продлить подписку</Button>
        </Card>
      )}

      <Card delay={4} onClick={() => go("refs")} className="ref-card">
        <div className="tile-icon tile-icon-violet"><Icon name="gift" /></div>
        <div className="grow">
          <b>Приглашай друзей</b>
          <p className="muted small">
            Бонусные дни за каждого друга · приглашено {me.referral.invited}, получено {me.referral.bonus_days} дн.
          </p>
        </div>
        <Icon name="chevron" />
      </Card>
    </div>
  );
}
