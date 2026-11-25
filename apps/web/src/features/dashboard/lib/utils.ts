import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { CreateSourceInput, Settings } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function initials(username: string) {
  return username
    .replace(/^@/, "")
    .split(/[._\s]/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

export function relativeTime(iso: string | null) {
  if (!iso) return "Not collected yet";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 0) {
    const future = Math.abs(minutes);
    return future < 60 ? `In ${future} min` : `In ${Math.round(future / 60)} hr`;
  }
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

export function formatTime(iso: string, timezone: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(iso));
}

export function formatDate(iso: string | null, timezone: string, withTime = false) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
    timeZone: timezone,
  }).format(new Date(iso));
}

export function readable(value: string) {
  return value.replace(/_/g, " ").replace(/^\w/, (letter) => letter.toUpperCase());
}

export function validateSource(input: CreateSourceInput) {
  const errors: Record<string, string> = {};
  if (!input.label.trim()) errors.label = "Give this source a friendly name.";
  if (input.type !== "post") {
    if (!/^@?[a-zA-Z0-9._]{1,30}$/.test(input.value.trim())) {
      errors.value = "Enter a valid handle, like @quiet.form (up to 30 characters).";
    }
  } else {
    try {
      const url = new URL(input.value);
      if (
        !["https:", "http:"].includes(url.protocol) ||
        !["instagram.com", "www.instagram.com"].includes(url.hostname) ||
        !/^\/(p|reel|reels)\/[a-zA-Z0-9_-]+\/?$/.test(url.pathname)
      ) {
        throw new Error("Invalid post");
      }
    } catch {
      errors.value =
        "Use an Instagram post or Reel URL, like https://www.instagram.com/p/example/.";
    }
  }
  if (
    !Number.isInteger(input.collection_size) ||
    input.collection_size < 1 ||
    input.collection_size > (input.type === "mutual" ? 3 : 100)
  ) {
    errors.collection_size = `Choose a collection size between 1 and ${input.type === "mutual" ? 3 : 100}.`;
  }
  return errors;
}

export function validateSettings(settings: Settings) {
  const errors: Record<string, string> = {};
  for (const key of ["follows_per_day", "unfollows_per_day"] as const) {
    const value = settings[key];
    if (key === "follows_per_day" && value === null) continue;
    if (value === null || !Number.isInteger(value) || value < 0 || value > 500) {
      errors[key] = "Use a whole number between 0 and 500.";
    }
  }
  if (settings.follows_per_day === null && settings.follow_pacing !== "hourly") {
    errors.follows_per_day = "Choose hourly pacing for unlimited daily follows.";
  }
  const low = settings.follows_per_hour_min ?? 75;
  const high = settings.follows_per_hour_max ?? 100;
  if (!Number.isInteger(low) || low < 1 || low > 1000 || low > high) {
    errors.follows_per_hour_min = "Use 1–1,000, no higher than the hourly maximum.";
  }
  if (!Number.isInteger(high) || high < 1 || high > 1000) {
    errors.follows_per_hour_max = "Use a whole number between 1 and 1,000.";
  }
  if (
    settings.request_ceiling !== null &&
    (!Number.isInteger(settings.request_ceiling) ||
      settings.request_ceiling < 0 ||
      settings.request_ceiling > 10000)
  ) {
    errors.request_ceiling = "Use a whole number between 0 and 10,000.";
  }
  if (
    !Number.isInteger(settings.collection_batch_size) ||
    settings.collection_batch_size < 1 ||
    settings.collection_batch_size > 100
  ) {
    errors.collection_batch_size = "Use a whole number between 1 and 100.";
  }
  if (
    !Number.isInteger(settings.retention_days) ||
    settings.retention_days < 1 ||
    settings.retention_days > 365
  ) {
    errors.retention_days = "Use a whole number between 1 and 365.";
  }
  for (const key of ["window_start", "window_end"] as const) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(settings[key])) errors[key] = "Enter a valid time.";
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: settings.timezone });
  } catch {
    errors.timezone = "Choose a valid timezone.";
  }
  return errors;
}

export function isWithinWindow(date: Date, settings: Settings) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: settings.timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const minutes =
    Number(parts.find((p) => p.type === "hour")?.value) * 60 +
    Number(parts.find((p) => p.type === "minute")?.value);
  const toMinutes = (time: string) => {
    const [hours, mins] = time.split(":").map(Number);
    return hours * 60 + mins;
  };
  const start = toMinutes(settings.window_start);
  const end = toMinutes(settings.window_end);
  if (start === end) return true;
  return start < end ? minutes >= start && minutes <= end : minutes >= start || minutes <= end;
}

export function nextAllowedAt(date: Date, settings: Settings) {
  let result = new Date(date);
  for (let minute = 0; minute < 2880; minute += 1) {
    if (isWithinWindow(result, settings)) return result.toISOString();
    result = new Date(result.getTime() + 60_000);
  }
  return result.toISOString();
}
