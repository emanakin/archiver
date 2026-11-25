import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import {
  ArrowRight,
  Clock3,
  Flag,
  FlaskConical,
  Info,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { ConflictError, jobView } from "../lib/service";
import { formatDate, readable } from "../lib/utils";
import type { Activity, Relationship } from "../lib/types";
import { Badge, Button, ConfirmDialog, Sheet, Switch } from "./ui";
import { Avatar, DetailRow, Identity, JobOperation, Notice, StatusBadge, CleanupStatus } from "./shared";

export function ActivityDetail({
  activity,
  onClose,
}: {
  activity: Activity | null;
  onClose: () => void;
}) {
  const { data } = useArchive();
  return (
    <Sheet
      open={!!activity}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={activity?.operation ?? "Activity details"}
      description="A durable record of this operation."
    >
      {activity && (
        <div className="detail-content">
          <Identity username={activity.target} description={activity.operation} />
          <div className="detail-badges">
            <StatusBadge status={activity.result.toLowerCase().replace(/ /g, "_")} />
            <Badge>
              <FlaskConical size={12} />
              {activity.mode === "mock" ? "Simulation" : "Instagram"}
            </Badge>
          </div>
          <Notice title="The record" icon={<Info size={17} />}>
            {activity.reason}
          </Notice>
          <div className="detail-section">
            <h3>Audit details</h3>
            <DetailRow label="Recorded">
              {formatDate(activity.created_at, data.settings.timezone, true)}
            </DetailRow>
            <DetailRow label="Timezone">{data.settings.timezone}</DetailRow>
            <DetailRow label="Operation">{activity.operation}</DetailRow>
            <DetailRow label="Activity ID" mono>
              {activity.id}
            </DetailRow>
            <DetailRow label="Job ID" mono>
              {activity.job_id ?? "Not associated with a job"}
            </DetailRow>
            <DetailRow label="Request identifier" mono>
              {activity.request_id}
            </DetailRow>
            <DetailRow label="UTC timestamp" mono>
              {activity.created_at}
            </DetailRow>
          </div>
          <p className="detail-disclaimer">
            <ShieldCheck size={14} />
            {activity.mode === "mock" ? "Simulation history. No Instagram request was sent." : "Recorded account operation with its request identifier."}
          </p>
        </div>
      )}
    </Sheet>
  );
}

export function JobDetail({ jobId, onClose }: { jobId: string | null; onClose: () => void }) {
  const { navigate } = useRouter();
  const { data, service, perform } = useArchive();
  const tracked = useQuery({ queryKey: ["job", jobId], queryFn: () => api<components["schemas"]["JobResponse"]>(`/jobs/${jobId}`), enabled: !!jobId, retry: false,
    refetchInterval: query => ["queued", "running"].includes(query.state.data?.state ?? "") ? 5000 : false });
  const job = tracked.data ? jobView(tracked.data, data.account.mode, data.relationships) : data.jobs.find((entry) => entry.id === jobId);
  const activity = data.activity.filter((entry) => entry.job_id === jobId);
  const attempts = useQuery({ queryKey: ["attempts", jobId], queryFn: () => api<{ items: components["schemas"]["AttemptResponse"][] }>(`/jobs/${jobId}/attempts`), enabled: !!jobId, retry: false });
  return (
    <Sheet
      open={!!jobId}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="A closer look at the job"
      description="Scheduling, outcome, and the record in between."
    >
      {tracked.isError && <Notice tone="red" title="Job could not be loaded">Close this panel and refresh to try again.</Notice>}
      {tracked.isPending && !job && <p role="status">Loading job details…</p>}
      {job && (
        <div className="detail-content">
          <Identity username={job.username} description={`${job.mode === "mock" ? "Simulated" : "Instagram"} ${readable(job.kind)} operation`} />
          <div className="detail-badges">
            <StatusBadge status={job.state} />
            <Badge>
              <FlaskConical size={12} />
              {job.mode === "mock" ? "Simulation only" : "Instagram"}
            </Badge>
          </div>
          {job.error && (
            <Notice
              tone={job.state === "failed" || job.state === "unknown" ? "red" : "amber"}
              title={
                job.state === "unknown"
                  ? "Check before acting again"
                  : job.state === "skipped"
                    ? "Deliberately skipped"
                    : "Worth a look"
              }
            >
              {job.error}
            </Notice>
          )}
          {job.state === "simulated" && (
            <Notice title="Simulated, not sent" icon={<FlaskConical size={17} />}>
              This sample operation did not change the observed relationship or contact Instagram.
            </Notice>
          )}
          <div className="detail-section">
            <h3>Job details</h3>
            <DetailRow label="Operation">
              <JobOperation kind={job.kind} />
            </DetailRow>
            <DetailRow label="Scheduled">
              {formatDate(job.scheduled_at, data.settings.timezone, true)}
            </DetailRow>
            <DetailRow label="Completed">
              {formatDate(job.completed_at, data.settings.timezone, true)}
            </DetailRow>
            <DetailRow label="Timezone">{data.settings.timezone}</DetailRow>
            <DetailRow label="Source">
              {data.sources.find((source) => source.id === job.source_id)?.label ?? "Not recorded"}
            </DetailRow>
            <DetailRow label="Attempts">{job.attempt_count}</DetailRow>
            <DetailRow label="Job ID" mono>
              {job.id}
            </DetailRow>
            <DetailRow label="Request identifier" mono>
              {job.request_id}
            </DetailRow>
            <DetailRow label="Scheduled UTC" mono>
              {job.scheduled_at}
            </DetailRow>
          </div>
          {activity.length > 0 && (
            <div className="detail-section">
              <h3>Related activity</h3>
              {activity.map((entry) => (
                <div className="detail-timeline-entry" key={entry.id}>
                  <span className="timeline-point" />
                  <div>
                    <strong>{entry.operation}</strong>
                    <p>{entry.reason}</p>
                    <time>{formatDate(entry.created_at, data.settings.timezone, true)}</time>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="detail-actions">
            {job.state === "queued" && (
              <Button
                variant="outline"
                onClick={() => {
                  void perform(() => service.cancelJob(job.id), "Queued job cancelled.");
                }}
              >
                Cancel queued job
              </Button>
            )}
            {job.state === "failed" && ["reconcile", "discover"].includes(job.kind) && (
              <Button
                disabled={!data.connection?.can_collect}
                onClick={() => {
                  void perform(
                    () => service.retryJob(job.id),
                    "Read retry queued. No write will be retried.",
                  );
                }}
              >
                <RefreshCw size={15} />
                Retry read
              </Button>
            )}
            {["publish_photo", "publish_story", "publish_carousel", "publish_reel", "edit_caption", "delete_story", "delete_post", "reconcile_content"].includes(job.kind) && ["unknown", "blocked", "failed"].includes(job.state) && <Button variant="outline" onClick={() => { onClose(); navigate("/content"); }}>Review outcome in Content</Button>}
            {!["publish_photo", "publish_story", "publish_carousel", "publish_reel", "edit_caption", "delete_story", "delete_post", "reconcile_content"].includes(job.kind) && (job.state === "unknown" || (job.state === "blocked" && job.error === "OUTCOME_UNKNOWN")) && (
              <Button
                disabled={!job.dispatch_started_at || !data.connection?.can_collect}
                onClick={() => {
                  void perform(
                    () => service.checkOutcome(job.id),
                    "Outcome check queued. No write was retried.",
                  );
                }}
              >
                <RefreshCw size={15} />
                Check outcome
              </Button>
            )}
          </div>
          <div className="detail-section">
            <h3>Execution attempts</h3>
            {attempts.isError ? <p>Attempt history could not be loaded.</p> : attempts.isPending ? <p role="status">Loading attempts…</p> : attempts.data?.items.length ? attempts.data.items.map(attempt => <div className="detail-timeline-entry" key={attempt.id}>
              <div><strong>{readable(attempt.outcome)}</strong><p>{attempt.request_count} outgoing requests · {attempt.error_code ?? "No recorded error"}</p><time>{formatDate(attempt.started_at, data.settings.timezone, true)}</time>
                <details><summary>Request journal ({attempt.events?.length ?? 0})</summary><ol>{(attempt.events ?? []).map(event => <li key={event.id}>{readable(event.event)}{typeof event.detail.ordinal === "number" ? ` #${event.detail.ordinal}` : ""} · {formatDate(event.created_at, data.settings.timezone, true)}</li>)}</ol></details>
              </div>
            </div>) : <p>No external execution attempts recorded.</p>}
          </div>
          <p className="detail-disclaimer">
            Cancellation can only stop a queued job. It cannot undo an operation already sent.
          </p>
        </div>
      )}
    </Sheet>
  );
}

export function RelationshipDetail({
  targetId,
  onClose,
  context = "ledger",
}: {
  targetId: string | null;
  onClose: () => void;
  context?: "ledger" | "candidate";
}) {
  const { data, service, notify, perform } = useArchive();
  const { navigate } = useRouter();
  const live = data.relationships.find((relationship) => relationship.target_id === targetId);
  const candidate = data.candidates.find((entry) => entry.target_id === targetId);
  const [record, setRecord] = useState<Relationship | undefined>(live);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [adopt, setAdopt] = useState(false);
  const [adoptionPlanned, setAdoptionPlanned] = useState(false);
  useEffect(() => {
    setRecord(live);
    setConflict(false);
    setAdoptionPlanned(false);
  }, [targetId]);
  const queued = data.jobs.some(
    (job) =>
      job.target_id === targetId &&
      job.kind === "follow" &&
      ["queued", "running", "blocked"].includes(job.state),
  );
  const patch = async (changes: Partial<Pick<Relationship, "protected" | "excluded">>) => {
    if (!record) return;
    setBusy(true);
    try {
      const updated = await service.patchRelationship(record.id, changes, record.version);
      setRecord(updated);
      setConflict(false);
      notify(
        changes.protected
          ? "Account protected. Queued actions will recheck protection before execution."
          : changes.excluded
            ? "Account excluded from acquisition and cleanup."
            : "Relationship preferences updated.",
      );
    } catch (error) {
      if (error instanceof ConflictError) setConflict(true);
      else notify(error instanceof Error ? error.message : "Update failed.", "error");
    } finally {
      setBusy(false);
    }
  };
  const history = data.activity.filter((entry) => entry.target === record?.username).slice(0, 4);
  return (
    <>
      <Sheet
        open={!!targetId}
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
        title={record ? `@${record.username}` : "Account details"}
        description="The relationship, the context, and what comes next."
      >
        {record && (
          <div className="detail-content relationship-detail">
            <div className="detail-person">
              <Avatar username={record.username} size="large" />
              <div>
                <h3>@{record.username}</h3>
                <span>Instagram relationship</span>
              </div>
              {record.protected && <ShieldCheck className="text-mint" size={22} />}
            </div>
            <div className="detail-badges">
              <StatusBadge status={record.observed_state} />
              <Badge>{record.managed ? "Managed" : "Unmanaged"}</Badge>
              <Badge>
                <FlaskConical size={11} />
                Real account data
              </Badge>
            </div>
            {candidate && context === "candidate" && (
              <Notice
                tone={candidate.eligible ? "green" : "neutral"}
                title={
                  queued
                    ? "Already in the queue"
                    : candidate.eligible
                      ? "Eligible for acquisition"
                      : readable(candidate.category)
                }
              >
                {queued
                  ? "A follow job is already scheduled. Queue acceptance does not confirm a relationship change."
                  : (candidate.reason ??
                    "No existing relationship, protection, exclusion, or permanent follow history prevents acquisition.")}
              </Notice>
            )}
            {conflict && (
              <Notice
                tone="amber"
                title="This record changed. Refresh to apply your update."
                action={
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const records = await service.listRelationships();
                      setRecord(records.find((entry) => entry.id === record.id));
                      setConflict(false);
                    }}
                  >
                    Refresh record
                    <RefreshCw size={13} />
                  </Button>
                }
              >
                Your change was not saved. Another edit has a newer version.
              </Notice>
            )}
            <div className="detail-section">
              <h3>The relationship</h3>
              <DetailRow label="Stable target ID" mono>
                {record.target_id}
              </DetailRow>
              <DetailRow label="Observed state">
                <StatusBadge status={record.observed_state} />
              </DetailRow>
              <DetailRow label="Follow requested">
                {formatDate(record.follow_requested_at, data.settings.timezone)}
              </DetailRow>
              <DetailRow label="Follow confirmed">
                {formatDate(record.follow_confirmed_at, data.settings.timezone)}
              </DetailRow>
              <DetailRow label="Cleanup assessment">
                <CleanupStatus record={record} timezone={data.settings.timezone} />
              </DetailRow>
              <DetailRow label="Retention threshold">
                {formatDate(record.cleanup?.retention_due_at ?? null, data.settings.timezone, true)}
              </DetailRow>
              <DetailRow label="Follow-back observed">
                {record.follows_back === true ? "Yes" : record.follows_back === false ? "No" : "Unknown"}
              </DetailRow>
              <DetailRow label="Follow-back last checked">
                {formatDate(record.follows_back_observed_at ?? null, data.settings.timezone, true)}
              </DetailRow>
              <DetailRow label="Unfollow confirmed">
                {formatDate(record.unfollow_confirmed_at ?? null, data.settings.timezone, true)}
              </DetailRow>
              <DetailRow label="Permanent acquisition history">
                {record.ever_followed ? "Retained; cannot be acquired again" : "No past follow or request recorded"}
              </DetailRow>
              <DetailRow label="Last checked">
                {formatDate(record.last_checked_at, data.settings.timezone, true)}
              </DetailRow>
              <DetailRow label="Record version">{record.version}</DetailRow>
            </div>
            {record.cleanup && <Notice title="Why this cleanup state">{record.cleanup.reason}</Notice>}
            <div className="detail-section">
              <h3>Keep what matters</h3>
              <div className="switch-row">
                <div>
                  <label htmlFor="detail-protection">
                    <ShieldCheck size={15} />
                    Protected account
                  </label>
                  <p>Preserve this relationship. Skip automatic follows and unfollows.</p>
                </div>
                <Switch
                  id="detail-protection"
                  label={`Protect ${record.username}`}
                  checked={record.protected}
                  disabled={busy}
                  onCheckedChange={(checked) => {
                    void patch({ protected: checked });
                  }}
                />
              </div>
              <div className="switch-row">
                <div>
                  <label htmlFor="detail-exclusion">
                    <Flag size={15} />
                    Excluded account
                  </label>
                  <p>Skip both acquisition and cleanup.</p>
                </div>
                <Switch
                  id="detail-exclusion"
                  label={`Exclude ${record.username}`}
                  checked={record.excluded}
                  disabled={busy}
                  onCheckedChange={(checked) => {
                    void patch({ excluded: checked });
                  }}
                />
              </div>
            </div>
            {record.ever_followed && record.observed_state === "none" && (
              <Notice title="A permanent place in the record" icon={<Clock3 size={17} />}>
                Previously followed accounts stay in history after unfollowing and cannot be
                requeued for acquisition.
              </Notice>
            )}
            {!record.managed && (
              <Notice title="An existing, unmanaged relationship" icon={<Info size={17} />}>
                Imported relationships are excluded from automatic cleanup. Adopting one into
                management starts a new retention period. Cleanup still requires explicit account enablement and keeps follow-backs.
              </Notice>
            )}
            {!record.managed && record.observed_state === "following" && (
              <Button variant="outline" disabled={data.account.mode === "mock" || busy || record.protected || record.excluded} onClick={() => setAdopt(true)}>
                Adopt into management
                <ArrowRight size={14} />
              </Button>
            )}
            <div className="detail-section">
              <h3>Source history</h3>
              {record.source_ids.map((id) => {
                const source = data.sources.find((entry) => entry.id === id);
                return (
                  <button
                    className="detail-source"
                    key={id}
                    onClick={() => {
                      onClose();
                      navigate(`/candidates?source=${id}`);
                    }}
                  >
                    <span>{source?.label ?? "Archived source"}</span>
                    <span>
                      {source?.type === "profile" ? source.value : "Post collection"}
                      <ArrowRight size={13} />
                    </span>
                  </button>
                );
              })}
            </div>
            {history.length > 0 && (
              <div className="detail-section">
                <h3>Action timeline</h3>
                {history.map((entry) => (
                  <div key={entry.id} className="detail-timeline-entry">
                    <span className="timeline-point" />
                    <div>
                      <strong>
                        {entry.operation}
                        <Badge>{entry.result}</Badge>
                      </strong>
                      <p>{entry.reason}</p>
                      <time>{formatDate(entry.created_at, data.settings.timezone, true)}</time>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="detail-actions">
              <Button
                variant="outline"
                loading={busy}
                disabled={!data.connection?.can_collect}
                onClick={async () => {
                  setBusy(true);
                  await perform(
                    () => service.recheckCandidate(record.target_id),
                    "Fresh relationship check queued.",
                  );
                  const records = await service.listRelationships();
                  setRecord(records.find((entry) => entry.id === record.id));
                  setBusy(false);
                }}
              >
                <RefreshCw size={14} />
                Recheck state
              </Button>
              {context === "candidate" && (
                <Button
                  disabled={!data.connection?.can_follow || !candidate?.eligible || queued || record.protected || record.excluded}
                  onClick={() => {
                    void perform(
                      () => service.enqueueFollows([record.target_id]),
                      (result) =>
                        `${result.jobs.length} jobs queued · ${result.excluded} excluded · ${result.skipped} skipped.`,
                    );
                  }}
                >
                  <UserPlus size={14} />
                  {queued ? "Already queued" : "Queue follow"}
                </Button>
              )}
            </div>
            <p className="detail-disclaimer">
              <FlaskConical size={13} />
              {data.account.mode === "mock" ? "Fictional identity in a simulation workspace." : "Relationship observations include their last checked time."}
            </p>
          </div>
        )}
      </Sheet>
      <ConfirmDialog
        open={adopt}
        onOpenChange={setAdopt}
        title="Adopt this relationship?"
        description={`Adopting @${record?.username} starts a new ${data.settings.retention_days}-day retention period now. Cleanup only runs when explicitly enabled, and keeps protected accounts and follow-backs.`}
        confirmLabel="Adopt into management"
        loading={busy}
        onConfirm={async () => {
          if (!record) return;
          setBusy(true);
          try {
            const updated = await service.adoptRelationship(record.id, record.version);
            setRecord(updated);
            setAdopt(false);
            notify("Relationship adopted. Retention starts now.");
          } catch (error) {
            notify(error instanceof Error ? error.message : "Could not adopt this relationship.", "error");
          } finally { setBusy(false); }
        }}
      />
    </>
  );
}
