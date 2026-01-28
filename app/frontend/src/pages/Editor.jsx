import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import CodeViewer from "../components/CodeViewer.jsx";
import FileTree from "../components/FileTree.jsx";
import { useWorkspace } from "../context/WorkspaceContext.jsx";
import { useToast } from "../context/ToastContext.jsx";
import { apiGet, apiPost } from "../lib/api.js";
import {
  Badge,
  ButtonPrimary,
  ButtonSecondary,
  PanelHeader,
  Select,
  Textarea,
  Input
} from "../components/ui.jsx";

const buildTreeUrl = (workspaceId) =>
  `/api/repo/tree?workspace_id=${encodeURIComponent(workspaceId)}`;

const buildFileUrl = (workspaceId, path) =>
  `/api/repo/file?workspace_id=${encodeURIComponent(
    workspaceId
  )}&path=${encodeURIComponent(path)}`;

const renderPatchLines = (patchText) =>
  patchText.split("\n").map((line, idx) => {
    let className = "text-slate-700";
    if (line.startsWith("+") && !line.startsWith("+++")) {
      className = "text-green-700 bg-green-50";
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      className = "text-red-700 bg-red-50";
    } else if (line.startsWith("@@")) {
      className = "text-primary-700 bg-primary-50";
    }
    return (
      <div key={`${idx}-${line}`} className={`px-4 py-1 ${className}`}>
        {line}
      </div>
    );
  });

export default function Editor() {
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const { pushToast } = useToast();
  const [provider, setProvider] = useState(
    localStorage.getItem("provider") || "gemini"
  );
  const [tree, setTree] = useState(null);
  const [treeError, setTreeError] = useState("");
  const [search, setSearch] = useState("");
  const [openFiles, setOpenFiles] = useState([]);
  const [activeFile, setActiveFile] = useState(null);
  const [fileCache, setFileCache] = useState({});
  const [fileSummaryCache, setFileSummaryCache] = useState({});
  const [fileSummary, setFileSummary] = useState("");
  const [fileSummaryCached, setFileSummaryCached] = useState(false);
  const [fileSummaryGeneratedAt, setFileSummaryGeneratedAt] = useState("");
  const [fileSummaryLoading, setFileSummaryLoading] = useState(false);
  const [taskPrompt, setTaskPrompt] = useState("");
  const [taskId, setTaskId] = useState("");
  const [taskStatus, setTaskStatus] = useState("");
  const [logs, setLogs] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [patch, setPatch] = useState("");
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [taskMessages, setTaskMessages] = useState([]);
  const lastStatusRef = useRef("");

  useEffect(() => {
    if (!activeWorkspaceId) {
      setTree(null);
      setOpenFiles([]);
      setActiveFile(null);
      setFileCache({});
      setFileSummaryCache({});
      setFileSummary("");
      setFileSummaryCached(false);
      setFileSummaryGeneratedAt("");
      setTaskId("");
      setTaskStatus("");
      setLogs([]);
      setPatch("");
      setTaskMessages([]);
      return;
    }
    const load = async () => {
      setTreeError("");
      setLoadingTree(true);
      try {
        const data = await apiGet(buildTreeUrl(activeWorkspaceId));
        setTree(data.tree);
      } catch (err) {
        setTreeError(err.message || "Failed to load tree");
      } finally {
        setLoadingTree(false);
      }
    };
    load();
  }, [activeWorkspaceId]);

  const openFile = async (path) => {
    if (!activeWorkspaceId) return;
    setError("");
    if (fileCache[path]) {
      setActiveFile(fileCache[path]);
      if (!openFiles.includes(path)) {
        setOpenFiles((prev) => [...prev, path]);
      }
      const cachedSummary = fileSummaryCache[path];
      if (cachedSummary) {
        setFileSummary(cachedSummary.summary_markdown || "");
        setFileSummaryCached(Boolean(cachedSummary.cached));
        setFileSummaryGeneratedAt(
          cachedSummary.generated_at
            ? new Date(cachedSummary.generated_at).toLocaleString()
            : ""
        );
      } else {
        setFileSummary("");
        setFileSummaryCached(false);
        setFileSummaryGeneratedAt("");
      }
      return;
    }
    setLoadingFile(true);
    setActiveFile(null);
    try {
      const data = await apiGet(buildFileUrl(activeWorkspaceId, path));
      setFileCache((prev) => ({ ...prev, [path]: data }));
      setOpenFiles((prev) => [...prev, path]);
      setActiveFile(data);
      const cachedSummary = fileSummaryCache[path];
      if (cachedSummary) {
        setFileSummary(cachedSummary.summary_markdown || "");
        setFileSummaryCached(Boolean(cachedSummary.cached));
        setFileSummaryGeneratedAt(
          cachedSummary.generated_at
            ? new Date(cachedSummary.generated_at).toLocaleString()
            : ""
        );
      } else {
        setFileSummary("");
        setFileSummaryCached(false);
        setFileSummaryGeneratedAt("");
      }
    } catch (err) {
      setError(err.message || "Failed to open file");
    } finally {
      setLoadingFile(false);
    }
  };

  const closeTab = (path) => {
    setOpenFiles((prev) => prev.filter((item) => item !== path));
    if (activeFile?.path === path) {
      const nextPath = openFiles.find((item) => item !== path);
      setActiveFile(nextPath ? fileCache[nextPath] : null);
    }
  };

  const runTask = async () => {
    if (!activeWorkspaceId) {
      setError("Open a workspace to run a task.");
      return;
    }
    if (!taskPrompt.trim()) {
      setError("Task prompt is required.");
      return;
    }
    setError("");
    setRunning(true);
    try {
      const data = await apiPost("/api/tasks", {
        workspace_id: activeWorkspaceId,
        task: taskPrompt.trim(),
        provider
      });
      setTaskId(data.task_id);
      setTaskMessages((prev) => [
        ...prev,
        { role: "user", content: taskPrompt.trim() }
      ]);
      setLogs([]);
      setCursor(0);
      setPatch("");
      localStorage.setItem("provider", provider);
      pushToast({
        title: "Task started",
        message: `Task ${data.task_id} running`,
        variant: "success"
      });
    } catch (err) {
      setError(err.message || "Failed to start task");
      setRunning(false);
      pushToast({
        title: "Task failed",
        message: err.message || "Failed to start task",
        variant: "error"
      });
    }
  };

  useEffect(() => {
    if (!taskId) return;
    const pollStatus = async () => {
      try {
        const data = await apiGet(`/api/tasks/${taskId}`);
        setTaskStatus(data.status);
        if (data.status === "succeeded" || data.status === "failed") {
          setRunning(false);
        }
      } catch (err) {
        setError(err.message || "Failed to fetch task status");
      }
    };
    pollStatus();
    const timer = setInterval(pollStatus, 2000);
    return () => clearInterval(timer);
  }, [taskId]);

  useEffect(() => {
    if (!taskStatus || taskStatus === lastStatusRef.current) return;
    lastStatusRef.current = taskStatus;
    if (taskStatus === "succeeded" || taskStatus === "failed") {
      setTaskMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            taskStatus === "succeeded"
              ? "Task completed. Patch is ready for review."
              : "Task failed. Review logs for details."
        }
      ]);
      pushToast({
        title: "Task finished",
        message:
          taskStatus === "succeeded"
            ? "Patch is ready for review"
            : "Task failed. Check logs.",
        variant: taskStatus === "succeeded" ? "success" : "error"
      });
    }
  }, [taskStatus]);

  useEffect(() => {
    if (!taskId) return;
    const fetchLogs = async () => {
      try {
        const data = await apiGet(`/api/tasks/${taskId}/logs?cursor=${cursor}`);
        if (data.lines?.length) {
          setLogs((prev) => [...prev, ...data.lines]);
        }
        if (data.next_cursor !== null && data.next_cursor !== undefined) {
          setCursor(data.next_cursor);
        }
      } catch (err) {
        setError(err.message || "Failed to fetch logs");
      }
    };
    fetchLogs();
    const timer = setInterval(fetchLogs, 2000);
    return () => clearInterval(timer);
  }, [taskId, cursor]);

  const loadPatch = async () => {
    if (!taskId) return;
    try {
      const data = await apiGet(`/api/tasks/${taskId}/patch`);
      setPatch(data.patch || "");
      pushToast({
        title: "Patch loaded",
        message: data.patch ? "Review changes before applying" : "No diff",
        variant: "success"
      });
    } catch (err) {
      setError(err.message || "Failed to load patch");
    }
  };

  const applyPatch = async () => {
    if (!taskId) return;
    try {
      const ok = window.confirm(
        "Apply patch to workspace? This will modify files."
      );
      if (!ok) return;
      await apiPost(`/api/tasks/${taskId}/apply`, { confirm: true });
      pushToast({
        title: "Patch applied",
        message: "Workspace updated",
        variant: "success"
      });
    } catch (err) {
      setError(err.message || "Failed to apply patch");
      pushToast({
        title: "Apply failed",
        message: err.message || "Failed to apply patch",
        variant: "error"
      });
    }
  };

  const handleLineClick = (path, line) => {
    setTaskPrompt(`Explain ${path} around line ${line}.`);
  };

  const loadFileSummary = async () => {
    if (!activeWorkspaceId || !activeFile?.path) {
      setError("Select a file to summarize.");
      return;
    }
    const cachedSummary = fileSummaryCache[activeFile.path];
    if (cachedSummary) {
      setFileSummary(cachedSummary.summary_markdown || "");
      setFileSummaryCached(Boolean(cachedSummary.cached));
      setFileSummaryGeneratedAt(
        cachedSummary.generated_at
          ? new Date(cachedSummary.generated_at).toLocaleString()
          : ""
      );
      pushToast({
        title: "Using cached summary",
        message: activeFile.path,
        variant: "success"
      });
      return;
    }
    setError("");
    setFileSummaryLoading(true);
    try {
      const data = await apiPost("/api/repo/file-summary", {
        workspace_id: activeWorkspaceId,
        path: activeFile.path,
        provider
      });
      setFileSummaryCache((prev) => ({
        ...prev,
        [activeFile.path]: data
      }));
      setFileSummary(data.summary_markdown || "");
      setFileSummaryCached(Boolean(data.cached));
      setFileSummaryGeneratedAt(
        data.generated_at ? new Date(data.generated_at).toLocaleString() : ""
      );
      pushToast({
        title: "Summary ready",
        message: activeFile.path,
        variant: "success"
      });
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        setError(
          err.data.retry_after_seconds
            ? `Rate limited. Retry in ${err.data.retry_after_seconds}s.`
            : "Rate limited. Please retry shortly."
        );
      } else {
        setError(err.message || "Failed to summarize file");
      }
    } finally {
      setFileSummaryLoading(false);
    }
  };

  const tabs = openFiles.map((path) => ({
    path,
    label: path.split(/[\\/]/).pop()
  }));

  useEffect(() => {
    if (!activeFile?.path) {
      setFileSummary("");
      setFileSummaryCached(false);
      setFileSummaryGeneratedAt("");
      return;
    }
    const cachedSummary = fileSummaryCache[activeFile.path];
    if (cachedSummary) {
      setFileSummary(cachedSummary.summary_markdown || "");
      setFileSummaryCached(Boolean(cachedSummary.cached));
      setFileSummaryGeneratedAt(
        cachedSummary.generated_at
          ? new Date(cachedSummary.generated_at).toLocaleString()
          : ""
      );
    } else {
      setFileSummary("");
      setFileSummaryCached(false);
      setFileSummaryGeneratedAt("");
    }
  }, [activeFile, fileSummaryCache]);

  return (
    <div className="grid h-full min-h-0 grid-cols-1 px-4 py-4 lg:grid-cols-[18rem_minmax(0,1fr)_420px]">
      <aside className="hidden min-h-0 border-r border-slate-200 bg-white lg:flex lg:flex-col">
        <PanelHeader
          title="Explorer"
          subtitle={activeWorkspace ? activeWorkspace.name : "No workspace"}
        />
        <div className="border-b border-slate-200 px-4 py-2">
          <Input
            placeholder="Search files..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="flex-1 min-h-0 overflow-auto px-2 py-2">
          {treeError ? (
            <p className="px-4 text-sm text-red-600">{treeError}</p>
          ) : null}
          {loadingTree ? (
            <div className="space-y-2 px-4">
              {Array.from({ length: 10 }).map((_, idx) => (
                <div key={idx} className="skeleton h-4" />
              ))}
            </div>
          ) : (
            <FileTree
              tree={tree}
              onSelectFile={openFile}
              filter={search}
              selectedPath={activeFile?.path}
            />
          )}
        </div>
      </aside>

      <section className="flex min-h-0 flex-col bg-white">
        <div className="border-b border-slate-200 px-4 py-2">
          {tabs.length ? (
            <div className="flex flex-wrap gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab.path}
                  className={`group flex items-center gap-2 rounded-md border px-2 py-2 text-xs font-semibold transition ${
                    activeFile?.path === tab.path
                      ? "border-primary-300 bg-primary-50 text-primary-700"
                      : "border-slate-200 text-slate-600 hover:border-primary-200"
                  }`}
                  type="button"
                  onClick={() => setActiveFile(fileCache[tab.path])}
                >
                  <span className="truncate">{tab.label}</span>
                  <span
                    className="text-slate-400 group-hover:text-slate-600"
                    onClick={(event) => {
                      event.stopPropagation();
                      closeTab(tab.path);
                    }}
                    role="button"
                  >
                    x
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="text-xs text-slate-500">
              No files open.
            </div>
          )}
        </div>
        <div className="border-b border-slate-200 px-4 py-2 text-xs text-slate-500">
          {activeFile?.path || "Select a file to preview"}
        </div>
        <div className="flex-1 min-h-0">
          <CodeViewer
            file={activeFile}
            onLineClick={handleLineClick}
            loading={loadingFile}
          />
        </div>
      </section>

      <aside className="hidden min-h-0 border-l border-slate-200 bg-white lg:flex lg:flex-col">
        <PanelHeader
          title="Agent Tasks"
          subtitle={taskStatus ? `Status: ${taskStatus}` : "Ready"}
          action={
            <Select
              className="h-8 w-[140px]"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              <option value="gemini">gemini</option>
              <option value="azure_mi">azure_mi</option>
            </Select>
          }
        />

        <div className="flex-1 min-h-0 overflow-auto bg-slate-50/70 px-4 py-4">
          {taskMessages.length ? (
            <div className="space-y-2">
              {taskMessages.map((message, idx) => (
                <div
                  key={`${message.role}-${idx}`}
                  className={`flex ${
                    message.role === "user" ? "justify-end" : "justify-start"
                  }`}
                >
                  <div
                    className={`max-w-[85%] rounded-md px-4 py-2 text-sm shadow-sm ${
                      message.role === "user"
                        ? "bg-primary-600 text-white"
                        : "bg-white text-slate-700"
                    }`}
                  >
                    <p className="text-[11px] uppercase tracking-wide opacity-70">
                      {message.role === "user" ? "You" : "Codex"}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap">
                      {message.content}
                    </p>
                  </div>
                </div>
              ))}
              {running ? (
                <div className="flex justify-start">
                  <div className="skeleton h-10 w-32" />
                </div>
              ) : null}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
              Start a task to see the conversation history here.
            </div>
          )}
        </div>

        <div className="border-t border-slate-200 p-4">
          <label className="label">Task prompt</label>
          <Textarea
            className="mt-2 min-h-[120px]"
            value={taskPrompt}
            onChange={(event) => setTaskPrompt(event.target.value)}
            placeholder="Fix bug, refactor, add feature..."
          />
          {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
          <div className="mt-2 flex flex-wrap gap-2">
            <ButtonPrimary type="button" onClick={runTask} disabled={running}>
              {running ? "Running" : "Run Task"}
            </ButtonPrimary>
            <ButtonSecondary type="button" disabled title="Stop is not available yet">
              Stop
            </ButtonSecondary>
            <ButtonSecondary type="button" onClick={loadPatch} disabled={!taskId}>
              View Patch
            </ButtonSecondary>
            <ButtonSecondary type="button" onClick={applyPatch} disabled={!taskId}>
              Apply Patch
            </ButtonSecondary>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge>Logs</Badge>
            <Badge>Patch</Badge>
            <Badge>File Summary</Badge>
          </div>

          <div className="mt-2 space-y-2">
            <details className="rounded-md border border-slate-200 bg-white p-2">
              <summary className="cursor-pointer text-sm font-semibold text-slate-700">
                Plan
              </summary>
              <div className="mt-2 rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-2 text-xs text-slate-500">
                Plan output will appear here when available.
              </div>
            </details>

            <details className="rounded-md border border-slate-200 bg-white p-2" open>
              <summary className="cursor-pointer text-sm font-semibold text-slate-700">
                Logs
              </summary>
              <div className="mt-2 max-h-64 overflow-auto rounded-md bg-slate-900 px-4 py-2 font-mono text-[11px] text-slate-100">
                {logs.length ? logs.join("\n") : "No logs yet."}
              </div>
            </details>

            <details className="rounded-md border border-slate-200 bg-white p-2">
              <summary className="cursor-pointer text-sm font-semibold text-slate-700">
                Patch
              </summary>
              <div className="mt-2 max-h-72 overflow-auto rounded-md border border-slate-200 bg-white font-mono text-[11px]">
                {patch ? renderPatchLines(patch) : (
                  <div className="px-4 py-2 text-slate-500">No patch loaded.</div>
                )}
              </div>
            </details>

            <details className="rounded-md border border-slate-200 bg-white p-2">
              <summary className="cursor-pointer text-sm font-semibold text-slate-700">
                File Summary
              </summary>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {fileSummaryCached ? <Badge>Cached</Badge> : null}
                {fileSummaryGeneratedAt ? (
                  <span className="text-xs text-slate-500">
                    Generated {fileSummaryGeneratedAt}
                  </span>
                ) : null}
                <ButtonSecondary
                  type="button"
                  onClick={loadFileSummary}
                  disabled={fileSummaryLoading || !activeFile?.path}
                >
                  {fileSummaryLoading ? "Summarizing" : "Generate Summary"}
                </ButtonSecondary>
              </div>
              <div className="mt-2 max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-700">
                {fileSummaryLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, idx) => (
                      <div key={idx} className="skeleton h-4" />
                    ))}
                  </div>
                ) : fileSummary ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeSanitize]}
                    className="prose prose-sm max-w-none"
                  >
                    {fileSummary}
                  </ReactMarkdown>
                ) : (
                  <div className="text-xs text-slate-500">
                    Generate a summary for the selected file.
                  </div>
                )}
              </div>
            </details>
          </div>
        </div>
      </aside>
    </div>
  );
}
