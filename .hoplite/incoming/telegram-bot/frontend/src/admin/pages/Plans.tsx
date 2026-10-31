import { call } from "../api";
import { Icon } from "../Icon";
import { Badge, Load, Panel, Table, useData, useMutate } from "../components";
import type { Field } from "../ctx";
import { fmtMoney } from "../../shared/format";
import type { Plan } from "../../shared/types";

const rub = (v: string) => Math.round(Number(v.replace(",", ".")) * 100);

function planFields(p?: Plan): Field[] {
  return [
    ...(p ? [] : [{ name: "code", label: "Код (латиница, уникальный)", required: true }]),
    { name: "title", label: "Название", required: true, value: p?.title ?? "" },
    { name: "description", label: "Описание", type: "textarea", value: p?.description ?? "" },
    { name: "price", label: "Цена, ₽", type: "number", required: true, value: p ? String(p.price_minor / 100) : "" },
    { name: "is_lifetime", label: "Бессрочный (Lifetime)", type: "checkbox", value: p?.is_lifetime ? "true" : "" },
    { name: "duration", label: "Срок, дней (для Lifetime игнорируется)", type: "number", value: p?.duration_days != null ? String(p.duration_days) : "30" },
    { name: "device_limit", label: "Лимит устройств", type: "number", required: true, value: p ? String(p.device_limit) : "1" },
    { name: "sort_order", label: "Порядок сортировки", type: "number", value: p ? String(p.sort_order) : "100" },
    { name: "is_active", label: "Тариф доступен для покупки", type: "checkbox", value: !p || p.is_active ? "true" : "" },
  ];
}

export function Plans() {
  const res = useData<{ plans: Plan[] }>("/api/admin/plans");
  const mutate = useMutate();

  const create = () => void mutate("Создать тариф", planFields(), (v) => call("POST", "/api/admin/plans", {
    code: v.code.trim(), title: v.title.trim(), description: v.description ?? "", price_minor: rub(v.price),
    duration_days: v.is_lifetime === "true" ? null : Math.trunc(Number(v.duration)),
    is_lifetime: v.is_lifetime === "true", device_limit: Math.trunc(Number(v.device_limit)),
    is_active: v.is_active === "true", sort_order: Math.trunc(Number(v.sort_order)), reason: v.reason,
  }), () => res.reload());

  const edit = (p: Plan) => void mutate(`Редактировать тариф «${p.title}»`, planFields(p), (v) => {
    const lifetime = v.is_lifetime === "true";
    const next = {
      title: v.title.trim(), description: v.description ?? "", price_minor: rub(v.price), is_lifetime: lifetime,
      duration_days: lifetime ? null : Math.trunc(Number(v.duration)), device_limit: Math.trunc(Number(v.device_limit)),
      sort_order: Math.trunc(Number(v.sort_order)), is_active: v.is_active === "true",
    };
    const changes: Record<string, unknown> = {};
    (Object.keys(next) as (keyof typeof next)[]).forEach((k) => { if (next[k] !== p[k]) changes[k] = next[k]; });
    if (Object.keys(changes).length === 0) throw new Error("Нет изменений");
    return call("PATCH", `/api/admin/plans/${p.id}`, { changes, reason: v.reason });
  }, () => res.reload());

  const toggle = (p: Plan) => void mutate(`${p.is_active ? "Отключить" : "Включить"} тариф «${p.title}»`, [],
    (v) => call("PATCH", `/api/admin/plans/${p.id}`, { changes: { is_active: !p.is_active }, reason: v.reason }), () => res.reload());

  return (
    <Panel title="Тарифы" actions={<button className="btn primary" onClick={create}><Icon name="plus" size={16} />Создать тариф</button>}>
      <Load res={res}>
        {(d) => (
          <Table head={["Код", "Название", "Цена", "Срок", "Устройств", "Порядок", "Статус", ""]} empty={d.plans.length === 0}>
            {d.plans.map((p) => (
              <tr key={p.id}>
                <td className="mono">{p.code}</td><td>{p.title}</td><td>{fmtMoney(p.price_minor)}</td>
                <td>{p.is_lifetime ? <Badge tone="ok">Lifetime</Badge> : `${p.duration_days} дн.`}</td>
                <td>{p.device_limit}</td><td>{p.sort_order}</td>
                <td><Badge tone={p.is_active ? "ok" : "neutral"}>{p.is_active ? "Активен" : "Отключён"}</Badge></td>
                <td className="row"><button className="btn" onClick={() => edit(p)}>Изменить</button>
                  <button className="btn" onClick={() => toggle(p)}>{p.is_active ? "Отключить" : "Включить"}</button></td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}
