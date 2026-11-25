import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  CircleAlert,
  CircleCheck,
  Clock3,
  Database,
  FlaskConical,
  Info,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  UserPlus,
  X,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { cn, initials, readable, relativeTime, formatDate } from "../lib/utils";
import type { Activity, Job, Source, Relationship } from "../lib/types";
import { Badge, Button, Card, ConfirmDialog, Skeleton, Tooltip, Modal } from "./ui";

export function PageHeader({
  title,
  description,
  eyebrow,
  children,
}: {
  title: string;
  description: string;
  eyebrow?: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

export function Avatar({
  username,
  size = "default",
  className,
}: {
  username: string;
  size?: "small" | "default" | "large";
  className?: string;
}) {
  const color = username.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % 6;
  return (
    <span
      aria-hidden="true"
      className={cn("avatar", `avatar-${color}`, `avatar-${size}`, className)}
    >
      {initials(username)}
    </span>
  );
}

export function Identity({
  username,
  description,
  protected: isProtected,
  small = false,
}: {
  username: string;
  description?: string;
  protected?: boolean;
  small?: boolean;
}) {
  return (
    <div className="identity">
      <Avatar username={username} size={small ? "small" : "default"} />
      <div className="identity-copy">
        <span className="identity-name">
          @{username}
          {isProtected && <ShieldCheck size={13} className="text-mint" aria-label="Protected" />}
        </span>
        {description && <span className="identity-description">{description}</span>}
      </div>
    </div>
  );
}

export function SourceArt({
  source,
  compact = false,
}: {
  source: Pick<Source, "art" | "label">;
  compact?: boolean;
}) {
  const designs = [
    <>
      <span className="art-overline">A STUDY OF THE EVERYDAY</span>
      <span className="art-form">
        form<span>&</span>field
      </span>
      <span className="art-bottom">OBJECTS · PEOPLE · PLACES</span>
    </>,
    <>
      <span className="art-overline">AN INDEPENDENT PICTURE JOURNAL</span>
      <span className="art-film">
        After
        <br />
        <i>the cut.</i>
      </span>
      <span className="art-bottom">STORIES BETWEEN THE FRAMES — № 024</span>
    </>,
    <>
      <span className="art-overline">NOT EVERYTHING IS A RACE.</span>
      <span className="art-running">
        THE
        <br />
        SLOW
        <br />
        <i>MILE</i>
      </span>
      <span className="art-track" />
      <span className="art-bottom">GO A LITTLE FURTHER.</span>
    </>,
    <>
      <span className="art-overline">OBJECT STUDIES / 004</span>
      <span className="art-object" />
      <span className="art-bottom">TOMORROW, CONSIDERED.</span>
    </>,
    <>
      <span className="art-overline">MATERIAL DIALOGUES</span>
      <span className="art-soft">
        soft
        <br />
        <i>forms.</i>
      </span>
      <span className="art-bottom">A COLLECTION IN PROGRESS</span>
    </>,
    <>
      <span className="art-overline">LITTLE THINGS, KEPT.</span>
      <span className="art-weekend">
        Scenes
        <br />
        <i>from here.</i>
      </span>
      <span className="art-bottom">WEEKEND NOTES / VOL. 06</span>
    </>,
  ];
  return (
    <div
      className={cn("source-art", `source-art-${source.art % 6}`, compact && "source-art-compact")}
      role="img"
      aria-label={`Fictional editorial artwork for ${source.label}`}
    >
      {designs[source.art % 6]}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone = ["eligible", "following", "completed", "succeeded", "connected"].includes(status)
    ? "green"
    : ["queued", "requested", "partial", "blocked", "needs_review", "paused"].includes(status)
      ? "amber"
      : ["failed", "error", "unknown", "expired"].includes(status)
        ? "red"
        : status === "protected"
          ? "blue"
          : "neutral";
  const label = status === "already_following" ? "Following" : readable(status);
  return (
    <Badge tone={tone} dot={["running", "queued", "blocked"].includes(status)}>
      {label}
    </Badge>
  );
}

export function SampleLabel({ children = "Simulation" }: { children?: ReactNode }) {
  const { data } = useArchive();
  return (
    <span className="sample-label">
      {data.account.mode === "mock" ? <FlaskConical size={11} /> : <Database size={11} />}
      {data.account.mode === "mock" ? children : "Account records"}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon ?? <Database size={25} />}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="loading-state" role="status" aria-label="Loading workspace">
      <Skeleton className="h-8 w-44" />
      <Skeleton className="mt-3 h-4 w-72 max-w-full" />
      <div className="metrics-grid mt-8">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-36" />
        ))}
      </div>
      <div className="overview-middle mt-6">
        <Skeleton className="h-80" />
        <Skeleton className="h-80" />
      </div>
      <span className="sr-only">Loading workspace…</span>
    </div>
  );
}

export function ErrorState() {
  const { service } = useArchive();
  return (
    <Card>
      <EmptyState
        icon={<CircleAlert size={26} />}
        title="The workspace couldn’t load"
        description="The backend could not be reached. Try again to refresh your workspace."
        action={
          <Button onClick={() => service.setFixture("normal")}>
            <RefreshCw size={16} />
            Try again
          </Button>
        }
      />
    </Card>
  );
}

export function Notice({
  tone = "neutral",
  icon,
  title,
  children,
  action,
  className,
}: {
  tone?: "neutral" | "amber" | "green" | "red";
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("notice", `notice-${tone}`, className)}>
      <span className="notice-icon">{icon ?? <Info size={18} />}</span>
      <div className="notice-copy">
        <strong>{title}</strong>
        {children && <p>{children}</p>}
      </div>
      {action && <div className="notice-action">{action}</div>}
    </div>
  );
}

export function RecoveryDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { navigate } = useRouter();
  return <Modal open={open} onOpenChange={onOpenChange} title="Account recovery" description="Keep execution paused until the account has been verified.">
    <ol className="recovery-steps"><li>Review any account prompts in Instagram.</li><li>Open account settings and reconnect using your username and password.</li><li>If Instagram requests verification, enter the code on the next screen. Connecting keeps automations paused.</li></ol>
    <Button onClick={() => { onOpenChange(false); navigate("/settings"); }}>Open account settings</Button>
  </Modal>;
}

export function CleanupStatus({ record, timezone }: { record: Relationship; timezone: string }) {
  const cleanup = record.cleanup;
  if (!cleanup) return <span>Refresh cleanup assessment</span>;
  const labels: Record<typeof cleanup.state, string> = {
    protected: "Protected",
    excluded: "Excluded",
    outcome_unknown: "Reconcile earlier write",
    manual_review: "Pending request: manual review",
    not_following: record.ever_followed ? "History retained" : "Not following",
    unmanaged: "Not managed",
    needs_observation: "Awaiting fresh relationship check",
    follows_back: "Preserved: follows back",
    retention_unknown: "Retention start unknown",
    retention_pending: "Retention ends",
    due_for_check: "Due for a fresh relationship check",
  };
  return <span className="cleanup-summary" title={cleanup.reason}>
    {labels[cleanup.state]}
    {cleanup.state === "retention_pending" && <span className="table-subline">{formatDate(cleanup.retention_due_at, timezone)}</span>}
  </span>;
}

export function PauseControl({ compact = false }: { compact?: boolean }) {
  const { data, service, perform } = useArchive();
  const { navigate } = useRouter();
  const [recovery, setRecovery] = useState(false);
  const [busy, setBusy] = useState(false);
  const restricted = data.account.restricted || (data.account.mode === "live" && (data.account.session_state !== "connected" || !!data.account.pause_reason));
  return (
    <>
      <Button
        variant={data.account.paused ? "default" : "outline"}
        size={compact ? "sm" : "default"}
        loading={busy}
        onClick={async () => {
          if (data.account.paused && restricted) {
            navigate("/settings");
            return;
          }
          setBusy(true);
          await perform(
            () => service.setPaused(!data.account.paused),
            data.account.paused
                ? data.account.mode === "mock" ? "Simulation queue resumed. Nothing is sent to Instagram." : "Queue resumed. Jobs still respect workflow settings, budgets and cooldowns."
                : "Queue paused. An in-flight operation may finish; no new job will start.",
          );
          setBusy(false);
        }}
      >
        {data.account.paused ? <Play size={14} fill="currentColor" /> : <Pause size={14} />}
        <span>
          {restricted
            ? "Connect Instagram"
            : data.account.paused
                ? "Resume queue"
                : "Pause queue"}
        </span>
      </Button>
      <RecoveryDialog open={recovery} onOpenChange={setRecovery} />
    </>
  );
}

export function ActivityIcon({ activity }: { activity: Activity }) {
  return (
    <span
      className={cn(
        "activity-icon",
        activity.category === "error" && "activity-icon-error",
        activity.operation.toLowerCase().includes("protect") && "activity-icon-protected",
      )}
    >
      {activity.category === "error" ? (
        <CircleAlert size={16} />
      ) : activity.operation.toLowerCase().includes("protect") ? (
        <ShieldCheck size={16} />
      ) : activity.operation.toLowerCase().includes("paus") ? (
        <Pause size={15} />
      ) : activity.operation.toLowerCase().includes("collect") ? (
        <Database size={15} />
      ) : activity.category === "configuration" ? (
        <Check size={16} />
      ) : (
        <UserPlus size={15} />
      )}
    </span>
  );
}

export function ActivityItem({
  activity,
  onClick,
  compact = false,
}: {
  activity: Activity;
  onClick?: () => void;
  compact?: boolean;
}) {
  return (
    <button className={cn("activity-item", compact && "activity-item-compact")} onClick={onClick}>
      <ActivityIcon activity={activity} />
      <div className="activity-item-copy">
        <p>
          {readable(activity.operation)}
          <span className="activity-target"> · @{activity.target}</span>
        </p>
        <span>
          {compact
            ? activity.result === "Simulated"
              ? "Simulated · No live action"
              : activity.reason
            : activity.reason}
        </span>
      </div>
      <div className="activity-item-end">
        {!compact && <StatusBadge status={activity.result.toLowerCase().replace(/ /g, "_")} />}
        <time dateTime={activity.created_at}>{relativeTime(activity.created_at)}</time>
      </div>
      {!compact && <ArrowRight className="activity-arrow" size={16} />}
    </button>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  noun = "records",
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  noun?: string;
}) {
  return (
    <div className="pagination">
      <p>
        {total === 0 ? "0" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`}{" "}
        <span>
          of {total} {noun}
        </span>
      </p>
      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page * pageSize >= total}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ArrowRight size={13} />
        </Button>
      </div>
    </div>
  );
}

export function JobOperation({ kind }: { kind: Job["kind"] }) {
  return (
    <span className="job-operation">
      {kind === "follow" ? (
        <UserPlus size={14} />
      ) : kind === "unfollow" ? (
        <X size={14} />
      ) : (
        <RefreshCw size={14} />
      )}
      {kind === "reconcile" ? "State check" : readable(kind)}
    </span>
  );
}

export function DetailRow({
  label,
  children,
  mono,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="detail-row">
      <span>{label}</span>
      <span className={mono ? "mono" : ""}>{children}</span>
    </div>
  );
}

export function FooterNote() {
  const { data } = useArchive();
  return (
    <div className="footer-note">
      <ShieldCheck size={13} />
      <span>Private by design. Thoughtfully collected.</span>
      <span className="footer-note-end">{data.account.mode === "mock" ? "Simulation only · No Instagram connection" : `${data.connection?.transport_mode ?? "disabled"} execution · ${data.account.session_state}`}</span>
    </div>
  );
}

export function InlineHelp({ content }: { content: string }) {
  return (
    <Tooltip content={content}>
      <button type="button" className="inline-help" aria-label={content}>
        <Info size={13} />
      </button>
    </Tooltip>
  );
}

export const resultIcons = {
  processed: CircleCheck,
  skipped: ArrowRight,
  failed: CircleAlert,
  review: Clock3,
};
