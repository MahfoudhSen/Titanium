import { useRef, useState } from "react";
import { api, UploadResult } from "../api";

interface Props {
  onUploaded: () => void;
  compact?: boolean;
}

export function UploadZone({ onUploaded, compact }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handle(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.upload(Array.from(files));
      setResults(r.uploads);
      onUploaded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className={compact ? "upload compact" : "upload"}>
      <div
        className={`dropzone ${drag ? "drag" : ""} ${busy ? "busy" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          handle(e.dataTransfer.files);
        }}
      >
        <input
          ref={input}
          type="file"
          multiple
          accept=".csv,.xlsx,.xls,.txt"
          hidden
          onChange={(e) => handle(e.target.files)}
        />
        {busy ? (
          <span>Cleaning and classifying…</span>
        ) : compact ? (
          <span>
            <strong>+ Upload exports</strong>
          </span>
        ) : (
          <>
            <div className="dz-icon">⇪</div>
            <strong>Drop bank exports here</strong>
            <span className="muted">CSV or Excel from any bank. Multiple files and overlapping statements are fine.</span>
          </>
        )}
      </div>
      {error && <div className="alert error">{error}</div>}
      {results && !compact && (
        <ul className="upload-results">
          {results.map((r) => (
            <li key={r.filename}>
              <strong>{r.filename}</strong>: {r.rows_added} added
              {r.duplicates_skipped > 0 && <>, {r.duplicates_skipped} duplicates skipped</>}
              {r.flagged > 0 && <span className="pill warn">{r.flagged} flagged</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
