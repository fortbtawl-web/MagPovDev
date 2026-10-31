import { useRef, useState } from "react";
import { publicApi, setToken } from "./api";
import { errText } from "../shared/api";
import { Icon } from "./Icon";

export function Login({ onLogin, notice }: { onLogin: () => void; notice?: string }) {
  const [tid, setTid] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"id" | "code">("id");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const id = Number(tid);
  const idOk = Number.isInteger(id) && id > 0;

  async function guarded(fn: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setErr("");
    try { await fn(); } catch (e) { setErr(errText(e)); }
    lock.current = false; setBusy(false);
  }
  const request = () => guarded(async () => {
    const r = await publicApi<{ ok: boolean; message: string }>("POST", "/api/admin/auth/request-code", { telegram_id: id });
    setMsg(r.message); setStep("code");
  });
  const verify = () => guarded(async () => {
    const r = await publicApi<{ token: string }>("POST", "/api/admin/auth/verify", { telegram_id: id, code });
    setToken(r.token);
    onLogin();
  });

  return (
    <div className="login">
      <form className="login-card" onSubmit={(e) => { e.preventDefault(); void (step === "id" ? request() : verify()); }}>
        <div className="logo"><Icon name="shield" size={28} /></div>
        <h2>SpirittVPN</h2>
        <p className="login-sub">Вход для администратора</p>
        {notice && !err && <div className="warnbox"><Icon name="alert" size={16} /><span>{notice}</span></div>}
        {step === "id" ? (
          <>
            <label>Telegram ID
              <input inputMode="numeric" autoFocus value={tid} onChange={(e) => setTid(e.target.value.trim())} placeholder="123456789" />
            </label>
            <p className="muted">Код подтверждения придёт в Telegram-бот.</p>
            <button className="btn primary block" disabled={busy || !idOk}>{busy ? "Отправляем…" : "Получить код"}</button>
          </>
        ) : (
          <>
            {msg && <div className="infobox"><Icon name="info" size={16} /><span>{msg}</span></div>}
            <label>Код из Telegram (6 цифр)
              <input className="code-input" inputMode="numeric" autoFocus maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="000000" />
            </label>
            <button className="btn primary block" disabled={busy || code.length !== 6}>{busy ? "Проверяем…" : "Войти"}</button>
            <button type="button" className="btn block" disabled={busy} onClick={() => { setStep("id"); setCode(""); setErr(""); }}>Назад</button>
          </>
        )}
        {err && <div className="alert" role="alert"><Icon name="alert" /><span className="grow">{err}</span></div>}
      </form>
    </div>
  );
}
