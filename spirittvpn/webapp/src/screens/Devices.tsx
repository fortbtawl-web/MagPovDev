import { useState } from "react";
import { api, type Connection, type Device, type DevicesPayload } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Icon, Sheet, SkeletonCards, copyText, useAction, useApp, useLoad } from "../ui";
import { fmtDate } from "../format";
import { haptic, openLink } from "../tg";
import { play } from "../sound";

const STATUS: Record<Device["status"], { tone: "ok" | "warn" | "bad" | "muted"; label: string }> = {
  active: { tone: "ok", label: "Активно" },
  suspended: { tone: "warn", label: "Приостановлено" },
  pending: { tone: "muted", label: "Настраивается" },
  revoked: { tone: "bad", label: "Отозвано" },
};

const GUIDES = [
  { os: "iPhone / iPad", apps: "Streisand, v2RayTun или Hiddify из App Store" },
  { os: "Android", apps: "v2rayNG, Hiddify или v2RayTun из Google Play" },
  { os: "Windows / macOS", apps: "Hiddify или v2RayN (Windows)" },
];

export default function Devices() {
  const { me, refreshMe, toast, fail, confirm, go } = useApp();
  const list = useLoad(() => api<DevicesPayload>("/devices"));
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<Device | null>(null);
  const [conn, setConn] = useState<{ device: Device; data: Connection | null; phase: "loading" | "ready" | "error"; error?: string } | null>(null);
  const action = useAction();

  const update = async (payload: DevicesPayload) => {
    list.setData(payload);
    await refreshMe();
  };

  const add = () =>
    action.run(async () => {
      try {
        await update(await api<DevicesPayload>("/devices", { method: "POST", body: { name: name.trim() } }));
        setAdding(false);
        setName("");
        haptic.success();
        play("connect");
        toast("Устройство добавлено", "ok");
      } catch (e) {
        fail(e);
      }
    });

  const rename = () =>
    action.run(async () => {
      if (!renaming) return;
      try {
        await update(await api<DevicesPayload>(`/devices/${renaming.id}`, { method: "PATCH", body: { name: name.trim() } }));
        setRenaming(null);
        toast("Название сохранено", "ok");
      } catch (e) {
        fail(e);
      }
    });

  const revoke = async (d: Device) => {
    const ok = await confirm({ title: `Отозвать «${d.name}»?`, text: "Ссылка подключения перестанет работать на сервере, место освободится.", ok: "Отозвать", danger: true });
    if (!ok) return;
    await action.run(async () => {
      try {
        await update(await api<DevicesPayload>(`/devices/${d.id}`, { method: "DELETE" }));
        toast("Доступ отозван, устройство удалено", "ok");
      } catch (e) {
        fail(e);
      }
    });
  };

  const replace = async (d: Device) => {
    const ok = await confirm({ title: `Заменить «${d.name}»?`, text: "Старая ссылка перестанет работать, будет выдана новая.", ok: "Заменить", danger: true });
    if (!ok) return;
    await action.run(async () => {
      try {
        await update(await api<DevicesPayload>(`/devices/${d.id}/replace`, { method: "POST", body: {} }));
        toast("Устройство заменено — получи новую ссылку", "ok");
      } catch (e) {
        fail(e);
      }
    });
  };

  const openConnection = async (d: Device) => {
    setConn({ device: d, data: null, phase: "loading" });
    const started = Date.now();
    try {
      const data = await api<Connection>(`/devices/${d.id}/connection`);
      const minDelay = document.documentElement.classList.contains("reduce-motion") ? 0 : 900;
      await new Promise((r) => setTimeout(r, Math.max(0, minDelay - (Date.now() - started))));
      setConn({ device: d, data, phase: "ready" });
      haptic.success();
      play("connect");
    } catch (e) {
      setConn({ device: d, data: null, phase: "error", error: e instanceof Error ? e.message : "" });
      play("error");
    }
  };

  if (!me || (list.loading && !list.data)) return <SkeletonCards n={2} />;
  if (list.error && !list.data) return <ErrorBox text={list.error} onRetry={() => void list.reload()} />;
  const data = list.data!;

  if (!data.vpn_ready) {
    return <Empty icon="server" title="VPN-серверы готовятся к запуску" text="Устройства можно будет добавить после настройки инфраструктуры." />;
  }

  return (
    <div className="stack">
      <Card glow delay={0}>
        <div className="card-head">
          <h2>Мои устройства</h2>
          {data.test_mode && <Badge tone="warn">Тест</Badge>}
        </div>
        <p className="muted">Использовано {data.used} из {data.limit || 0} устройств</p>
        <div className="meter meter-lg"><i style={{ width: `${data.limit ? (100 * data.used) / data.limit : 0}%` }} /></div>
        {data.can_add ? (
          <Button block icon="plus" onClick={() => { setName(`Устройство ${data.used + 1}`); setAdding(true); }}>Добавить устройство</Button>
        ) : data.limit === 0 || me.subscription.status === "expired" || me.subscription.status === "none" ? (
          <Button block icon="bolt" onClick={() => go("sub")}>Оформить подписку</Button>
        ) : (
          <p className="small muted">Все места заняты. Отзови устройство или выбери тариф с большим лимитом.</p>
        )}
      </Card>

      {data.devices.length === 0 ? (
        <Empty icon="devices" title="Пока нет устройств" text="Добавь устройство — оно получит собственную ссылку подключения." />
      ) : (
        data.devices.map((d, i) => (
          <Card key={d.id} delay={i + 1} className="device">
            <div className="device-head">
              <div className="tile-icon"><Icon name="devices" /></div>
              <div className="grow">
                <b>{d.name}</b>
                <span className="muted small">Добавлено {fmtDate(d.created_at, false)} · {d.lifetime ? "бессрочно" : `до ${fmtDate(d.expires_at, false)}`}</span>
              </div>
              <Badge tone={STATUS[d.status].tone}>{STATUS[d.status].label}</Badge>
            </div>
            <div className="device-actions">
              <Button kind="primary" icon="link" disabled={d.status !== "active"} onClick={() => void openConnection(d)}>Подключить</Button>
              <button className="icon-btn" aria-label="Переименовать" onClick={() => { setName(d.name); setRenaming(d); }}><Icon name="edit" /></button>
              <button className="icon-btn" aria-label="Заменить" onClick={() => void replace(d)}><Icon name="swap" /></button>
              <button className="icon-btn icon-danger" aria-label="Отозвать" onClick={() => void revoke(d)}><Icon name="trash" /></button>
            </div>
            <span className="mono tiny muted">ID {d.id.slice(0, 8)}{d.client_id ? ` · клиент ${d.client_id}` : ""}</span>
          </Card>
        ))
      )}

      <p className="small muted center">
        Лимит устройств — это число выданных ссылок. Копирование ссылки на другое устройство технически
        ограничивается только средствами VPN-панели, поэтому не передавай её другим.
      </p>

      <Sheet open={adding || !!renaming} onClose={() => { setAdding(false); setRenaming(null); }} title={renaming ? "Переименовать" : "Новое устройство"}>
        <input className="field" value={name} maxLength={32} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Например, iPhone" aria-label="Название устройства" />
        <Button block busy={action.busy} disabled={!name.trim()} onClick={() => void (renaming ? rename() : add())}>
          {renaming ? "Сохранить" : "Добавить"}
        </Button>
      </Sheet>

      <Sheet open={!!conn} onClose={() => setConn(null)} title={conn?.device.name ?? ""}>
        {conn?.phase === "loading" && (
          <div className="connect-anim">
            <div className="connect-rings"><span /><span /><span /><Icon name="shield" size={30} /></div>
            <b>Готовим подключение…</b>
          </div>
        )}
        {conn?.phase === "error" && <ErrorBox text={conn.error || "Не удалось получить настройки"} onRetry={() => void openConnection(conn.device)} />}
        {conn?.phase === "ready" && conn.data && (
          <div className="stack">
            {conn.data.is_test && <Badge tone="warn">Тестовые данные — это не рабочий VPN</Badge>}
            <div className="qr" dangerouslySetInnerHTML={{ __html: conn.data.qr_svg }} />
            <div className="link-box mono small">{conn.data.link}</div>
            <div className="actions">
              <Button kind="soft" icon="copy" onClick={async () => toast((await copyText(conn.data!.link)) ? "Ссылка скопирована" : "Не удалось скопировать", "ok")}>Копировать</Button>
              <Button kind="ghost" icon="link" sound={false} onClick={() => openLink(conn.data!.link)}>Открыть</Button>
            </div>
            <details className="guide">
              <summary>Инструкция по настройке</summary>
              <ol className="small">
                <li>Установи приложение:
                  <ul>{GUIDES.map((g) => <li key={g.os}><b>{g.os}:</b> {g.apps}</li>)}</ul>
                </li>
                <li>Скопируй ссылку или отсканируй QR-код в приложении («Добавить подписку» / «Импорт из буфера»).</li>
                <li>Включи VPN. Ссылка обновляет список серверов автоматически.</li>
              </ol>
            </details>
            <p className="tiny muted">Не пересылай ссылку — она привязана к этому устройству.</p>
          </div>
        )}
      </Sheet>
    </div>
  );
}
