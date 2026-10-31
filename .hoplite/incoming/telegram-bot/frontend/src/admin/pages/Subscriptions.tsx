import { useState } from "react";
import { Badge, Load, Panel, Table, tone, useData } from "../components";
import { useAdmin } from "../ctx";
import { fmtDate } from "../../shared/format";
import type { Sub, VpnOp } from "../../shared/types";
import { OpsTable } from "./parts";

const STATUSES = ["", "pending", "active", "expired", "suspended", "revoked", "failed"];

export function Subscriptions() {
  const [status, setStatus] = useState("");
  const subs = useData<{ subscriptions: Sub[] }>(`/api/admin/subscriptions${status ? `?status=${status}` : ""}`);
  const ops = useData<{ operations: VpnOp[] }>("/api/admin/vpn-operations?problems=true");
  const { go } = useAdmin();
  return (
    <>
      <Panel title="Подписки" actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s || "Все статусы"}</option>)}
        </select>
      }>
        <Load res={subs}>
          {(d) => (
            <Table head={["ID", "Пользователь", "Статус", "Тариф", "Занято мест", "Источник", "Начало", "Окончание"]} empty={d.subscriptions.length === 0}>
              {d.subscriptions.map((s) => (
                <tr key={s.id} className="click" onClick={() => go("users", s.user_id)}>
                  <td>{s.id}</td><td>{s.user_id}</td><td><Badge tone={tone(s.status)}>{s.status_label}</Badge></td>
                  <td>{s.plan_code ?? "—"}</td><td>{s.devices_used ?? 0} из {s.device_limit}</td><td>{s.source}</td>
                  <td>{fmtDate(s.starts_at)}</td><td>{s.is_lifetime ? "Бессрочно" : fmtDate(s.expires_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Load>
      </Panel>
      <Panel title="Ошибки синхронизации с VPN"><Load res={ops}>{(d) => <OpsTable ops={d.operations} />}</Load></Panel>
    </>
  );
}
