import { useState } from "react";
import {
  ArrowUpRight,
  AtSign,
  Clock3,
  Layers3,
  Link2,
  MoreHorizontal,
  Plus,
  Power,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { SourceSchedule } from "../components/source-schedule";
import type { Source } from "../lib/types";
import { useRouter, useRouteState, useRoutePage } from "../lib/router";
import { relativeTime } from "../lib/utils";
import { EmptyState, PageHeader, Pagination, SampleLabel, SourceArt, StatusBadge } from "../components/shared";
import { Badge, Button, Card, DropdownMenu, Input, Tabs } from "../components/ui";

export function SourcesPage({ onAddSource }: { onAddSource: (type: Source["type"]) => void }) {
  const { data, service, perform } = useArchive();
  const { navigate } = useRouter();
  const [tab, setTab] = useRouteState("tab", "profile");
  const sourceType = tab === "mutual" || tab === "post" ? tab : "profile";
  const [search, setSearch] = useRouteState("q", "");
  const [page, setPage] = useRoutePage();
  const [schedule, setSchedule] = useState<Source | null>(null);
  const sources = data.sources.filter(
    (source) =>
      source.type === sourceType &&
      `${source.label} ${source.value}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="page sources-page">
      <PageHeader
        title="Sources"
        eyebrow="FOLLOW YOUR CURIOSITY"
        description="The people and posts where discovery begins."
      >
        <Button onClick={() => onAddSource(sourceType)}>
          <Plus size={16} />
          Add source
        </Button>
      </PageHeader>
      <div className="page-toolbar">
        <Tabs
          value={tab}
          onValueChange={setTab}
          items={[
            {
              value: "profile",
              label: "Profiles",
              count: data.sources.filter((s) => s.type === "profile").length,
            },
            {
              value: "mutual",
              label: "Mutual following",
              count: data.sources.filter((s) => s.type === "mutual").length,
            },
            {
              value: "post",
              label: "Posts",
              count: data.sources.filter((s) => s.type === "post").length,
            },
          ]}
        />
        <div className="input-with-icon search-input">
          <Search size={15} />
          <Input
            aria-label="Search sources"
            placeholder="Find a starting point…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      <div className="section-kicker">
        <span>
          {sources.length} {sourceType === "mutual" ? "MUTUAL" : sourceType === "profile" ? "PROFILE" : "POST"}{" "}
          {sources.length === 1 ? "SOURCE" : "SOURCES"}
        </span>
        <SampleLabel>Saved sources · simulated collection</SampleLabel>
      </div>
      {sources.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Layers3 size={26} />}
            title={search ? "No sources in this corner." : "A starting point is all it takes."}
            description={
              search
                ? "Try a different name or handle to find your source."
                : data.account.mode === "mock" ? "Add a profile or an Instagram post. Sample collection brings fictional candidates into this workspace." : "Add a profile or Instagram post to collect a bounded batch of accessible candidates."
            }
            action={
              <Button
                variant={search ? "outline" : "default"}
                onClick={() => (search ? setSearch("") : onAddSource(sourceType))}
              >
                {search ? "Clear search" : "Add your first source"}
                <Plus size={15} />
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="source-grid">
          {sources.map((source) => {
            const collecting = ["queued", "running"].includes(source.result);
            return (
              <Card
                key={source.id}
                className={`source-card ${!source.enabled ? "source-card-disabled" : ""}`}
              >
                <div className="source-card-art">
                  <SourceArt source={source} />
                  <span className="source-art-category">{source.category}</span>
                  <Badge
                    className="source-enabled-badge"
                    tone={source.enabled ? "neutral" : "amber"}
                    dot
                  >
                    {source.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
                <div className="source-card-body">
                  <div className="source-card-title">
                    <div>
                      <h2>{source.label}</h2>
                      <p>
                        {source.type !== "post" ? <AtSign size={12} /> : <Link2 size={12} />}
                        {source.type !== "post"
                          ? source.value.replace(/^@/, "")
                          : source.value.replace("https://www.", "")}
                      </p>
                    </div>
                    <DropdownMenu
                      label={`Actions for ${source.label}`}
                      trigger={<MoreHorizontal size={18} />}
                      items={[
                        { label: "Collection schedule", icon: <Clock3 size={14} />, onSelect: () => setSchedule(source) },
                        {
                          label: "View candidates",
                          icon: <Users size={14} />,
                          onSelect: () => navigate(`/candidates?source=${source.id}`),
                        },
                        {
                          label: source.enabled ? "Disable source" : "Enable source",
                          icon: <Power size={14} />,
                          disabled: collecting,
                          onSelect: () => {
                            void perform(
                              () => service.updateSource(source.id, { enabled: !source.enabled }),
                              source.enabled ? "Source disabled." : "Source enabled.",
                            );
                          },
                        },
                      ]}
                    />
                  </div>
                  <div className="source-stat-row">
                    <div>
                      <strong>{source.discovered_count}</strong>
                      <span>Candidates discovered</span>
                    </div>
                    <div>
                      <strong>{source.overlap_count.toString().padStart(2, "0")}</strong>
                      <span>Across other sources</span>
                    </div>
                  </div>
                  <div className="source-last-result">
                    {source.schedule_enabled && <span className="source-schedule-label">Daily {source.schedule_local_time} · {data.settings.timezone}</span>}
                    <span>
                      <Clock3 size={13} />
                      {relativeTime(source.last_collected_at)}
                    </span>
                    <StatusBadge status={source.result} />
                  </div>
                  {source.result === "partial" && (
                    <p className="source-partial-note">
                      {source.collection_note ?? "Partial collection. More candidates may exist."}
                    </p>
                  )}
                  {source.result === "completed" && (
                    <p className="source-partial-note">
                      {source.collection_note ?? "This collection pass is complete. Instagram may return a limited list."}
                    </p>
                  )}
                  {source.result === "idle" && (
                    <p className="source-partial-note">
                      A fresh starting point. Ready for bounded collection.
                    </p>
                  )}
                  {source.result === "error" && (
                    <p className="source-partial-note text-danger">
                      Collection needs attention. {source.collection_note ?? "Review the job for details."}{" "}
                      {source.enabled
                        ? "Review the queue before starting another bounded read."
                        : "Enable this source, then collect again."}
                    </p>
                  )}
                  {collecting && (
                    <p className="source-partial-note">
                      {source.result === "queued"
                        ? "Collection queued…"
                        : data.account.mode === "mock" ? "Collecting a bounded fictional sample…" : "Collecting an accessible bounded batch…"}
                    </p>
                  )}
                  <div className="source-card-actions">
                    {source.type !== "post" && <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(`/insights?observation=posts&target=${encodeURIComponent(source.value)}`)}
                    >Recent posts</Button>}
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!source.enabled || collecting || !data.connection?.can_collect}
                      loading={collecting}
                      onClick={() => {
                        void perform(
                          () => service.collectCandidates(source.id),
                          "Collection queued. The worker processes it when the queue is resumed.",
                        );
                      }}
                    >
                      {!collecting && <RefreshCw size={13} />}
                      {collecting ? "Collecting…" : "Collect candidates"}
                    </Button>
                    <button
                      className="icon-button"
                      aria-label={`View candidates from ${source.label}`}
                      onClick={() => navigate(`/candidates?source=${source.id}`)}
                    >
                      <ArrowUpRight size={18} />
                    </button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
      <Pagination page={page} pageSize={8} total={data.pagination?.sources ?? sources.length} onPageChange={setPage} noun="sources" />
      <SourceSchedule source={schedule} onClose={() => setSchedule(null)} />
      <div className="quiet-note">
        <Layers3 size={15} />
        <p>
          Discovery, with a little perspective. Instagram may not return a complete or chronological
          list. {data.account.mode === "mock" ? "Collections here are bounded fictional samples." : "Partial and inaccessible results are recorded explicitly."}
        </p>
      </div>
    </div>
  );
}
