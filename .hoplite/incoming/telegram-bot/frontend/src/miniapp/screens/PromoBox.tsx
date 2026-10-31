import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { errText } from "../../shared/api";
import { haptic } from "../telegram";
import type { PromoActivateResp } from "../../shared/types";

export function PromoBox({ onDone }: { onDone?: () => void }) {
  const { toast, reloadMe } = useApp();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  async function activate() {
    const c = code.trim();
    if (!c || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<PromoActivateResp>("POST", "/api/promo/activate", { code: c });
      haptic("success");
      setMsg({ text: r.result.message, ok: true });
      toast(r.result.message);
      setCode("");
      await reloadMe();
      onDone?.();
    } catch (e) {
      haptic("error");
      setMsg({ text: errText(e), ok: false });
    }
    setBusy(false);
  }

  return (
    <form onSubmit={(e) => { e.preventDefault(); void activate(); }}>
      <div className="stack">
        <label className="field"><input value={code} maxLength={32} placeholder="Введите промокод" autoCapitalize="characters" autoComplete="off"
          onChange={(e) => setCode(e.target.value.toUpperCase())} /></label>
        <button className="btn" disabled={busy || !code.trim()}>{busy ? "Проверяем…" : "Активировать"}</button>
      </div>
      {msg && <p className={msg.ok ? "ok-text" : "err-text"}>{msg.text}</p>}
    </form>
  );
}
