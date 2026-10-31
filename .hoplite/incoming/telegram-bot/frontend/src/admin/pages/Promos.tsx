import { Icon } from "../Icon";
import { useState } from "react";
import { call } from "../api";
import { Badge, Load, Panel, Table, useData, useMutate } from "../components";
import { useAdmin, type Field } from "../ctx";
import { fmtDateTime, localInputToIso } from "../../shared/format";
import type { Promo, PromoActivation, PromoKind } from "../../shared/types";

const KIND_LABEL: Record<PromoKind, string> = { percent: "Скидка %", fixed: "Скидка ₽", days: "Бесплатные дни" };
const ACT_LABEL: Record<PromoActivation["status"], string> = { reserved: "Зарезервирован", used: "Использован", applied: "Применён" };

function promoStatus(p: Promo): { label: string; tone: "ok" | "warn" | "err" | "neutral" } {
  if (!p.is_active) return { label: "Отключён", tone: "neutral" };
  if (p.expires_at && new Date(p.expires_at).getTime() < Date.now()) return { label: "Истёк", tone: "err" };
  if (p.max_activations !== null && p.activations >= p.max_activations) return { label: "Исчерпан", tone: "warn" };
  return { label: "Активен", tone: "ok" };
}

const createFields: Field[] = [
  { name: "code", label: "Код (необязательно; A-Z, 0-9, _ и -, 3–32 символа)", hint: "Если пусто — сервер сгенерирует код" },
  { name: "kind", label: "Тип", type: "select", value: "percent",
    options: [{ value: "percent", label: "Скидка в процентах" }, { value: "fixed", label: "Фиксированная скидка, ₽" }, { value: "days", label: "Бесплатные дни" }] },
  { name: "value", label: "Значение", type: "number", required: true, hint: "Процент: 1–90 · рубли: сумма скидки · дни: 1–365" },
  { name: "max_activations", label: "Лимит активаций (необязательно)", type: "number" },
  { name: "expires_at", label: "Действует до (необязательно)", type: "datetime-local" },
  { name: "note", label: "Заметка (до 200 символов)" },
];

function buildBody(v: Record<string, string>) {
  const code = (v.code ?? "").trim().toUpperCase();
  if (code && !/^[A-Z0-9_-]{3,32}$/.test(code)) throw new Error("Код: A-Z, 0-9, _ и -, от 3 до 32 символов");
  const kind = v.kind as PromoKind;
  const raw = Number((v.value ?? "").replace(",", "."));
  if (!Number.isFinite(raw) || raw <= 0) throw new Error("Укажите значение");
  let value: number;
  if (kind === "fixed") {
    value = Math.round(raw * 100);
    if (value < 1) throw new Error("Сумма скидки слишком мала");
  } else {
    if (!Number.isInteger(raw)) throw new Error("Значение должно быть целым");
    value = raw;
    if (kind === "percent" && (value < 1 || value > 90)) throw new Error("Процент скидки: от 1 до 90");
    if (kind === "days" && (value < 1 || value > 365)) throw new Error("Бесплатные дни: от 1 до 365");
  }
  const body: Record<string, unknown> = { kind, value, reason: v.reason };
  if (code) body.code = code;
  const mx = (v.max_activations ?? "").trim();
  if (mx) {
    const n = Number(mx);
    if (!Number.isInteger(n) || n < 1) throw new Error("Лимит активаций — целое число от 1");
    body.max_activations = n;
  }
  if (v.expires_at) body.expires_at = localInputToIso(v.expires_at);
  const note = (v.note ?? "").trim();
  if (note.length > 200) throw new Error("Заметка не длиннее 200 символов");
  if (note) body.note = note;
  return body;
}

function Activations({ promo, onClose }: { promo: Promo; onClose: () => void }) {
  const { go } = useAdmin();
  const res = useData<{ activations: PromoActivation[] }>(`/api/admin/promos/${promo.id}/activations`);
  return (
    <Panel title={`Активации ${promo.code}`} actions={<button className="btn" onClick={onClose}>Закрыть</button>}>
      <Load res={res}>
        {(d) => (
          <Table head={["Пользователь", "Статус", "Заказ", "Создана", "Использована"]} empty={d.activations.length === 0}>
            {d.activations.map((a) => (
              <tr key={a.id}>
                <td><a href={`#/users/${a.user_id}`} onClick={(e) => { e.preventDefault(); go("users", a.user_id); }}>{a.user_public_id}</a></td>
                <td><Badge tone={a.status === "reserved" ? "warn" : "ok"}>{ACT_LABEL[a.status] ?? a.status}</Badge></td>
                <td>{a.order_id ?? "—"}</td>
                <td>{fmtDateTime(a.created_at)}</td>
                <td>{fmtDateTime(a.used_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}

export function Promos() {
  const res = useData<{ promos: Promo[] }>("/api/admin/promos");
  const mutate = useMutate();
  const [selected, setSelected] = useState<Promo | null>(null);

  const create = () => void mutate("Создать промокод", createFields,
    (v) => call("POST", "/api/admin/promos", buildBody(v)), () => res.reload());

  const toggle = (p: Promo) => void mutate(`${p.is_active ? "Отключить" : "Включить"} промокод ${p.code}`, [],
    (v) => call("POST", `/api/admin/promos/${p.id}/${p.is_active ? "disable" : "enable"}`, { reason: v.reason }), () => res.reload());

  return (
    <>
      <Panel title="Промокоды" actions={<button className="btn primary" onClick={create}><Icon name="plus" size={16} />Создать промокод</button>}>
        <Load res={res}>
          {(d) => (
            <Table head={["Код", "Тип", "Значение", "Активаций", "Действует до", "Статус", ""]} empty={d.promos.length === 0}>
              {d.promos.map((p) => {
                const st = promoStatus(p);
                return (
                  <tr key={p.id} style={{ cursor: "pointer" }} onClick={() => setSelected(p)}>
                    <td className="mono">{p.code}{p.note && <><br /><small className="muted">{p.note}</small></>}</td>
                    <td>{KIND_LABEL[p.kind]}</td>
                    <td>{p.value_label}</td>
                    <td>{p.activations} / {p.max_activations ?? "∞"}</td>
                    <td>{p.expires_at ? fmtDateTime(p.expires_at) : "бессрочно"}</td>
                    <td><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td className="row" onClick={(e) => e.stopPropagation()}>
                      <button className="btn" onClick={() => toggle(p)}>{p.is_active ? "Отключить" : "Включить"}</button>
                    </td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Load>
      </Panel>
      {selected && <Activations key={selected.id} promo={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
