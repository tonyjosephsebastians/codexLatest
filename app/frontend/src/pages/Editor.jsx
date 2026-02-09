import { useEffect, useMemo, useRef, useState } from "react";

import CodeViewer from "../components/CodeViewer.jsx";
import FileTree from "../components/FileTree.jsx";
import { useWorkspace } from "../context/WorkspaceContext.jsx";
import { useToast } from "../context/ToastContext.jsx";
import { apiGet, apiPost } from "../lib/api.js";
import {
  Badge,
  ButtonPrimary,
  ButtonSecondary,
  Input,
  PanelHeader,
  Select,
  Textarea
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
      <div key={`${idx}-${line}`} className={`px-3 py-1 ${className}`}>
        {line}
      </div>
    );
  });

const flattenTree = (node) => {
  if (!node) return [];
  const files = [];
  const walk = (item) => {
    if (!item) return;
    if (item.type === "file") {
      files.push(item.path);
      return;
    }
    if (item.children) {
      item.children.forEach(walk);
    }
  };
  walk(node);
  return files;
};

const parsePatchForFile = (patchText, filePath) => {
  if (!patchText || !filePath) {
    return { added: new Set(), removed: new Set() };
  }
  const added = new Set();
  const removed = new Set();
  let currentFile = null;
  let oldLine = 0;
  let newLine = 0;
  patchText.split("\n").forEach((line) => {
    if (line.startsWith("+++ ")) {
      currentFile = line.replace("+++ ", "").replace(/^b\//, "");
      return;
    }
    if (line.startsWith("@@")) {
      const match = /@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@/.exec(line);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[3], 10);
      }
      return;
    }
    if (!currentFile || currentFile !== filePath) {
      return;
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      added.add(newLine);
      newLine += 1;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      removed.add(oldLine);
      oldLine += 1;
    } else {
      oldLine += 1;
      newLine += 1;
    }
  });
  return { added, removed };
};

function ExplorerPanel({
  tree,
  loading,
  error,
  filter,
  onFilterChange,
  onSelectFile,
  selectedPath
}) {
  return (
    <aside className="hidden min-h-0 flex-col border-r border-slate-200 bg-white lg:flex">
      <PanelHeader title="Explorer" subtitle="Workspace files" />
      <div className="border-b border-slate-200 px-3 py-2">
        <Input
          placeholder="Filter files..."
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto px-2 py-2">
        {error ? <p className="px-3 text-xs text-red-600">{error}</p> : null}
        {loading ? (
          <div className="space-y-2 px-3">
            {Array.from({ length: 12 }).map((_, idx) => (
              <div key={idx} className="skeleton h-4" />
            ))}
          </div>
        ) : (
          <FileTree
            tree={tree}
            onSelectFile={onSelectFile}
            filter={filter}
            selectedPath={selectedPath}
          />
        )}
      </div>
    </aside>
  );
}

function EditorPanel({
  tabs,
  activeFile,
  fileCache,
  onSelectTab,
  onCloseTab,
  onLineSelect,
  selectedRange,
  diffHighlights,
  loadingFile,
  showReviewBar,
  onApply,
  onDiscard,
  onViewDiff
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white">
      <div className="border-b border-slate-200 px-3 py-2">
        {tabs.length ? (
          <div className="flex flex-wrap gap-2">
            {tabs.map((tab) => (
              <button
                key={tab.path}
                className={`group flex items-center gap-2 rounded-md border px-2 py-1 text-xs font-semibold transition ${
                  activeFile?.path === tab.path
                    ? "border-primary-300 bg-primary-50 text-primary-700"
                    : "border-slate-200 text-slate-600 hover:border-primary-200"
                }`}
                type="button"
                onClick={() => onSelectTab(fileCache[tab.path])}
              >
                <span className="truncate">{tab.label}</span>
                <span
                  className="text-slate-400 group-hover:text-slate-600"
                  onClick={(event) => {
                    event.stopPropagation();
                    onCloseTab(tab.path);
                  }}
                  role="button"
                >
                  x
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-500">No files open.</div>
        )}
      </div>
      {showReviewBar ? (
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <span>Changes proposed</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="text-xs text-slate-500 hover:text-slate-700"
              onClick={onViewDiff}
            >
              View diff
            </button>
            <ButtonSecondary type="button" onClick={onDiscard}>
              Discard
            </ButtonSecondary>
            <ButtonPrimary type="button" onClick={onApply}>
              Apply
            </ButtonPrimary>
          </div>
        </div>
      ) : null}
      <div className="border-b border-slate-200 px-3 py-2 text-xs text-slate-500">
        {activeFile?.path || "Select a file from the explorer"}
      </div>
      <div className="flex-1 min-h-0">
        <CodeViewer
          file={activeFile}
          loading={loadingFile}
          onSelectRange={onLineSelect}
          selectedRange={selectedRange}
          diffHighlights={diffHighlights}
        />
      </div>
    </section>
  );
}

function ChatPanel({
  provider,
  onProviderChange,
  workspaceName,
  messages,
  input,
  onInputChange,
  onSubmit,
  loading,
  onQuickAction,
  contextPills,
  onRemovePill,
  mentionSuggestions,
  onSelectMention,
  showMentions,
  inputRef,
  onToggleActivity
}) {
  return (
    <aside className="hidden min-h-0 flex-col border-l border-slate-200 bg-white lg:flex">
      <PanelHeader
        title="Copilot Chat"
        subtitle={workspaceName || "Ask, refactor, or add features"}
        action={
          <div className="flex items-center gap-2">
            <Select
              className="h-8 w-[130px]"
              value={provider}
              onChange={(event) => onProviderChange(event.target.value)}
            >
              <option value="gemini">gemini</option>
              <option value="azure_mi">azure_mi</option>
            </Select>
            <ButtonSecondary type="button" onClick={onToggleActivity}>
              Activity
            </ButtonSecondary>
          </div>
        }
      />
      <div className="flex-1 min-h-0 overflow-auto bg-slate-50/60 px-4 py-3">
        {messages.length ? (
          <div className="space-y-3">
            {messages.map((message, idx) => (
              <div
                key={`${message.role}-${idx}`}
                className={`flex ${
                  message.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-[85%] rounded-md px-3 py-2 text-sm shadow-sm ${
                    message.role === "user"
                      ? "bg-primary-600 text-white"
                      : "bg-white text-slate-700"
                  }`}
                >
                  <p className="text-[10px] uppercase tracking-wide opacity-60">
                    {message.role === "user" ? "You" : "Codex"}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap">{message.content}</p>
                </div>
              </div>
            ))}
            {loading ? (
              <div className="flex justify-start">
                <div className="skeleton h-10 w-32" />
              </div>
            ) : null}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
            Ask Codex to explain, fix bugs, or refactor the current file.
          </div>
        )}
      </div>
      <form className="border-t border-slate-200 p-3" onSubmit={onSubmit}>
        <div className="mb-2 flex flex-wrap gap-2">
          {["Explain file", "Find bug", "Refactor", "Add tests"].map((label) => (
            <button
              key={label}
              type="button"
              className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold text-slate-600 hover:border-primary-200 hover:text-primary-700"
              onClick={() => onQuickAction(label)}
            >
              {label}
            </button>
          ))}
        </div>
        {contextPills.length ? (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {contextPills.map((pill) => (
              <div key={pill.id} className="flex items-center gap-1">
                <Badge>{pill.label}</Badge>
                {pill.removable ? (
                  <button
                    type="button"
                    className="text-[11px] text-slate-500 hover:text-primary-600"
                    onClick={() => onRemovePill(pill.id)}
                  >
                    x
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
        <Textarea
          ref={inputRef}
          className="min-h-[96px]"
          value={input}
          onChange={(event) => onInputChange(event.target.value)}
          placeholder="Type a request or /command..."
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit(event);
            }
          }}
        />
        {showMentions && mentionSuggestions.length ? (
          <div className="relative">
            <div className="absolute -top-2 left-0 z-20 w-full rounded-md border border-slate-200 bg-white shadow">
              {mentionSuggestions.map((item) => (
                <button
                  key={item}
                  type="button"
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
                  onClick={() => onSelectMention(item)}
                >
                  <span className="truncate">{item}</span>
                  <span className="text-[10px] text-slate-400">file</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className="mt-2 flex items-center justify-between">
          <p className="text-xs text-slate-500">Shift + Enter for newline</p>
          <ButtonPrimary type="submit" disabled={loading}>
            {loading ? "Thinking" : "Send"}
          </ButtonPrimary>
        </div>
      </form>
    </aside>
  );
}

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
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [messages, setMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [fileMentions, setFileMentions] = useState([]);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionSuggestions, setMentionSuggestions] = useState([]);
  const [showMentions, setShowMentions] = useState(false);
  const [selectedRange, setSelectedRange] = useState(null);
  const [selectionAnchor, setSelectionAnchor] = useState(null);
  const [includeCurrentFile, setIncludeCurrentFile] = useState(true);
  const [includeSelection, setIncludeSelection] = useState(true);
  const [proposedPatch, setProposedPatch] = useState("");
  const [showReview, setShowReview] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  const [activity, setActivity] = useState([]);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (!activeWorkspaceId) {
      setTree(null);
      setOpenFiles([]);
      setActiveFile(null);
      setFileCache({});
      setMessages([]);
      setChatInput("");
      setSelectedRange(null);
      setSelectionAnchor(null);
      setProposedPatch("");
      setShowReview(false);
      setActivity([]);
      setError("");
      setFileMentions([]);
      setMentionQuery("");
      setMentionSuggestions([]);
      setShowMentions(false);
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

  const fileList = useMemo(() => flattenTree(tree), [tree]);

  useEffect(() => {
    setSelectedRange(null);
    setSelectionAnchor(null);
    setIncludeCurrentFile(true);
    setIncludeSelection(true);
  }, [activeFile?.path]);

  const openFile = async (path) => {
    if (!activeWorkspaceId) return;
    setError("");
    if (fileCache[path]) {
      setActiveFile(fileCache[path]);
      if (!openFiles.includes(path)) {
        setOpenFiles((prev) => [...prev, path]);
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

  const handleLineSelect = (line, isRange) => {
    if (!activeFile) return;
    setIncludeCurrentFile(true);
    setIncludeSelection(true);
    if (!isRange || selectionAnchor === null) {
      setSelectionAnchor(line);
      setSelectedRange({ start_line: line, end_line: line });
      return;
    }
    const start = Math.min(selectionAnchor, line);
    const end = Math.max(selectionAnchor, line);
    setSelectedRange({ start_line: start, end_line: end });
  };

  const contextPills = useMemo(() => {
    const pills = [];
    if (activeFile && includeCurrentFile) {
      pills.push({ id: "current", label: activeFile.path, removable: true });
    }
    if (activeFile && includeCurrentFile && selectedRange && includeSelection) {
      pills.push({
        id: "selection",
        label: `${activeFile.path}:${selectedRange.start_line}-${selectedRange.end_line}`,
        removable: true
      });
    }
    fileMentions.forEach((path) => {
      pills.push({ id: `mention:${path}`, label: `@${path}`, removable: true });
    });
    return pills;
  }, [
    activeFile,
    includeCurrentFile,
    includeSelection,
    selectedRange,
    fileMentions
  ]);

  const updateMentionsFromInput = (value) => {
    const cursor =
      inputRef.current && typeof inputRef.current.selectionStart === "number"
        ? inputRef.current.selectionStart
        : value.length;
    const before = value.slice(0, cursor);
    const match = /@([A-Za-z0-9_./-]*)$/.exec(before);
    if (match) {
      const query = match[1] || "";
      setMentionQuery(query);
      const suggestions = fileList
        .filter((path) =>
          path.toLowerCase().includes(query.toLowerCase())
        )
        .slice(0, 8);
      setMentionSuggestions(suggestions);
      setShowMentions(Boolean(suggestions.length));
    } else {
      setMentionQuery("");
      setMentionSuggestions([]);
      setShowMentions(false);
    }
  };

  const handleInputChange = (value) => {
    setChatInput(value);
    updateMentionsFromInput(value);
  };

  const handleSelectMention = (path) => {
    const value = chatInput;
    const cursor =
      inputRef.current && typeof inputRef.current.selectionStart === "number"
        ? inputRef.current.selectionStart
        : value.length;
    const before = value.slice(0, cursor);
    const after = value.slice(cursor);
    const nextBefore = before.replace(/@([A-Za-z0-9_./-]*)$/, `@${path} `);
    const next = `${nextBefore}${after}`;
    setChatInput(next);
    setFileMentions((prev) =>
      prev.includes(path) ? prev : [...prev, path]
    );
    setMentionQuery("");
    setMentionSuggestions([]);
    setShowMentions(false);
    requestAnimationFrame(() => {
      if (inputRef.current) {
        inputRef.current.focus();
      }
    });
  };

  const removeMention = (path) => {
    setFileMentions((prev) => prev.filter((item) => item !== path));
  };

  const sendChat = async (event) => {
    event.preventDefault();
    if (!activeWorkspaceId) {
      setError("Open a workspace to chat.");
      return;
    }
    if (!chatInput.trim()) return;
    const inlineMentions = Array.from(
      new Set(
        (chatInput.match(/@([A-Za-z0-9_./-]+)/g) || []).map((item) =>
          item.replace("@", "")
        )
      )
    );
    const combinedMentions = Array.from(
      new Set([...fileMentions, ...inlineMentions])
    );
    const nextMessages = [
      ...messages,
      { role: "user", content: chatInput.trim() }
    ];
    setMessages(nextMessages);
    setChatInput("");
    setMentionQuery("");
    setMentionSuggestions([]);
    setShowMentions(false);
    setChatLoading(true);
    setError("");
    const context =
      activeFile && includeCurrentFile
        ? {
            file_path: activeFile.path,
            selection:
              selectedRange && includeSelection ? selectedRange : undefined,
            open_files: openFiles,
            file_mentions: combinedMentions
          }
        : {
            file_path: undefined,
            selection: undefined,
            open_files: openFiles,
            file_mentions: combinedMentions
          };

    try {
      const data = await apiPost("/api/editor/chat", {
        workspace_id: activeWorkspaceId,
        provider,
        messages: nextMessages,
        context
      });
      setMessages([
        ...nextMessages,
        data.assistant_message || { role: "assistant", content: "" }
      ]);
      if (data.proposed_changes?.patch) {
        setProposedPatch(data.proposed_changes.patch);
        setShowReview(true);
      }
      setActivity((prev) => [
        {
          ts: new Date().toLocaleTimeString(),
          message: "Chat response received"
        },
        ...prev
      ]);
      localStorage.setItem("provider", provider);
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        setError(
          err.data.retry_after_seconds
            ? `Rate limited. Retry in ${err.data.retry_after_seconds}s.`
            : "Rate limited. Please retry shortly."
        );
      } else {
        setError(err.message || "Chat failed");
      }
      setActivity((prev) => [
        {
          ts: new Date().toLocaleTimeString(),
          message: "Chat failed"
        },
        ...prev
      ]);
    } finally {
      setChatLoading(false);
    }
  };

  const handleQuickAction = (label) => {
    if (!activeFile) {
      setError("Open a file first.");
      return;
    }
    const map = {
      "Explain file": "/explain",
      "Find bug": "/fix",
      Refactor: "/refactor",
      "Add tests": "/test"
    };
    const next = `${map[label] || ""} ${activeFile.path}`.trim();
    setChatInput(next);
    updateMentionsFromInput(next);
  };

  const applyPatch = async () => {
    if (!proposedPatch || !activeWorkspaceId) return;
    try {
      await apiPost("/api/patch/apply", {
        workspace_id: activeWorkspaceId,
        patch: proposedPatch,
        confirm: true,
        repo_scope: { allow_apply_patch: true }
      });
      pushToast({
        title: "Patch applied",
        message: "Changes written to workspace",
        variant: "success"
      });
      setShowReview(false);
      setProposedPatch("");
    } catch (err) {
      pushToast({
        title: "Apply failed",
        message: err.message || "Failed to apply patch",
        variant: "error"
      });
    }
  };

  const tabs = openFiles.map((path) => ({
    path,
    label: path.split(/[\\/]/).pop()
  }));

  const diffHighlights = useMemo(() => {
    if (!activeFile?.path || !proposedPatch) {
      return { added: new Set(), removed: new Set() };
    }
    return parsePatchForFile(proposedPatch, activeFile.path);
  }, [proposedPatch, activeFile?.path]);

  const handleRemovePill = (pillId) => {
    if (pillId === "current") {
      setIncludeCurrentFile(false);
      return;
    }
    if (pillId === "selection") {
      setIncludeSelection(false);
      return;
    }
    if (pillId.startsWith("mention:")) {
      removeMention(pillId.replace("mention:", ""));
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[18rem_minmax(0,1fr)_420px]">
      <ExplorerPanel
        tree={tree}
        loading={loadingTree}
        error={treeError}
        filter={search}
        onFilterChange={setSearch}
        onSelectFile={openFile}
        selectedPath={activeFile?.path}
      />

      <EditorPanel
        tabs={tabs}
        activeFile={activeFile}
        fileCache={fileCache}
        onSelectTab={setActiveFile}
        onCloseTab={closeTab}
        onLineSelect={handleLineSelect}
        selectedRange={selectedRange}
        diffHighlights={diffHighlights}
        loadingFile={loadingFile}
        showReviewBar={Boolean(proposedPatch)}
        onApply={applyPatch}
        onDiscard={() => {
          setProposedPatch("");
          setShowReview(false);
        }}
        onViewDiff={() => setShowReview(true)}
      />

      <ChatPanel
        provider={provider}
        onProviderChange={setProvider}
        workspaceName={activeWorkspace?.name}
        messages={messages}
        input={chatInput}
        onInputChange={handleInputChange}
        onSubmit={sendChat}
        loading={chatLoading}
        onQuickAction={handleQuickAction}
        contextPills={contextPills}
        onRemovePill={handleRemovePill}
        mentionSuggestions={mentionSuggestions}
        onSelectMention={handleSelectMention}
        showMentions={showMentions}
        inputRef={inputRef}
        onToggleActivity={() => setShowActivity(true)}
      />

      {showReview ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="flex h-[70vh] w-full max-w-3xl flex-col rounded-md border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
              <div>
                <p className="text-sm font-semibold text-slate-900">
                  Review changes
                </p>
                <p className="text-xs text-slate-500">
                  Preview the proposed patch before applying.
                </p>
              </div>
              <button
                type="button"
                className="text-sm text-slate-500 hover:text-slate-700"
                onClick={() => setShowReview(false)}
              >
                Close
              </button>
            </div>
            <div className="flex-1 overflow-auto bg-slate-50 font-mono text-xs">
              {renderPatchLines(proposedPatch)}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
              <ButtonSecondary
                type="button"
                onClick={() => {
                  setShowReview(false);
                  setProposedPatch("");
                }}
              >
                Discard
              </ButtonSecondary>
              <ButtonPrimary type="button" onClick={applyPatch}>
                Apply
              </ButtonPrimary>
            </div>
          </div>
        </div>
      ) : null}

      {showActivity ? (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/20">
          <div className="flex h-full w-[360px] flex-col border-l border-slate-200 bg-white">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <p className="text-sm font-semibold text-slate-900">Activity</p>
              <button
                type="button"
                className="text-xs text-slate-500 hover:text-slate-700"
                onClick={() => setShowActivity(false)}
              >
                Close
              </button>
            </div>
            <div className="flex-1 overflow-auto px-4 py-3 text-sm text-slate-600">
              {activity.length ? (
                <ul className="space-y-2">
                  {activity.map((item, idx) => (
                    <li key={`${item.ts}-${idx}`} className="text-xs">
                      <span className="mr-2 text-slate-400">{item.ts}</span>
                      {item.message}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-slate-500">No activity yet.</p>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="fixed bottom-4 left-4 rounded-md border border-red-200 bg-white px-3 py-2 text-xs text-red-600 shadow">
          {error}
        </div>
      ) : null}
    </div>
  );
}
