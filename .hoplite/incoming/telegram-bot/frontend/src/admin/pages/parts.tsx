import { Badge, Table, tone } from "../components";
import { fmtDateTime, fmtJson } from "../../shared/format";
import type { AdminNode, Audit, Order, VpnOp } from "../../shared/types";

export function NodesTable({ nodes }: { nodes: AdminNode[] }) {
  return (
    <Table head={["Сервер", "Код", "Активен", "API", "Проверка", "Клиентов на ноде", "Ключей в БД", "Последняя ошибка"]} empty={nodes.length === 0}>
      {nodes.map((n) => (
        <tr key={n.id}>
          <td>{n.flag} {n.name}</td>
          <td>{n.code}</td>
          <td>{n.is_active ? "Да" : "Нет"}</td>
          <td>{n.health_ok === null ? <Badge>нет данных</Badge> : n.health_ok ? <Badge tone="ok">доступен</Badge> : <Badge tone="err">недоступен</Badge>}</td>
          <td>{fmtDateTime(n.health_at)}</td>
          <td>{n.remote_clients ?? "—"}</td>
          <td>{n.active_devices}</td>
          <td className="wrap err">{n.last_error ?? ""}</td>
        </tr>
      ))}
    </Table>
  );
}

export function OpsTable({ ops }: { ops: VpnOp[] }) {
  return (
    <Table head={["ID", "Операция", "Статус", "Устройство", "Нода", "Попыток", "Ошибка", "След. попытка", "Кто", "Создана"]} empty={ops.length === 0}>
      {ops.map((o) => (
        <tr key={o.id}>
          <td>{o.id}</td><td>{o.op_type}</td><td><Badge tone={tone(o.status)}>{o.status}</Badge></td>
          <td>{o.device_id ?? "—"}</td><td>{o.node_id ?? "—"}</td><td>{o.attempts}</td>
          <td className="wrap err">{o.last_error ?? ""}</td><td>{fmtDateTime(o.next_retry_at)}</td>
          <td>{o.actor ?? ""}</td><td>{fmtDateTime(o.created_at)}</td>
        </tr>
      ))}
    </Table>
  );
}

export function AuditTable({ items }: { items: Audit[] }) {
  return (
    <Table head={["Время", "Админ", "Источник", "Действие", "Объект", "Причина", "Было", "Стало"]} empty={items.length === 0}>
      {items.map((a) => (
        <tr key={a.id}>
          <td>{fmtDateTime(a.created_at)}</td><td>{a.admin_id ?? "—"}</td><td>{a.source}</td><td>{a.action}</td>
          <td>{a.entity_type}{a.entity_id ? `#${a.entity_id}` : ""}</td>
          <td className="wrap">{a.reason ?? ""}</td>
          <td className="wrap mono">{fmtJson(a.old_value)}</td><td className="wrap mono">{fmtJson(a.new_value)}</td>
        </tr>
      ))}
    </Table>
  );
}

export function OrdersTable({ orders, extra, onUser }: { orders: Order[]; extra?: (o: Order) => React.ReactNode; onUser?: (id: number) => void }) {
  return (
    <Table head={["№", "Пользователь", "Тариф", "Сумма", "Статус", "Платёж", "Выдача", "Создан", "Оплачен", ""]} empty={orders.length === 0}>
      {orders.map((o) => (
        <tr key={o.id}>
          <td>{o.id}</td>
          <td>{onUser ? <a href={`#/users/${o.user_id}`} onClick={() => onUser(o.user_id)}>{o.user_id}</a> : o.user_id}</td>
          <td>{o.plan_title}</td><td>{o.amount_label}</td>
          <td><Badge tone={tone(o.status)}>{o.status_label}</Badge></td>
          <td className="wrap">{o.payment ? `${o.payment.provider}: ${o.payment.status}${o.payment.is_test ? " (тест)" : ""}${o.payment.confirmed_at ? ` · подтв. ${fmtDateTime(o.payment.confirmed_at)}` : ""}${o.payment.error ? ` · ${o.payment.error}` : ""}` : "—"}</td>
          <td><Badge tone={tone(o.fulfillment_status)}>{o.fulfillment_status}</Badge>{o.fulfillment_error && <div className="err wrap">{o.fulfillment_error}</div>}</td>
          <td>{fmtDateTime(o.created_at)}</td><td>{fmtDateTime(o.paid_at)}</td>
          <td>{extra?.(o)}</td>
        </tr>
      ))}
    </Table>
  );
}
