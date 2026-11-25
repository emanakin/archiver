import { useEffect, useState } from "react";
import { ArrowRight, BookOpen, Download, Search, ShieldCheck } from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter, useRouteState, useRoutePage } from "../lib/router";
import { ConflictError } from "../lib/service";
import { formatDate, relativeTime } from "../lib/utils";
import type { Relationship } from "../lib/types";
import { RelationshipDetail } from "../components/details";
import {
  EmptyState,
  Identity,
  Notice,
  PageHeader,
  Pagination,
  SampleLabel,
  StatusBadge,
  CleanupStatus,
} from "../components/shared";
import { Button, Card, Input, Switch, Tabs } from "../components/ui";

const PAGE_SIZE = 8;

export function LedgerPage() {
  const { data, service, notify } = useArchive();
  const { search: routeSearch } = useRouter();
  const [tab, setTab] = useRouteState("tab", "all");
  const [search, setSearch] = useRouteState("q", "");
  const [page, setPage] = useRoutePage();
  const [detail, setDetail] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const filtered = data.relationships;
  const total = data.pagination?.ledger ?? filtered.length;
  const actualPage = page;
  const visible = filtered;
  const protect = async (record: Relationship, value: boolean) => {
    setBusy(record.id);
    try {
      await service.patchRelationship(record.id, { protected: value }, record.version);
      notify(
        value
          ? "Account protected. Queued actions will recheck protection before execution."
          : "Protection removed. Previously followed accounts still cannot be reacquired.",
      );
      setConflict(null);
    } catch (error) {
      if (error instanceof ConflictError) setConflict(record.id);
      else
        notify(
          error instanceof Error ? error.message : "Could not update this relationship.",
          "error",
        );
    } finally {
      setBusy(null);
    }
  };
  const exportLedger = () => {
    const csv = [
      "username,stable_id,observed_state,managed,protected,ever_followed",
      ...filtered.map((record) =>
        [
          record.username,
          `="${record.target_id}"`,
          record.observed_state,
          record.managed,
          record.protected,
          record.ever_followed,
        ].join(","),
      ),
    ].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "archive-desk-ledger-page.csv";
    anchor.click();
    URL.revokeObjectURL(url);
    notify("The current ledger page was exported.");
  };
  return (
    <div className="page ledger-page">
      <PageHeader
        title="Ledger"
        eyebrow="A RECORD WORTH KEEPING"
        description="Every relationship has a history. This is where it stays."
      >
        <Button variant="outline" disabled={!filtered.length} onClick={exportLedger}>
          <Download size={15} />
          Export current page
        </Button>
      </PageHeader>
      <Notice icon={<BookOpen size={18} />} title="A little memory goes a long way.">
        Previously followed accounts remain in the record and cannot be requeued. Existing unmanaged
        relationships are never automatically cleaned up.
      </Notice>
      {conflict && (
        <Notice
          tone="amber"
          title="This record changed. Refresh to apply your update."
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                await service.listRelationships();
                setConflict(null);
                notify("Record refreshed. You can apply your update again.", "info");
              }}
            >
              Refresh record
            </Button>
          }
        >
          Your update was not applied. The newest record version is now available.
        </Notice>
      )}
      <div className="page-toolbar ledger-toolbar">
        <Tabs
          value={tab}
          onValueChange={setTab}
          items={[
            { value: "all", label: "All", count: data.relationships.length },
            { value: "following", label: "Following" },
            { value: "requested", label: "Requests" },
            { value: "previous", label: "Previously followed" },
            { value: "protected", label: "Protected", count: data.overview.protected_accounts },
          ]}
        />
      </div>
      <div className="filters-bar">
        <div className="input-with-icon search-input ledger-search">
          <Search size={15} />
          <Input
            aria-label="Search ledger by username or stable ID"
            placeholder="Find a handle or stable ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="filter-result-count">{total} relationships</span>
        <SampleLabel />
      </div>
      <Card className="records-card">
        {filtered.length === 0 ? (
          <EmptyState
            icon={<BookOpen size={27} />}
            title="No entries on this page."
            description={
              search
                ? "Try another handle or stable ID."
                : "Relationships matching this view will appear here. The permanent record never forgets a previously followed account."
            }
            action={
              search ? (
                <Button variant="outline" onClick={() => setSearch("")}>
                  Clear search
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <div className="desktop-table">
              <table className="data-table ledger-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Observed state</th>
                    <th>Follow / request</th>
                    <th>Cleanup assessment</th>
                    <th>Protected</th>
                    <th>
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((record) => (
                    <tr key={record.id}>
                      <td>
                        <button
                          className="identity-button"
                          onClick={() => setDetail(record.target_id)}
                        >
                          <Identity
                            username={record.username}
                            protected={record.protected}
                            description={`${record.managed ? "Managed" : "Unmanaged"} · Checked ${relativeTime(record.last_checked_at).toLowerCase()}`}
                          />
                        </button>
                      </td>
                      <td>
                        <StatusBadge
                          status={
                            record.ever_followed && record.observed_state === "none"
                              ? "previously_followed"
                              : record.observed_state
                          }
                        />
                        {record.excluded && <span className="table-subline">Excluded</span>}
                      </td>
                      <td className="muted-cell">
                        {formatDate(
                          record.follow_confirmed_at ?? record.follow_requested_at,
                          data.settings.timezone,
                        )}
                      </td>
                      <td className="muted-cell">
                        <CleanupStatus record={record} timezone={data.settings.timezone} />
                      </td>
                      <td>
                        <Switch
                          label={`Protect ${record.username}`}
                          checked={record.protected}
                          disabled={busy === record.id}
                          onCheckedChange={(checked) => {
                            void protect(record, checked);
                          }}
                        />
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          aria-label={`View relationship with ${record.username}`}
                          onClick={() => setDetail(record.target_id)}
                        >
                          <ArrowRight size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mobile-records">
              {visible.map((record) => (
                <div key={record.id} className="mobile-record">
                  <div className="mobile-record-top">
                    <button className="identity-button" onClick={() => setDetail(record.target_id)}>
                      <Identity
                        username={record.username}
                        protected={record.protected}
                        description={
                          record.managed
                            ? "Managed relationship"
                            : "Unmanaged · No automatic cleanup"
                        }
                      />
                    </button>
                    <button
                      className="icon-button"
                      aria-label={`View relationship with ${record.username}`}
                      onClick={() => setDetail(record.target_id)}
                    >
                      <ArrowRight size={16} />
                    </button>
                  </div>
                  <div className="mobile-record-meta">
                    <StatusBadge
                      status={
                        record.ever_followed && record.observed_state === "none"
                          ? "previously_followed"
                          : record.observed_state
                      }
                    />
                    <label className="mobile-protect-label">
                      <span>Protected</span>
                      <Switch
                        label={`Protect ${record.username}`}
                        checked={record.protected}
                        disabled={busy === record.id}
                        onCheckedChange={(checked) => {
                          void protect(record, checked);
                        }}
                      />
                    </label>
                  </div>
                  <div className="mobile-record-bottom">
                    <span>
                      Followed{" "}
                      {formatDate(
                        record.follow_confirmed_at ?? record.follow_requested_at,
                        data.settings.timezone,
                      )}
                    </span>
                    <span>
                      <CleanupStatus record={record} timezone={data.settings.timezone} />
                    </span>
                  </div>
                </div>
              ))}
            </div>
            <Pagination
              page={actualPage}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={setPage}
              noun="relationships"
            />
          </>
        )}
      </Card>
      <div className="quiet-note">
        <ShieldCheck size={16} />
        <p>
          Protecting someone skips their queued automatic follows and unfollows. It cannot undo an
          operation already in flight. Protection is checked again immediately before sending.
        </p>
      </div>
      <RelationshipDetail targetId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
