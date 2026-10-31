import { useState } from "react";
import { api, canOpenAdmin, openAdminPanel } from "../api";
import { useApp } from "../ctx";
import { Badge, BannedBanner, Card, Skeleton, statusTone } from "../ui";
import { Icon, Logo, type IconName } from "../Icon";
import { fmtDate, fmtRemaining } from "../../shared/format";
import { errText } from "../../shared/api";
import { haptic, openExternal } from "../telegram";
import type { DeviceMutation, DevicesResp, LinkResp, PlansResp } from "../../shared/types";
import { planName } from "./planName";
import { useLoad } from "../useLoad";

const ACCESS: Record<string, { text: string; tone: "ok" | "warn" | "err" | "" }> = {
  none: { text: "Нет подписки", tone: "" },
  no_devices: { text: "Подписка активна — добавьте устройство", tone: "warn" },
  pending: { text: "Ожидаем подтверждения VPN-сервера", tone: "warn" },
  ready: { text: "Доступ к VPN выдан", tone: "ok" },
  inactive: { text: "Подписка не активна", tone: "err" },
};

function MenuItem({ icon, label, onClick, badge }: { icon: IconName; label: string; onClick: () => void; badge?: number }) {
  return (
    <button className="menu-item" onClick={onClick}>
      <span className="mi"><Icon name={icon} size={18} /></span>
      <span>{label}</span>
      {!!badge && <span className="count">{badge}</span>}
      <Icon name="chevron" size={18} className="chev" />
    </button>
  );
}

export function Home() {
  const { me, reloadMe, toast, goTab, open } = useApp();
  const [busy, setBusy] = useState(false);
  const plans = useLoad<PlansResp>("/api/plans");
  const devices = useLoad<DevicesResp>("/api/devices");
  if (!me) return <Skeleton />;
  const sub = me.subscription;
  const access = ACCESS[me.access_state] ?? ACCESS.none;
  const firstActive = devices.data?.devices.find((d) => d.status === "active");
  const ready = me.access_state === "ready" || (!!sub && me.devices_active > 0);
  const name = me.user.first_name ?? "друг";

  async function trial() {
    setBusy(true);
    try {
      const r = await api<DeviceMutation & { ok: boolean }>("POST", "/api/trial");
      haptic("success");
      toast(r.ok ? "Пробный период активирован" : "Сервер не подтвердил активацию", r.ok ? "ok" : "err");
      await reloadMe();
      if (r.ok) goTab("devices");
    } catch (e) {
      haptic("error");
      toast(errText(e), "err");
    }
    setBusy(false);
  }

  async function connect() {
    if (!firstActive) return;
    setBusy(true);
    try {
      const r = await api<LinkResp>("GET", `/api/devices/${firstActive.id}/link`);
      const url = r.subscription_url ?? r.link;
      if (url) openExternal(url); else open({ type: "device", id: firstActive.id });
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  let primary;
  if (!sub && me.trial.available) {
    primary = <button className="btn" disabled={busy} onClick={() => void trial()}><Icon name="zap" size={18} /><span>{busy ? "Активируем…" : `Пробный период на ${me.trial.days} дн.`}</span></button>;
  } else if (!sub) {
    primary = <button className="btn" onClick={() => goTab("plans")}><Icon name="plans" size={18} /><span>Выбрать тариф</span></button>;
  } else if (firstActive) {
    primary = <button className="btn" disabled={busy} onClick={() => void connect()}><Icon name="power" size={18} /><span>{busy ? "Открываем…" : "Подключиться"}</span></button>;
  } else {
    primary = <button className="btn" onClick={() => goTab("devices")}><Icon name="plus" size={18} /><span>Добавить устройство</span></button>;
  }

  return (
    <>
      <div className="hero">
        <div className="hero-top">
          <div className="hero-user">
            <div className="avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</div>
            <div>
              <div className="hero-brand" style={{ margin: 0 }}><Logo size={14} /> SpirittVPN</div>
              <h1>Привет, {name}</h1>
            </div>
          </div>
          <button className="icon-btn" aria-label="Поддержка и настройки" onClick={() => open({ type: "settings" })}><Icon name="settings" size={20} /></button>
        </div>
      </div>

      {me.account_status === "banned" && <BannedBanner />}

      <div className={ready ? "connect" : "connect off"}>
        <div className="connect-orb"><Icon name={ready ? "shield" : "power"} size={32} /></div>
        <div>
          <h2>{ready ? "VPN готов к подключению" : "VPN не настроен"}</h2>
          <p className="status-line" style={{ marginTop: 6 }}><span className={`dot ${access.tone}`} />{access.text}</p>
        </div>
        {primary}
        {sub && firstActive && (
          <button className="btn btn-secondary" onClick={() => open({ type: "device", id: firstActive.id })}><Icon name="link" size={18} /><span>QR-код и ссылка</span></button>
        )}
        <p className="hint small">Статус подписки не означает, что VPN сейчас включён на устройстве.</p>
      </div>

      <Card>
        <div className="row-between">
          <h2>Подписка</h2>
          {sub ? <Badge tone={statusTone(sub.status)}>{sub.status_label}</Badge> : <Badge>Нет подписки</Badge>}
        </div>
        {sub ? (
          <>
            <div className="kv"><span>Тариф</span><b>{plans.data?.plans.find((p) => p.code === sub.plan_code)?.title ?? planName(sub.plan_code)}</b></div>
            <div className="kv"><span>Срок</span><b>{sub.is_lifetime ? "Бессрочная подписка" : `до ${fmtDate(sub.expires_at)}`}</b></div>
            {!sub.is_lifetime && <div className="kv"><span>Осталось</span><b>{fmtRemaining(sub.remaining_seconds)}</b></div>}
            <div className="kv"><span>Устройства</span><b>{me.devices_used} из {sub.device_limit}</b></div>
            {sub.device_limit > 0 && <div className="meter" aria-hidden="true"><i style={{ width: `${Math.min(100, (me.devices_used / sub.device_limit) * 100)}%` }} /></div>}
            <button className="btn btn-secondary" onClick={() => goTab("plans")}><Icon name="refresh" size={18} /><span>Продлить или сменить тариф</span></button>
          </>
        ) : (
          <p className="hint">У вас пока нет активной подписки. {me.trial.available ? `Доступен пробный период на ${me.trial.days} дн.` : me.trial.reason ?? ""}</p>
        )}
        <div className="kv"><span>Активных ключей</span><b>{me.devices_active}</b></div>
        <div className="kv"><span>Бонусных дней</span><b>{me.bonus_balance}</b></div>
      </Card>

      <Card className="menu">
        <MenuItem icon="device" label="Мои устройства" onClick={() => goTab("devices")} />
        <MenuItem icon="plans" label={sub ? "Тарифы и продление" : "Купить VPN"} onClick={() => goTab("plans")} />
        <MenuItem icon="sparkle" label="Задания и SpirCoin" onClick={() => open({ type: "tasks" })} />
        <MenuItem icon="gift" label="Пригласить друга" onClick={() => goTab("bonus")} />
        <MenuItem icon="chat" label="Поддержка" badge={me.support_open_tickets} onClick={() => open({ type: "support" })} />
        {canOpenAdmin() && <MenuItem icon="admin" label="Админ-панель" onClick={openAdminPanel} />}
      </Card>
    </>
  );
}
