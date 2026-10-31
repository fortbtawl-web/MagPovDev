import { Load, Panel, useData } from "../components";
import type { Audit as A } from "../../shared/types";
import { AuditTable } from "./parts";

export function Audit() {
  const res = useData<{ items: A[] }>("/api/admin/audit");
  return <Load res={res}>{(d) => <Panel title="Журнал действий" actions={<button className="btn" onClick={() => void res.reload()}>Обновить</button>}><AuditTable items={d.items} /></Panel>}</Load>;
}
