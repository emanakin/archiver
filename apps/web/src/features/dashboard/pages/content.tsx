"use client";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ImagePlus, RotateCcw, Save } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { useRouteState } from "../lib/router";
import { Badge, Button, Card, Field, Input, Select, Tabs } from "../components/ui";
import { Notice, PageHeader, Pagination, PauseControl } from "../components/shared";
import { ContentActions } from "../components/content-actions";
import { CircularLoader } from "../components/account-connection";
import { MusicLibrary } from "../components/music-library";
import { DmInbox } from "../components/dm-inbox";

type Draft = components["schemas"]["DraftResponse"];
type Values = { kind: NonNullable<Draft["kind"]>; caption: string; music_canonical_id: string | null; fit_mode: NonNullable<Draft["fit_mode"]>; rotation: 0 | 90 | 180 | 270; overlay_text: string; text_position: "top" | "center" | "bottom"; photo_order: number[] | null; keep_playlist_music: boolean; use_source_music: boolean };
const values = (draft: Draft): Values => ({ kind: draft.kind ?? "feed_photo", caption: draft.caption ?? "", music_canonical_id: draft.music_canonical_id ?? null, fit_mode: draft.fit_mode ?? "fit", rotation: draft.rotation ?? 0, overlay_text: draft.overlay_text ?? "", text_position: draft.text_position ?? "bottom", photo_order: draft.kind === "feed_carousel" ? draft.photos.map(photo => photo.number) : null, keep_playlist_music: !!draft.playlist_music, use_source_music: false });

export function ContentPage() {
  const { data, notify } = useArchive();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<"feed_photo" | "story_photo" | "feed_reel">("feed_photo");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useRouteState("draft", "");
  const [section, setSection] = useRouteState("view", "drafts");
  const [draftPage, setDraftPage] = useState(1);
  const pageSize = 5;
  const list = useQuery({ queryKey: ["content-drafts", data.account.id, draftPage], queryFn: () => api<{ items: Draft[]; total: number }>(`/content-drafts?account_id=${data.account.id}&limit=${pageSize}&offset=${(draftPage-1)*pageSize}`), refetchInterval: 15000 });
  useEffect(() => { if (list.data && draftPage > 1 && !list.data.items.length) setDraftPage(draftPage - 1); }, [draftPage,list.data]);
  const listedDraft = list.data?.items.find(item => item.id === selected);
  const selectedDraft = useQuery({ queryKey: ["content-draft", selected], queryFn: () => api<Draft>(`/content-drafts/${selected}`), enabled: !!selected && !listedDraft, refetchInterval: 10000 });
  const draft = listedDraft ?? (selectedDraft.data?.account_id === data.account.id ? selectedDraft.data : undefined) ?? list.data?.items[0];
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["content-drafts", data.account.id] }),
    client.invalidateQueries({ queryKey: ["content-draft", selected] }),
  ]);
  return <div className="page content-page"><PageHeader title="Content" eyebrow="MAKE ROOM FOR YOUR IDEAS" description="Prepare photos, stories and Reels for your Instagram account." />
    <Tabs value={section} onValueChange={setSection} items={[{value:"drafts",label:"Drafts"},{value:"inbox",label:"DM inbox"},{value:"music",label:"Music library"}]} />
    {section === "music" ? <MusicLibrary key={data.account.id}/> : section === "inbox" ? <DmInbox key={data.account.id} openDraft={id=>{setSelected(id);setSection("drafts");void refresh();}}/> : <>
    {data.account.paused && data.account.session_state === "connected" && <Notice title="Your queue is paused" action={<PauseControl compact />}>An approved publication waits until you resume the account queue.</Notice>}
    <Card className="content-upload"><div className="settings-card-heading"><div><h2>Create a draft</h2><p>JPEG or PNG up to 8 MiB, or MP4 up to 32 MiB and 90 seconds.</p></div><ImagePlus size={23} /></div><form className="insight-form" onSubmit={async event => {
      event.preventDefault(); if (!file) return;
      const isVideo = file.type === "video/mp4" || /\.mp4$/i.test(file.name);
      if ((kind === "feed_reel") !== isVideo) { notify("Choose an MP4 for a Reel or an image for a photo draft.", "error"); return; }
      if (file.size > (isVideo ? 32 : 8) * 1024 * 1024) { notify("This file exceeds the draft size limit.", "error"); return; }
      setBusy(true);
      try {
        const asset = await api<components["schemas"]["AssetResponse"]>(`/accounts/${data.account.id}/content-assets`, { method: "POST", headers: { "Content-Type": isVideo ? "video/mp4" : file.type || "application/octet-stream" }, body: file });
        const result = await api<Draft>("/content-drafts", { method: "POST", body: JSON.stringify({ account_id: data.account.id, asset_id: asset.id, kind, idempotency_key: crypto.randomUUID() }) });
        setSelected(result.id); setFile(null); await refresh(); notify("Draft created.");
      } catch (error) { notify(error instanceof Error ? error.message : "Could not create this draft.", "error"); }
      finally { setBusy(false); }
    }}><Field label="Media file" htmlFor="content-file"><Input id="content-file" type="file" accept="image/jpeg,image/png,video/mp4,.mp4" required onChange={event => { const selected = event.target.files?.[0]; setFile(selected ?? null); if (selected && (selected.type === "video/mp4" || /\.mp4$/i.test(selected.name))) setKind("feed_reel"); else if (kind === "feed_reel") setKind("feed_photo"); }} /></Field><Field label="Draft type" htmlFor="content-kind"><Select id="content-kind" label="Draft type" value={kind} options={[{ value: "feed_photo", label: "Photo post · 4:5" }, { value: "story_photo", label: "Photo story · 9:16" }, { value: "feed_reel", label: "Reel" }]} onValueChange={value => setKind(value as typeof kind)} /></Field><Button type="submit" loading={busy} disabled={!file}>Create draft</Button></form></Card>

    <div className="content-workspace"><Card><div className="settings-card-heading"><div><h2>Your drafts</h2><p>Saved with this Instagram account.</p></div></div>{list.isPending ? <CircularLoader label="Loading drafts" /> : list.isError ? <p role="alert" className="p-5">Drafts could not be loaded. <button className="text-link" onClick={() => void list.refetch()}>Refresh drafts</button></p> : !list.data.items.length ? <p className="quiet-note p-5">No drafts yet. Add an image to get started.</p> : <div className="insight-history-list">{list.data.items.map(item => <button key={item.id} className={draft?.id === item.id ? "selected" : ""} onClick={() => setSelected(item.id)}><strong>{item.kind === "feed_reel" ? "Reel" : item.kind === "feed_carousel" ? "Photo carousel" : item.kind === "story_photo" ? "Photo story" : "Photo post"}</strong><span>{item.overlay_text || item.caption || "Untitled draft"}</span><small>Revision {item.revision} · {new Date(item.updated_at).toLocaleString([], { timeZone: data.settings.timezone })}</small><Badge>{item.status}</Badge></button>)}</div>}{(list.data?.total ?? 0) > pageSize && <Pagination page={draftPage} pageSize={pageSize} total={list.data!.total} noun="drafts" onPageChange={page=>{setSelected("");setDraftPage(page);}}/>}</Card>
      {draft ? <DraftEditor key={draft.id} draft={draft} refresh={refresh} onDiscard={() => {
        client.setQueryData<{ items: Draft[]; total:number }>(["content-drafts", data.account.id, draftPage], previous => previous && { ...previous, total:Math.max(0,previous.total-1), items: previous.items.filter(item => item.id !== draft.id) });
        client.removeQueries({ queryKey: ["content-draft", draft.id], exact: true });
        setSelected("");
        void refresh();
      }} onStoryDraft={story => {
        client.setQueryData(["content-draft", story.id], story);
        setSelected(story.id);
        void refresh();
      }} /> : <Card className="insight-detail-body"><h2>A considered first draft</h2><p>Upload an image to preview the exact saved composition. Posting is always a separate step.</p></Card>}
    </div></>}
  </div>;
}

function DraftEditor({ draft, refresh, onStoryDraft, onDiscard }: { draft: Draft; refresh: () => Promise<unknown>; onStoryDraft: (story: Draft) => void; onDiscard: () => void }) {
  const { notify } = useArchive();
  const baseline = useRef({ revision: draft.revision, values: values(draft) });
  const [form, setForm] = useState<Values>(() => values(draft));
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{url: string; loaded: boolean}>();
  const [selectedPhoto, setSelectedPhoto] = useState<number>();
  const reel = draft.kind === "feed_reel";
  const captionState = draft.caption_review?.state;
  const captionReason = draft.caption_review?.reason;
  const regenerate = async () => {
    setBusy(true);
    try {
      await api(`/content-drafts/${draft.id}/caption`, {
        method: "POST", body: JSON.stringify({ expected_revision: draft.revision }),
      });
      await refresh();
      notify("Caption analysis queued.");
    } catch (error) { notify(error instanceof Error ? error.message : "Could not regenerate the caption.", "error"); }
    finally { setBusy(false); }
  };
  const activePhoto = selectedPhoto ?? form.photo_order?.[0];
  const previewUrl = draft.photos?.find(photo => photo.number === activePhoto)?.preview_url ?? draft.preview_url;
  const playlistTrack = form.use_source_music ? draft.source_music : form.keep_playlist_music && !form.music_canonical_id ? draft.playlist_music : null;
  const reusableSource = draft.source_music && (draft.source_music.id || (draft.source_music.audio_asset_id && draft.source_music.audio_cluster_id)) ? draft.source_music : null;
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline.current.values);
  useEffect(() => {
    if (!dirty) { baseline.current = { revision: draft.revision, values: values(draft) }; setForm(values(draft)); }
  }, [draft.revision]);
  const previewReady = preview?.url === previewUrl && preview.loaded;
  const previewError = preview?.url === previewUrl && !preview.loaded;
  const change = <K extends keyof Values>(key: K, value: Values[K]) => setForm(current => ({ ...current, [key]: value }));
  const movePhoto = (index: number, offset: number) => {
    const order = [...(form.photo_order ?? [])];
    const [photo] = order.splice(index, 1);
    order.splice(index + offset, 0, photo);
    change("photo_order", order);
  };
  const makeStoryDraft = async () => {
    setBusy(true);
    try {
      const story = await api<Draft>(`/content-drafts/${draft.id}/story-draft`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: draft.revision }),
      });
      onStoryDraft(story);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not open a story draft from this photo.", "error");
    } finally {
      setBusy(false);
    }
  };
  return <Card className="draft-editor"><div className="settings-card-heading"><div><h2>Adjust your draft</h2><p>Saved revision{draft.revision} · {dirty ? "Unsaved changes" : "Preview matches the saved composition"}</p></div><Badge>{draft.status}</Badge></div><div className="draft-editor-body">
    <div className={`draft-preview ${draft.kind === "story_photo" ? "story-preview" : draft.fit_mode === "native" ? "native-preview" : ""}`}>{previewError ? <p role="alert">Preview could not be loaded. <Button variant="ghost" onClick={() => setPreview(undefined)}>Retry preview</Button></p> : reel ? <video className="w-full max-h-[32rem]" key={previewUrl} src={previewUrl} controls playsInline preload="metadata" aria-label="Saved Reel preview" onLoadedMetadata={() => setPreview({url: previewUrl, loaded: true})} onError={() => setPreview({url: previewUrl, loaded: false})} /> : <img key={previewUrl} src={previewUrl} alt={activePhoto ? `Draft photo ${activePhoto}` : "Saved draft preview"} onLoad={() => setPreview({url: previewUrl, loaded: true})} onError={() => setPreview({url: previewUrl, loaded: false})} />}</div>
    {reel && typeof draft.reel_source?.url === "string" && <p><a className="text-link" href={draft.reel_source.url} target="_blank" rel="noreferrer">Original Reel</a></p>}
    {captionState === "processing" && <p role="status">Analyzing the media and drafting a caption.</p>}
    {typeof captionReason === "string" && <p role="status">{captionReason}</p>}
    <form className="connection-form" onSubmit={async event => {
      event.preventDefault(); setBusy(true);
      try {
        const result = await api<Draft>(`/content-drafts/${draft.id}`, { method: "PATCH", body: JSON.stringify({ ...form, expected_revision: baseline.current.revision }) });
        baseline.current = { revision: result.revision, values: values(result) }; setForm(values(result)); await refresh(); notify("Draft revision saved. The preview is updated.");
      } catch (error) { notify(error instanceof Error ? error.message : "Could not save this draft.", "error"); }
      finally { setBusy(false); }
    }}><fieldset disabled={busy || draft.status !== "draft"} className="connection-form">
      {!reel && <>{form.photo_order ? <div>
        <h3>Photo order</h3>
        <ol className="carousel-draft-order">
          {form.photo_order.map((number, index) => <li key={number}>
            <Button type="button" size="sm" variant={activePhoto === number ? "outline" : "ghost"} aria-pressed={activePhoto === number} onClick={() => setSelectedPhoto(number)}>
              <img src={draft.photos.find(photo => photo.number === number)?.preview_url} alt="" loading="lazy" /> Photo {number}
            </Button>
            <Button type="button" size="sm" variant="ghost" aria-label={`Move photo ${number} up`} disabled={index === 0} onClick={() => movePhoto(index, -1)}><ArrowUp size={15} /></Button>
            <Button type="button" size="sm" variant="ghost" aria-label={`Move photo ${number} down`} disabled={index === form.photo_order!.length - 1} onClick={() => movePhoto(index, 1)}><ArrowDown size={15} /></Button>
          </li>)}
        </ol>
      </div> : draft.source_draft_id ? <p>Photo story from a published post</p> : <Field label="Publication format" htmlFor="draft-format"><Select id="draft-format" label="Publication format" value={form.kind} options={[{ value: "feed_photo", label: "Photo post · 4:5" }, { value: "story_photo", label: "Photo story · 9:16" }]} onValueChange={value => setForm(current => ({ ...current, kind: value as Values["kind"], music_canonical_id: value === "story_photo" ? null : current.music_canonical_id, use_source_music: value === "story_photo" ? false : current.use_source_music }))} /></Field>}
      {form.kind !== "story_photo" && <div className="connection-form">
        <p>Music {playlistTrack ? `${playlistTrack.title} · ${playlistTrack.artist}` : form.music_canonical_id ? (form.music_canonical_id === draft.source_music?.id ? [draft.source_music.title, draft.source_music.artist].filter(Boolean).join(" · ") || "Original track selected" : "Catalog track selected") : "None selected"}</p>
        <div className="flex flex-wrap gap-2">
          {reusableSource && <Button type="button" variant="outline" disabled={form.use_source_music || (!!reusableSource.id && form.music_canonical_id === reusableSource.id)} onClick={() => setForm(current => ({ ...current, music_canonical_id: null, keep_playlist_music: false, use_source_music: true }))}>Use original track</Button>}
          {(form.music_canonical_id || playlistTrack) && <Button type="button" variant="ghost" onClick={() => setForm(current => ({ ...current, music_canonical_id: null, keep_playlist_music: false, use_source_music: false }))}>Clear music</Button>}
        </div>
        <details>
          <summary>Enter a catalog ID</summary>
          <Field label="Instagram music track ID" htmlFor="draft-music" hint="The image preview is silent."><Input id="draft-music" inputMode="numeric" pattern="[0-9]{1,32}" maxLength={32} value={form.music_canonical_id ?? ""} onChange={event => setForm(current => ({ ...current, music_canonical_id: event.target.value || null, use_source_music: false }))} /></Field>
        </details>
      </div>}
      <Field label="Composition" htmlFor="draft-fit"><Select id="draft-fit" label="Composition" value={form.fit_mode} options={[{ value: "native", label: "Original frame · extend edges if needed" }, { value: "blend", label: "Seamless fit · blurred edges" }, { value: "fill", label: "Fill frame · centre crop" }, { value: "fit", label: "Fit whole image · dark background" }]} onValueChange={value => change("fit_mode", value as Values["fit_mode"])} /></Field>
      <Button variant="outline" type="button" onClick={() => change("rotation", (((form.rotation ?? 0) + 90) % 360) as Values["rotation"])}><RotateCcw size={15} /> Rotate left · {form.rotation ?? 0}°</Button>
      <Field label="Text on image" htmlFor="draft-overlay" hint="Optional; review the saved preview to check line breaks and characters."><Input id="draft-overlay" maxLength={180} value={form.overlay_text ?? ""} onChange={event => change("overlay_text", event.target.value)} /></Field>
      <Field label="Text position" htmlFor="draft-text-position"><Select id="draft-text-position" label="Text position" value={form.text_position ?? "bottom"} options={[{ value: "top", label: "Top" }, { value: "center", label: "Centre" }, { value: "bottom", label: "Bottom" }]} onValueChange={value => change("text_position", value as Values["text_position"])} /></Field>
      </>}
      <Field label={form.kind === "story_photo" ? "Story caption metadata" : "Post caption"} htmlFor="draft-caption" hint={form.kind === "story_photo" ? "Use Text on image for visible story text." : "Up to2200 characters."}><textarea className="input draft-caption" id="draft-caption" maxLength={2200} rows={5} value={form.caption ?? ""} onChange={event => change("caption", event.target.value)} /></Field>
      <div className="flex flex-wrap gap-3"><Button type="submit" loading={busy} disabled={!dirty}><Save size={15} /> Save draft changes</Button><Button type="button" variant="ghost" disabled={!dirty} onClick={() => { baseline.current = { revision: draft.revision, values: values(draft) }; setForm(values(draft)); }}>Discard unsaved changes</Button><Button type="button" variant="outline" disabled={dirty} onClick={() => void regenerate()}>Regenerate caption</Button></div>
    </fieldset></form>
    {dirty && <p className="insight-note">The preview shows the last saved revision. Save changes to review the new image before publishing.</p>}
    <ContentActions draft={draft} dirty={dirty} previewReady={previewReady} previewError={previewError} refresh={refresh} onDiscard={onDiscard} />
    {draft.status === "published" && draft.kind === "feed_photo" && draft.media_id && <Button type="button" variant="outline" loading={busy} onClick={() => void makeStoryDraft()}>Make a story draft</Button>}
  </div></Card>;
}
