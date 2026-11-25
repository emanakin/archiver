"use client";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { Badge, Button, Card, Field, Input, Modal, Select } from "./ui";

type Watch = components["schemas"]["WatchResponse"];
type Observation = components["schemas"]["LikerResponse"];

export function PostWatches({ initialTarget }: { initialTarget?: string }) {
  const { data, notify } = useArchive();
  const client = useQueryClient();
  const [target, setTarget] = useState(initialTarget ?? "");
  const [interval, setInterval] = useState(60);
  const [size, setSize] = useState(5);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Watch | null>(null);
  const [busy, setBusy] = useState(false);
  const connected = data.account.session_state === "connected" && !data.account.restricted;
  useEffect(() => { if (initialTarget) setTarget(initialTarget); }, [initialTarget]);
  const watches = useQuery({ queryKey: ["post-watches", data.account.id], queryFn: () => api<{ items: Watch[] }>(`/insight-watches?account_id=${data.account.id}`), refetchInterval: 15000 });
  const watch = watches.data?.items.find(item => item.id === selected) ?? watches.data?.items[0];
  const observed = useQuery({ queryKey: ["post-watch-observations", watch?.id], queryFn: () => api<{ items: Observation[]; total: number }>(`/insight-watches/${watch!.id}/observations`), enabled: !!watch, refetchInterval: watch?.enabled ? 15000 : false });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["post-watches", data.account.id] });
    await client.invalidateQueries({ queryKey: ["post-watch-observations"] });
  };
  const update = async (item: Watch, enabled: boolean, minutes = item.interval_minutes, sample = item.sample_size) => {
    setBusy(true);
    try {
      await api(`/insight-watches/${item.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: item.version, enabled, interval_minutes: minutes, sample_size: sample }) });
      setEditing(null); await refresh(); notify(enabled ? "Post polling enabled. It runs while the account queue is resumed." : "Post polling paused.");
    } catch (error) { notify(error instanceof Error ? error.message : "Could not update this watch.", "error"); }
    finally { setBusy(false); }
  };
  return <Card className="post-watches"><div className="settings-card-heading"><div><h2>Post-liker watch</h2><p>Periodic samples with first and last observation times.</p></div><Badge>Polling</Badge></div>
    <div className="insight-detail-body"><p>Instagram does not provide a complete live like-event stream through this client. Newly observed means first seen by this watch, not the time someone liked the post. Missing users are never marked as unliked.</p>
      <form className="watch-create-form" onSubmit={async event => {
        event.preventDefault(); setBusy(true);
        try {
          const result = await api<Watch>("/insight-watches", { method: "POST", body: JSON.stringify({ account_id: data.account.id, target, interval_minutes: interval, sample_size: size }) });
          setSelected(result.id); await refresh(); notify("Post watch saved. Enable polling when ready.");
        } catch (error) { notify(error instanceof Error ? error.message : "Could not save this watch.", "error"); }
        finally { setBusy(false); }
      }}>
        <Field label="Post to watch" htmlFor="watch-target"><Input id="watch-target" type="url" required value={target} onChange={event => setTarget(event.target.value)} placeholder="https://www.instagram.com/p/…/" /></Field>
        <Field label="Refresh interval (minutes)" htmlFor="watch-interval" hint="5–1440 minutes; counts toward the account request allowance."><Input id="watch-interval" type="number" min={5} max={1440} value={interval} onChange={event => setInterval(Number(event.target.value))} required /></Field>
        <Field label="Liker sample size" htmlFor="watch-size"><Input id="watch-size" type="number" min={1} max={20} value={size} onChange={event => setSize(Number(event.target.value))} required /></Field>
        <Button type="submit" loading={busy} disabled={!connected}>Create paused watch</Button>
      </form>
      {watches.isError && <p role="alert">Post watches could not be loaded.</p>}
      {!!watches.data?.items.length && <>
        <Field label="Saved post watch" htmlFor="saved-watch"><Select id="saved-watch" label="Saved post watch" value={watch?.id ?? ""} options={watches.data.items.map(item => ({ value: item.id, label: item.target }))} onValueChange={setSelected} /></Field>
        {watch && <div className="watch-status"><Badge tone={watch.enabled ? "green" : "neutral"}>{watch.enabled ? "Polling enabled" : "Paused"}</Badge><p>Every {watch.interval_minutes} minutes · up to {watch.sample_size} likers per sample</p><p>Last successful observation: {watch.last_success_at ? new Date(watch.last_success_at).toLocaleString([], { timeZone: data.settings.timezone }) : "Not observed yet"}</p><div className="flex flex-wrap gap-3"><Button variant="outline" disabled={busy || (!watch.enabled && !connected)} onClick={() => void update(watch, !watch.enabled)}>{watch.enabled ? "Pause polling" : "Enable polling"}</Button><Button variant="ghost" disabled={busy} onClick={() => setEditing({ ...watch })}>Edit watch</Button></div></div>}
        {observed.isError ? <p role="alert">Liker observations could not be loaded.</p> : observed.data?.items.length ? <div className="watch-observations">{observed.data.items.map(item => <article key={item.target_id}><strong>@{item.username}</strong><Badge>{item.baseline ? "Baseline sample" : "Newly observed"}</Badge><small>First observed: {new Date(item.first_seen_at).toLocaleString([], { timeZone: data.settings.timezone })}<br />Last observed: {new Date(item.last_seen_at).toLocaleString([], { timeZone: data.settings.timezone })}</small></article>)}<p className="quiet-note">Showing the latest {observed.data.items.length} of {observed.data.total} observed accounts.</p></div> : <p className="quiet-note">No liker observations yet. Enable polling and resume the account queue to run the first sample.</p>}
      </>}
    </div>
    <Modal open={!!editing} onOpenChange={open => { if (!open) setEditing(null); }} title="Edit post watch" description="Changing settings invalidates queued samples from the earlier version.">
      {editing && <form className="connection-form" onSubmit={event => { event.preventDefault(); void update(editing, editing.enabled, editing.interval_minutes, editing.sample_size); }}><Field label="Refresh interval (minutes)" htmlFor="edit-watch-interval"><Input id="edit-watch-interval" type="number" min={5} max={1440} value={editing.interval_minutes} onChange={event => setEditing({ ...editing, interval_minutes: Number(event.target.value) })} required /></Field><Field label="Liker sample size" htmlFor="edit-watch-size"><Input id="edit-watch-size" type="number" min={1} max={20} value={editing.sample_size} onChange={event => setEditing({ ...editing, sample_size: Number(event.target.value) })} required /></Field><Button type="submit" loading={busy}>Save watch settings</Button></form>}
    </Modal>
  </Card>;
}
