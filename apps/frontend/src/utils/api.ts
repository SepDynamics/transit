/**
 * Shared API client utilities for Transit Sentinel.
 * Runtime config is injected via window.__TRANSIT_SENTINEL_CONFIG__ or Vite env.
 */

declare global {
  interface Window {
    __TRANSIT_SENTINEL_CONFIG__?: Record<string, unknown>;
  }
}

type RuntimeConfig = {
  API_URL?: string;
  API_BEARER_TOKEN?: string;
  DEMO_URL?: string;
};

const runtimeConfig: RuntimeConfig =
  (typeof window !== "undefined"
    ? (window.__TRANSIT_SENTINEL_CONFIG__ as RuntimeConfig | undefined)
    : undefined) || {};

const stringOrUndefined = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined;

export const API_BASE =
  stringOrUndefined(runtimeConfig.API_URL) ??
  (import.meta.env.VITE_API_HOST || "");

export const API_BEARER_TOKEN =
  stringOrUndefined(runtimeConfig.API_BEARER_TOKEN) ?? "";

export const DEMO_URL =
  stringOrUndefined(runtimeConfig.DEMO_URL) ??
  import.meta.env.VITE_DEMO_URL ??
  "https://calendly.com/sepdynamics/15min";

const normalisedBase = API_BASE ? API_BASE.replace(/\/$/, "") : "";

export const buildApiUrl = (path: string): string =>
  /^https?:\/\//i.test(path) ? path : `${normalisedBase}${path}`;

export const buildTransitQuery = (
  scope: string,
  traceId?: string | null,
): string => {
  const params = new URLSearchParams();
  params.set("scope", scope);
  if (traceId) {
    params.set("trace_id", traceId);
  }
  return params.toString();
};

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers();
  if (init?.headers) {
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  }
  if (API_BEARER_TOKEN) {
    headers.set("Authorization", `Bearer ${API_BEARER_TOKEN}`);
  }
  const response = await fetch(buildApiUrl(path), { ...init, headers });
  if (!response.ok) {
    throw new Error(path.replace(/^\//, ""));
  }
  return (await response.json()) as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (API_BEARER_TOKEN) {
    headers.set("Authorization", `Bearer ${API_BEARER_TOKEN}`);
  }
  const response = await fetch(buildApiUrl(path), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return (await response.json()) as T;
}
