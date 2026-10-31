import { useState } from "react";
import { api } from "../api";
import { Card } from "../ui";
import { Icon, Logo } from "../Icon";

import { errText } from "../../shared/api";
import { haptic, openExternal, openTg } from "../telegram";
import type { Gate as GateT } from "../../shared/types";

interface Props {
  gate: GateT;
  onGate: (g: GateT) => void;
  toast: (msg: string, kind?: "ok" | "err") => void;
}

export function Gate({ gate, onGate, toast }: Props) {
  const [busy, setBusy] = useState(false);
  const [notFound, setNotFound] = useState(false);

  async function accept() {
    setBusy(true);
    try {
      const r = await api<{ gate: GateT }>("POST", "/api/gate/accept-terms", { version: gate.terms_version });
      haptic("success");
      onGate(r.gate);
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  async function check() {
    setBusy(true);
    setNotFound(false);
    try {
      const r = await api<{ gate: GateT }>("POST", "/api/gate/check-channel", {});
      if (!r.gate.allowed && !r.gate.channel_error) setNotFound(true);
      if (r.gate.allowed) haptic("success");
      onGate(r.gate);
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  const header = (
    <div className="center-screen" style={{ minHeight: 0, paddingBottom: 0 }}>
      <div className="logo"><Logo size={72} /></div>
      <h1>SpirittVPN</h1>
      <p className="hint">Твоя свобода в сети</p>
    </div>
  );

  if (!gate.terms_accepted) {
    return (
      <div className="app"><main className="content">
        {header}
        <Card>
          <p>Перед использованием сервиса ознакомьтесь с документами и примите их.</p>
          <div className="stack">
            <button className="btn btn-secondary" onClick={() => openExternal(gate.privacy_url)}><Icon name="doc" size={18} /><span>Политика конфиденциальности</span></button>
            <button className="btn btn-secondary" onClick={() => openExternal(gate.terms_url)}><Icon name="doc" size={18} /><span>Пользовательское соглашение</span></button>
            <button className="btn" disabled={busy} onClick={() => void accept()}>{busy ? "Сохраняем…" : "Принимаю условия"}</button>
          </div>
        </Card>
      </main></div>
    );
  }

  return (
    <div className="app"><main className="content">
      {header}
      <Card>
        <p>Для использования SpirittVPN необходимо подписаться на наш Telegram-канал.</p>
        <p>В канале публикуются новости сервиса, обновления и важная информация для пользователей.</p>
        <ol className="rules">
          <li>Подпишитесь на канал.</li>
          <li>Вернитесь в бота.</li>
          <li>Нажмите кнопку проверки подписки.</li>
        </ol>
        {notFound && <p className="err-text">Подписка не найдена. Подпишитесь и нажмите «Проверить» ещё раз.</p>}
        {gate.channel_error && <div className="card warn-box">{gate.channel_error}</div>}
        <div className="stack">
          {gate.channel_url && (
            <button className="btn btn-secondary" onClick={() => openTg(gate.channel_url!)}><Icon name="megaphone" size={18} /><span>Подписаться на канал</span></button>
          )}
          <button className="btn" disabled={busy} onClick={() => void check()}>{busy ? <><span className="spinner sm" role="status" aria-label="Загрузка" /><span>Проверяем подписку…</span></> : "Проверить подписку"}</button>
        </div>
      </Card>
    </main></div>
  );
}
