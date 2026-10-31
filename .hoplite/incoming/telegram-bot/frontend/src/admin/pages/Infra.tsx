import { useState } from "react";
import { call } from "../api";
import { Badge, Load, Panel, Table, useData } from "../components";
import { useAdmin } from "../ctx";
import { errText } from "../../shared/api";
import type { AdminNode, VpnOp } from "../../shared/types";
import { NodesTable, OpsTable } from "./parts";

interface CheckResult { code: string; ok: boolean; detail: string; clients: number | null }

export function Infra() {
  const nodes = useData<{ nodes: AdminNode[] }>("/api/admin/nodes");
  const ops = useData<{ operations: VpnOp[] }>("/api/admin/vpn-operations");
  const { notify } = useAdmin();
  const [results, setResults] = useState<CheckResult[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    try {
      const r = await call<{ results: CheckResult[] }>("POST", "/api/admin/nodes/check");
      setResults(r.results);
      await Promise.all([nodes.reload(), ops.reload()]);
    } catch (e) { notify(errText(e), "err"); }
    setBusy(false);
  }

  return (
    <>
      <Panel title="Ноды" actions={<button className="btn primary" disabled={busy} onClick={() => void check()}>{busy ? "Проверяем…" : "Проверить сейчас"}</button>}>
        <Load res={nodes}>{(d) => <NodesTable nodes={d.nodes} />}</Load>
      </Panel>
      {results && (
        <Panel title="Результат проверки">
          <Table head={["Нода", "Результат", "Детали", "Клиентов"]} empty={results.length === 0}>
            {results.map((r) => (
              <tr key={r.code}><td>{r.code}</td><td><Badge tone={r.ok ? "ok" : "err"}>{r.ok ? "OK" : "Ошибка"}</Badge></td><td className="wrap">{r.detail}</td><td>{r.clients ?? "—"}</td></tr>
            ))}
          </Table>
        </Panel>
      )}
      <Panel title="История операций (создание, отзыв, синхронизация)">
        <Load res={ops}>{(d) => <OpsTable ops={d.operations} />}</Load>
      </Panel>
    </>
  );
}
