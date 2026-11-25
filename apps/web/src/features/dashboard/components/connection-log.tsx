"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";

type Log = components["schemas"]["ConnectionLog"];
type LogEvent = components["schemas"]["ConnectionLogEvent"];

const stages: Record<string, string> = {
  login: "after the password request",
  entrypoint: "on the verification screen",
  code_entry: "on the code screen",
  after_approved: "after you approved on your phone",
  after_approved_code_entry: "on the code screen after approval",
  after_code: "after you reported a code prompt",
  after_code_code_entry: "on the code screen after your code report",
};

function describe({ event, detail }: LogEvent): { title: string; extra?: string[] } {
  const value = (key: string) => detail[key];
  switch (event) {
    case "owner_connection_started": return { title: "Connection started" };
    case "request_started": return { title: `Instagram request #${String(value("ordinal") ?? "?")}` };
    case "owner_code_requested": return { title: "Waiting for you in the browser" };
    case "owner_code_received": return { title: "You responded in the browser" };
    case "owner_choice": return { title: value("choice") === "code" ? "You reported: Instagram is asking for a code" : "You reported: approved on your phone" };
    case "verification_screens": {
      const stage = String(value("stage") ?? "");
      const screens = Array.isArray(value("screens")) ? (value("screens") as string[]) : [];
      return {
        title: `Instagram screens ${stages[stage] ?? stage}${value("session") ? " · signed-in session returned" : ""}`,
        extra: screens.length ? screens : ["No screen names returned"],
      };
    }
    case "completed": return { title: `Finished: ${String(value("state") ?? "")}${value("code") ? ` (${String(value("code"))})` : ""}` };
    default: return { title: event.replace(/_/g, " ") };
  }
}

export function ConnectionLog({ connectionId, live }: { connectionId: string; live: boolean }) {
  const log = useQuery({
    queryKey: ["connection-log", connectionId],
    queryFn: () => api<Log>(`/account-connections/${connectionId}/log`),
    refetchInterval: live ? 2000 : false,
  });
  return <details className="connection-log">
    <summary>Connection log{log.data ? ` · ${log.data.request_count} Instagram request${log.data.request_count === 1 ? "" : "s"}` : ""}</summary>
    {log.isLoading && <p>Loading log…</p>}
    {log.isError && <p className="connection-error" role="alert">Could not load the connection log. <button className="text-link" onClick={() => void log.refetch()}>Retry</button></p>}
    {log.data && (log.data.events.length === 0 ? <p>No activity recorded yet.</p> : <ol>
      {log.data.events.map((item, index) => {
        const { title, extra } = describe(item);
        return <li key={`${item.at}-${index}`}>
          <time dateTime={item.at}>{new Date(item.at).toLocaleTimeString()}</time>
          <span>{title}</span>
          {extra && <ul>{extra.map(name => <li key={name}><code>{name}</code></li>)}</ul>}
        </li>;
      })}
    </ol>)}
    <p className="connection-log-note">Shows request counts, Instagram screen names and your choices only. Passwords, codes and session data are never recorded.</p>
  </details>;
}
