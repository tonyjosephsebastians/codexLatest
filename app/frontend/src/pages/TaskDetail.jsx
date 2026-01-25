import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { apiGet, apiPost } from "../lib/api.js";

export default function TaskDetail() {
  const { taskId } = useParams();
  const [status, setStatus] = useState("queued");
  const [startedAt, setStartedAt] = useState("");
  const [endedAt, setEndedAt] = useState("");
  const [error, setError] = useState("");
  const [logs, setLogs] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [patch, setPatch] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [applyResult, setApplyResult] = useState("");
  const [queuedMs, setQueuedMs] = useState(null);

  useEffect(() => {
    let active = true;
    const fetchStatus = async () => {
      try {
        const data = await apiGet(`/api/tasks/${taskId}`);
        if (!active) return;
        setStatus(data.status);
        setStartedAt(data.started_at || "");
        setEndedAt(data.ended_at || "");
        setError(data.error || "");
      } catch (err) {
        if (active) setError(err.message || "Failed to fetch status");
      }
    };
    fetchStatus();
    const timer = setInterval(fetchStatus, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [taskId]);

  useEffect(() => {
    let active = true;
    const fetchLogs = async () => {
      try {
        const data = await apiGet(`/api/tasks/${taskId}/logs?cursor=${cursor}`);
        if (!active) return;
        if (data.lines && data.lines.length) {
          setLogs((prev) => [...prev, ...data.lines]);
          data.lines.forEach((line) => {
            try {
              const parsed = JSON.parse(line);
              if (
                parsed &&
                parsed.event_type === "llm_throttle" &&
                typeof parsed.queued_ms === "number"
              ) {
                setQueuedMs(parsed.queued_ms);
              }
            } catch (parseErr) {
              // Ignore non-JSON log lines.
            }
          });
        }
        if (data.next_cursor !== null && data.next_cursor !== undefined) {
          setCursor(data.next_cursor);
        }
      } catch (err) {
        if (active) setError(err.message || "Failed to fetch logs");
      }
    };
    fetchLogs();
    const timer = setInterval(fetchLogs, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [taskId, cursor]);

  useEffect(() => {
    if (status === "succeeded" || status === "failed") {
      loadPatch();
    }
  }, [status]);

  const loadPatch = async () => {
    try {
      const data = await apiGet(`/api/tasks/${taskId}/patch`);
      setPatch(data.patch || "");
    } catch (err) {
      setPatch("");
    }
  };

  const copyPatch = async () => {
    await navigator.clipboard.writeText(patch);
  };

  const downloadPatch = () => {
    const blob = new Blob([patch], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${taskId}.patch`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const applyPatch = async () => {
    try {
      const data = await apiPost(`/api/tasks/${taskId}/apply`, { confirm: true });
      setApplyResult(data.status || "applied");
    } catch (err) {
      setApplyResult(err.message || "Apply failed");
    }
  };

  return (
    <div className="grid gap-6">
      <section className="card">
        <h2 className="card-title">Task {taskId}</h2>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="chip">Status: {status}</span>
          <span className="text-sm text-slate-600">
            Started: {startedAt || "-"}
          </span>
          <span className="text-sm text-slate-600">
            Ended: {endedAt || "-"}
          </span>
        </div>
        {queuedMs ? (
          <p className="mt-3 text-sm text-slate-600">
            Queued due to rate limit for {Math.round(queuedMs / 1000)}s.
          </p>
        ) : null}
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </section>

      <section className="card">
        <h3 className="card-title">Live logs</h3>
        <div className="mono-panel mt-4 max-h-[360px] overflow-auto whitespace-pre-wrap">
          {logs.length ? logs.map((line, idx) => <div key={idx}>{line}</div>) : ""}
        </div>
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="card-title">Patch</h3>
            <p className="card-subtitle">
              Diff ready for review. Refresh after the task finishes.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="button-secondary" type="button" onClick={loadPatch}>
              Refresh Patch
            </button>
            <button className="button-secondary" type="button" onClick={copyPatch}>
              Copy
            </button>
            <button className="button-secondary" type="button" onClick={downloadPatch}>
              Download
            </button>
            <button
              className="button-primary"
              type="button"
              onClick={() => setShowModal(true)}
            >
              Apply Patch
            </button>
          </div>
        </div>
        <div className="mono-panel mt-4 max-h-[320px] overflow-auto whitespace-pre">
          {patch ? patch : "No patch available."}
        </div>
        {applyResult ? (
          <p className="mt-3 text-sm text-slate-700">
            Apply result: {applyResult}
          </p>
        ) : null}
      </section>

      {showModal ? (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/50 p-6">
          <div className="card max-w-lg">
            <h3 className="card-title">Apply patch?</h3>
            <p className="card-subtitle mt-2">
              This will apply the patch directly to your workspace. Confirm to
              proceed.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <button
                className="button-primary"
                type="button"
                onClick={() => {
                  setShowModal(false);
                  applyPatch();
                }}
              >
                Confirm Apply
              </button>
              <button
                className="button-secondary"
                type="button"
                onClick={() => setShowModal(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
