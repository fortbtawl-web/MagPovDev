import { call } from "../api";
import { Icon } from "../Icon";
import { Badge, Load, Panel, Table, useData, useMutate } from "../components";
import { useAdmin } from "../ctx";
import { fmtDateTime } from "../../shared/format";
import type { AdminEntry } from "../../shared/types";

export function Admins() {
  const res = useData<{ admins: AdminEntry[] }>("/api/admin/admins");
  const { isOwner } = useAdmin();
  const mutate = useMutate();

  const add = () => void mutate("Назначить администратора", [
    { name: "query", label: "Telegram ID, @username или SV-ID", required: true },
  ], (v) => call("POST", "/api/admin/admins", { query: v.query.trim(), reason: v.reason }), () => res.reload());

  const revoke = (a: AdminEntry) => void mutate(`Отозвать права администратора ${a.user_id}`, [],
    (v) => call("POST", `/api/admin/admins/${a.user_id}/revoke`, { reason: v.reason }), () => res.reload());

  return (
    <Panel title="Администраторы" actions={isOwner && <button className="btn primary" onClick={add}><Icon name="plus" size={16} />Назначить</button>}>
      <p className="muted">
        Администраторы, заданные в серверном .env (OWNER_TELEGRAM_ID / ADMIN_IDS), меняются только там.
        {!isOwner && " Назначать и отзывать права может только владелец."}
      </p>
      <Load res={res}>
        {(d) => (
          <Table head={["Telegram ID", "SV-ID", "Имя", "Username", "Роль", "Источник", "Назначен", ""]} empty={d.admins.length === 0}>
            {d.admins.map((a) => (
              <tr key={a.user_id}>
                <td><a href={`#/users/${a.user_id}`}>{a.user_id}</a></td>
                <td className="idtag">{a.public_id ?? "—"}</td>
                <td>{a.first_name ?? "—"}</td>
                <td>{a.username ? `@${a.username}` : "—"}</td>
                <td><Badge tone={a.role === "owner" ? "ok" : "neutral"}>{a.role === "owner" ? "владелец" : "админ"}</Badge></td>
                <td>{a.source === "env" ? ".env" : "база данных"}</td>
                <td>{a.granted_at ? `${fmtDateTime(a.granted_at)}${a.granted_by ? ` (${a.granted_by})` : ""}` : "—"}</td>
                <td>{isOwner && a.source === "db" && a.role === "admin" && <button className="btn danger" onClick={() => revoke(a)}>Отозвать</button>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}
