import { useState } from "react";
import { call } from "../api";
import { Badge, Load, Panel, Table, useData, useMutate, type Tone } from "../components";
import { useAdmin, type Field } from "../ctx";
import { Icon } from "../Icon";
import { fmtDateTime, localInputToIso, toLocalInput } from "../../shared/format";

interface Code {
  id: number; code: string; name: string; description: string; note: string | null; days: number; max_activations: number | null;
  activations: number; starts_at: string | null; ends_at: string | null; is_active: boolean; archived: boolean; status: string;
}
interface Act { user_id: number; name: string; username: string | null; status: string; days: number; activated_at: string | null; expires_at: string | null }

const STATUS: Record<string, { label: string; tone: Tone }> = {
  active: { label: "Активен", tone: "ok" }, disabled: { label: "Отключён", tone: "neutral" }, scheduled: { label: "Ожидает старта", tone: "warn" },
  expired: { label: "Истёк", tone: "err" }, exhausted: { label: "Исчерпан", tone: "warn" }, archived: { label: "В архиве", tone: "neutral" },
};
const FILTERS = [["all", "Все"], ...Object.entries(STATUS).map(([k, v]) => [k, v.label])];

function baseFields(c?: Code): Field[] {
  return [
    { name: "name", label: "Название", required: true, value: c?.name ?? "" },
    { name: "description", label: "Описание (необязательно)", type: "textarea", value: c?.description ?? "" },
    { name: "days", label: "Дней подписки", type: "number", required: true, value: String(c?.days ?? 5) },
    { name: "max_activations", label: "Лимит активаций (необязательно)", type: "number", value: c?.max_activations != null ? String(c.max_activations) : "" },
    { name: "starts_at", label: "Начало действия (необязательно)", type: "datetime-local", value: toLocalInput(c?.starts_at) },
    { name: "ends_at", label: "Окончание действия (необязательно)", type: "datetime-local", value: toLocalInput(c?.ends_at) },
    { name: "note", label: "Заметка для администраторов (необязательно)", value: c?.note ?? "" },
    { name: "is_active", label: "Код включён", type: "checkbox", value: (c ? c.is_active : true) ? "true" : "" },
  ];
}

function values(v: Record<string, string>): Record<string, unknown> {
  const days = Number(v.days);
  if (!Number.isInteger(days) || days < 1) throw new Error("Дней подписки: целое число от 1");
  const mx = (v.max_activations ?? "").trim();
  if (mx && (!Number.isInteger(Number(mx)) || Number(mx) < 1)) throw new Error("Лимит активаций: целое число от 1 или пусто");
  return {
    name: v.name.trim(), description: (v.description ?? "").trim(), days, max_activations: mx ? Number(mx) : null,
    starts_at: v.starts_at ? localInputToIso(v.starts_at) : null, ends_at: v.ends_at ? localInputToIso(v.ends_at) : null,
    note: (v.note ?? "").trim() || null, is_active: v.is_active === "true",
  };
}

function Activations({ code, onClose }: { code: Code; onClose: () => void }) {
  const { go } = useAdmin();
  const res = useData<{ items: Act[] }>(`/api/admin/trial-codes/${code.id}/activations`);
  return (
    <Panel title={`Активации кода ${code.code}`} actions={<button className="btn" onClick={onClose}>Закрыть</button>}>
      <Load res={res}>
        {(d) => (
          <Table head={["Пользователь", "Статус", "Дней", "Активирован", "Действует до"]} empty={d.items.length === 0}>
            {d.items.map((a, i) => (
              <tr key={i}>
                <td><a href={`#/users/${a.user_id}`} onClick={(e) => { e.preventDefault(); go("users", a.user_id); }}>{a.name}{a.username ? ` @${a.username}` : ""} · {a.user_id}</a></td>
                <td>{a.status}</td><td>{a.days}</td><td>{fmtDateTime(a.activated_at)}</td><td>{fmtDateTime(a.expires_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}

export function TrialCodes() {
  const [status, setStatus] = useState("all");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [sel, setSel] = useState<Code | null>(null);
  const res = useData<{ codes: Code[] }>(`/api/admin/trial-codes?status=${status}&q=${encodeURIComponent(applied)}`);
  const mutate = useMutate();
  const reload = () => res.reload();

  const create = () => void mutate("Создать тестовый код", [
    { name: "code", label: "Код (необязательно, 3–32 символа)", hint: "Если пусто — код сгенерируется" }, ...baseFields(),
  ], (v) => call("POST", "/api/admin/trial-codes", { ...values(v), ...(v.code?.trim() ? { code: v.code.trim() } : {}), reason: v.reason }), reload, "Код создан");

  const bulk = () => void mutate("Создать несколько тестовых кодов", [
    { name: "count", label: "Сколько кодов (1–200)", type: "number", required: true, value: "10" },
    { name: "length", label: "Длина кода (6–16)", type: "number", value: "8" },
    { name: "prefix", label: "Префикс (необязательно, до 12 символов)" },
    ...baseFields(),
  ], (v) => {
    const count = Number(v.count), length = Number(v.length || 8);
    if (!Number.isInteger(count) || count < 1 || count > 200) throw new Error("Количество: от 1 до 200");
    if (!Number.isInteger(length) || length < 6 || length > 16) throw new Error("Длина: от 6 до 16");
    return call<{ codes: Code[] }>("POST", "/api/admin/trial-codes/bulk", { ...values(v), count, length, prefix: (v.prefix ?? "").trim(), reason: v.reason });
  }, reload, "Коды созданы");

  const edit = (c: Code) => void mutate(`Изменить код ${c.code}`, baseFields(c),
    (v) => call("PATCH", `/api/admin/trial-codes/${c.id}`, { ...values(v), reason: v.reason }), reload, "Код сохранён");
  const toggle = (c: Code) => void mutate(`${c.is_active ? "Выключить" : "Включить"} код ${c.code}`, [],
    (v) => call("PATCH", `/api/admin/trial-codes/${c.id}`, { is_active: !c.is_active, reason: v.reason }), reload);
  const archive = (c: Code) => void mutate(`Отправить в архив код ${c.code}`, [],
    (v) => call("POST", `/api/admin/trial-codes/${c.id}/archive`, { reason: v.reason }), reload, "Код в архиве");
  const remove = (c: Code) => void mutate(`Удалить код ${c.code}`, [
    { name: "i", label: "Что произойдёт", value: "Код будет удалён без возможности восстановления.", readOnly: true },
  ], (v) => call("DELETE", `/api/admin/trial-codes/${c.id}`, { reason: v.reason }), async () => { setSel(null); await reload(); }, "Код удалён");

  return (
    <>
      <Panel title="Тестовые коды" actions={<>
        <button className="btn" onClick={bulk}>Создать пачку</button>
        <button className="btn primary" onClick={create}><Icon name="plus" size={16} />Создать код</button>
      </>}>
        <div className="seg" role="tablist" aria-label="Фильтр по статусу">
          {FILTERS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={status === k} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>{l}</button>
          ))}
        </div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
          <input placeholder="Поиск по коду или названию" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn">Найти</button>
        </form>
        <Load res={res}>
          {(d) => (
            <Table head={["Код", "Название", "Дней", "Активаций", "Действует", "Статус", ""]} empty={d.codes.length === 0}>
              {d.codes.map((c) => {
                const st = STATUS[c.status] ?? { label: c.status, tone: "neutral" as Tone };
                return (
                  <tr key={c.id} className="click" onClick={() => setSel(c)}>
                    <td className="mono">{c.code}</td>
                    <td className="wrap">{c.name}{c.note && <><br /><small className="muted">{c.note}</small></>}</td>
                    <td>{c.days}</td><td>{c.activations} / {c.max_activations ?? "∞"}</td>
                    <td>{c.starts_at ? `с ${fmtDateTime(c.starts_at)}` : ""}{c.starts_at && c.ends_at ? " " : ""}{c.ends_at ? `до ${fmtDateTime(c.ends_at)}` : (c.starts_at ? "" : "бессрочно")}</td>
                    <td><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td className="row" onClick={(e) => e.stopPropagation()}>
                      {!c.archived && <>
                        <button className="btn" onClick={() => edit(c)}>Изменить</button>
                        <button className="btn" onClick={() => toggle(c)}>{c.is_active ? "Выключить" : "Включить"}</button>
                        <button className="btn" onClick={() => archive(c)}>В архив</button>
                      </>}
                      <button className="btn danger" onClick={() => remove(c)}>Удалить</button>
                    </td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Load>
      </Panel>
      {sel && <Activations key={sel.id} code={sel} onClose={() => setSel(null)} />}
    </>
  );
}
