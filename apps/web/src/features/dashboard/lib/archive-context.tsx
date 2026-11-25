"use client";
import { createContext, useCallback, useContext, useState, useEffect, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert } from "lucide-react";
import { HttpProvider, loadWorkspace, NoAccountsError } from "./service";
import { AccountConnection, CircularLoader } from "../components/account-connection";
import { useRouter } from "./router";
import type { ArchiveProvider, Snapshot } from "./types";

type Toast = { id: number; message: string; type: "success" | "error" | "info" };
interface ArchiveContextValue {
  data: Snapshot; service: ArchiveProvider; loading: boolean;
  notify: (message: string, type?: Toast["type"]) => void;
  perform: <T>(action: () => Promise<T>, message?: string | ((result: T) => string)) => Promise<T | undefined>;
}
const ArchiveContext = createContext<ArchiveContextValue | null>(null);

function Workspace({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const { search, pathname, navigate } = useRouter();
  const workspaceParams = new URLSearchParams(search);
  if (pathname === "/content") { workspaceParams.delete("draft"); workspaceParams.delete("view"); }
  const workspaceSearch = workspaceParams.toString();
  const [debouncedSearch, setDebouncedSearch] = useState(workspaceSearch);
  useEffect(() => { const timer = setTimeout(() => setDebouncedSearch(workspaceSearch), 200); return () => clearTimeout(timer); }, [workspaceSearch]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const query = useQuery({ queryKey: ["workspace", pathname, search.get("account"), debouncedSearch], queryFn: ({ signal }) => {
    const params = new URLSearchParams(debouncedSearch);
    if (search.get("account")) params.set("account", search.get("account")!); else params.delete("account");
    return loadWorkspace(pathname.slice(1), params, signal);
  }, placeholderData: previous => {
    const selected = search.get("account") ?? previous?.accounts?.[0]?.id;
    return selected && previous?.account.id !== selected ? undefined : previous;
  },
    refetchInterval: query => (query.state.data?.overview.job_counts?.queued || query.state.data?.overview.job_counts?.running) ? 5000 : 30000,
    refetchIntervalInBackground: false, retry: false });
  const refresh = useCallback(async () => {
    void client.invalidateQueries({ queryKey: ["workspace"] });
    void client.invalidateQueries({ queryKey: ["job"] });
    void client.invalidateQueries({ queryKey: ["attempts"] });
  }, [client]);
  const notify = useCallback((message: string, type: Toast["type"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts(previous => [...previous.slice(-2), { id, message, type }]);
    setTimeout(() => setToasts(previous => previous.filter(x => x.id !== id)), 5500);
  }, []);
  const perform: ArchiveContextValue["perform"] = useCallback(async (action, message) => {
    try { const result = await action(); if (message) notify(typeof message === "function" ? message(result) : message); return result; }
    catch (error) { notify(error instanceof Error ? error.message : "The request failed. Try again.", "error"); return undefined; }
  }, [notify]);
  if (!query.data) {
    if (query.error instanceof NoAccountsError) return <main className="onboarding-welcome"><div className="onboarding-intro"><span className="onboarding-wordmark">Archive Desk</span><h1>Your archive starts here.</h1><p>Connect Instagram to bring your account into one considered workspace.</p></div><AccountConnection onStarted={id => { navigate(`/settings?account=${id}`); void refresh(); }} /></main>;
    if (query.isError) return <main className="workspace-error" role="alert"><h1>Your workspace is unavailable</h1><p>{query.error.message}</p><button className="button button-primary" onClick={() => void query.refetch()}>Try again</button></main>;
    return <CircularLoader full label="Loading workspace" />;
  }
  const service = new HttpProvider(query.data, refresh);
  return <ArchiveContext.Provider key={query.data.account.id} value={{ data: query.data, service, loading: query.isPlaceholderData, notify, perform }}>
    {query.isFetching && <div className="workspace-refresh" aria-hidden="true"><CircularLoader label="Refreshing page" /></div>}
    {query.isError && <div role="alert" className="p-3 text-amber-300">Could not refresh. Showing the last successful response. {query.error.message}</div>}
    {children}
    <div className="toast-region" aria-live="polite" aria-atomic="false">{toasts.map(toast =>
      <div className={`toast toast-${toast.type}`} key={toast.id} role={toast.type === "error" ? "alert" : "status"}>
        <span className="toast-icon">{toast.type === "error" ? <CircleAlert size={18} /> : <Check size={18} />}</span><p>{toast.message}</p>
      </div>)}</div>
  </ArchiveContext.Provider>;
}
export function ArchiveProviderRoot({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}><Workspace>{children}</Workspace></QueryClientProvider>;
}
export function useArchive() {
  const context = useContext(ArchiveContext);
  if (!context) throw new Error("ArchiveProviderRoot is required");
  return context;
}
