import { useEffect, useState } from "react";
import { ArrowRight, Clock3, FlaskConical, ListChecks, Pause, ShieldAlert, X } from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter, useRouteState, useRoutePage } from "../lib/router";
import { formatDate, formatTime } from "../lib/utils";
import type { Job } from "../lib/types";
import { JobDetail } from "../components/details";
import {
  EmptyState,
  Identity,
  JobOperation,
  Notice,
  PageHeader,
  Pagination,
  PauseControl,
  SampleLabel,
  StatusBadge,
} from "../components/shared";
import { Button, Card, Select, Tabs, Tooltip } from "../components/ui";

const PAGE_SIZE = 8;
const tabStates: Record<string, string[]> = {
  scheduled: ["queued"],
  running: ["running"],
  completed: ["simulated", "succeeded", "skipped", "cancelled"],
  review: ["failed", "unknown", "blocked"],
};

export function QueuePage() {
  const { data, service, perform } = useArchive();
  const { search, navigate } = useRouter();
  const [tab, setTab] = useRouteState("tab", "scheduled");
  const [kind, setKind] = useRouteState("kind", "all");
  const [date, setDate] = useRouteState("date", "all");
  const [page, setPage] = useRoutePage();
  const [detail, setDetail] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const tz = data.settings.timezone;
  const filtered = data.jobs;
  const total = data.pagination?.queue ?? filtered.length;
  const actualPage = page;
  const visible = filtered;
  const jobAction = (job: Job) =>
    job.state === "queued" ? (
      <Tooltip content="Cancel this queued job. This cannot undo an operation already sent.">
        <button
          className="icon-button"
          aria-label={`Cancel job for ${job.username}`}
          onClick={() => {
            void perform(() => service.cancelJob(job.id), "Queued job cancelled.");
          }}
        >
          <X size={16} />
        </button>
      </Tooltip>
    ) : job.state === "failed" && ["reconcile", "discover"].includes(job.kind) ? (
      <Button
        variant="outline"
        size="sm"
        disabled={!data.connection?.can_collect}
        onClick={() => {
          void perform(() => service.retryJob(job.id), "Read retry queued.");
        }}
      >
        Retry read
      </Button>
    ) : ["publish_photo", "publish_story", "publish_carousel", "publish_reel", "edit_caption", "delete_story", "delete_post", "reconcile_content"].includes(job.kind) && ["unknown", "blocked", "failed"].includes(job.state) ? (
      <Button variant="outline" size="sm" onClick={() => navigate("/content")}>Review in Content</Button>
    ) : job.state === "unknown" || (job.state === "blocked" && job.error === "OUTCOME_UNKNOWN") ? (
      <Button
        variant="outline"
        size="sm"
        disabled={!job.dispatch_started_at || !data.connection?.can_collect}
        onClick={() => {
          void perform(
            () => service.checkOutcome(job.id),
            "Outcome check queued. No write was retried.",
          );
        }}
      >
        Check outcome
      </Button>
    ) : (
      <button
        className="icon-button"
        aria-label={`View job for ${job.username}`}
        onClick={() => setDetail(job.id)}
      >
        <ArrowRight size={16} />
      </button>
    );
  return (
    <div className="page queue-page">
      <PageHeader
        title="Queue"
        eyebrow="ONE THING AT A TIME"
        description="A little intention behind every next step."
      >
        <PauseControl />
      </PageHeader>
      {data.account.paused && (
        <Notice
          tone={data.account.restricted ? "red" : "amber"}
          icon={data.account.restricted ? <ShieldAlert size={19} /> : <Pause size={17} />}
          title={
            data.account.restricted
              ? "Execution paused. Recovery comes first."
              : data.account.cooldown_until
                ? "A little time between steps."
                : "A pause, not a full stop."
          }
          action={<PauseControl compact />}
        >
          {data.account.restricted
            ? "Archive Desk is holding execution for account review. Check the connection details in Settings; this local hold alone does not prove an Instagram restriction."
            : data.account.cooldown_until
              ? "New jobs wait until the recorded cooldown ends."
              : "No new jobs will start. An operation already in flight may finish."}
        </Notice>
      )}
      <div className="page-toolbar queue-toolbar">
        <Tabs
          value={tab}
          onValueChange={setTab}
          items={[
            {
              value: "scheduled",
              label: "Scheduled",
              count: data.jobs.filter((j) => tabStates.scheduled.includes(j.state)).length,
            },
            {
              value: "running",
              label: "Running",
              count: data.jobs.filter((j) => j.state === "running").length,
            },
            {
              value: "completed",
              label: "Completed",
              count: data.jobs.filter((j) => tabStates.completed.includes(j.state)).length,
            },
            {
              value: "review",
              label: "Needs review",
              count: data.jobs.filter((j) => tabStates.review.includes(j.state)).length,
            },
          ]}
        />
      </div>
      <div className="filters-bar queue-filters">
        <Select
          label="Filter by operation"
          value={kind}
          onValueChange={setKind}
          options={[
            { value: "all", label: "All operations" },
            { value: "follow", label: "Follow" },
            { value: "unfollow", label: "Unfollow" },
            { value: "reconcile", label: "State check" },
            { value: "discover", label: "Discovery" },
          ]}
        />
        <Select
          label="Filter by date"
          value={date}
          onValueChange={setDate}
          options={[
            { value: "all", label: "All dates" },
            { value: "today", label: "Today" },
            { value: "week", label: "Within 7 days" },
          ]}
        />
        <span className="timezone-label">
          <Clock3 size={13} />
          {tz}
        </span>
        <SampleLabel>Simulated operations</SampleLabel>
      </div>
      <Card className="records-card">
        {filtered.length === 0 ? (
          <EmptyState
            icon={<ListChecks size={27} />}
            title={
              tab === "running"
                ? "A moment of stillness."
                : tab === "review"
                  ? "Nothing needs a closer look."
                  : "A little breathing room."
            }
            description={
              tab === "running"
                ? "No jobs are running. Resuming the queue allows the backend worker to process due work."
                : "There are no jobs in this view. Try another tab or adjust your filters."
            }
            action={
              kind !== "all" || date !== "all" ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setKind("all");
                    setDate("all");
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <div className="desktop-table">
              <table className="data-table queue-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Operation</th>
                    <th>Scheduled</th>
                    <th>Attempts</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <button className="identity-button" onClick={() => setDetail(job.id)}>
                          <Identity
                            username={job.username}
                            description={
                              data.sources.find((s) => s.id === job.source_id)?.label ??
                              "Archive operations"
                            }
                          />
                        </button>
                      </td>
                      <td>
                        <JobOperation kind={job.kind} />
                        <span className="table-subline">{job.mode === "mock" ? "Simulation" : "Instagram"}</span>
                      </td>
                      <td className="schedule-cell">
                        {formatTime(job.scheduled_at, tz)}
                        <span className="table-subline">{formatDate(job.scheduled_at, tz)}</span>
                      </td>
                      <td className="muted-cell tabular-nums">
                        {job.attempt_count.toString().padStart(2, "0")}
                      </td>
                      <td>
                        <StatusBadge status={job.state} />
                      </td>
                      <td className="row-action-cell">{jobAction(job)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {visible.map((job) => (
                <div className="mobile-record" key={job.id}>
                  <div className="mobile-record-top">
                    <button className="identity-button" onClick={() => setDetail(job.id)}>
                      <Identity
                        username={job.username}
                        description={
                          data.sources.find((source) => source.id === job.source_id)?.label
                        }
                      />
                    </button>
                    {jobAction(job)}
                  </div>
                  <div className="mobile-record-meta">
                    <JobOperation kind={job.kind} />
                    <StatusBadge status={job.state} />
                  </div>
                  <div className="mobile-record-bottom">
                    <span>{formatDate(job.scheduled_at, tz, true)}</span>
                    <span>{job.attempt_count} attempts · {job.mode === "mock" ? "Simulation" : "Instagram"}</span>
                    <button className="text-link" onClick={() => setDetail(job.id)}>
                      Details
                      <ArrowRight size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <Pagination
              page={actualPage}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={setPage}
              noun="jobs"
            />
          </>
        )}
      </Card>
      <div className="quiet-note">
        <FlaskConical size={16} />
        <p>
          Simulation jobs never contact Instagram or change observed relationships. Real account
          jobs follow the enabled workflow settings. Unknown writes require reconciliation before any retry.
        </p>
      </div>
      <JobDetail jobId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
