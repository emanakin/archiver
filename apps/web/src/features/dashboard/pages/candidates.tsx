import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { ArrowRight, Check, Ellipsis, Search, ShieldCheck, UserPlus, Users } from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter, useRouteState, useRoutePage } from "../lib/router";
import { relativeTime } from "../lib/utils";
import { RelationshipDetail } from "../components/details";
import {
  EmptyState,
  Identity,
  Notice,
  PageHeader,
  Pagination,
  SampleLabel,
  StatusBadge,
} from "../components/shared";
import { Badge, Button, Card, ConfirmDialog, DropdownMenu, Input, Select, Tooltip } from "../components/ui";
import type { Candidate } from "../lib/types";

const PAGE_SIZE = 8;

function AudienceEvidence({ candidate }: { candidate: Candidate }) {
  const { navigate } = useRouter();
  const likes = candidate.liked_posts ?? 0;
  const profiles = candidate.shared_profiles ?? 0;
  return <div><p>{likes} observed liked {likes === 1 ? "post" : "posts"} · {profiles} shared or watched {profiles === 1 ? "profile" : "profiles"}</p>
    <div className="flex items-center gap-2"><button className="text-link" onClick={() => navigate(`/insights?observation=following&target=${encodeURIComponent(candidate.username)}`)}>Inspect following</button>
    <DropdownMenu label={`More insights for @${candidate.username}`} trigger={<Ellipsis size={16}/>} items={[
      {label:"Profile counts",onSelect:()=>navigate(`/insights?observation=profile&target=${encodeURIComponent(candidate.username)}`)},
      {label:"Inspect followers",onSelect:()=>navigate(`/insights?observation=followers&target=${encodeURIComponent(candidate.username)}`)},
      {label:"Recent posts",onSelect:()=>navigate(`/insights?observation=posts&target=${encodeURIComponent(candidate.username)}`)},
    ]}/></div></div>;
}

export function CandidatesPage() {
  const { data, service, notify } = useArchive();
  const { search: routeSearch, navigate } = useRouter();
  const audience = useQuery({queryKey:["audience",data.account.id],queryFn:()=>api<components["schemas"]["AudienceSummary"]>(`/accounts/${data.account.id}/audience`),refetchInterval:30000});
  const timing = useQuery({queryKey:["follow-timing",data.account.id],queryFn:()=>api<components["schemas"]["FollowTelemetry"]>(`/accounts/${data.account.id}/follow-telemetry`),refetchInterval:15000});
  const [search, setSearch] = useRouteState("q", "");
  const [source, setSource] = useRouteState("source", "all");
  const [eligibility, setEligibility] = useRouteState("eligibility", "all");
  const [relationship, setRelationship] = useRouteState("relationship", "all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useRoutePage();
  const [detail, setDetail] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setSelected(new Set()); }, [page, search, source, eligibility, relationship, data.account.id]);
  const [result, setResult] = useState<{
    queued: number;
    excluded: number;
    skipped: number;
  } | null>(null);
  const activeIds = useMemo(
    () =>
      new Set(
        data.jobs
          .filter((j) => j.kind === "follow" && ["queued", "running", "blocked"].includes(j.state))
          .map((j) => j.target_id),
      ),
    [data.jobs],
  );
  const filtered = data.candidates;
  const total = data.pagination?.candidates ?? filtered.length;
  const actualPage = page;
  const visible = filtered;
  const selectable = visible.filter(
    (candidate) => !!data.connection?.can_follow && candidate.eligible && !activeIds.has(candidate.target_id),
  );
  const allSelected =
    selectable.length > 0 && selectable.every((candidate) => selected.has(candidate.target_id));
  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectedEligible = [...selected].filter(
    (id) =>
      data.candidates.find((candidate) => candidate.target_id === id)?.eligible &&
      !activeIds.has(id),
  );
  const previewExcluded = [...selected].filter(
    (id) => !data.candidates.find((candidate) => candidate.target_id === id)?.eligible,
  ).length;
  const previewSkipped = [...selected].filter((id) => activeIds.has(id)).length;
  const resetFilters = () => {
    setSearch("");
    setSource("all");
    setEligibility("all");
    setRelationship("all");
  };
  return (
    <div className="page candidates-page">
      <PageHeader
        title="Candidates"
        eyebrow={`@${data.account.username}`}
        description="Review active likers and people who follow the same profiles as your archive."
      >
        <Button disabled={!data.connection?.can_follow || selected.size === 0} onClick={() => setConfirm(true)}>
          <UserPlus size={16} />
          Queue follows{selected.size > 0 && <span className="button-count">{selected.size}</span>}
        </Button>
      </PageHeader>
      <Card className="audience-panel"><div className="audience-heading"><div><h2>Audience activity</h2><p>{audience.data?.last_observation ? `Ledger last checked ${relativeTime(audience.data.last_observation)}` : "Observed relationships in this workspace"}</p></div>
        <Button size="sm" variant="outline" onClick={()=>navigate("/settings")}>Adjust follow settings</Button></div>
        {audience.data && <div className="audience-metrics">
          <div><strong>{audience.data.profile_followers?.toLocaleString() ?? "—"}</strong><span>Profile followers{audience.data.follower_change != null && audience.data.compared_at && ` · ${audience.data.follower_change >= 0 ? "+" : ""}${audience.data.follower_change} since ${new Date(audience.data.compared_at).toLocaleDateString()}`}</span></div>
          <div><strong>{audience.data.profile_following?.toLocaleString() ?? "—"}</strong><span>Profile following{audience.data.profile_observed_at && ` · ${relativeTime(audience.data.profile_observed_at)}`}</span></div>
          <div><strong>{audience.data.requested}</strong><span>Pending requests</span></div>
          <div><strong>{audience.data.mutual}<small> / {audience.data.checked_following}</small></strong><span>Follow back among checked follows</span></div>
          <div><strong>{data.overview.follows_used}<small> / {data.settings.follows_per_day ?? "Unlimited"}</small></strong><span>Daily follow usage</span></div>
        </div>}
        {timing.data && <p className="audience-footer">Last 60 minutes: {timing.data.follow_dispatches} follows sent · {timing.data.follow_successes} verified · {timing.data.requests} HTTP requests · {timing.data.errors} errors{data.settings.follow_pacing === "hourly" && ` · Target ${data.settings.follows_per_hour_min}–${data.settings.follows_per_hour_max} / hour`}</p>}
        {audience.isError && <Button variant="ghost" onClick={()=>void audience.refetch()}>Retry audience counts</Button>}
        <div className="audience-footer"><Badge>{data.settings.ranked_follow_mode === "likers" ? "Active liker selection" : data.settings.ranked_follow_mode === "overlap" ? "Shared profile selection" : data.settings.ranked_follow_mode === "eligible" ? "All eligible candidates" : "Manual selection"}</Badge>
          <button className="text-link" onClick={()=>navigate(`/insights?observation=profile&target=${encodeURIComponent(data.account.username)}`)}>Check profile counts</button>
          {(["followers","following"] as const).map(kind=><button key={kind} className="text-link" onClick={()=>navigate(`/insights?observation=${kind}&target=${encodeURIComponent(data.account.username)}`)}>Inspect {kind}</button>)}</div>
      </Card>
      {!data.connection?.can_follow && <Notice title="Following is currently paused">You can review real candidates here. Follow actions are unavailable while this workspace supports read-only collection.</Notice>}
      {result && (
        <Notice
          tone="green"
          title={`${result.queued} ${data.account.mode === "mock" ? "sample " : ""}${result.queued === 1 ? "job" : "jobs"} added to the queue.`}
          icon={<Check size={17} />}
          action={
            <button className="text-link" onClick={() => navigate("/queue")}>
              View queue
              <ArrowRight size={14} />
            </button>
          }
        >
          {result.excluded} excluded · {result.skipped} skipped. Queue acceptance does not confirm a relationship change.
        </Notice>
      )}
      <div className="filters-bar">
        <div className="input-with-icon search-input">
          <Search size={15} />
          <Input
            aria-label="Search candidate handles"
            placeholder="Search by handle…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select
          label="Filter by source"
          value={source}
          onValueChange={setSource}
          options={[
            { value: "all", label: "All sources" },
            ...data.sources.map((entry) => ({ value: entry.id, label: entry.label })),
          ]}
        />
        <Select
          label="Filter by eligibility"
          value={eligibility}
          onValueChange={setEligibility}
          options={[
            { value: "all", label: "Current candidates" },
            { value: "eligible", label: "Eligible only" },
            { value: "ineligible", label: "Not available" },
            { value: "excluded", label: "Excluded" },
          ]}
        />
        <Select
          label="Filter by relationship"
          value={relationship}
          onValueChange={setRelationship}
          options={[
            { value: "all", label: "All relationships" },
            { value: "none", label: "No relationship" },
            { value: "following", label: "Following" },
            { value: "requested", label: "Requested" },
            { value: "unknown", label: "Unknown" },
          ]}
        />
      </div>
      <div className="section-kicker">
        <span>
          {total} CANDIDATES <span className="kicker-divider">/</span>{" "}
          {filtered.filter((c) => c.eligible && !activeIds.has(c.target_id)).length} AVAILABLE
        </span>
        <SampleLabel>Fictional people</SampleLabel>
      </div>
      <Card className="records-card">
        {filtered.length === 0 ? (
          <EmptyState
            icon={<Users size={27} />}
            title="A little quiet here."
            description={
              data.candidates.length
                ? "No candidates match these filters. Try widening your view."
                : "Collect a bounded batch from a source to meet your first candidates."
            }
            action={
              <Button
                variant="outline"
                onClick={() => (data.candidates.length ? resetFilters() : navigate("/sources"))}
              >
                {data.candidates.length ? "Clear filters" : "Explore sources"}
                <ArrowRight size={14} />
              </Button>
            }
          />
        ) : (
          <>
            <div className="desktop-table">
              <table className="data-table candidate-table">
                <thead>
                  <tr>
                    <th className="selection-cell">
                      <label className="checkbox-target">
                        <input
                          className="checkbox"
                          type="checkbox"
                          checked={allSelected}
                          disabled={!selectable.length}
                          aria-label="Select all eligible candidates on this page"
                          onChange={() =>
                            setSelected((previous) => {
                              const next = new Set(previous);
                              selectable.forEach((candidate) =>
                                allSelected
                                  ? next.delete(candidate.target_id)
                                  : next.add(candidate.target_id),
                              );
                              return next;
                            })
                          }
                        />
                      </label>
                    </th>
                    <th>Account</th>
                    <th>Discovered from</th>
                    <th>Discovered</th>
                    <th>Eligibility</th>
                    <th className="w-12">
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((candidate) => {
                    const queued = activeIds.has(candidate.target_id);
                    const canSelect = !!data.connection?.can_follow && candidate.eligible && !queued;
                    const reason = queued
                      ? "A follow job is already queued."
                      : (candidate.reason ?? "Eligible for reviewed follow.");
                    return (
                      <tr
                        key={candidate.target_id}
                        className={selected.has(candidate.target_id) ? "row-selected" : ""}
                      >
                        <td className="selection-cell">
                          <label
                            className="checkbox-target"
                            title={canSelect ? `Select ${candidate.username}` : reason}
                          >
                            <input
                              type="checkbox"
                              className="checkbox"
                              aria-label={
                                canSelect
                                  ? `Select ${candidate.username}`
                                  : `Unavailable: ${reason}`
                              }
                              disabled={!canSelect}
                              checked={selected.has(candidate.target_id)}
                              onChange={() => toggle(candidate.target_id)}
                            />
                          </label>
                        </td>
                        <td>
                          <button
                            className="identity-button"
                            onClick={() => setDetail(candidate.target_id)}
                          >
                            <Identity
                              username={candidate.username}
                              protected={candidate.category === "protected"}
                              description={
                                candidate.category === "previously_followed"
                                  ? "Remembered in the permanent ledger"
                                  : undefined
                              }
                            />
                          </button>
                          <AudienceEvidence candidate={candidate} />
                        </td>
                        <td>
                          <div className="source-badges">
                            {candidate.source_ids.slice(0, 1).map((id) => (
                              <Badge key={id}>
                                {data.sources.find((entry) => entry.id === id)?.label ??
                                  "Archived source"}
                              </Badge>
                            ))}
                            {candidate.source_ids.length > 1 && (
                              <Tooltip
                                content={candidate.source_ids
                                  .slice(1)
                                  .map((id) => data.sources.find((entry) => entry.id === id)?.label)
                                  .join(", ")}
                              >
                                <span className="extra-source-count" tabIndex={0}>
                                  +{candidate.source_ids.length - 1}
                                </span>
                              </Tooltip>
                            )}
                          </div>
                        </td>
                        <td className="muted-cell">{relativeTime(candidate.first_seen_at)}</td>
                        <td>
                          <Tooltip content={reason}>
                            <span tabIndex={0}>
                              <StatusBadge status={queued ? "queued" : candidate.category} />
                            </span>
                          </Tooltip>
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`View ${candidate.username}`}
                            onClick={() => setDetail(candidate.target_id)}
                          >
                            <ArrowRight size={16} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {visible.map((candidate) => {
                const queued = activeIds.has(candidate.target_id);
                return (
                  <div
                    key={candidate.target_id}
                    className={`mobile-record ${selected.has(candidate.target_id) ? "row-selected" : ""}`}
                  >
                    <div className="mobile-record-top">
                      <label className="checkbox-target">
                        <input
                          className="checkbox"
                          type="checkbox"
                          aria-label={`Select ${candidate.username}`}
                          disabled={!data.connection?.can_follow || !candidate.eligible || queued}
                          checked={selected.has(candidate.target_id)}
                          onChange={() => toggle(candidate.target_id)}
                        />
                      </label>
                      <button
                        className="identity-button"
                        onClick={() => setDetail(candidate.target_id)}
                      >
                        <Identity
                          username={candidate.username}
                          protected={candidate.category === "protected"}
                          description={relativeTime(candidate.first_seen_at)}
                        />
                      </button>
                      <button
                        className="icon-button"
                        aria-label={`View ${candidate.username}`}
                        onClick={() => setDetail(candidate.target_id)}
                      >
                        <ArrowRight size={16} />
                      </button>
                    </div>
                    <div className="mobile-record-meta">
                      <StatusBadge status={queued ? "queued" : candidate.category} />
                      <span>
                        {data.sources.find((entry) => entry.id === candidate.source_ids[0])?.label}
                      </span>
                    </div>
                    <AudienceEvidence candidate={candidate} />
                    {(!candidate.eligible || queued) && (
                      <p className="mobile-record-reason">
                        {queued ? "A follow job is already queued." : candidate.reason}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
            <Pagination
              page={actualPage}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={setPage}
              noun="candidates"
            />
          </>
        )}
      </Card>
      <div className="quiet-note">
        <ShieldCheck size={16} />
        <p>
          Eligibility comes first. Protected, excluded, existing, and previously followed accounts
          are kept out of automatic acquisition.
        </p>
      </div>
      {selected.size > 0 && (
        <div className="bulk-bar">
          <div>
            <span className="bulk-count">{selected.size}</span>
            <div>
              <strong>{selected.size === 1 ? "candidate" : "candidates"} selected</strong>
              <small>
                {selectedEligible.length} eligible · {previewExcluded} excluded · {previewSkipped}{" "}
                skipped
              </small>
            </div>
          </div>
          <div>
            <Button variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
            <Button onClick={() => setConfirm(true)}>
              <UserPlus size={15} />
              Queue follows
              <ArrowRight size={14} />
            </Button>
          </div>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Queue ${selectedEligible.length} ${data.account.mode === "mock" ? "sample " : ""}${selectedEligible.length === 1 ? "follow" : "follows"}?`}
        description={data.account.mode === "mock" ? "These are scheduled simulation jobs. Observed relationships stay unchanged after completion." : "These jobs can send real follow requests when acquisition is enabled and the queue is resumed. Each target is checked again immediately before sending."}
        confirmLabel={`Queue ${selectedEligible.length} ${selectedEligible.length === 1 ? "job" : "jobs"}`}
        disabled={!selectedEligible.length}
        loading={busy}
        onConfirm={async () => {
          setBusy(true);
          try {
            const response = await service.enqueueFollows([...selected]);
            setResult({
              queued: response.jobs.length,
              excluded: response.excluded,
              skipped: response.skipped,
            });
            setSelected(new Set());
            setConfirm(false);
            notify(
              `${response.jobs.length} jobs queued · ${response.excluded} excluded · ${response.skipped} skipped.`,
            );
          } catch (error) {
            notify(
              error instanceof Error ? error.message : "Could not queue these candidates.",
              "error",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="queue-preview">
          <div>
            <strong>{selectedEligible.length}</strong>
            <span>Eligible jobs</span>
          </div>
          <div>
            <strong>{previewExcluded}</strong>
            <span>Excluded</span>
          </div>
          <div>
            <strong>{previewSkipped}</strong>
            <span>Already queued / skipped</span>
          </div>
        </div>
      </ConfirmDialog>
      <RelationshipDetail targetId={detail} onClose={() => setDetail(null)} context="candidate" />
    </div>
  );
}
