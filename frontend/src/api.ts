export interface Transaction {
  id: number;
  date: string;
  raw_description: string;
  merchant: string;
  amount: number;
  category: string;
  is_flagged: boolean;
  flag_reason: string | null;
  classified_by: "rules" | "llm" | "user";
}

export interface Summary {
  total_spend: number;
  total_income: number;
  net: number;
  count: number;
  flagged_count: number;
  by_category: { category: string; amount: number }[];
  by_month: { month: string; spend: number; income: number }[];
  top_merchants: { merchant: string; amount: number; count: number }[];
  date_range: [string, string] | null;
}

export interface UploadResult {
  filename: string;
  rows_parsed: number;
  rows_added: number;
  duplicates_skipped: number;
  flagged: number;
}

export interface TxnFilters {
  category?: string;
  flagged?: boolean;
  q?: string;
}

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>) {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && s.set(k, String(v)));
  const str = s.toString();
  return str ? `?${str}` : "";
}

export const api = {
  health: () => request<{ ok: boolean; llm: string }>("/api/health"),
  categories: () => request<string[]>("/api/categories"),
  summary: () => request<Summary>("/api/summary"),
  transactions: (f: TxnFilters) =>
    request<Transaction[]>(`/api/transactions${qs({ ...f, limit: 2000 })}`),
  upload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return request<{ uploads: UploadResult[] }>("/api/upload", { method: "POST", body: fd });
  },
  updateTransaction: (id: number, patch: { category?: string; is_flagged?: boolean }) =>
    request<Transaction>(`/api/transactions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  reset: () => request<{ deleted: number }>("/api/transactions", { method: "DELETE" }),
};

export const money = (n: number, cents = false) =>
  n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  });
