"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { Badge, Button, Card, Field, Modal, Select } from "./ui";

type Inbox = components["schemas"]["InboxResponse"];
type Submission = components["schemas"]["SubmissionResponse"];
type Job = components["schemas"]["JobResponse"];
type Playlist = components["schemas"]["PlaylistResponse"];

const captionStages = { vision: "Photo analysis", ocr: "Printed text recognition", text_scan: "Printed text scan", writing: "Caption writing", revision: "Caption rewrite", fallback: "Local archive caption", result: "Caption check" };
const captionReasons: Record<string, string> = {
  timeout: "Model timed out",
  service_error: "Model service unavailable",
  invalid_response: "Invalid model response",
  incomplete_response: "Incomplete model response",
  empty_response: "Empty model response",
  archive_fallback: "Used a local archive line after the model could not supply a usable caption",
  no_context: "No photo description or source caption available",
  model_unconfigured: "Caption model is missing",
  no_valid_caption: "Caption did not pass the format and originality checks",
  literal_photo_description: "Caption reads like photo notes and needs an editorial rewrite",
  generic_caption: "Caption was too generic",
  unsupported_date: "The writer added a year absent from the source",
  unsupported_quote: "The writer introduced a quotation absent from the source",
  unsupported_subject: "The writer misidentified a named work in the source",
  no_printed_text: "Printed text could not be read",
  source_context_used: "Used the shared post for context",
  vision_fallback: "Used the photo description as a fallback",
};
const musicStates: Record<string, string> = {
  matched: "Matched from your playlist",
  source_matched: "Reused the source post’s track",
  named_matched: "Matched a song named in the post",
  saved_audio_matched: "Picked from saved Instagram audio",
  no_match: "No matching title and artist",
  missing_ids: "Matching result had no usable feed music ID",
  ambiguous: "Multiple matching recordings",
  already_used: "Track already used",
  search_failed: "Instagram search failed",
  budget_exhausted: "Request allowance exhausted",
};

function CaptionProgress({ item }: { item: Submission }) {
  const run = item.caption_run;
  if (!run) return null;
  const result = run.stages.result;
  const running = Object.entries(captionStages).find(([key]) => run.stages[key]?.state === "running");
  const label = item.state === "running" && running ? running[1] + " in progress"
    : result?.state === "complete" ? "Caption generated"
    : result?.state === "failed" ? "Caption needs review" : "Caption generation";
  return <details>
    <summary>{label}</summary>
    <p>{run.style_references} style {run.style_references === 1 ? "reference" : "references"} · {run.has_source_caption ? "Source caption available" : "No source caption"}</p>
    <ul>{Object.entries(captionStages).map(([key, name]) => {
      const stage = run.stages[key];
      if (!stage) return null;
      const detail = stage.reason ? captionReasons[stage.reason] : undefined;
      return <li key={key}><strong>{name}</strong> · {stage.state === "running" && item.state !== "running" ? "Interrupted" : detail ?? stage.state}{stage.model ? ` · ${stage.model}` : ""}{typeof stage.duration_ms === "number" ? ` · ${(stage.duration_ms / 1000).toFixed(1)} s` : ""}</li>;
    })}</ul>
  </details>;
}

export function DmInbox({ openDraft }: { openDraft: (id: string) => void }) {
  const { data, notify } = useArchive();
  const client = useQueryClient();
  const accountId = data.account.id;
  const path = `/accounts/${accountId}/dm-inbox`;
  const [choice, setChoice] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [confirmDirect, setConfirmDirect] = useState(false);
  const senders = (data.accounts ?? []).filter(item => item.mode === "live");
  const approved = useQuery({
    queryKey: ["dm-inboxes", accountId],
    queryFn: () => api<Inbox[]>(`/accounts/${accountId}/dm-inboxes`),
    refetchInterval: 8000,
  });
  const external = Array.isArray(approved.data) ? approved.data.filter(item => !senders.some(sender => sender.instagram_id === item.sender_id)) : [];
  const selectedInstagramId = choice?.startsWith("sender:") ? choice.slice(7) : senders.find(item => item.id === choice)?.instagram_id;
  const senderQuery = selectedInstagramId ? `?sender_id=${encodeURIComponent(selectedInstagramId)}` : "";
  const inbox = useQuery({ queryKey: ["dm-inbox", accountId, selectedInstagramId], queryFn: () => api<Inbox | null>(path + senderQuery), refetchInterval: 8000 });
  const inboxSenderId = inbox.data?.sender_id;
  const playlistPath = `/accounts/${accountId}/spotify-playlist`;
  const playlist = useQuery({ queryKey: ["spotify-playlist", accountId], queryFn: () => api<Playlist | null>(playlistPath), refetchInterval: 15000 });
  const submissions = useQuery({
    queryKey: ["dm-submissions", accountId, offset],
    queryFn: () => api<{ items: Submission[]; total: number }>(`/dm-submissions?account_id=${accountId}&limit=8&offset=${offset}`),
    refetchInterval: inbox.data?.enabled ? 5000 : false,
  });
  const polls = useQuery({
    queryKey: ["dm-polls", accountId, inbox.data?.sender_id],
    queryFn: () => api<{ items: Job[] }>(`/jobs?account_id=${accountId}&kind=dm_poll&limit=1${inbox.data?.sender_id ? `&dm_sender_id=${encodeURIComponent(inbox.data.sender_id)}` : ""}`),
    enabled: !!inbox.data?.enabled,
    refetchInterval: 4000,
  });
  const savedSender = senders.find(item => (inbox.data?.sender_id && item.instagram_id === inbox.data.sender_id) || item.username === inbox.data?.sender_username)?.id ?? (inbox.data?.sender_id ? `sender:${inbox.data.sender_id}` : undefined);
  const sender = choice ?? savedSender ?? "none";
  const poll = polls.data?.items.find(job => !job.payload?.sender_id || job.payload.sender_id === inbox.data?.sender_id);
  const pending = poll?.state === "queued" || poll?.state === "running";
  const connected = data.account.session_state === "connected" && !data.account.restricted;

  const act = async (url: string, method: "PUT" | "POST" | "PATCH", body: object, message: string) => {
    setBusy(true);
    try {
      await api(url, { method, body: JSON.stringify(body) });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["dm-inbox", accountId] }),
        client.invalidateQueries({ queryKey: ["dm-inboxes", accountId] }),
        client.invalidateQueries({ queryKey: ["dm-submissions", accountId] }),
        client.invalidateQueries({ queryKey: ["dm-polls", accountId] }),
        client.invalidateQueries({ queryKey: ["spotify-playlist", accountId] }),
        client.invalidateQueries({ queryKey: ["workspace"] }),
      ]);
      notify(message);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not update the DM inbox.", "error");
    } finally {
      setBusy(false);
    }
  };

  return <Card className="content-upload">
    <div className="settings-card-heading"><div><h2>DM inbox</h2><p>Share photos or Reels to prepare a draft.</p></div></div>
    <div className="insight-detail-body">
      {inbox.isError && <p role="alert">The DM inbox could not be loaded.</p>}
      {inbox.data && <p>Approved sender: @{inbox.data.sender_username} <Badge>{inbox.data.enabled ? "Enabled" : "Paused"}</Badge></p>}
      {(inbox.data?.poll_state === "needs_conversation" ||
        (inbox.data?.poll_state === "paused" && !inbox.data.last_polled_at &&
          ["DM_CONVERSATION_MISSING", "SOURCE_INACCESSIBLE"].includes(inbox.data.last_poll_error ?? ""))) &&
        <p role="status">Instagram couldn’t find a conversation with this sender. Automatic checks are paused. Start a conversation on Instagram, enable the inbox if paused, then choose Check for new shares.</p>}
      {inbox.data?.feed_autopost_default && <p>New feed shares use original fit and shuffle carousel photos. {inbox.data.local_captions && "Captions are generated locally. "}{inbox.data.direct_publish ? "Shares autopost after a follow-up inbox check. Send ^ draft right after the share to review first." : "Shares stay in Drafts until direct publishing is enabled."}</p>}
      <details className="quiet-note">
        <summary>DM publishing commands</summary>
        <ul>
          <li><code>^ caption: chip em</code> sets the draft caption.</li>
          <li><code>^ order: 3, 1, 2</code> reorders a three-photo carousel by its original photo numbers.</li>
          <li><code>^ order: randomize</code> shuffles all photos once.</li>
          <li><code>^ order: original</code> keeps the shared post’s photo order.</li>
          <li><code>^ fit: original</code> keeps the whole image and extends its edges when needed.</li>
          <li><code>^ fit: seamless</code> fits the whole image with a blurred background.</li>
          <li><code>^ story: Visible text</code> makes a photo story.</li>
          <li><code>^ music: original</code> uses the shared post’s Instagram catalog track for a feed post.</li>
          {playlist.data?.playlist_id && <li><code>^ music: playlist</code> picks an unused playlist track for a feed post.</li>}
          <li><code>^ music: none</code> clears the track.</li>
          <li><code>^ draft</code> saves for review.</li>
          <li><code>^ rights</code> confirms permission to publish a shared Reel and its audio.</li>
          <li><code>^ autopost</code> queues the current format when direct publishing is enabled.</li>
        </ul>
      </details>
      {senders.length > 0 && <Field label="Sender account" htmlFor="dm-sender">
        <Select id="dm-sender" label="Sender account" value={sender} options={[
          { value: "none", label: "Select an account" },
          ...senders.map(item => ({ value: item.id, label: `@${item.username}${item.id === accountId ? " (self)" : ""}` })),
          ...external.map(item => ({ value: `sender:${item.sender_id}`, label: `@${item.sender_username}` })),
        ]} onValueChange={setChoice} />
      </Field>}
      {!senders.length && <p>Connect and verify an Instagram account first.</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || sender === "none" || (inbox.data?.enabled && sender === savedSender)} onClick={() => {
          if (sender.startsWith("sender:")) void act(path, "PATCH", { sender_id: sender.slice(7), enabled: true }, "DM sender approved.");
          else void act(path, "PUT", { sender_account_id: sender, enabled: true }, "DM sender approved.");
        }}>Approve sender</Button>
        {inbox.data?.enabled && <>
          <Button variant="ghost" disabled={busy || !inboxSenderId} onClick={() => void act(path, "PATCH", { sender_id: inboxSenderId, enabled: false }, "DM inbox paused.")}>Pause inbox</Button>
          <Button disabled={busy || pending || !connected} onClick={() => void act(`${path}/poll${inboxSenderId ? `?sender_id=${encodeURIComponent(inboxSenderId)}` : ""}`, "POST", {}, data.account.paused ? "Inbox check queued. Resume the account queue to run it." : "Inbox check queued.")}>{inbox.data.has_older ? "Check older messages" : "Check for new shares"}</Button>
          <Button variant="outline" disabled={busy || (!connected && !inbox.data.direct_publish)} onClick={() => {
            if (inbox.data?.direct_publish) void act(`${path}/direct-publication`, "PUT", {enabled: false, sender_id: inbox.data.sender_id}, "Direct publishing disabled.");
            else setConfirmDirect(true);
          }}>{inbox.data.direct_publish ? "Disable direct publishing" : "Enable direct publishing"}</Button>
        </>}
      </div>
      {inbox.data?.direct_publish && inbox.data.poll_state !== "needs_conversation" && <p>{inbox.data.poll_state === "needs_attention"
        ? `Automatic inbox checks are paused after a failed read (${inbox.data.last_poll_error}). Use Check for new shares to retry once; a successful check resumes automatic polling.`
        : inbox.data.poll_state === "backoff" && inbox.data.next_poll_at
          ? `The last inbox read failed (${inbox.data.last_poll_error}). Next automatic check: ${new Date(inbox.data.next_poll_at).toLocaleString([], { timeZone: data.settings.timezone })}.`
          : `Automatic inbox checks run every ${inbox.data.poll_interval_seconds} seconds while the account queue runs.`}</p>}
      {poll && <p role="status">Last inbox check: {poll.state}{poll.error ? ` · ${poll.error}` : ""}</p>}
      {submissions.isError && <p role="alert">The imported shares could not be loaded.</p>}
      {submissions.data?.items.map(item => <article key={item.id} className="content-operation">
        <div className="flex flex-wrap gap-2"><strong>{item.reel_url ? "Shared Reel" : "Shared post"}</strong><Badge>{item.state === "reel_ready" ? "Source saved" : item.state.replaceAll("_", " ")}</Badge></div>
        {item.reel_url && <p><a href={item.reel_url} target="_blank" rel="noreferrer">Open Reel on Instagram</a></p>}
        {item.reel_import_note && <p>{item.reel_import_note} You can upload an MP4 from Drafts.</p>}
        {item.reel_caption && <details><summary>Caption for your review</summary>
          <p className="whitespace-pre-wrap break-words">{item.reel_caption}</p>
        </details>}
        {item.media_ref?.startsWith("https://www.instagram.com/p/") && <a href={item.media_ref} target="_blank" rel="noreferrer">Source post</a>}
        {item.caption_status === "pending" && !item.reel_url && <p>Caption saved for the draft.</p>}
        {item.caption_status === "applied" && <p>DM caption applied.</p>}
        {item.caption_status === "generated" && !item.caption_run && <p>Caption generated locally.</p>}
        {item.caption_status === "generation_failed" && !item.caption_run && <p>A caption could not be generated. Open the draft to add a caption.</p>}
        {item.caption_status === "needs_review" && <p>Review the caption in Content. This draft already has a publication approval.</p>}
        <CaptionProgress item={item} />
        {!!item.music_checks?.length && <details>
          <summary>Music selection · {item.music_checks.some(track => ["matched", "source_matched", "named_matched", "saved_audio_matched"].includes(track.state)) ? "Matched" : "Needs review"}</summary>
          <ul>{item.music_checks.map(track => {
            const label = [track.title, track.artist].filter(Boolean).join(" · ") || "Source track";
            return <li key={track.spotify_id ?? track.title}>{track.spotify_id ? <a href={`https://open.spotify.com/track/${track.spotify_id}`} target="_blank" rel="noreferrer">{label}</a> : label} · {musicStates[track.state] ?? track.state}</li>;
          })}</ul>
        </details>}
        {item.command_status === "edits_saved" && <p>DM edits saved.</p>}
        {item.command_status === "held_as_draft" && <p>Saved as a draft by the sender.</p>}
        {item.command_status === "default_held" && <p>The follow-up inbox check could not confirm the latest commands. Review this draft before publishing.</p>}
        {item.command_status === "publication_queued" && <p>Publication queued from the approved sender.</p>}
        {item.command_status === "review_publication" && <p>{item.publication_hold ?? "Review the draft and approve publication in Content."}</p>}
        {item.command_status === "source_music_unavailable" && <p>The share did not include a reusable Instagram catalog track. Review the draft before publishing.</p>}
        {item.command_status === "playlist_unavailable" && <p>No unused playlist track could be matched in this batch. The share is in Drafts for review.</p>}
        {item.command_status === "review_commands" && <p>Review the DM commands. Photo order needs every original photo number. Music applies to feed posts, and a photo story uses one image.</p>}
        {!!item.skipped_videos && <p>Imported the photos and skipped {item.skipped_videos} video{item.skipped_videos === 1 ? "" : "s"}.</p>}
        {item.error && <p>{item.error === "CONTENT_STORAGE_UNAVAILABLE" ? "Image storage is unavailable. Check the Cloudflare R2 configuration before retrying." : item.error === "DM_MEDIA_UNSUPPORTED" ? "No supported photo set was available when this share was checked." : item.error}</p>}
        {item.draft_id ? <Button variant="outline" onClick={() => openDraft(item.draft_id!)}>Open draft</Button> :
          ["failed", "blocked", "cancelled", "reel_ready"].includes(item.state) && <Button variant="outline" disabled={busy || !connected || !inbox.data?.enabled} onClick={() => void act(`/dm-submissions/${item.id}/import`, "POST", {}, "Media import queued.")}>{item.reel_url ? "Prepare Reel draft" : "Retry photo import"}</Button>}
      </article>)}
      {!!submissions.data && submissions.data.total > 8 && <div className="flex gap-2">
        <Button variant="ghost" disabled={!offset} onClick={() => setOffset(offset - 8)}>Previous shares</Button>
        <Button variant="ghost" disabled={offset + 8 >= submissions.data.total} onClick={() => setOffset(offset + 8)}>More shares</Button>
      </div>}
    </div>
    <Modal open={confirmDirect} onOpenChange={setConfirmDirect} title="Enable direct DM publishing?" description={inbox.data?.feed_autopost_default ? "New feed shares from the approved sender can publish after a follow-up inbox check. Send ^ draft to hold a share. The account queue and its limits still apply." : "A new explicit publish command from the approved sender can publish without an additional browser approval. The account queue and its limits still apply."}>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setConfirmDirect(false)}>Cancel</Button>
        <Button disabled={busy} onClick={() => { setConfirmDirect(false); void act(`${path}/direct-publication`, "PUT", {enabled: true, sender_id: inbox.data?.sender_id}, "Direct publishing enabled for the approved sender."); }}>Enable for approved sender</Button>
      </div>
    </Modal>
  </Card>;
}
