import { useState } from "react";
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Check,
  ChevronRight,
  Clock3,
  FileStack,
  Layers3,
  ListChecks,
  Pause,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { formatTime, relativeTime } from "../lib/utils";
import {
  ActivityItem,
  EmptyState,
  Notice,
  PageHeader,
  SampleLabel,
  SourceArt,
} from "../components/shared";
import { Badge, Button, Card, Select, Tooltip } from "../components/ui";
import { ActivityDetail } from "../components/details";
import { MetricCard } from "../components/metric-card";
import type { Activity } from "../lib/types";

export function OverviewPage({ onAddSource }: { onAddSource: () => void }) {
  const { data } = useArchive();
  const { navigate } = useRouter();
  const [period, setPeriod] = useState("7");
  const [activity, setActivity] = useState<Activity | null>(null);
  const overview = data.overview;
  const metrics = [
    {
      label: "Candidates available",
      value: overview.candidates_available,
      icon: Users,
      note: "Ready for a closer look",
      path: "/candidates",
      tone: "mint",
    },
    {
      label: "Queued follows",
      value: overview.queued_follows,
      icon: ListChecks,
      note: "Scheduled, not sent",
      path: "/queue",
      tone: "neutral",
    },
    {
      label: "Unfollows due",
      value: overview.unfollows_due,
      icon: ArrowDownLeft,
      note: "On your terms, as always",
      path: "/queue?kind=unfollow",
      tone: "amber",
    },
    {
      label: "Protected accounts",
      value: overview.protected_accounts,
      icon: ShieldCheck,
      note: "Relationships worth keeping",
      path: "/ledger?tab=protected",
      tone: "blue",
    },
  ] as const;
  const chart = overview.chart.slice(-Number(period));
  const totalFollows = chart.reduce((sum, day) => sum + day.follows, 0);
  const totalBacks = chart.reduce((sum, day) => sum + day.follow_backs, 0);
  const enabledSources = data.sources.filter((source) => source.enabled);
  return (
    <div className="page overview-page">
      <PageHeader
        title="Overview"
        eyebrow="YOUR ARCHIVE, AT A GLANCE"
        description="A considered view of your growing archive."
      >
        <Button onClick={onAddSource}>
          <Plus size={16} />
          Add source
        </Button>
      </PageHeader>
      <div className="overview-status">
        {data.account.restricted ? (
          <Notice
            tone="red"
            title="A little attention is needed."
            icon={<ShieldCheck size={18} />}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate("/queue")}>
                Review queue
                <ArrowRight size={14} />
              </Button>
            }
          >
            Archive Desk has paused execution for account review. Check the connection details in Settings.
          </Notice>
        ) : data.account.paused ? (
          <Notice
            tone="amber"
            title="Your queue is paused. You set the pace."
            icon={<Pause size={17} />}
            action={
              <button className="text-link" onClick={() => navigate("/queue")}>
                View queue
                <ArrowRight size={14} />
              </button>
            }
          >
            No new jobs will start until you’re ready to resume.
          </Notice>
        ) : (
          <Notice
            tone="green"
            title="A little progress, at your pace."
            icon={<Check size={18} />}
            action={
              <button className="text-link" onClick={() => navigate("/queue")}>
                View queue
                <ArrowRight size={14} />
              </button>
            }
          >
            {data.account.mode === "mock" ? "The worker processes due simulation jobs while this queue is resumed." : "The worker processes due jobs for each enabled workflow while this queue is resumed."}
          </Notice>
        )}
      </div>
      <div className="section-kicker">
        <span>
          <span className="live-dot" />
          WORKSPACE SNAPSHOT
        </span>
        <span>
          <SampleLabel /> <span className="kicker-divider">·</span> Synced{" "}
          {relativeTime(data.last_synced_at).toLowerCase()}
        </span>
      </div>
      <div className="metrics-grid">
        {metrics.map(({ path, ...metric }) => (
          <MetricCard key={metric.label} {...metric} onClick={() => navigate(path)} />
        ))}
      </div>
      <Card className="operations-card">
        <div className="card-heading"><div><h2>Operating status</h2><p>Recorded queue health and observation freshness.</p></div></div>
        <dl className="operations-grid">
          <div><dt>Worker</dt><dd>{data.worker_status ?? "unavailable"}</dd><small>{data.worker_last_heartbeat ? `Heartbeat ${relativeTime(data.worker_last_heartbeat)}` : "No heartbeat recorded"}</small></div>
          <div><dt>Oldest due job</dt><dd>{overview.operations?.oldest_due_at ? relativeTime(overview.operations.oldest_due_at) : "None due"}</dd><small>{data.account.paused ? "Queue is manually paused" : "Due work waits for its account checks"}</small></div>
          <div><dt>Relationship observation</dt><dd>{overview.operations?.last_relationship_observation ? relativeTime(overview.operations.last_relationship_observation) : "Not recorded"}</dd><small>Collection {overview.operations?.last_collection_at ? relativeTime(overview.operations.last_collection_at) : "not recorded"}</small></div>
          <div><dt>Uncertain writes</dt><dd>{overview.operations?.unknown_writes ?? 0}</dd><small>{data.account.restricted ? "Account review required" : "Review unknown outcomes before more actions"}</small></div>
        </dl>
      </Card>
      {data.sources.length === 0 ? (
        <Card className="setup-card">
          <EmptyState
            icon={<Layers3 size={28} />}
            title="Every archive starts somewhere."
            description="Bring in a source, choose your operating preferences, and make a little room for discovery."
          />
          <div className="setup-checklist">
            <button onClick={() => navigate("/sources")}>
              <span>01</span>
              <div>
                <strong>Add your first source</strong>
                <small>A profile or post that feels like your corner of the internet.</small>
              </div>
              <ArrowRight size={17} />
            </button>
            <button onClick={() => navigate("/settings")}>
              <span>02</span>
              <div>
                <strong>Set a pace that works for you</strong>
                <small>Review your configured allowances, schedule, and cleanup rules.</small>
              </div>
              <ArrowRight size={17} />
            </button>
          </div>
        </Card>
      ) : (
        <>
          <div className="overview-middle">
            <Card className="activity-chart-card">
              <div className="card-heading">
                <div>
                  <h2>
                    Recorded activity
                    <span className="heading-period">.</span>
                  </h2>
                  <p>All recorded backend job outcomes.</p>
                </div>
                <SampleLabel />
              </div>
<p className="quiet-note p-5">A growth chart is unavailable. {data.account.mode === "mock" ? "Simulation jobs do not create relationship observations." : "The ledger shows recorded relationships and when they were last checked."}</p>
              <div className="run-summary">
                <div className="run-summary-label">
                  <span>RUN SUMMARY</span>
                  <small>Recorded job totals</small>
                </div>
                {[
                  { label: "Processed", value: overview.run.processed, tone: "green" },
                  { label: "Skipped", value: overview.run.skipped, tone: "neutral" },
                  { label: "Failed", value: overview.run.failed, tone: "red" },
                  { label: "Needs review", value: overview.run.review, tone: "amber" },
                ].map((item) => (
                  <button
                    key={item.label}
                    onClick={() =>
                      navigate(
                        `/queue?tab=${item.tone === "red" || item.tone === "amber" ? "review" : "completed"}`,
                      )
                    }
                    className="run-stat"
                  >
                    <span>
                      <i className={`status-dot dot-${item.tone}`} />
                      {item.label}
                    </span>
                    <strong>{String(item.value).padStart(2, "0")}</strong>
                  </button>
                ))}
              </div>
            </Card>
            <Card className="allowances-card">
              <div className="card-heading">
                <div>
                  <h2>At your own pace</h2>
                  <p>Your daily configured allowance.</p>
                </div>
                <button
                  className="icon-button"
                  aria-label="Edit configured allowances"
                  onClick={() => navigate("/settings")}
                >
                  <SlidersHorizontal size={17} />
                </button>
              </div>
              <div className="allowance-list">
                {[
                  {
                    label: "Follows",
                    used: overview.follows_used,
                    total: data.settings.follows_per_day,
                  },
                  {
                    label: "Unfollows",
                    used: overview.unfollows_used,
                    total: data.settings.unfollows_per_day,
                  },
                ].map((allowance) => (
                  <div className="allowance" key={allowance.label}>
                    <div className="allowance-title">
                      <span>{allowance.label}</span>
                      <span>
                        <strong>{allowance.used}</strong>
                        <span> / {allowance.total ?? "Unlimited"}</span>
                      </span>
                    </div>
                    <div
                      className="progress-track"
                      role="progressbar"
                      aria-label={`${allowance.label} owner-configured allowance`}
                      aria-valuenow={allowance.used}
                      aria-valuemax={allowance.total === null ? undefined : Math.max(allowance.used, allowance.total)}
                    >
                      <span
                        style={{
                          width: allowance.total === null ? "0%" : `${Math.min(100, (allowance.used / Math.max(1, allowance.total)) * 100)}%`,
                        }}
                      />
                    </div>
                    <div className="allowance-note">
                      <span>{allowance.used} used</span>
                      <span>{allowance.total === null ? "No daily cap" : `${Math.max(0, allowance.total - allowance.used)} remaining`}</span>
                    </div>
                  </div>
                ))}
              </div>
              <p className="allowance-disclaimer">
                <ShieldCheck size={12} />
                HTTP requests today: {overview.requests_used ?? 0}
                {data.settings.request_ceiling === null ? " · No daily cap" : ` / ${data.settings.request_ceiling}`}.
              </p>
              <div className="next-job">
                <div className="next-job-icon">
                  <Clock3 size={17} />
                </div>
                <div>
                  <span className="eyebrow">UP NEXT</span>
                  <strong>
                    {overview.next_job
                      ? `${overview.next_job.kind.replace(/_/g, " ")}${data.account.mode === "mock" ? " simulation" : ""}`
                      : "A little breathing room"}
                  </strong>
                  <p>
                    {overview.next_job
                      ? `${formatTime(overview.next_job.scheduled_at, data.settings.timezone)} · ${(data.settings.timezone.split("/")[1] ?? data.settings.timezone).replace(/_/g, " ")}`
                      : "No scheduled jobs"}
                  </p>
                </div>
                {overview.next_job && (
                  <Badge tone={data.account.paused ? "amber" : "neutral"}>
                    {data.account.paused ? "Paused" : "Queued"}
                  </Badge>
                )}
              </div>
            </Card>
          </div>
          <div className="overview-bottom">
            <Card className="recent-activity-card">
              <div className="card-heading">
                <div>
                  <h2>Notes from the desk</h2>
                  <p>The latest in your archive.</p>
                </div>
                <button className="text-link muted-link" onClick={() => navigate("/activity")}>
                  View activity
                  <ArrowUpRight size={14} />
                </button>
              </div>
              <div className="recent-activity-list">
                {data.activity.slice(0, 4).map((item) => (
                  <ActivityItem
                    key={item.id}
                    activity={item}
                    onClick={() => setActivity(item)}
                    compact
                  />
                ))}
              </div>
              <div className="card-bottom-note">
                <span className="status-dot dot-neutral" />{data.account.mode === "mock" ? "Simulation workspace · zero Instagram requests." : "Recorded account operations."}
              </div>
            </Card>
            <Card className="sources-preview-card">
              <div className="card-heading">
                <div>
                  <h2>Where it begins</h2>
                  <p>Your sources of discovery.</p>
                </div>
                <Badge tone="neutral">{enabledSources.length} enabled</Badge>
              </div>
              <div className="source-preview-list">
                {enabledSources.slice(0, 3).map((source) => (
                  <button
                    key={source.id}
                    className="source-preview"
                    onClick={() => navigate(`/candidates?source=${source.id}`)}
                  >
                    <SourceArt source={source} compact />
                    <div>
                      <strong>{source.label}</strong>
                      <span>
                        {source.type !== "post" ? source.value : "Post collection"}
                        <span> · {relativeTime(source.last_collected_at)}</span>
                      </span>
                    </div>
                    <span className="source-preview-count">{source.discovered_count}</span>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
              <button className="card-footer-link" onClick={() => navigate("/sources")}>
                All sources
                <span>
                  {data.sources.length} starting points
                  <ArrowRight size={14} />
                </span>
              </button>
            </Card>
          </div>
        </>
      )}
      <div className="roadmap-preview">
        <div className="roadmap-icon">
          <FileStack size={21} strokeWidth={1.5} />
        </div>
        <div>
          <div>
            <span>Good finds deserve a second life.</span>
            <Badge>PHASE 2</Badge>
          </div>
          <p>Create a photo or story draft, adjust its image and caption, and review the saved preview in Content.</p>
        </div>
        <span className="roadmap-caption">Collected now. Created next.</span>
      </div>
      <ActivityDetail activity={activity} onClose={() => setActivity(null)} />
    </div>
  );
}
