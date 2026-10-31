import { useState } from "react";
import { call } from "../api";
import { Load, Panel, useData, useMutate } from "../components";
import { useAdmin } from "../ctx";
import type { Order } from "../../shared/types";
import { OrdersTable } from "./parts";

const STATUSES = ["", "pending", "paid", "canceled", "failed", "expired"];

export function Orders() {
  const [status, setStatus] = useState("");
  const res = useData<{ orders: Order[] }>(`/api/admin/orders${status ? `?status=${encodeURIComponent(status)}` : ""}`);
  const mutate = useMutate();
  const { go } = useAdmin();
  return (
    <Panel title="Заказы и платежи" actions={
      <select value={status} onChange={(e) => setStatus(e.target.value)}>
        {STATUSES.map((s) => <option key={s} value={s}>{s || "Все статусы"}</option>)}
      </select>
    }>
      <Load res={res}>
        {(d) => (
          <OrdersTable orders={d.orders} onUser={(id) => go("users", id)} extra={(o) =>
            o.status === "paid" && o.fulfillment_status !== "done" ? (
              <button className="btn" onClick={() => void mutate(`Повторить выдачу доступа по заказу №${o.id}`, [],
                (v) => call("POST", `/api/admin/orders/${o.id}/refulfill`, { reason: v.reason }), () => res.reload())}>
                Повторить выдачу доступа
              </button>
            ) : null
          } />
        )}
      </Load>
    </Panel>
  );
}
