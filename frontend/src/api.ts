/** Small fetch helper shared by the newer pages. Surfaces the API's error message. */
export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (res.status === 401) window.dispatchEvent(new Event('fp-unauthorized'));
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const detail = await res.json();
      if (typeof detail.detail === 'string') msg = detail.detail;
      else if (Array.isArray(detail.detail) && detail.detail[0]?.msg) msg = String(detail.detail[0].msg);
    } catch { /* keep default */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type Role = 'owner' | 'admin' | 'editor' | 'viewer';
export const roleHelp: Record<Role, string> = {
  owner: 'Everything, including managing users',
  admin: 'Build and run workflows, manage credentials',
  editor: 'Build and run workflows',
  viewer: 'Read only',
};
