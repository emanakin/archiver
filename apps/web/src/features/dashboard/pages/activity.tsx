import { useRouteState, useRoutePage } from "../lib/router";
import { useState } from "react";
import { Activity, Search } from "lucide-react";
import { useArchive } from "../lib/archive-context";
import type { Activity as ActivityRecord } from "../lib/types";
import { formatDate } from "../lib/utils";
import { ActivityDetail } from "../components/details";
import {
  ActivityItem,
  EmptyState,
  PageHeader,
  Pagination,
  SampleLabel,
} from "../components/shared";
import { Card, Input, Tabs } from "../components/ui";

const PAGE_SIZE = 10;

export function ActivityPage() {
  const { data } = useArchive();
  const [filter, setFilter] = useRouteState("category", "all");
  const [search, setSearch] = useRouteState("q", "");
  const [page, setPage] = useRoutePage();
  const [detail, setDetail] = useState<ActivityRecord | null>(null);
  const filtered = data.activity;
  const total = data.pagination?.activity ?? filtered.length;
  const actualPage = page;
  const visible = filtered;
  return (
    <div className="page activity-page">
      <PageHeader
        title="Activity"
        eyebrow="THE STORY SO FAR"
        description="A clear record of the actions, decisions, and pauses along the way."
      >
        <SampleLabel>Simulated history</SampleLabel>
      </PageHeader>
      <div className="page-toolbar">
        <Tabs
          value={filter}
          onValueChange={(value) => {
            setFilter(value);
            setPage(1);
          }}
          items={[
            { value: "all", label: "All activity", count: data.activity.length },
            { value: "action", label: "Actions" },
            { value: "configuration", label: "Configuration" },
            {
              value: "error",
              label: "Errors",
              count: data.activity.filter((entry) => entry.category === "error").length,
            },
          ]}
        />
        <div className="input-with-icon search-input">
          <Search size={15} />
          <Input
            aria-label="Search activity"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Find a note in the record…"
          />
        </div>
      </div>
      <div className="section-kicker">
        <span>{total} ENTRIES IN THE RECORD</span>
        <span>DISPLAYED IN {data.settings.timezone.replace(/_/g, " ")}</span>
      </div>
      <Card className="activity-feed">
        {visible.length === 0 ? (
          <EmptyState
            icon={<Activity size={26} />}
            title="Nothing in the record just yet."
            description={
              search || filter !== "all"
                ? "No activity matches this view. Try another filter or search."
                : "Collections, queue decisions, protection changes, and recorded operations appear here."
            }
          />
        ) : (
          visible.map((entry, i) => {
            const day = formatDate(entry.created_at, data.settings.timezone);
            const previousDay =
              i > 0 ? formatDate(visible[i - 1].created_at, data.settings.timezone) : "";
            return (
              <div key={entry.id}>
                {day !== previousDay && (
                  <div className="activity-day-heading">
                    <span>
                      {day === formatDate(new Date().toISOString(), data.settings.timezone)
                        ? "Today"
                        : day}
                    </span>
                    <span>Simulation record</span>
                  </div>
                )}
                <ActivityItem activity={entry} onClick={() => setDetail(entry)} />
              </div>
            );
          })
        )}
        {filtered.length > 0 && (
          <Pagination
            page={actualPage}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
            noun="entries"
          />
        )}
      </Card>
      <div className="quiet-note">
        <Activity size={15} />
        <p>
          A useful record, without the private details. Activity shows safe identifiers and
          timestamps, never tokens, credentials, or private-message bodies.
        </p>
      </div>
      <ActivityDetail activity={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
