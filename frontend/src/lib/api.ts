export type Role = "owner" | "admin" | "viewer";
export type ShopMode = "demo" | "live";
export type Plan = "free" | "growth" | "pro";

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface ShopSummary {
  id: string;
  name: string;
  domain: string;
  mode: ShopMode;
  plan: Plan;
  currency: string;
  timezone: string;
  role: Role;
}

export interface Me {
  user: User;
  shops: ShopSummary[];
  csrf_token: string;
}

export interface ShopSettings {
  kill_switch: boolean;
  dry_run: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  fraud_threshold: number;
  max_discount_pct: number;
  refund_ceiling_minor: number;
  alert_email: string | null;
  support_email: string | null;
  sender_name: string | null;
  physical_address: string | null;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  joined_at: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const CSRF_COOKIE = "storeops_csrf";
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : undefined;
}

function describeError(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined;
      return field ? `${String(field)}: ${first.msg ?? "invalid"}` : (first.msg ?? "Invalid request");
    }
  }
  return status >= 500 ? "Something went wrong on our side. Please try again." : `Request failed (${status})`;
}

/** Same-origin fetch to the API (proxied by Next.js), with session cookie and CSRF header. */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const method = (init.method ?? (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("accept", "application/json");
  if (init.json !== undefined) headers.set("content-type", "application/json");
  if (UNSAFE.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers.set("x-csrf-token", csrf);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: "include",
      body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
    });
  } catch {
    throw new ApiError(0, "Can't reach StoreOps right now. Check your connection.");
  }

  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, describeError(body, response.status));
  return body as T;
}
