import { useRef, useState } from "react";
import { Icon } from "../Icon";
import { call } from "../api";
import { errText } from "../../shared/api";
import { fmtDateTime, localInputToIso, localOffset, toLocalInput } from "../../shared/format";
import type { GrantResult, Plan, Sub } from "../../shared/types";

type Mode = "extend" | "replace" | "set_end" | "lifetime" | "shorten" | "terminate" | "revoke";
type Unit = "hours" | "days" | "weeks" | "months";
type Source = "custom" | "plan" | "date";

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "extend", label: "Продлить", hint: "добавить к текущему сроку" },
  { id: "replace", label: "Новая подписка", hint: "заменить срок, считать от сейчас" },
  { id: "set_end", label: "Точная дата окончания", hint: "задать дату и время окончания" },
  { id: "lifetime", label: "Бессрочно", hint: "is_lifetime, без даты окончания" },
  { id: "shorten", label: "Сократить срок", hint: "вычесть время из текущего срока" },
  { id: "terminate", label: "Завершить досрочно", hint: "ключи останутся, но перестанут работать" },
  { id: "revoke", label: "Отозвать доступ", hint: "удалить ключи на серверах" },
];
const UNITS: { id: Unit; label: string }[] = [
  { id: "hours", label: "часы" }, { id: "days", label: "дни" }, { id: "weeks", label: "недели" }, { id: "months", label: "месяцы" },
];
const PRESETS: { label: string; amount: number; unit: Unit }[] = [
  { label: "+12 ч", amount: 12, unit: "hours" }, { label: "+1 д", amount: 1, unit: "days" }, { label: "+7 д", amount: 7, unit: "days" },
  { label: "+1 нед", amount: 1, unit: "weeks" }, { label: "+1 мес", amount: 1, unit: "months" }, { label: "+3 мес", amount: 3, unit: "months" },
];

export function GrantModal({ telegramId, sub, plans, onClose }: {
  telegramId: number; sub: Sub | null; plans: Plan[]; onClose: (changed: boolean) => void;
}) {
  const [mode, setMode] = useState<Mode>(sub ? "extend" : "replace");
  const [source, setSource] = useState<Source>("custom");
  const [amount, setAmount] = useState("30");
  const [unit, setUnit] = useState<Unit>("days");
  const [planCode, setPlanCode] = useState(plans[0]?.code ?? "");
  const [endsAt, setEndsAt] = useState(toLocalInput(null));
  const [startsAt, setStartsAt] = useState("");
  const [limit, setLimit] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [sure, setSure] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<GrantResult | null>(null);

  const durationMode = mode === "extend" || mode === "replace";
  const src: Source = mode === "set_end" ? "date" : mode === "shorten" ? "custom" : source;
  const amountN = Number(amount);
  const amountOk = Number.isInteger(amountN) && amountN >= 1 && amountN <= 100000;
  const limitOk = limit === "" || (Number.isInteger(Number(limit)) && Number(limit) >= 1 && Number(limit) <= 50);
  let durOk = true;
  if (mode === "extend" || mode === "replace" || mode === "shorten") {
    durOk = src === "custom" ? amountOk : src === "plan" ? planCode !== "" : endsAt !== "";
  } else if (mode === "set_end") durOk = endsAt !== "";
  const reasonOk = reason.trim().length >= 3 && reason.length <= 500;
  const noteOk = note.length <= 200;
  const showDevices = mode !== "terminate" && mode !== "revoke" && mode !== "shorten";
  const destructive = mode === "terminate" || mode === "revoke";
  const ok = durOk && limitOk && reasonOk && noteOk && !busy && (!destructive || sure);

  async function submit() {
    const body: Record<string, unknown> = { mode, reason: reason.trim(), notify };
    if (mode === "extend" || mode === "replace" || mode === "shorten") {
      if (src === "custom") { body.amount = amountN; body.unit = unit; }
      else if (src === "plan") body.plan_code = planCode;
      else body.ends_at = localInputToIso(endsAt);
    }
    if (mode === "set_end") body.ends_at = localInputToIso(endsAt);
    if (mode === "replace" && startsAt) body.starts_at = localInputToIso(startsAt);
    if (showDevices && limit !== "") body.device_limit = Number(limit);
    if (note.trim()) body.note = note.trim();
    if (lock.current) return;
    lock.current = true; setBusy(true); setErr(null);
    try { setResult(await call<GrantResult>("POST", `/api/admin/users/${telegramId}/grant`, body)); }
    catch (e) { setErr(errText(e)); }
    lock.current = false; setBusy(false);
  }

  if (result) {
    const v = result.vpn;
    const warn = v.failed > 0 || v.pending > 0;
    return (
      <div className="modal-bg">
        <div className="modal">
          <h3>Результат</h3>
          <div className="okbox">{result.message}</div>
          {result.subscription && (
            <p className="muted">
              Подписка: {result.subscription.status_label}, {result.subscription.is_lifetime ? "бессрочно" : `до ${fmtDateTime(result.subscription.expires_at)}`}
            </p>
          )}
          <p className="muted">VPN: синхронизировано {v.synced}, отозвано {v.revoked}, ошибок {v.failed}, в очереди {v.pending}
            {v.provisioned !== null && `, ключ ${v.provisioned ? "создан" : "не создан"}`}</p>
          {warn && <div className="warnbox">VPN-сервер не подтвердил {v.failed + v.pending} операций — будет повторено автоматически.</div>}
          <div className="row end"><button className="btn primary" onClick={() => onClose(true)}>Закрыть</button></div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(false); }}>
      <form className="modal" onSubmit={(e) => { e.preventDefault(); if (ok) void submit(); }}>
        <h3>Выдать / изменить подписку</h3>
        <p className="muted">
          Сейчас: {sub ? `${sub.status_label}, ${sub.is_lifetime ? "бессрочно" : `до ${fmtDateTime(sub.expires_at)}`}, устройств: ${sub.device_limit}` : "подписки нет"}
        </p>

        <div className="radios">
          {MODES.map((m) => (
            <label key={m.id}>
              <input type="radio" name="mode" checked={mode === m.id} onChange={() => { setMode(m.id); setSure(false); }} />
              <span><b>{m.label}</b><small>{m.hint}</small></span>
            </label>
          ))}
        </div>

        {durationMode && (
          <label>Источник срока
            <select value={source} onChange={(e) => setSource(e.target.value as Source)}>
              <option value="custom">Произвольный срок</option>
              <option value="plan">По тарифу</option>
              {mode === "replace" && <option value="date">Точная дата окончания</option>}
            </select>
          </label>
        )}

        {(src === "custom" && (durationMode || mode === "shorten")) && (
          <>
            <div className="row">
              <input type="number" min={1} max={100000} value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: 110 }} aria-label="Количество" />
              <select value={unit} onChange={(e) => setUnit(e.target.value as Unit)} aria-label="Единица">
                {UNITS.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
              </select>
            </div>
            <div className="chips">
              {PRESETS.map((p) => (
                <button type="button" key={p.label} className="btn" onClick={() => { setAmount(String(p.amount)); setUnit(p.unit); }}>{p.label}</button>
              ))}
            </div>
          </>
        )}
        {src === "plan" && durationMode && (
          <label>Тариф
            <select value={planCode} onChange={(e) => setPlanCode(e.target.value)}>
              {plans.map((p) => <option key={p.code} value={p.code}>{p.title} ({p.is_lifetime ? "бессрочно" : `${p.duration_days} дн.`}, {p.device_limit} устр.)</option>)}
            </select>
          </label>
        )}
        {src === "date" && (mode === "set_end" || mode === "replace") && (
          <label>Дата окончания (ваше время, UTC{localOffset()})
            <input type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} />
          </label>
        )}
        {mode === "replace" && (
          <label>Дата начала (необязательно, не в будущем; UTC{localOffset()})
            <input type="datetime-local" value={startsAt} max={toLocalInput(null)} onChange={(e) => setStartsAt(e.target.value)} />
          </label>
        )}
        {showDevices && (
          <label>Лимит устройств
            <input type="number" min={1} max={50} value={limit} placeholder="без изменений" onChange={(e) => setLimit(e.target.value)} />
          </label>
        )}

        <label>Причина (для аудита, обязательно, 3–500 символов)
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </label>
        <label>Комментарий для пользователя (необязательно, до 200 символов)
          <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="Компенсация, Подарок от администрации…" />
        </label>
        <label className="check"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Уведомить пользователя в боте</label>

        {destructive && (
          <div className="warnbox">
            <Icon name="alert" size={16} />
            <span>{mode === "revoke"
              ? "Ключи пользователя будут удалены на VPN-серверах, доступ прекратится сразу."
              : "Подписка будет завершена досрочно: ключи останутся, но перестанут работать."}
              <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> Понимаю последствия</label></span>
          </div>
        )}
        {err && <div className="alert" role="alert"><Icon name="alert" /><span className="grow">{err}</span></div>}
        <div className="row end">
          <button type="button" className="btn" disabled={busy} onClick={() => onClose(false)}>Отмена</button>
          <button className={destructive ? "btn danger-solid" : "btn primary"} disabled={!ok}>{busy ? "Выполняем…" : "Применить"}</button>
        </div>
      </form>
    </div>
  );
}
