/**
 * Same-origin client for endpoints that require server-side operator auth.
 *
 * This helper deliberately does not read runtime bearer-token configuration
 * and never creates an Authorization header. The protected operations host or
 * VPN proxy must authenticate the browser and supply upstream authorization.
 */

export interface OperatorPreviewJsonResult<T> {
  ok: boolean;
  status: number;
  payload: T | null;
}

export async function fetchOperatorPreviewJson<T>(
  path: string,
  signal?: AbortSignal,
): Promise<OperatorPreviewJsonResult<T>> {
  if (!path.startsWith("/api/transit/alternative-advisories")) {
    throw new Error("operator preview client received an unsupported path");
  }

  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });

  let payload: T | null = null;
  try {
    payload = (await response.json()) as T;
  } catch {
    // Preserve the HTTP state even when a proxy returns an empty/non-JSON body.
  }

  return { ok: response.ok, status: response.status, payload };
}
