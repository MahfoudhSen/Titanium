import { useCallback, useEffect, useState } from "react";
import { api, money, Summary, Transaction, TxnFilters } from "./api";
import { CategoryChart, MonthlyChart } from "./components/Charts";
import { TransactionsTable } from "./components/TransactionsTable";
import { UploadZone } from "./components/UploadZone";

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [txns, setTxns] = useState<Transaction[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [filters, setFilters] = useState<TxnFilters>({});
  const [llm, setLlm] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([api.summary(), api.transactions(filters)]);
      setSummary(s);
      setTxns(t);
      setError(null);
    } catch (e) {
      setError(`Can't reach the API: ${(e as Error).message}`);
    }
  }, [filters]);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => {});
    api.health().then((h) => setLlm(h.llm)).catch(() => {});
  }, []);
  useEffect(() => {
    const id = setTimeout(refresh, filters.q ? 250 : 0);
    return () => clearTimeout(id);
  }, [refresh, filters.q]);

  async function reset() {
    if (!window.confirm("Delete all transactions?")) return;
    await api.reset();
    setFilters({});
    refresh();
  }

  const hasData = summary && summary.count > 0;
  const savingsRate = hasData && summary.total_income > 0 ? summary.net / summary.total_income : null;

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">Ti</span>
          <div>
            <h1>Titanium</h1>
            <span className="muted small">
              {summary?.date_range
                ? `${fmtDate(summary.date_range[0])} – ${fmtDate(summary.date_range[1])}`
                : "AI financial data platform"}
            </span>
          </div>
        </div>
        <div className="header-actions">
          {llm && <span className={`pill ${llm === "rules-only" ? "" : "ok"}`}>Classifier: {llm === "rules-only" ? "rules" : `LLM (${llm})`}</span>}
          {hasData && <UploadZone onUploaded={refresh} compact />}
          {hasData && <button className="ghost" onClick={reset}>Reset</button>}
        </div>
      </header>

      {error && <div className="alert error">{error}</div>}

      {!hasData ? (
        <section className="onboard card">
          <h2>Turn messy bank exports into a clear picture of your spending</h2>
          <p className="muted">
            Titanium cleans dates, amounts and merchant names, removes duplicate rows across overlapping
            statements, categorizes every transaction, and flags charges that look unusual.
          </p>
          <UploadZone onUploaded={refresh} />
        </section>
      ) : (
        <>
          <section className="kpis">
            <Kpi label="Spending" value={money(summary.total_spend)} />
            <Kpi label="Income" value={money(summary.total_income)} />
            <Kpi
              label="Net"
              value={money(summary.net)}
              sub={savingsRate !== null ? `${Math.round(savingsRate * 100)}% savings rate` : undefined}
              tone={summary.net >= 0 ? "pos" : "neg"}
            />
            <Kpi
              label="Flagged charges"
              value={String(summary.flagged_count)}
              sub={`of ${summary.count} transactions`}
              tone={summary.flagged_count ? "warn" : undefined}
              onClick={() => setFilters((f) => ({ ...f, flagged: f.flagged ? undefined : true }))}
              active={!!filters.flagged}
            />
          </section>

          <section className="grid">
            <div className="card">
              <h3>Income vs. spending by month</h3>
              <MonthlyChart data={summary.by_month} />
            </div>
            <div className="card">
              <h3>
                Spending by category <span className="muted small">click to filter</span>
              </h3>
              <CategoryChart
                data={summary.by_category}
                active={filters.category}
                onSelect={(category) => setFilters((f) => ({ ...f, category }))}
              />
            </div>
          </section>

          <section className="grid wide-left">
            <div className="card">
              <div className="table-head">
                <h3>Transactions</h3>
                <div className="filters">
                  <input
                    placeholder="Search merchant…"
                    value={filters.q ?? ""}
                    onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value || undefined }))}
                  />
                  <select
                    value={filters.category ?? ""}
                    onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value || undefined }))}
                  >
                    <option value="">All categories</option>
                    {categories.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={!!filters.flagged}
                      onChange={(e) => setFilters((f) => ({ ...f, flagged: e.target.checked || undefined }))}
                    />
                    Flagged only
                  </label>
                </div>
              </div>
              <TransactionsTable rows={txns} categories={categories} onChanged={refresh} />
            </div>
            <div className="card">
              <h3>Top merchants</h3>
              <ol className="merchants">
                {summary.top_merchants.map((m) => (
                  <li key={m.merchant}>
                    <button className="link" onClick={() => setFilters((f) => ({ ...f, q: m.merchant }))}>
                      {m.merchant}
                    </button>
                    <span className="muted small">{m.count}×</span>
                    <span className="num">{money(m.amount)}</span>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Kpi(props: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg" | "warn";
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <div
      className={`card kpi ${props.tone ?? ""} ${props.onClick ? "clickable" : ""} ${props.active ? "active" : ""}`}
      onClick={props.onClick}
    >
      <span className="muted small">{props.label}</span>
      <strong>{props.value}</strong>
      {props.sub && <span className="muted small">{props.sub}</span>}
    </div>
  );
}

const fmtDate = (d: string) =>
  new Date(`${d}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
