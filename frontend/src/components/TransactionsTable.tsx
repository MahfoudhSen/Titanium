import { api, money, Transaction } from "../api";

interface Props {
  rows: Transaction[];
  categories: string[];
  onChanged: () => void;
}

const SOURCE_LABEL: Record<Transaction["classified_by"], string> = {
  llm: "AI",
  rules: "Rule",
  user: "You",
};

export function TransactionsTable({ rows, categories, onChanged }: Props) {
  async function setCategory(t: Transaction, category: string) {
    await api.updateTransaction(t.id, { category });
    onChanged();
  }
  async function toggleFlag(t: Transaction) {
    await api.updateTransaction(t.id, { is_flagged: !t.is_flagged });
    onChanged();
  }

  if (!rows.length) return <div className="empty">No transactions match these filters.</div>;

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Merchant</th>
            <th>Category</th>
            <th className="num">Amount</th>
            <th aria-label="flag" />
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className={t.is_flagged ? "flagged" : ""}>
              <td className="nowrap muted">
                {new Date(`${t.date}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
              </td>
              <td>
                <div className="merchant">{t.merchant}</div>
                <div className="raw" title={t.raw_description}>{t.raw_description}</div>
                {t.flag_reason && <div className="flag-reason">⚠ {t.flag_reason}</div>}
              </td>
              <td>
                <select value={t.category} onChange={(e) => setCategory(t, e.target.value)}>
                  {categories.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <span className={`src src-${t.classified_by}`}>{SOURCE_LABEL[t.classified_by]}</span>
              </td>
              <td className={`num nowrap ${t.amount > 0 ? "pos" : ""}`}>
                {t.amount > 0 ? "+" : ""}
                {money(t.amount, true)}
              </td>
              <td>
                <button
                  className={`flag-btn ${t.is_flagged ? "on" : ""}`}
                  onClick={() => toggleFlag(t)}
                  title={t.is_flagged ? "Mark as reviewed" : "Flag for review"}
                >
                  {t.is_flagged ? "✓ Dismiss" : "⚑"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
