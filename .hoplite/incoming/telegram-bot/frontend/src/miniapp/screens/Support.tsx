import { Icon } from "../Icon";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, ErrorBox, Skeleton, Title, ticketTone } from "../ui";
import { fmtDateTime } from "../../shared/format";
import { openExternal, openTg } from "../telegram";
import type { TicketsResp } from "../../shared/types";

export function Support() {
  const { me, open } = useApp();
  const res = useLoad<TicketsResp>("/api/support/tickets");
  if (res.loading && !res.data) return <Skeleton />;
  if (res.error || !res.data) return <ErrorBox text={res.error ?? "Ошибка"} onRetry={() => void res.reload()} />;
  const url = me?.support_url;

  return (
    <>
      <Title sub="Мы отвечаем в этом чате и в боте">Поддержка</Title>
      <div className="stack">
        <button className="btn" onClick={() => open({ type: "support_new" })}><Icon name="edit" size={18} /><span>Новое обращение</span></button>
      </div>
      <Card>
        <h2>Мои обращения</h2>
        {res.data.tickets.length === 0 && <p className="hint">Обращений пока нет.</p>}
        {res.data.tickets.map((t) => (
          <div className="ticket" key={t.id} onClick={() => open({ type: "ticket", id: t.id })}>
            <div className="row-between">
              <b>{t.number}</b>
              <Badge tone={ticketTone(t.status)}>{t.status_label}</Badge>
            </div>
            <div>{t.category_label}{t.subject ? ` · ${t.subject}` : ""}</div>
            {t.last_message_preview && <small>{t.last_message_preview}</small>}
            <small>{fmtDateTime(t.updated_at)}</small>
          </div>
        ))}
      </Card>
      {url && (
        <button className="btn btn-secondary" onClick={() => (/^https?:\/\/t\.me\//.test(url) ? openTg(url) : openExternal(url))}>
          <Icon name="help" size={18} /><span>Другой способ связи</span>
        </button>
      )}
    </>
  );
}
