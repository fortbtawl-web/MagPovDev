import { useEffect, useRef, useState } from "react";
import type { Field, FormResult } from "./ctx";
import { Icon } from "./Icon";

const DANGER = /отозв|удал|блокир|заблок|возврат|отмен|сторн|завершить|выключ|админист|владел|сократ|списан|массов|останов/i;

export function FormModal({ title, fields, reasonOptional = false, onClose }: { title: string; fields: Field[]; reasonOptional?: boolean; onClose: (v: FormResult | null) => void }) {
  const [vals, setVals] = useState<FormResult>(() => Object.fromEntries(fields.map((f) => [f.name, f.value ?? ""])));
  const [reason, setReason] = useState("");
  const done = useRef(false);
  const set = (k: string, v: string) => setVals({ ...vals, [k]: v });
  const reasonOk = reasonOptional || reason.trim().length >= 3;
  const fieldsOk = fields.every((f) => {
    if (!f.required) return true;
    return f.type === "checkbox" ? vals[f.name] === "true" : (vals[f.name] ?? "").trim() !== "";
  });
  const danger = DANGER.test(title);

  const close = (v: FormResult | null) => { if (done.current) return; done.current = true; onClose(v); };
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") close(null); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) close(null); }}>
      <form className="modal" role="dialog" aria-modal="true" aria-label={title}
        onSubmit={(e) => { e.preventDefault(); if (reasonOk && fieldsOk) close({ ...vals, reason: reason.trim() }); }}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button type="button" className="icon-btn" aria-label="Закрыть" onClick={() => close(null)}><Icon name="close" /></button>
        </div>
        <div className={danger ? "warnbox" : "infobox"}>
          <Icon name={danger ? "alert" : "info"} size={16} />
          <span>{danger
            ? "Действие применяется сразу и может затронуть пользователя. Оно будет записано в журнал с вашей причиной."
            : reasonOptional
            ? "Действие применяется сразу и записывается в журнал."
            : "Изменение применяется сразу и записывается в журнал вместе с указанной причиной."}</span>
        </div>
        {fields.map((f) => (
          <label key={f.name} className={f.type === "checkbox" ? "check" : ""}>
            {f.label}
            {f.type === "select" ? (
              <select value={vals[f.name]} onChange={(e) => set(f.name, e.target.value)}>
                {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            ) : f.type === "textarea" ? (
              <textarea rows={f.readOnly ? 4 : 3} readOnly={f.readOnly} value={vals[f.name]} onChange={(e) => set(f.name, e.target.value)} />
            ) : f.type === "checkbox" ? (
              <input type="checkbox" checked={vals[f.name] === "true"} onChange={(e) => set(f.name, e.target.checked ? "true" : "")} />
            ) : (
              <input type={f.type ?? "text"} readOnly={f.readOnly} value={vals[f.name]} step={f.type === "number" ? "any" : undefined} onChange={(e) => set(f.name, e.target.value)} />
            )}
            {f.hint && <small className="muted">{f.hint}</small>}
          </label>
        ))}
        <label>{reasonOptional ? "Причина (необязательно)" : "Причина (обязательно, от 3 символов)"}
          <textarea rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder={reasonOptional ? "Можно оставить пустым" : "Почему вы делаете это изменение?"} />
        </label>
        <div className="row end">
          <button type="button" className="btn" onClick={() => close(null)}>Отмена</button>
          <button className={danger ? "btn danger-solid" : "btn primary"} disabled={!reasonOk || !fieldsOk}>Подтвердить</button>
        </div>
      </form>
    </div>
  );
}
