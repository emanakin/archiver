"use client";
import { useMemo } from "react";
import { usePathname, useRouter as useNextRouter, useSearchParams } from "next/navigation";

export const routes = [
  { path: "/", label: "Overview" }, { path: "/sources", label: "Sources" },
  { path: "/candidates", label: "Candidates" }, { path: "/queue", label: "Queue" },
  { path: "/ledger", label: "Ledger" }, { path: "/activity", label: "Activity" },
  { path: "/settings", label: "Settings" },
  { path: "/insights", label: "Insights" },
] as const;

export function useRouter() {
  const router = useNextRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const search = useMemo(() => new URLSearchParams(params.toString()), [params]);
  return { pathname, search, navigate: (to: string) => {
    const target = new URL(to, window.location.origin);
    if (search.get("account") && !target.searchParams.has("account")) target.searchParams.set("account", search.get("account")!);
    router.push(`${target.pathname}${target.search}${target.hash}`);
  } };
}

// Native history is supported by App Router and preserves the current form/focus.
export function useRouteState(key: string, initial: string): [string, (value: string) => void] {
  const params = useSearchParams();
  return [params.get(key) ?? initial, (value: string) => {
    const next = new URLSearchParams(window.location.search);
    if (value === initial) next.delete(key); else next.set(key, value);
    if (key !== "page") next.delete("page");
    const target = `${window.location.pathname}${next.size ? `?${next}` : ""}`;
    if (target === `${window.location.pathname}${window.location.search}`) return;
    if (key === "q") window.history.replaceState(null, "", target);
    else window.history.pushState(null, "", target);
  }];
}

export function useRoutePage(): [number, (value: number) => void] {
  const [page, setPage] = useRouteState("page", "1");
  return [Math.max(1, Number(page) || 1), value => setPage(String(value))];
}
