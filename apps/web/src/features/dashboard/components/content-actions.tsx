"use client";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { Badge, Button, ConfirmDialog, Field, Input, Modal } from "./ui";

type Draft = components["schemas"]["DraftResponse"];
type Job = components["schemas"]["JobResponse"];

function verificationReason(job: Job) {
  if (job.state === "running") return "Instagram is processing the earlier media action. The queue is waiting for its result.";
  const track = (job.payload.snapshot as Partial<Draft> | undefined)?.playlist_music;
  if (typeof job.payload.verification_detail === "string") {
    if (track?.title && job.payload.verification_detail.includes("music")) {
      return `Instagram returned the post, but its readback did not confirm “${track.title}”${track.artist ? ` by ${track.artist}` : ""}. Open the blocking publication and verify the existing post.`;
    }
    return job.payload.verification_detail;
  }
  if (job.payload.media_id && job.kind.startsWith("publish_")) {
    return `Instagram returned media ID ${job.payload.media_id}, but verification is incomplete.${track?.title ? ` Check the approved track “${track.title}” on the published post.` : " Check the published media in Instagram."}`;
  }
  return "Archive Desk sent the action to Instagram and could not confirm its outcome. Check Instagram before verifying the earlier action.";
}

export function ContentActions({ draft, dirty, previewReady, previewError, refresh, onDiscard }: { draft: Draft; dirty: boolean; previewReady: boolean; previewError: boolean; refresh: () => Promise<unknown>; onDiscard: () => void }) {
  const { data, notify } = useArchive();
  const client = useQueryClient();
  const { navigate } = useRouter();
  const [review, setReview] = useState<"publish" | "caption" | "remove" | "discard" | null>(null);
  const [caption, setCaption] = useState(draft.caption ?? "");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const operations = useQuery({ queryKey: ["content-operations", data.account.id], queryFn: () => api<components["schemas"]["ContentOperations"]>(`/content-operations?account_id=${data.account.id}`), refetchInterval: query => query.state.data?.items.some(job => ["queued", "running"].includes(job.state)) ? 1500 : 15000 });
  useEffect(() => { if (operations.data) void refresh(); }, [operations.dataUpdatedAt]);
  useEffect(() => setCaption(draft.caption ?? ""), [draft.revision]);
  const uncertain = operations.data?.blocking_job ?? operations.data?.items.find(job => job.kind !== "reconcile_content" && job.dispatch_started_at && ["unknown", "blocked", "running"].includes(job.state));
  const jobs = operations.data?.items.filter(job => job.payload.draft_id === draft.id) ?? [];
  if (uncertain?.payload.draft_id === draft.id && !jobs.some(job => job.id === uncertain.id)) jobs.unshift(uncertain);
  const pending = jobs.some(job => ["queued", "running"].includes(job.state));
  const connectionReason = data.account.restricted ? "Instagram restricted this account. Check its status in Settings."
    : data.account.session_state !== "connected" ? "Reconnect the Instagram account in Settings."
    : data.connection?.transport_mode !== "live" ? "The workspace needs live publishing transport."
    : !data.connection.last_validated_at ? "Verify the connected session in Settings." : null;
  const ready = !connectionReason;
  const story = draft.kind === "story_photo";
  const carousel = draft.kind === "feed_carousel";
  const reel = draft.kind === "feed_reel";
  const missingCarouselPhotos = carousel && (draft.photos?.length ?? 0) < 2;
  const reviewReasons = [
    connectionReason, dirty && "Save or discard the edits to review the saved revision.",
    !previewReady && (previewError ? "Retry the saved preview above." : "Waiting for the saved preview to load."),
    operations.isPending && "Checking publication history.",
    operations.isError && "Publication history could not be loaded. Retry the history check.",
    missingCarouselPhotos && "A carousel requires at least two photos to publish.",
    reel && draft.caption_review?.state === "processing" && "Wait for caption analysis.",
    reel && !draft.caption.trim() && "Save a caption before publishing this Reel.",
    pending && "A media action for this draft is already in the queue.",
    uncertain && verificationReason(uncertain),
  ].filter((value): value is string => typeof value === "string");
  const publication = reel ? "Reel" : story ? "photo story" : carousel ? `carousel of ${draft.photos.length} photos` : "photo post";
  const discard = async () => {
    setBusy(true);
    try {
      await api(`/content-drafts/${draft.id}?revision=${draft.revision}`, { method: "DELETE" });
      setReview(null);
      onDiscard();
      await client.invalidateQueries({ queryKey: ["dm-submissions", data.account.id] });
      notify("Draft deleted from Content.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not delete this draft.", "error");
    } finally {
      setBusy(false);
    }
  };
  const action = async (path: string, body: unknown, message: string) => {
    setBusy(true);
    try {
      await api(path, { method: "POST", body: JSON.stringify(body) }); setReview(null);
      await client.invalidateQueries({ queryKey: ["content-operations", data.account.id] });
      await refresh(); await client.invalidateQueries({ queryKey: ["workspace"] });
      notify(data.account.paused ? `${message} Resume the queue when ready.` : message);
    } catch (error) { notify(error instanceof Error ? error.message : "Could not prepare this media action.", "error"); }
    finally { setBusy(false); }
  };
  return <div className="draft-publication-controls">
    <h3>{draft.status === "published" ? "Your published media" : draft.status === "archived" ? "Publication archived" : draft.status === "deleted" ? "Publication removed" : "Review and publish"}</h3>
    {draft.status === "draft" && <><p>Review this {publication} and caption before publishing to @{data.account.username}.</p>
      {!!reviewReasons.length && <div role="status" id="publication-hold"><strong>Publication is on hold</strong><ul>{reviewReasons.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      <Button aria-describedby={reviewReasons.length ? "publication-hold" : undefined} disabled={busy || !!reviewReasons.length} onClick={() => { setRightsConfirmed(false); setReview("publish"); }}>Review publication</Button></>}
    {draft.status !== "draft" && connectionReason && <p>{connectionReason}</p>}
    {draft.status !== "draft" && uncertain && <p role="status">{verificationReason(uncertain)}</p>}
    {uncertain && typeof uncertain.payload.draft_id === "string" && uncertain.payload.draft_id !== draft.id && <Button variant="outline" onClick={() => navigate(`/content?draft=${encodeURIComponent(String(uncertain.payload.draft_id))}`)}>Open the blocking draft</Button>}
    {draft.status === "published" && <><p>Instagram media ID {draft.media_id}.</p>{!reel && <div className="flex flex-wrap gap-2">
      {!story && !carousel && <Button variant="outline" disabled={busy || pending || !!uncertain || !ready} onClick={() => setReview("caption")}>Edit published caption</Button>}
      <Button variant="outline" disabled={busy || pending || !!uncertain || !ready} onClick={() => setReview("remove")}>{story ? "Review story removal" : "Review post removal"}</Button>
    </div>}</>}
    {draft.status === "published" && jobs.some(j=>j.payload.music_status==="unverified") && <p className="insight-note">Publication verified. Instagram did not report the soundtrack on the returned post.</p>}
    {draft.status === "deleted" && <p>Publication removed from Instagram.</p>}
    {draft.status === "archived" && <p>Find this post in your Instagram archive.</p>}
    {["draft", "failed", "cancelled", "deleted"].includes(draft.status) && <Button variant="ghost" disabled={busy || pending} onClick={() => setReview("discard")}>Delete draft</Button>}
    {["failed", "cancelled"].includes(draft.status) && <p>This publication was held before confirmation. Review its job; create a new draft only after checking that Instagram did not receive a publication.</p>}
    {operations.isError && <Button variant="outline" onClick={() => void operations.refetch()}>Retry publication history</Button>}
    {jobs.map(job => <article key={job.id} className="content-operation"><div className="flex flex-wrap gap-2"><strong>{job.kind.replaceAll("_", " ")}</strong><Badge tone={job.state === "succeeded" ? "green" : ["unknown", "blocked", "failed"].includes(job.state) ? "amber" : "neutral"}>{job.state}</Badge></div>{job.error && <p>{job.error}</p>}{job.state === "queued" && <Button variant="ghost" disabled={busy} onClick={() => void action(`/jobs/${job.id}/cancel`, {}, "Queued media action cancelled.")}>Cancel queued action</Button>}{job.kind !== "reconcile_content" && job.dispatch_started_at && ["unknown", "blocked"].includes(job.state) && <form className="connection-form" onSubmit={event => { event.preventDefault(); void action(`/content-drafts/${draft.id}/reconcile`, { job_id: job.id, media_ref: reference || null }, "Read-only outcome check queued."); }}><p>Check the official Instagram app. This reads the actual media without repeating the upload or change. A linked publication must be the exact content you approved. An inaccessible story does not prove a deletion.</p>{!draft.media_id && <Field label="Actual published Instagram URL or media ID" htmlFor={`media-ref-${job.id}`}><Input id={`media-ref-${job.id}`} value={reference} onChange={event => setReference(event.target.value)} required /></Field>}<Button type="submit" variant="outline" disabled={busy || pending}>Verify outcome without reposting</Button></form>}</article>)}
    {!!jobs.length && <Button variant="ghost" onClick={() => navigate("/queue?tab=review")}>Open media jobs in queue</Button>}
    <ConfirmDialog open={review === "publish"} onOpenChange={open => { if (!open) setReview(null); }} title={`Publish revision ${draft.revision} to @${data.account.username}?`} description={`Approve this ${publication} with the saved composition and caption. Approval adds this revision to your account queue.`} confirmLabel="Approve this publication" loading={busy} disabled={!!reviewReasons.length || draft.status !== "draft" || (reel && !rightsConfirmed)} onConfirm={() => void action(`/content-drafts/${draft.id}/publish`, { expected_revision: draft.revision, reviewed: true, ...(reel ? { rights_confirmed: rightsConfirmed } : {}) }, "Publication approved and queued.")}>
      {reel ? <video className="publication-review-image" src={draft.preview_url} controls playsInline aria-label="Reel publication preview" /> : carousel ? <ol className="publication-review-carousel">{draft.photos.map(photo => <li key={photo.number}><img src={photo.preview_url} alt={`Photo ${photo.number}`} /></li>)}</ol> : <img className="publication-review-image" src={draft.preview_url} alt="Publication revision preview" />}
      <p className="publication-review-caption">{draft.caption || "No caption"}</p>
      {reel && <label className="flex items-center gap-2"><input type="checkbox" checked={rightsConfirmed} onChange={event => setRightsConfirmed(event.target.checked)} /> I have permission to publish this video and its audio.</label>}
      {draft.playlist_music && <p>{draft.playlist_music.title} · {draft.playlist_music.artist}</p>}
      {draft.music_canonical_id && <p>{draft.source_music?.id === draft.music_canonical_id && draft.source_music.title ? [draft.source_music.title, draft.source_music.artist].filter(Boolean).join(" · ") : `Instagram catalog track ${draft.music_canonical_id}`}</p>}
    </ConfirmDialog>
    <Modal open={review === "caption"} onOpenChange={open => { if (!open) setReview(null); }} title="Change your published caption" description={`Review the replacement caption for media ${draft.media_id} on @${data.account.username}. This does not replace the image.`}><form className="connection-form" onSubmit={event => { event.preventDefault(); void action(`/content-drafts/${draft.id}/media-action`, { expected_revision: draft.revision, reviewed: true, operation: "edit_caption", caption, idempotency_key: crypto.randomUUID() }, "Caption change approved and queued."); }}><Field label="Replacement post caption" htmlFor="published-caption"><textarea id="published-caption" className="input draft-caption" value={caption} maxLength={2200} rows={5} onChange={event => setCaption(event.target.value)} /></Field><Button type="submit" loading={busy} disabled={!ready || pending || !!uncertain}>Approve caption change</Button></form></Modal>
    <ConfirmDialog open={review === "remove"} onOpenChange={open => { if (!open) setReview(null); }} title={`Remove this published photo ${story ? "story" : "post"}?`} description={`The worker checks ownership again, then removes media ${draft.media_id} from @${data.account.username}. Your draft remains in Content.`} confirmLabel={story ? "Approve story removal" : "Approve post removal"} destructive loading={busy} disabled={!ready || pending || !!uncertain} onConfirm={() => void action(`/content-drafts/${draft.id}/media-action`, { expected_revision: draft.revision, reviewed: true, operation: story ? "delete_story" : "delete_post", idempotency_key: crypto.randomUUID() }, "Publication removal approved and queued.")} />
    <ConfirmDialog open={review === "discard"} onOpenChange={open => { if (!open) setReview(null); }} title="Delete this draft?" description="Remove the draft from Content. Its original files and action history are retained." confirmLabel="Delete draft" destructive loading={busy} disabled={pending} onConfirm={() => void discard()} />
  </div>;
}
