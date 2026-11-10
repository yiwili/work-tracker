declare global {
  interface ImportMetaEnv {
    readonly VITE_API_BASE_URL?: string;
  }
  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}

export function getApiBase(): string {
  const base =
    (window as unknown as { __AWT_API_BASE__?: string }).__AWT_API_BASE__ ||
    import.meta.env.VITE_API_BASE_URL ||
    "/api";
  return base.replace(/\/+$/, "");
}

export function apiUrl(path: string): string {
  let normalized = path.startsWith("/api/") ? path.slice(4) : path;
  if (!normalized.startsWith("/")) normalized = "/" + normalized;
  return getApiBase() + normalized;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch(apiUrl(path), {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r.json();
}
