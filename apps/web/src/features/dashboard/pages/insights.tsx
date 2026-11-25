"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, BarChart3, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { Badge, Button, Card, Field, Input, Select } from "../components/ui";
import { Notice, PageHeader, PauseControl } from "../components/shared";
import { CircularLoader } from "../components/account-connection";
import { PostWatches } from "../components/post-watches";
import { RelationshipTest } from "../components/relationship-test";
import { SourceForm } from "../components/source-form";

type Check = components["schemas"]["InsightResponse"];
type Kind = components["schemas"]["InsightCreate"]["kind"];
const choices: { value: Kind; label: string }[] = [
  { value: "profile", label: "Profile counts" }, { value: "relationship", label: "Our relationship" },
  { value: "following", label: "Accounts they follow" }, { value: "followers", label: "Their followers" },
  { value: "posts", label: "Recent posts" }, { value: "stories", label: "Active stories" },
  { value: "likers", label: "Post likers" },
];

export function InsightsPage() {
  const { data, notify } = useArchive();
  const { search } = useRouter();
  const client = useQueryClient();
  const [kind, setKind] = useState<Kind>(choices.find(choice => choice.value === search.get("observation"))?.value ?? "profile");
  const [target, setTarget] = useState(search.get("target") ?? "");
  const [limit, setLimit] = useState(5);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [watchTarget, setWatchTarget] = useState<string>();
  const [source, setSource] = useState<{type: "profile" | "post"; value: string}>();
  const connected = data.account.session_state === "connected" && !data.account.restricted;
  const history = useQuery({ queryKey: ["insights", data.account.id], queryFn: () => api<{ items: Check[]; total: number }>(`/insight-checks?account_id=${data.account.id}`),
    refetchInterval: query => query.state.data?.items.some(check => ["queued", "running"].includes(check.state)) ? 1500 : 15000 });
  const check = history.data?.items.find(item => item.id === selected) ?? history.data?.items[0];
  const items = check?.items ?? [];
  const latestPost = items.reduce<(typeof items)[number] | undefined>((latest, item) => {
    if (item.kind !== "post" || !item.posted_at) return latest;
    return !latest?.posted_at || Date.parse(item.posted_at) > Date.parse(latest.posted_at) ? item : latest;
  }, undefined);
  const queue = async (cursor?: string | null, previous?: Check) => {
    setBusy(true);
    try {
      const result = await api<Check>("/insight-checks", { method: "POST", body: JSON.stringify({ account_id: data.account.id,
        kind: previous?.kind ?? kind, target: previous?.target ?? (target.trim() || data.account.username),
        requested_limit: previous?.requested_limit ?? limit, cursor: cursor || null, idempotency_key: crypto.randomUUID() }) });
      setSelected(result.id);
      await client.invalidateQueries({ queryKey: ["insights", data.account.id] });
      await client.invalidateQueries({ queryKey: ["workspace"] });
      notify(data.account.paused ? "Observation queued. Resume the queue when ready to run it." : "Observation queued.");
    } catch (error) { notify(error instanceof Error ? error.message : "Could not queue this observation.", "error"); }
    finally { setBusy(false); }
  };
  const importPhoto = async (checkId: string, itemId: string) => {
    setBusy(true);
    try {
      await api(`/insight-checks/${checkId}/import-photo`, { method: "POST", body: JSON.stringify({ item_id: itemId }) });
      await client.invalidateQueries({ queryKey: ["workspace"] });
      notify(data.account.paused ? "Photo import saved. Resume the queue to create its draft." : "Photo import saved. The draft will appear in Content.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not import the photo.", "error");
    } finally {
      setBusy(false);
    }
  };
  return <div className="page insights-page">
    <PageHeader title="Insights" eyebrow="OBSERVE YOUR CORNER" description="Account lists, post activity and accessible stories, with clear freshness and limits." />
    {!connected && <Notice title="Connect Instagram to run observations">Saved results remain available. New observations are paused until the account is connected.</Notice>}
    {data.account.paused && connected && <Notice title="Your queue is paused" action={<PauseControl compact />}>You can prepare an observation below. It starts after you resume the queue.</Notice>}
    <Card className="insight-form-card"><div className="settings-card-heading"><div><h2>Explore an account or post</h2><p>Each check is bounded by the selected sample size and your request allowance.</p></div><BarChart3 size={23} /></div>
      <form className="insight-form" onSubmit={event => { event.preventDefault(); void queue(); }}>
        <Field label="Observation" htmlFor="insight-kind"><Select id="insight-kind" label="Observation" value={kind} options={choices} onValueChange={value => { setKind(value as Kind); setTarget(""); }} /></Field>
        <Field label={kind === "likers" ? "Instagram post URL" : "Instagram account"} htmlFor="insight-target" hint={kind === "likers" ? "Use the full Instagram post or Reel link." : "Leave blank to inspect the connected account."}>
          <Input id="insight-target" value={target} onChange={event => setTarget(event.target.value)} placeholder={kind === "likers" ? "https://www.instagram.com/p/…/" : `@${data.account.username}`} required={kind === "likers"} maxLength={512} />
        </Field>
        <Field label="Sample size" htmlFor="insight-limit" hint="One bounded page, up to 20 records."><Input id="insight-limit" type="number" min={1} max={20} value={limit} onChange={event => setLimit(Number(event.target.value))} /></Field>
        <Button type="submit" loading={busy} disabled={!connected}>Queue observation <ArrowUpRight size={15} /></Button>
      </form>
    </Card>
    <div className="insight-results-grid">
      <Card className="insight-history"><div className="settings-card-heading"><div><h2>Saved observations</h2><p>Results stay with this account.</p></div><Button variant="ghost" size="icon" aria-label="Refresh observations" onClick={() => void history.refetch()}><RefreshCw size={16} /></Button></div>
        {history.isPending ? <CircularLoader label="Loading observations" /> : history.isError ? <p role="alert" className="p-5">Observations could not be loaded. Use Refresh to try again.</p> : !history.data.items.length ? <p className="quiet-note p-5">No observations yet. Start with a profile or recent-post check.</p> : <div className="insight-history-list">{history.data.items.map(item => <button key={item.id} className={check?.id === item.id ? "selected" : ""} onClick={() => setSelected(item.id)}><strong>{choices.find(choice => choice.value === item.kind)?.label}</strong><span>{item.target}</span><small>{new Date(item.created_at).toLocaleString([], { timeZone: data.settings.timezone })}</small><Badge tone={item.state === "succeeded" ? "green" : ["failed", "blocked"].includes(item.state) ? "red" : "neutral"}>{item.state}</Badge></button>)}</div>}
      </Card>
      <Card className="insight-detail"><div className="settings-card-heading"><div><h2>{check ? choices.find(choice => choice.value === check.kind)?.label : "An honest view of your data"}</h2><p>{check ? check.target : "No invented growth or engagement metrics."}</p></div>{check && <Badge>{check.state === "succeeded" ? "Observed" : check.state}</Badge>}</div>
        <div className="insight-detail-body">{!check ? <p>Choose a saved observation to see its records, timestamp and completeness information.</p> : <>
          {["queued", "running"].includes(check.state) && <div className="flex items-center gap-3"><CircularLoader label="Observation pending" /><p>{check.state === "queued" ? "Waiting for the account queue." : "Reading the bounded response from Instagram."}</p></div>}
          {check.error_code && <Notice tone="red" title="This observation did not complete">{check.error_code}. No missing or inaccessible data is treated as an empty success.</Notice>}
          {check.observed_at && <p className="quiet-note">Observed {new Date(check.observed_at).toLocaleString([], { timeZone: data.settings.timezone })} · {check.requests_used} HTTP requests</p>}
          {check.profile && <><h3>@{check.profile.username}</h3><div className="insight-counts">{[["Followers", check.profile.followers], ["Following", check.profile.following], ["Posts", check.profile.posts]].map(([label, value]) => <div key={label as string}><span>{label}</span><strong>{typeof value === "number" ? value.toLocaleString() : "Unavailable"}</strong></div>)}</div></>}
          {check.relationship && <div className="insight-relationship"><Badge>{check.relationship.state}</Badge><p>Follows you back: {check.relationship.follows_back === null ? "Unknown" : check.relationship.follows_back ? "Yes" : "No"}</p></div>}
          {check.observed_at && !(check.items?.length) && !check.profile && !check.relationship && <p>No records were returned in this bounded response.</p>}
          {check.kind === "followers" && check.state === "succeeded" && <Button variant="outline" size="sm" onClick={() => setSource({type: "profile", value: check.target})}>Save follower source</Button>}
          <div className="insight-items">{items.map(item => <article key={item.id}><div><strong>{item.kind === "user" ? `@${item.username}` : item.media_type || item.kind}</strong>{item.id === latestPost?.id && <Badge>Latest observed post</Badge>}{item.posted_at && <small>{new Date(item.posted_at).toLocaleString([], { timeZone: data.settings.timezone })}</small>}</div>{item.caption && <p>{item.caption}</p>}{item.kind !== "user" && <p className="quiet-note">Likes: {item.likes ?? "Unavailable"} · Comments: {item.comments ?? "Unavailable"}</p>}{item.url && <a className="text-link" href={item.url} target="_blank" rel="noreferrer">Open in Instagram <ArrowUpRight size={13} /></a>}{item.kind === "post" && item.url && <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!connected || busy} onClick={() => { setKind("likers"); setTarget(item.url!); document.getElementById("insight-target")?.focus(); }}>Inspect post likers</Button>
            <Button variant="outline" size="sm" disabled={!connected} onClick={() => { setWatchTarget(item.url!); document.getElementById("watch-target")?.focus(); }}>Watch post likers</Button>
            <Button variant="ghost" size="sm" onClick={() => setSource({type: "post", value: item.url!})}>Save liker source</Button>
            {item.media_type === "Photo" && <Button variant="ghost" size="sm" disabled={!connected || busy} onClick={() => void importPhoto(check.id, item.id)}>Import photo as draft</Button>}
          </div>}</article>)}</div>
          <p className="insight-note">{check.note}</p>
          {check.next_cursor && check.state === "succeeded" && <Button variant="outline" loading={busy} disabled={!connected} onClick={() => void queue(check.next_cursor, check)}>Observe next page</Button>}
        </>}</div>
      </Card>
    </div>
    <PostWatches initialTarget={watchTarget ?? (check?.kind === "likers" ? check.target : undefined)} />
    <RelationshipTest check={check} />
    <SourceForm open={!!source} onOpenChange={open => { if (!open) setSource(undefined); }} initialType={source?.type} initialValue={source?.value} />
  </div>;
}
