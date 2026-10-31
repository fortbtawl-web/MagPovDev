import { Load, Panel, Table, useData } from "../components";
import { useAdmin } from "../ctx";
import { fmtDateTime } from "../../shared/format";

export interface CoinRow {
  id: number; user_id: number; user_name?: string; amount: number; balance_after: number; kind: string; kind_label: string;
  reason: string | null; actor: string | null; created_at: string;
}

export function CoinTable({ items }: { items: CoinRow[] }) {
  const { go } = useAdmin();
  return (
    <Table head={["Дата", "Пользователь", "Сумма", "Баланс после", "Тип", "Причина"]} empty={items.length === 0}>
      {items.map((t) => (
        <tr key={t.id}>
          <td>{fmtDateTime(t.created_at)}</td>
          <td><a href={`#/users/${t.user_id}`} onClick={(e) => { e.preventDefault(); go("users", t.user_id); }}>{t.user_name ? `${t.user_name} · ` : ""}{t.user_id}</a></td>
          <td><b className={t.amount < 0 ? "err" : ""}>{t.amount > 0 ? `+${t.amount}` : t.amount}</b></td>
          <td>{t.balance_after}</td><td>{t.kind_label}</td><td className="wrap">{t.reason ?? ""}</td>
        </tr>
      ))}
    </Table>
  );
}

export function Coins() {
  const res = useData<{ items: CoinRow[] }>("/api/admin/coins/transactions?limit=200");
  return (
    <Panel title="Операции SpirCoin">
      <Load res={res}>{(d) => <CoinTable items={d.items} />}</Load>
    </Panel>
  );
}
