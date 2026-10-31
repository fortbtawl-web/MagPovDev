import { useState } from "react";
import { call } from "../api";
import { Badge, Load, Panel, Table, useData, useMutate } from "../components";
import { useAdmin, type Field } from "../ctx";
import { Icon } from "../Icon";
import { fmtDateTime } from "../../shared/format";

interface TaskRow {
  id: number; code: string; title: string; description: string; url: string; reward: number; verify: string; chat_id: string | null;
  is_active: boolean; archived: boolean; budget_total: number | null; issued_total: number; max_completions: number | null;
  completions: number; pending: number; sort_order: number; last_check_error: string | null;
}
interface Claim { id: number; task_id: number; task_title: string; user_id: number; user_name: string; status: string; reason: string | null; created_at: string | null }

const VERIFY_OPTIONS = [
  { value: "telegram", label: "Переход по ссылке — награда сразу" },
  { value: "manual", label: "Ручная проверка модератором" },
];
const VERIFY_LABEL: Record<string, string> = { telegram: "Переход по ссылке", manual: "Ручная проверка" };
const CLAIM_LABEL: Record<string, string> = { pending: "На проверке", rewarded: "Одобрено", rejected: "Отклонено" };
const CLAIM_TONE: Record<string, "ok" | "warn" | "err" | "neutral"> = { pending: "warn", rewarded: "ok", rejected: "err" };

function taskFields(t?: TaskRow): Field[] {
  return [
    { name: "title", label: "Название", required: true, value: t?.title ?? "" },
    { name: "description", label: "Описание", type: "textarea", value: t?.description ?? "" },
    { name: "url", label: "Ссылка (https://…)", required: true, value: t?.url ?? "" },
    { name: "reward", label: "Награда, SpirCoin", type: "number", required: true, value: String(t?.reward ?? 10) },
    { name: "verify", label: "Тип задания", type: "select", value: t?.verify ?? "telegram", options: VERIFY_OPTIONS },
    { name: "chat_id", label: "Канал @username (необязательно, для справки)", value: t?.chat_id ?? "" },
    { name: "budget_total", label: "Общий бюджет наград (необязательно)", type: "number", value: t?.budget_total != null ? String(t.budget_total) : "" },
    { name: "max_completions", label: "Лимит выполнений (необязательно)", type: "number", value: t?.max_completions != null ? String(t.max_completions) : "" },
    { name: "sort_order", label: "Порядок в списке", type: "number", value: String(t?.sort_order ?? 0) },
    { name: "is_active", label: "Задание включено", type: "checkbox", value: (t ? t.is_active : true) ? "true" : "" },
  ];
}

function intOrNull(s: string, label: string): number | null {
  const v = s.trim();
  if (!v) return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) throw new Error(`${label}: целое число от 1 или пусто`);
  return n;
}

/** Собирает значения формы; при редактировании возвращает только изменённые поля. */
function buildBody(v: Record<string, string>, t?: TaskRow) {
  const reward = Number(v.reward);
  if (!Number.isInteger(reward) || reward < 1) throw new Error("Награда: целое число от 1");
  const sort = Number((v.sort_order ?? "").trim() || 0);
  if (!Number.isInteger(sort)) throw new Error("Порядок: целое число");
  const all: Record<string, unknown> = {
    title: v.title.trim(), description: (v.description ?? "").trim(), url: v.url.trim(), reward, verify: v.verify,
    chat_id: (v.chat_id ?? "").trim() || null,
    budget_total: intOrNull(v.budget_total ?? "", "Бюджет"), max_completions: intOrNull(v.max_completions ?? "", "Лимит"),
    sort_order: sort, is_active: v.is_active === "true",
  };
  if (!t) return all;
  const cur: Record<string, unknown> = {
    title: t.title, description: t.description, url: t.url, reward: t.reward, verify: t.verify, chat_id: t.chat_id,
    budget_total: t.budget_total, max_completions: t.max_completions, sort_order: t.sort_order, is_active: t.is_active,
  };
  const diff: Record<string, unknown> = {};
  for (const k of Object.keys(all)) if (all[k] !== cur[k]) diff[k] = all[k];
  if (Object.keys(diff).length === 0) throw new Error("Нечего сохранять — поля не изменились");
  return diff;
}

function Claims({ onChanged }: { onChanged: () => void }) {
  const { go } = useAdmin();
  const mutate = useMutate();
  const [status, setStatus] = useState("pending");
  const res = useData<{ items: Claim[] }>(`/api/admin/task-claims?status=${status}`);
  const done = async () => { await res.reload(); onChanged(); };

  const approve = (c: Claim) => void mutate(`Одобрить заявку #${c.id}`, [
    { name: "i", label: "Задание и пользователь", value: `${c.task_title} · ${c.user_name} (${c.user_id})`, readOnly: true },
  ], (v) => call("POST", `/api/admin/task-claims/${c.id}/approve`, { reason: v.reason }), done, "Награда начислена");
  const reject = (c: Claim) => void mutate(`Отклонить заявку #${c.id}`, [
    { name: "i", label: "Задание и пользователь", value: `${c.task_title} · ${c.user_name} (${c.user_id})`, readOnly: true },
  ], (v) => { if (v.reason.length < 3) throw new Error("Укажите причину отклонения"); return call("POST", `/api/admin/task-claims/${c.id}/reject`, { reason: v.reason }); }, done, "Заявка отклонена");

  return (
    <Panel title="Заявки на проверку" actions={
      <div className="seg" role="tablist" aria-label="Статус заявок">
        {Object.entries(CLAIM_LABEL).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={status === k} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>{l}</button>
        ))}
      </div>
    }>
      <Load res={res}>
        {(d) => (
          <Table head={["№", "Задание", "Пользователь", "Статус", "Комментарий", "Создана", ""]} empty={d.items.length === 0}>
            {d.items.map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td><td>{c.task_title}</td>
                <td><a href={`#/users/${c.user_id}`} onClick={(e) => { e.preventDefault(); go("users", c.user_id); }}>{c.user_name} · {c.user_id}</a></td>
                <td><Badge tone={CLAIM_TONE[c.status] ?? "neutral"}>{CLAIM_LABEL[c.status] ?? c.status}</Badge></td>
                <td className="wrap">{c.reason ?? ""}</td><td>{fmtDateTime(c.created_at)}</td>
                <td className="row">{c.status === "pending" && <>
                  <button className="btn primary" onClick={() => approve(c)}>Одобрить</button>
                  <button className="btn danger" onClick={() => reject(c)}>Отклонить</button>
                </>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}

export function Tasks() {
  const res = useData<{ tasks: TaskRow[] }>("/api/admin/tasks");
  const mutate = useMutate();

  const create = () => void mutate("Создать задание", taskFields(),
    (v) => call("POST", "/api/admin/tasks", { ...buildBody(v), reason: v.reason }), () => res.reload(), "Задание создано");
  const edit = (t: TaskRow) => void mutate(`Изменить задание «${t.title}»`, taskFields(t),
    (v) => call("PATCH", `/api/admin/tasks/${t.id}`, { ...buildBody(v, t), reason: v.reason }), () => res.reload(), "Задание сохранено");
  const toggle = (t: TaskRow) => void mutate(`${t.is_active ? "Выключить" : "Включить"} задание «${t.title}»`, [],
    (v) => call("PATCH", `/api/admin/tasks/${t.id}`, { is_active: !t.is_active, reason: v.reason }), () => res.reload());
  const archive = (t: TaskRow) => void mutate(`Удалить в архив задание «${t.title}»`, [
    { name: "i", label: "Что произойдёт", value: "Задание исчезнет из списка пользователей. Выданные награды и история сохранятся.", readOnly: true },
  ], (v) => call("POST", `/api/admin/tasks/${t.id}/archive`, { reason: v.reason }), () => res.reload(), "Задание в архиве");

  return (
    <>
      <Panel title="Задания" actions={<button className="btn primary" onClick={create}><Icon name="plus" size={16} />Создать задание</button>}>
        <p className="muted">Для заданий «Переход по ссылке» награда начисляется сразу при нажатии кнопки; повторно её получить нельзя.</p>
        <Load res={res}>
          {(d) => (
            <Table head={["Задание", "Награда", "Тип", "Статус", "Выполнено", "Выдано / бюджет", "Заявки", ""]} empty={d.tasks.length === 0}>
              {d.tasks.map((t) => (
                <tr key={t.id}>
                  <td className="wrap"><b>{t.title}</b><br /><small className="muted">{t.url}</small>
                    {t.last_check_error && <><br /><small className="err">{t.last_check_error}</small></>}</td>
                  <td>{t.reward}</td>
                  <td>{VERIFY_LABEL[t.verify] ?? t.verify}</td>
                  <td>{t.archived ? <Badge>В архиве</Badge> : <Badge tone={t.is_active ? "ok" : "neutral"}>{t.is_active ? "Включено" : "Выключено"}</Badge>}</td>
                  <td>{t.completions}{t.max_completions != null ? ` / ${t.max_completions}` : ""}</td>
                  <td>{t.issued_total} / {t.budget_total ?? "∞"}</td>
                  <td>{t.pending > 0 ? <Badge tone="warn">{t.pending}</Badge> : 0}</td>
                  <td className="row">{!t.archived && <>
                    <button className="btn" onClick={() => edit(t)}>Изменить</button>
                    <button className="btn" onClick={() => toggle(t)}>{t.is_active ? "Выключить" : "Включить"}</button>
                    <button className="btn danger" onClick={() => archive(t)}>В архив</button>
                  </>}</td>
                </tr>
              ))}
            </Table>
          )}
        </Load>
      </Panel>
      <Claims onChanged={() => void res.reload()} />
    </>
  );
}
