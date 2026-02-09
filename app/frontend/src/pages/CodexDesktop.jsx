import { useEffect, useMemo, useRef, useState } from "react";
import MonacoEditor from "@monaco-editor/react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import mermaid from "mermaid";
import {
  Bot,
  CircleDot,
  Copy,
  Diff,
  FolderOpen,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Plus,
  Search,
  Send,
  Settings2,
  X
} from "lucide-react";
import {
  Button as AntButton,
  Input as AntInput,
  Tag,
} from "antd";

import {
  Box,
  Chip,
  Divider,
  Paper,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";

import WorkspaceModal from "../components/WorkspaceModal.jsx";
import { useToast } from "../context/ToastContext.jsx";
import { useWorkspace } from "../context/WorkspaceContext.jsx";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "../lib/api.js";
import {
  Badge,
  ButtonPrimary,
  ButtonSecondary,
  Input,
  Select
} from "../components/ui.jsx";

mermaid.initialize({
  startOnLoad: false,
  theme: "base",
  themeVariables: {
    primaryColor: "#f0fdf4",
    primaryTextColor: "#052e16",
    primaryBorderColor: "#166534",
    lineColor: "#14532d",
    secondaryColor: "#f8fafc",
    tertiaryColor: "#ffffff",
    background: "#ffffff",
    mainBkg: "#f0fdf4",
    secondBkg: "#ffffff",
    tertiaryBkg: "#f8fafc",
    clusterBkg: "#f8fafc",
    clusterBorder: "#d1d5db",
    edgeLabelBackground: "#ffffff",
    fontFamily: "Manrope"
  }
});

const DEFAULT_MODEL_BY_PROVIDER = {
  openai: "gpt-4o",
  azure: ""
};

const OPENAI_MODELS = ["gpt-4o", "gpt-4.1", "gpt-5.1"];
const REASONING_OPTIONS = ["medium", "high", "extra_high"];

const DRAWER_TABS = [
  { id: "diffs", label: "Diffs", icon: Diff },
  { id: "context", label: "Context", icon: Paperclip },
  { id: "activity", label: "Activity", icon: CircleDot },
  { id: "settings", label: "Settings", icon: Settings2 },
];
const SLASH_HINTS = [
  "/explain",
  "/summarize",
  "/fix",
  "/refactor",
  "/test",
  "/search",
  "/wiki",
  "/diagram"
];
const EMPTY_THREAD_TITLE = "New thread";

const normalizeThread = (item) => ({
  thread_id: item.thread_id,
  title: item.title || EMPTY_THREAD_TITLE,
  pinned: Boolean(item.pinned),
  workspace_id: item.workspace_id || "",
  created_at: item.created_at || new Date().toISOString(),
  updated_at: item.updated_at || item.created_at || new Date().toISOString(),
  messages: Array.isArray(item.messages) ? item.messages : []
});

const sortThreads = (items) =>
  [...items].sort((a, b) => {
    if (a.pinned !== b.pinned) {
      return a.pinned ? -1 : 1;
    }
    const aTs = Date.parse(a.updated_at || a.created_at || "") || 0;
    const bTs = Date.parse(b.updated_at || b.created_at || "") || 0;
    return bTs - aTs;
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
    if (Array.isArray(item.children)) {
      item.children.forEach(walk);
    }
  };
  walk(node);
  return files;
};

const flattenDirectories = (node) => {
  if (!node) return [];
  const dirs = [];
  const walk = (item) => {
    if (!item || item.type === "file") return;
    if (item.path && item.path !== ".") {
      dirs.push(item.path);
    }
    if (Array.isArray(item.children)) {
      item.children.forEach(walk);
    }
  };
  walk(node);
  return dirs;
};

const parsePatchChangedFiles = (patch, explicit = []) => {
  const fromPatch = new Set(explicit);
  if (!patch) return Array.from(fromPatch);
  patch.split("\n").forEach((line) => {
    if (!line.startsWith("+++ ")) return;
    const next = line.replace("+++ ", "").replace(/^b\//, "").trim();
    if (next && next !== "/dev/null") fromPatch.add(next);
  });
  return Array.from(fromPatch);
};

const parseDiffMeta = (patch) => {
  if (!patch) return [];
  const result = [];
  let current = null;
  patch.split("\n").forEach((line) => {
    if (line.startsWith("+++ ")) {
      const path = line.replace("+++ ", "").replace(/^b\//, "").trim();
      if (!path || path === "/dev/null") {
        current = null;
        return;
      }
      current = { path, added: 0, removed: 0 };
      result.push(current);
      return;
    }
    if (!current) return;
    if (line.startsWith("+") && !line.startsWith("+++")) current.added += 1;
    if (line.startsWith("-") && !line.startsWith("---")) current.removed += 1;
  });
  return result;
};

const summarizeThreadTitle = (thread) => {
  if (thread.title && thread.title !== EMPTY_THREAD_TITLE) {
    return thread.title;
  }
  const firstUser = thread.messages.find(
    (msg) => msg.type === "chat" && msg.role === "user"
  );
  if (!firstUser) return EMPTY_THREAD_TITLE;
  return firstUser.content.slice(0, 48) || EMPTY_THREAD_TITLE;
};

const toSlug = (text) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");

const parseHeadings = (markdown) => {
  if (!markdown) return [];
  return markdown
    .split("\n")
    .map((line) => /^(##|###)\s+(.+)$/.exec(line.trim()))
    .filter(Boolean)
    .map((match) => ({
      level: match[1] === "##" ? 2 : 3,
      text: match[2].trim(),
      id: toSlug(match[2].trim())
    }));
};

const parseInlineCitations = (text) => {
  if (!text) return [];
  const pattern = /([A-Za-z0-9_./-]+):L(\d+)-L(\d+)/g;
  const out = [];
  const seen = new Set();
  let match = pattern.exec(text);
  while (match) {
    const key = `${match[1]}:${match[2]}:${match[3]}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push({
        path: match[1],
        start_line: Number(match[2]),
        end_line: Number(match[3]),
      });
    }
    match = pattern.exec(text);
  }
  return out;
};

function MermaidBlock({ code }) {
  const [svg, setSvg] = useState("");
  const renderId = useRef(`mermaid-${Math.random().toString(36).slice(2, 10)}`);

  useEffect(() => {
    let active = true;
    const render = async () => {
      try {
        const rendered = await mermaid.render(renderId.current, code);
        if (active) setSvg(rendered.svg);
      } catch {
        if (active) setSvg("");
      }
    };
    render();
    return () => {
      active = false;
    };
  }, [code]);

  if (!svg) {
    return (
      <pre className="overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
        {code}
      </pre>
    );
  }
  return <div className="overflow-auto" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function MessageBubble({
  message,
  fileSet,
  onOpenFile,
  onReviewChanges,
  onOpenCitation,
}) {
  if (message.type === "event") {
    return (
      <div className="mx-1 border-l-2 border-emerald-600/80 pl-3 text-xs font-medium text-slate-500">
        {message.content}
      </div>
    );
  }

  if (message.type === "review") {
    const files = parsePatchChangedFiles(message.patch, message.filesChanged);
    return (
      <div className="mx-1 rounded-sm border border-emerald-200 bg-emerald-50/70 p-3 text-sm shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-emerald-900">Review changes</p>
          <button
            type="button"
            className="text-xs font-semibold text-emerald-700 hover:text-emerald-900"
            onClick={() => onReviewChanges(message.patch, files)}
          >
            View diff
          </button>
        </div>
        <p className="mt-1 text-xs text-emerald-800">
          {files.length ? `${files.length} file(s) changed` : "Patch proposed"}
        </p>
      </div>
    );
  }

  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={[
          "max-w-[78ch] rounded-sm border px-4 py-3 text-sm leading-6 shadow-sm",
          isUser
            ? "border-emerald-700 bg-emerald-700 text-white"
            : "border-slate-200 bg-white text-slate-800"
        ].join(" ")}
      >
        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] opacity-70">
          {isUser ? "You" : "Codex"}
        </div>
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSanitize]}
              components={{
                code({ inline, children, className }) {
                  const raw = String(children || "").trim();
                  if (!inline && className?.includes("language-mermaid")) {
                    return <MermaidBlock code={raw} />;
                  }
                  if (inline && fileSet.has(raw)) {
                    return (
                      <button
                        type="button"
                        className="rounded border border-slate-300 bg-slate-100 px-1.5 py-0.5 text-xs text-emerald-700 hover:border-emerald-300"
                        onClick={() => onOpenFile(raw)}
                      >
                        {raw}
                      </button>
                    );
                  }
                  return (
                    <code className="rounded bg-slate-100 px-1 py-0.5 text-xs text-slate-800">
                      {raw}
                    </code>
                  );
                },
                p(props) {
                  return <p className="mb-2 whitespace-pre-wrap" {...props} />;
                },
                ul(props) {
                  return <ul className="mb-2 list-disc pl-5" {...props} />;
                },
                ol(props) {
                  return <ol className="mb-2 list-decimal pl-5" {...props} />;
                },
                h1(props) {
                  return (
                    <h1 className="mb-2 mt-4 text-xl font-semibold text-slate-900" {...props} />
                  );
                },
                h2(props) {
                  return (
                    <h2 className="mb-2 mt-4 text-lg font-semibold text-slate-900" {...props} />
                  );
                },
                h3(props) {
                  return (
                    <h3 className="mb-2 mt-3 text-base font-semibold text-slate-900" {...props} />
                  );
                },
                pre(props) {
                  return (
                    <pre
                      className="mb-2 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
                      {...props}
                    />
                  );
                }
              }}
            >
              {message.content}
            </ReactMarkdown>
            {Array.isArray(message.citations) && message.citations.length ? (
              <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                {message.citations.slice(0, 10).map((citation, idx) => (
                  <Chip
                    key={`${citation.path}-${citation.start_line}-${idx}`}
                    size="small"
                    variant="outlined"
                    label={`${citation.path}:L${citation.start_line || 1}-L${
                      citation.end_line || citation.start_line || 1
                    }`}
                    onClick={() => onOpenCitation(citation)}
                  />
                ))}
              </Box>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function DiffView({ patch }) {
  const lines = patch ? patch.split("\n") : [];
  return (
    <div className="h-full overflow-auto bg-white font-mono text-xs">
      {lines.length ? (
        lines.map((line, idx) => {
          let row = "text-slate-700";
          if (line.startsWith("+") && !line.startsWith("+++")) {
            row = "bg-emerald-50 text-emerald-800";
          } else if (line.startsWith("-") && !line.startsWith("---")) {
            row = "bg-red-50 text-red-700";
          } else if (line.startsWith("@@")) {
            row = "bg-slate-100 text-slate-700";
          }
          return (
            <div key={`${idx}-${line}`} className={`border-b border-slate-100 px-3 py-1 ${row}`}>
              {line || " "}
            </div>
          );
        })
      ) : (
        <div className="p-4 text-sm text-slate-500">No diff to display.</div>
      )}
    </div>
  );
}

function FileViewer({
  file,
  loading,
  editable,
  content,
  onContentChange,
  onSelectionChange,
}) {
  if (loading) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 12 }).map((_, idx) => (
          <div key={idx} className="skeleton h-4" />
        ))}
      </div>
    );
  }

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">
        Select a file reference from chat or use @mentions to load code context.
      </div>
    );
  }

  return (
    <MonacoEditor
      height="100%"
      language={file.language || "plaintext"}
      value={content}
      theme="vs"
      options={{
        readOnly: !editable,
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbersMinChars: 4,
        renderLineHighlight: "all",
        scrollBeyondLastLine: false,
        automaticLayout: true
      }}
      onChange={(value) => onContentChange(value ?? "")}
      onMount={(editor) => {
        editor.onDidChangeCursorSelection((event) => {
          const selection = event.selection;
          if (!selection) return;
          onSelectionChange({
            start_line: selection.startLineNumber,
            end_line: selection.endLineNumber
          });
        });
      }}
    />
  );
}

function Sidebar({
  threads,
  activeThreadId,
  onThreadSelect,
  onNewThread,
  workspaces,
  activeWorkspaceId,
  onWorkspaceChange,
  onOpenFolder,
  threadMenuId,
  onThreadMenu,
  renamingThreadId,
  renameDraft,
  onRenameDraft,
  onRenameStart,
  onRenameSubmit,
  onRenameCancel,
  onTogglePin,
  onDeleteThread,
  collapsed,
  onToggleCollapse,
  threadSearch,
  onThreadSearch,
  tree,
  fileList,
  fileSearch,
  onFileSearch,
  expandedPaths,
  onTogglePath,
  onOpenFile,
  activeFilePath
}) {
  return (
    <aside
      className={[
        "flex min-h-0 flex-col border-r border-[#2a7f33] bg-gradient-to-b from-[#1f5d2c] to-[#35B234] text-white transition-all",
        "w-72",
      ].join(" ")}
    >
      <div className="flex items-center justify-between border-b border-[#2a7f33] px-3 py-3">
        <div>
          <>
            <p className="text-[10px] uppercase tracking-[0.24em] text-emerald-50/80">
              Codex V2.0
            </p>
            <p className="mt-1 text-sm font-semibold text-white">Web Workspace</p>
          </>
        </div>
      </div>

      <div className="border-b border-[#2a7f33] px-3 py-3">
        <button
          type="button"
          className="inline-flex w-full items-center gap-2 rounded-md border border-white/40 bg-white px-3 py-2 text-sm font-medium text-slate-900 shadow-sm hover:border-white"
          onClick={onNewThread}
          title="New thread"
        >
          <Plus size={14} />
          New thread
        </button>
      </div>

      <div className="border-b border-[#2a7f33] px-3 py-3">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-emerald-50/90">
          Workspace
        </p>
        <Select
          className="h-9 border-slate-300 bg-white text-slate-900"
          value={activeWorkspaceId}
          onChange={(event) => onWorkspaceChange(event.target.value)}
        >
          <option value="">Select local folder</option>
          {workspaces.map((workspace) => (
            <option key={workspace.workspace_id} value={workspace.workspace_id}>
              {workspace.name}
            </option>
          ))}
        </Select>
        <button
          type="button"
          className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/40 bg-white px-3 py-2 text-sm font-medium text-slate-900 shadow-sm hover:border-white"
          onClick={onOpenFolder}
        >
          <FolderOpen size={14} />
          Open Folder
        </button>
      </div>

      <div className="border-b border-[#2a7f33] px-3 py-2">
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
          />
          <Input
            className="h-8 border-slate-300 bg-white pl-8 text-xs text-slate-900 placeholder:text-slate-400"
            value={threadSearch}
            onChange={(event) => onThreadSearch(event.target.value)}
            placeholder="Search threads..."
          />
        </div>
      </div>

      <div className="border-b border-[#2a7f33] px-3 py-2">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-emerald-50/90">
          Files
        </p>
        <Input
          className="h-8 border-slate-300 bg-white text-xs text-slate-900 placeholder:text-slate-400"
          value={fileSearch}
          onChange={(event) => onFileSearch(event.target.value)}
          placeholder="Search files..."
        />
        <div className="mt-2 max-h-48 overflow-auto rounded-sm border border-slate-300 bg-white p-1">
          {fileSearch.trim() ? (
            fileList
              .filter((path) =>
                path.toLowerCase().includes(fileSearch.trim().toLowerCase())
              )
              .slice(0, 250)
              .map((path) => (
                <button
                  key={path}
                  type="button"
                  className={[
                    "block w-full rounded-sm px-2 py-1 text-left text-xs",
                    activeFilePath === path
                      ? "bg-emerald-50 text-emerald-800"
                      : "text-slate-700 hover:bg-slate-100"
                  ].join(" ")}
                  onClick={() => onOpenFile(path)}
                >
                  {path}
                </button>
              ))
          ) : tree ? (
            <TreeNode
              node={tree}
              expandedPaths={expandedPaths}
              onTogglePath={onTogglePath}
              onOpenFile={onOpenFile}
              activePath={activeFilePath || ""}
            />
          ) : (
            <p className="px-2 py-1 text-xs text-slate-500">No files loaded.</p>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-2 py-3">
        <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-emerald-50/90">
          Threads
        </p>
        <div className="space-y-1">
          {threads.map((thread) => (
            <div
              key={thread.thread_id}
              className={[
                "group relative rounded-md border px-3 py-2 shadow-sm",
                thread.thread_id === activeThreadId
                  ? "border-[#54B949] bg-[#eef9ec] text-slate-900"
                  : "border-transparent bg-white text-slate-800 hover:border-slate-300"
              ].join(" ")}
            >
              {renamingThreadId === thread.thread_id ? (
                <div className="flex items-center gap-1">
                  <input
                    className="h-8 min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 text-xs text-slate-900 outline-none focus:ring-2 focus:ring-primary-500/30"
                    value={renameDraft}
                    onChange={(event) => onRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        onRenameSubmit(thread.thread_id);
                      }
                      if (event.key === "Escape") {
                        event.preventDefault();
                        onRenameCancel();
                      }
                    }}
                    autoFocus
                  />
                  <button
                    type="button"
                    className="rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-700"
                    onClick={() => onRenameSubmit(thread.thread_id)}
                  >
                    Save
                  </button>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => onThreadSelect(thread.thread_id)}
                    className="w-full text-left"
                  >
                    <div className="flex items-start gap-1">
                      <span
                        className={[
                          "mt-1 inline-block h-1.5 w-1.5 rounded-full",
                          thread.thread_id === activeThreadId
                            ? "bg-primary-600"
                            : "bg-slate-300"
                        ].join(" ")}
                      />
                      <p className="min-w-0 flex-1 truncate font-medium">
                        {summarizeThreadTitle(thread)}
                      </p>
                      {thread.pinned ? (
                        <span className="text-[10px] text-[#3a9f30]">PIN</span>
                      ) : null}
                    </div>
                    <p className="text-[11px] text-slate-500">
                      {new Date(
                        thread.updated_at || thread.created_at
                      ).toLocaleDateString()}
                    </p>
                  </button>
                  <button
                    type="button"
                    className="absolute right-2 top-2 hidden rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-50 group-hover:block"
                    onClick={() =>
                      onThreadMenu(
                        threadMenuId === thread.thread_id ? "" : thread.thread_id
                      )
                    }
                  >
                    ...
                  </button>
                  {threadMenuId === thread.thread_id ? (
                    <div className="absolute right-2 top-8 z-20 min-w-[130px] rounded-md border border-slate-200 bg-white p-1 shadow-lg">
                      <button
                        type="button"
                        className="block w-full rounded-md px-2 py-1 text-left text-xs text-slate-700 hover:bg-slate-100"
                        onClick={() => onRenameStart(thread)}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="block w-full rounded-md px-2 py-1 text-left text-xs text-slate-700 hover:bg-slate-100"
                        onClick={() => onTogglePin(thread)}
                      >
                        {thread.pinned ? "Unpin" : "Pin"}
                      </button>
                      <button
                        type="button"
                        className="block w-full rounded-md px-2 py-1 text-left text-xs text-red-600 hover:bg-red-50"
                        onClick={() => onDeleteThread(thread)}
                      >
                        Delete
                      </button>
                    </div>
                  ) : null}
                </>
              )}
            </div>
          ))}
          {!threads.length ? (
            <p className="px-2 text-xs text-slate-500">No threads yet.</p>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

function TreeNode({ node, expandedPaths, onTogglePath, onOpenFile, activePath, depth = 0 }) {
  if (!node) return null;
  const isDir = node.type !== "file";
  const label = node.path === "." ? "(root)" : node.path.split("/").pop();
  if (!isDir) {
    return (
      <button
        type="button"
        className={[
          "block w-full rounded-sm px-2 py-1 text-left text-xs",
          activePath === node.path
            ? "bg-emerald-50 text-emerald-800"
            : "text-slate-700 hover:bg-slate-100"
        ].join(" ")}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => onOpenFile(node.path)}
      >
        {label}
      </button>
    );
  }

  const isOpen = expandedPaths.has(node.path);
  return (
    <div>
      <button
        type="button"
        className="block w-full rounded-sm px-2 py-1 text-left text-xs font-semibold text-slate-700 hover:bg-slate-100"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => onTogglePath(node.path)}
      >
        {isOpen ? "v" : ">"} {label}
      </button>
      {isOpen && Array.isArray(node.children) ? (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              expandedPaths={expandedPaths}
              onTogglePath={onTogglePath}
              onOpenFile={onOpenFile}
              activePath={activePath}
              depth={depth + 1}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function WikiDocumentView({
  wikiDoc,
  wikiLoading,
  onRefreshWiki,
  onOpenFile,
  fileSet,
  compact = false,
}) {
  const headings = useMemo(
    () => parseHeadings(wikiDoc?.markdown || ""),
    [wikiDoc?.markdown]
  );

  return (
    <div className="flex h-full min-h-0 bg-white text-slate-800">
      <div className="w-60 shrink-0 border-r border-slate-200 bg-slate-50 p-4">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          On this page
        </p>
        <div className="max-h-[calc(100%-40px)] space-y-1 overflow-auto text-xs">
          {headings.length ? (
            headings.map((item) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={[
                  "block rounded-sm border-l border-transparent px-2 py-1 text-slate-600 hover:border-primary-500 hover:bg-primary-50 hover:text-slate-900",
                  item.level === 3 ? "ml-2" : ""
                ].join(" ")}
              >
                {item.text}
              </a>
            ))
          ) : (
            <p className="text-slate-500">No headings</p>
          )}
        </div>
      </div>

      <div className="min-w-0 flex-1 overflow-auto bg-white">
        <div className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-slate-900">Repository Wiki</p>
              <p className="text-xs text-slate-500">
                Last generated:{" "}
                {wikiDoc?.generated_at
                  ? new Date(wikiDoc.generated_at).toLocaleString()
                  : "-"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {wikiDoc?.cached ? (
                <span className="rounded border border-primary-300 bg-primary-50 px-2 py-0.5 text-[11px] text-primary-700">
                  Cached
                </span>
              ) : null}
              <ButtonSecondary type="button" onClick={onRefreshWiki} disabled={wikiLoading}>
                {wikiLoading ? "Refreshing..." : "Refresh docs"}
              </ButtonSecondary>
            </div>
          </div>
        </div>
        <div
          className={[
            "prose prose-slate max-w-none px-6 py-5 prose-headings:text-slate-900 prose-a:text-primary-700 prose-strong:text-slate-900 prose-code:text-slate-900",
            compact ? "text-sm" : "",
          ].join(" ")}
        >
          {wikiLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 10 }).map((_, idx) => (
                <div key={idx} className="skeleton h-4" />
              ))}
            </div>
          ) : wikiDoc?.markdown ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSanitize]}
              components={{
                code({ inline, children, className }) {
                  const raw = String(children || "").trim();
                  if (!inline && className?.includes("language-mermaid")) {
                    return <MermaidBlock code={raw} />;
                  }
                  if (inline && fileSet.has(raw)) {
                    return (
                      <button
                        type="button"
                        className="rounded border border-primary-300 bg-primary-50 px-1.5 py-0.5 text-xs text-primary-700"
                        onClick={() => onOpenFile(raw)}
                      >
                        {raw}
                      </button>
                    );
                  }
                  return <code>{raw}</code>;
                },
                h2(props) {
                  const text = String(props.children || "");
                  return <h2 id={toSlug(text)} {...props} />;
                },
                h3(props) {
                  const text = String(props.children || "");
                  return <h3 id={toSlug(text)} {...props} />;
                }
              }}
            >
              {wikiDoc.markdown}
            </ReactMarkdown>
          ) : (
            <p className="text-sm text-slate-500">
              Run <code>/wiki</code> in chat or click Refresh docs.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function ContextualPanel({
  open,
  tab,
  onTabChange,
  patch,
  changedFiles,
  onApply,
  onDiscard,
  onOpenFile,
  applying,
  contextPills,
  onRemovePill,
  activityEvents,
  provider,
  onProviderChange,
  model,
  onModelChange,
  temperature,
  onTemperatureChange,
  reasoningLevel,
  onReasoningLevelChange,
  deployments,
  azureConfig,
  onAzureConfigChange,
  historyWindow,
  onHistoryWindowChange,
  width,
  onWidthChange,
}) {
  if (!open) return null;
  const diffMeta = parseDiffMeta(patch);

  return (
    <Paper
      elevation={0}
      sx={{
        width,
        minWidth: 380,
        maxWidth: 760,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        borderLeft: "1px solid",
        borderColor: "divider",
        position: "relative",
        borderRadius: 0,
      }}
    >
      <Box
        role="separator"
        sx={{
          position: "absolute",
          left: -5,
          top: 0,
          bottom: 0,
          width: 10,
          cursor: "col-resize",
          zIndex: 20,
        }}
        onMouseDown={(event) => {
          const startX = event.clientX;
          const startWidth = width;
          const onMove = (moveEvent) => {
            const next = startWidth - (moveEvent.clientX - startX);
            onWidthChange(Math.min(760, Math.max(380, next)));
          };
          const onUp = () => {
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
          };
          window.addEventListener("mousemove", onMove);
          window.addEventListener("mouseup", onUp);
        }}
      />
      <Box
        sx={{
          px: 1.5,
          py: 0.8,
          borderBottom: "1px solid",
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Tabs
          value={tab}
          onChange={(_event, value) => onTabChange(value)}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ minHeight: 34 }}
        >
          {DRAWER_TABS.map((item) => (
            <Tab
              key={item.id}
              value={item.id}
              icon={<item.icon size={14} />}
              aria-label={item.label}
              sx={{
                minHeight: 34,
                minWidth: 36,
                px: 0.8,
                py: 0.2,
              }}
            />
          ))}
        </Tabs>
      </Box>

      {tab === "diffs" ? (
        <>
          <Box sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.8, mb: 1 }}>
              {diffMeta.length ? (
                diffMeta.map((item) => (
                  <Chip
                    key={item.path}
                    size="small"
                    variant="outlined"
                    label={`${item.path} +${item.added}/-${item.removed}`}
                  />
                ))
              ) : (
                <Typography variant="caption" color="text.secondary">
                  No pending diff.
                </Typography>
              )}
            </Box>
            {changedFiles.length ? (
              <TextField
                select
                SelectProps={{ native: true }}
                size="small"
                fullWidth
                value=""
                onChange={(event) => onOpenFile(event.target.value)}
              >
                <option value="">Open changed file</option>
                {changedFiles.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </TextField>
            ) : null}
          </Box>
          <Box sx={{ minHeight: 0, flex: 1 }}>
            <DiffView patch={patch} />
          </Box>
          <Box
            sx={{
              px: 1.5,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
              display: "flex",
              justifyContent: "flex-end",
              gap: 1,
            }}
          >
            <ButtonSecondary type="button" onClick={onDiscard} disabled={applying}>
              Discard
            </ButtonSecondary>
            <ButtonPrimary type="button" onClick={onApply} disabled={applying}>
              {applying ? "Applying..." : "Apply"}
            </ButtonPrimary>
          </Box>
        </>
      ) : null}

      {tab === "context" ? (
        <Box sx={{ minHeight: 0, flex: 1, overflow: "auto", p: 2 }}>
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            Active Context
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            {contextPills.length ? (
              contextPills.map((pill) => (
                <Chip
                  key={pill.id}
                  size="small"
                  label={pill.label}
                  onDelete={pill.removable ? () => onRemovePill(pill.id) : undefined}
                  color={pill.id === "workspace" ? "primary" : "default"}
                  variant={pill.id === "workspace" ? "filled" : "outlined"}
                />
              ))
            ) : (
              <Typography variant="caption" color="text.secondary">
                No active context. Use @file, @folder:path or @repo.
              </Typography>
            )}
          </Box>
        </Box>
      ) : null}

      {tab === "activity" ? (
        <Box sx={{ minHeight: 0, flex: 1, overflow: "auto", p: 2 }}>
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            Activity
          </Typography>
          {activityEvents.length ? (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {activityEvents.map((item, idx) => (
                <Paper key={`${item.ts}-${idx}`} elevation={0} sx={{ p: 1.2 }}>
                  <Typography variant="caption" color="text.secondary">
                    {item.ts}
                  </Typography>
                  <Typography variant="body2">{item.message}</Typography>
                </Paper>
              ))}
            </Box>
          ) : (
            <Typography variant="caption" color="text.secondary">
              No activity yet.
            </Typography>
          )}
        </Box>
      ) : null}

      {tab === "settings" ? (
        <Box sx={{ minHeight: 0, flex: 1, overflow: "auto", p: 2 }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <TextField
              select
              SelectProps={{ native: true }}
              size="small"
              label="Provider"
              value={provider}
              onChange={(event) => onProviderChange(event.target.value)}
            >
              <option value="openai">OpenAI (Personal)</option>
              <option value="azure">Azure OpenAI MI (Org)</option>
            </TextField>
            {provider === "openai" ? (
              <TextField
                select
                SelectProps={{ native: true }}
                size="small"
                label="Model"
                value={model}
                onChange={(event) => onModelChange(event.target.value)}
              >
                {OPENAI_MODELS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </TextField>
            ) : (
              <TextField
                select
                SelectProps={{ native: true }}
                size="small"
                label="Azure deployment"
                value={azureConfig.deployment || model}
                onChange={(event) =>
                  onAzureConfigChange("deployment", event.target.value)
                }
              >
                <option value="">Select deployment</option>
                {deployments.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </TextField>
            )}
            <TextField
              select
              SelectProps={{ native: true }}
              size="small"
              label="Reasoning"
              value={reasoningLevel}
              onChange={(event) => onReasoningLevelChange(event.target.value)}
            >
              {REASONING_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </TextField>
            <TextField
              size="small"
              label="Temperature"
              type="number"
              inputProps={{ min: 0, max: 2, step: 0.1 }}
              value={temperature}
              onChange={(event) =>
                onTemperatureChange(Number(event.target.value || 0))
              }
            />
            <TextField
              select
              SelectProps={{ native: true }}
              size="small"
              label="History window"
              value={historyWindow}
              onChange={(event) =>
                onHistoryWindowChange(Number(event.target.value))
              }
            >
              <option value={4}>4 messages</option>
              <option value={6}>6 messages</option>
              <option value={10}>10 messages</option>
            </TextField>
            {provider === "azure" ? (
              <>
                <Divider />
                <TextField
                  size="small"
                  label="Azure endpoint"
                  value={azureConfig.endpoint}
                  onChange={(event) =>
                    onAzureConfigChange("endpoint", event.target.value)
                  }
                />
                <TextField
                  size="small"
                  label="API version"
                  value={azureConfig.api_version}
                  onChange={(event) =>
                    onAzureConfigChange("api_version", event.target.value)
                  }
                />
                <TextField
                  size="small"
                  label="Managed identity client ID"
                  value={azureConfig.managed_identity_client_id}
                  onChange={(event) =>
                    onAzureConfigChange(
                      "managed_identity_client_id",
                      event.target.value
                    )
                  }
                />
              </>
            ) : null}
          </Box>
        </Box>
      ) : null}
    </Paper>
  );
}

export default function CodexDesktop() {
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    setActiveWorkspaceId,
    isModalOpen,
    setIsModalOpen
  } = useWorkspace();
  const { pushToast } = useToast();

  const [threads, setThreads] = useState([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeThreadId, setActiveThreadId] = useState("");
  const [threadMenuId, setThreadMenuId] = useState("");
  const [renamingThreadId, setRenamingThreadId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [threadToDelete, setThreadToDelete] = useState(null);

  const [provider, setProvider] = useState(
    localStorage.getItem("codex_provider") || "openai"
  );
  const [reasoningLevel, setReasoningLevel] = useState("medium");
  const [model, setModel] = useState(DEFAULT_MODEL_BY_PROVIDER.openai);
  const [temperature, setTemperature] = useState(0.2);

  const [deployments, setDeployments] = useState([]);
  const [azureConfig, setAzureConfig] = useState({
    endpoint: "",
    deployment: "",
    api_version: "",
    managed_identity_client_id: ""
  });

  const [repoTree, setRepoTree] = useState(null);
  const [fileList, setFileList] = useState([]);
  const [folderList, setFolderList] = useState([]);
  const [fileSearch, setFileSearch] = useState("");
  const [expandedPaths, setExpandedPaths] = useState(() => new Set(["."]));
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  const [rightPanelTab, setRightPanelTab] = useState("diffs");
  const [inspectorWidth, setInspectorWidth] = useState(520);
  const [activeFile, setActiveFile] = useState(null);
  const [editorEditable, setEditorEditable] = useState(false);
  const [editorDraft, setEditorDraft] = useState("");
  const [savingFile, setSavingFile] = useState(false);
  const [openFileTabs, setOpenFileTabs] = useState([]);
  const [loadingFile, setLoadingFile] = useState(false);
  const [fileCache, setFileCache] = useState({});
  const [wikiDoc, setWikiDoc] = useState(null);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [mainStageMode, setMainStageMode] = useState("thread");

  const [selectedRange, setSelectedRange] = useState(null);
  const [includeSelection, setIncludeSelection] = useState(true);
  const [includeCurrentFile, setIncludeCurrentFile] = useState(true);
  const [fileMentions, setFileMentions] = useState([]);

  const [draft, setDraft] = useState("");
  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionMode, setMentionMode] = useState("file");
  const [isThinking, setIsThinking] = useState(false);

  const [pendingPatch, setPendingPatch] = useState("");
  const [pendingFiles, setPendingFiles] = useState([]);
  const [applying, setApplying] = useState(false);

  const [activity, setActivity] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [historyWindow, setHistoryWindow] = useState(6);
  const [threadsLoaded, setThreadsLoaded] = useState(false);

  const inputRef = useRef(null);
  const timelineRef = useRef(null);

  const activeThread = useMemo(
    () => threads.find((thread) => thread.thread_id === activeThreadId) || null,
    [threads, activeThreadId]
  );
  const fileSet = useMemo(() => new Set(fileList), [fileList]);

  const mentionSuggestions = useMemo(() => {
    if (!mentionOpen) return [];
    const query = mentionQuery.toLowerCase();
    if (mentionMode === "folder") {
      return folderList
        .filter((path) => path.toLowerCase().includes(query))
        .map((path) => `folder:${path}`)
        .slice(0, 8);
    }
    if (mentionMode === "repo") {
      return ["repo"];
    }
    const files = fileList
      .filter((path) => path.toLowerCase().includes(query))
      .slice(0, 6);
    const folderMatches = folderList
      .filter((path) => path.toLowerCase().includes(query))
      .map((path) => `folder:${path}`)
      .slice(0, 3);
    return ["repo", ...folderMatches, ...files].slice(0, 8);
  }, [mentionOpen, mentionQuery, mentionMode, fileList, folderList]);

  const slashSuggestions = useMemo(() => {
    if (!commandMenuOpen) return [];
    const query = commandQuery.toLowerCase();
    return SLASH_HINTS.filter((item) => item.includes(query)).slice(0, 8);
  }, [commandMenuOpen, commandQuery]);

  const filteredThreads = useMemo(() => {
    const sorted = sortThreads(threads);
    if (!searchQuery.trim()) return sorted;
    const query = searchQuery.toLowerCase();
    return sorted.filter((thread) => {
      const title = summarizeThreadTitle(thread).toLowerCase();
      if (title.includes(query)) return true;
      return thread.messages.some((msg) =>
        (msg.content || "").toLowerCase().includes(query)
      );
    });
  }, [threads, searchQuery]);

  useEffect(() => {
    localStorage.setItem("codex_provider", provider);
  }, [provider]);

  useEffect(() => {
    const loadThreads = async () => {
      try {
        const data = await apiGet("/api/threads");
        const next = (data.threads || []).map(normalizeThread);
        if (!next.length) {
          const created = await apiPost("/api/threads", {
            title: EMPTY_THREAD_TITLE,
            workspace_id: activeWorkspaceId || null,
          });
          const normalized = normalizeThread(created);
          setThreads([normalized]);
          setActiveThreadId(normalized.thread_id);
        } else {
          const sorted = sortThreads(next);
          setThreads(sorted);
          setActiveThreadId((prev) => {
            if (prev && sorted.some((item) => item.thread_id === prev)) return prev;
            return sorted[0].thread_id;
          });
        }
      } catch (err) {
        pushToast({
          title: "Threads unavailable",
          message: err.message || "Failed to load threads",
          variant: "error"
        });
      } finally {
        setThreadsLoaded(true);
      }
    };
    loadThreads();
  }, [activeWorkspaceId, pushToast]);

  useEffect(() => {
    const loadDeployments = async () => {
      try {
        const data = await apiGet("/api/config/deployments");
        setDeployments(data.deployments || []);
        setAzureConfig((prev) => ({
          endpoint: prev.endpoint || data.defaults?.endpoint || "",
          deployment: prev.deployment || data.defaults?.deployment || "",
          api_version: prev.api_version || data.defaults?.api_version || "",
          managed_identity_client_id:
            prev.managed_identity_client_id ||
            data.defaults?.managed_identity_client_id ||
            ""
        }));
      } catch {
        // manual values only
      }
    };
    loadDeployments();
  }, []);

  useEffect(() => {
    if (!activeWorkspaceId) {
      setRepoTree(null);
      setFileList([]);
      setFolderList([]);
      setActiveFile(null);
      setEditorEditable(false);
      setEditorDraft("");
      setOpenFileTabs([]);
      return;
    }
    setActiveFile(null);
    setEditorEditable(false);
    setEditorDraft("");
    setOpenFileTabs([]);
    const loadTree = async () => {
      try {
        const data = await apiGet(
          `/api/repo/tree?workspace_id=${encodeURIComponent(activeWorkspaceId)}`
        );
        setRepoTree(data.tree || null);
        setFileList(flattenTree(data.tree));
        setFolderList(flattenDirectories(data.tree));
      } catch (err) {
        pushToast({
          title: "Explorer unavailable",
          message: err.message || "Failed to load repository tree",
          variant: "error"
        });
      }
    };
    loadTree();
  }, [activeWorkspaceId, pushToast]);

  useEffect(() => {
    if (timelineRef.current) {
      timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
    }
  }, [activeThread?.messages, isThinking]);

  const upsertThread = (item) => {
    const normalized = normalizeThread(item);
    setThreads((prev) => {
      const next = prev.filter((thread) => thread.thread_id !== normalized.thread_id);
      next.push(normalized);
      return sortThreads(next);
    });
  };

  const patchThread = async (threadId, payload) => {
    const updated = await apiPatch(`/api/threads/${threadId}`, payload);
    upsertThread(updated);
    return normalizeThread(updated);
  };

  const appendMessages = async (nextMessages, overrideTitle = null) => {
    if (!activeThreadId) return;
    setThreads((prev) =>
      sortThreads(
        prev.map((thread) =>
          thread.thread_id === activeThreadId
            ? {
                ...thread,
                messages: nextMessages,
                title: overrideTitle || thread.title,
                updated_at: new Date().toISOString(),
              }
            : thread
        )
      )
    );
    try {
      await patchThread(activeThreadId, {
        messages: nextMessages,
        workspace_id: activeWorkspaceId || null,
        title: overrideTitle || undefined,
      });
    } catch {
      // Keep optimistic local updates even if backend sync fails.
    }
  };

  const addActivity = (message) => {
    setActivity((prev) => [{ ts: new Date().toLocaleTimeString(), message }, ...prev]);
  };

  const createThread = async () => {
    try {
      const created = await apiPost("/api/threads", {
        title: EMPTY_THREAD_TITLE,
        workspace_id: activeWorkspaceId || null,
      });
      const normalized = normalizeThread(created);
      setThreads((prev) => sortThreads([normalized, ...prev]));
      setActiveThreadId(normalized.thread_id);
      setThreadMenuId("");
      setRenamingThreadId("");
      setRenameDraft("");
      setPendingPatch("");
      setPendingFiles([]);
      setSelectedRange(null);
      setFileMentions([]);
      setMainStageMode("thread");
    } catch (err) {
      pushToast({
        title: "Thread create failed",
        message: err.message || "Could not create thread",
        variant: "error"
      });
    }
  };

  const openFile = async (path) => {
    if (!activeWorkspaceId || !path) return;
    setMainStageMode("editor");
    setOpenFileTabs((prev) => (prev.includes(path) ? prev : [...prev, path]));
    if (fileCache[path]) {
      setActiveFile(fileCache[path]);
      setEditorEditable(false);
      setEditorDraft(fileCache[path].content || "");
      return;
    }
    setLoadingFile(true);
    try {
      const data = await apiGet(
        `/api/repo/file?workspace_id=${encodeURIComponent(
          activeWorkspaceId
        )}&path=${encodeURIComponent(path)}`
      );
      const extension = (path.split(".").pop() || "").toLowerCase();
      const normalized = {
        ...data,
        language:
          extension === "py"
            ? "python"
            : ["js", "jsx"].includes(extension)
              ? "javascript"
              : ["ts", "tsx"].includes(extension)
                ? "typescript"
                : extension || "plaintext"
      };
      setFileCache((prev) => ({ ...prev, [path]: normalized }));
      setActiveFile(normalized);
      setEditorEditable(false);
      setEditorDraft(normalized.content || "");
    } catch (err) {
      pushToast({
        title: "File open failed",
        message: err.message || "Unable to read file",
        variant: "error"
      });
    } finally {
      setLoadingFile(false);
    }
  };

  const closeFileTab = (path) => {
    const next = openFileTabs.filter((item) => item !== path);
    setOpenFileTabs(next);
    if (activeFile?.path !== path) return;
    const fallback = next[next.length - 1];
    if (fallback && fileCache[fallback]) {
      setActiveFile(fileCache[fallback]);
      return;
    }
    if (fallback) {
      void openFile(fallback);
      return;
    }
    setActiveFile(null);
    setEditorEditable(false);
    setEditorDraft("");
    setMainStageMode("thread");
  };

  const saveActiveFile = async () => {
    if (!activeWorkspaceId || !activeFile?.path) return;
    setSavingFile(true);
    try {
      const payload = {
        workspace_id: activeWorkspaceId,
        path: activeFile.path,
        content: editorDraft,
      };
      try {
        await apiPut("/api/repo/file", payload);
      } catch (err) {
        if (err?.status === 405) {
          await apiPost("/api/repo/file", payload);
        } else {
          throw err;
        }
      }
      const updated = {
        ...activeFile,
        content: editorDraft,
      };
      setFileCache((prev) => ({ ...prev, [activeFile.path]: updated }));
      setActiveFile(updated);
      setEditorEditable(false);
      pushToast({
        title: "File saved",
        message: activeFile.path,
        variant: "success",
      });
      addActivity(`Saved ${activeFile.path}`);
    } catch (err) {
      pushToast({
        title: "Save failed",
        message: err.message || "Could not save file",
        variant: "error",
      });
      addActivity(`Save failed for ${activeFile.path}`);
    } finally {
      setSavingFile(false);
    }
  };

  const openCitation = async (citation) => {
    if (!citation?.path) return;
    await openFile(citation.path);
    if (citation.start_line || citation.end_line) {
      setSelectedRange({
        start_line: citation.start_line || 1,
        end_line: citation.end_line || citation.start_line || 1,
      });
      setIncludeSelection(true);
    }
  };

  const applyPatch = async () => {
    if (!pendingPatch.trim() || !activeWorkspaceId) return;
    setApplying(true);
    try {
      await apiPost("/api/patch/apply", {
        workspace_id: activeWorkspaceId,
        patch: pendingPatch,
        confirm: true,
        repo_scope: { allow_apply_patch: true }
      });
      pushToast({
        title: "Changes applied",
        message: "Patch applied to local workspace",
        variant: "success"
      });
      addActivity("Applied patch to workspace");
      setPendingPatch("");
      setPendingFiles([]);
      setRightPanelOpen(false);
    } catch (err) {
      pushToast({
        title: "Apply failed",
        message: err.message || "Could not apply patch",
        variant: "error"
      });
      addActivity("Patch apply failed");
    } finally {
      setApplying(false);
    }
  };

  const discardPatch = () => {
    setPendingPatch("");
    setPendingFiles([]);
    setRightPanelOpen(false);
    addActivity("Discarded proposed changes");
  };

  const reviewChanges = (patch, files = []) => {
    setPendingPatch(patch || "");
    setPendingFiles(files);
    setRightPanelTab("diffs");
    setRightPanelOpen(true);
  };

  const togglePath = (path) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const refreshWiki = async () => {
    if (!activeWorkspaceId) {
      pushToast({
        title: "Workspace required",
        message: "Open a local folder first",
        variant: "error"
      });
      return;
    }
    setWikiLoading(true);
    try {
      const llmParams = {
        model: provider === "openai" ? model : undefined,
        reasoning_level: reasoningLevel,
        temperature,
      };
      if (provider === "azure" && azureConfig.deployment) {
        llmParams.model = azureConfig.deployment;
      }
      const data = await apiPost("/api/wiki/explain", {
        workspace_id: activeWorkspaceId,
        provider,
        llm_params: llmParams,
        azure_config: provider === "azure" ? azureConfig : undefined
      });
      setWikiDoc(data);
      setMainStageMode("wiki");
    } catch (err) {
      pushToast({
        title: "Wiki failed",
        message: err.message || "Could not generate wiki",
        variant: "error"
      });
    } finally {
      setWikiLoading(false);
    }
  };

  const pushMentionFromEditor = (path) => {
    if (!path) return;
    setFileMentions((prev) => (prev.includes(path) ? prev : [...prev, path]));
  };

  const handleDraftChange = (value) => {
    setDraft(value);
    const cursor = inputRef.current?.selectionStart ?? value.length;
    const before = value.slice(0, cursor);
    const slash = /(^|\s)\/([A-Za-z_]*)$/.exec(before);
    if (slash) {
      setCommandQuery(slash[2] || "");
      setCommandMenuOpen(true);
    } else {
      setCommandQuery("");
      setCommandMenuOpen(false);
    }
    const mention = /@([A-Za-z0-9_./:-]*)$/.exec(before);
    if (mention) {
      const raw = mention[1] || "";
      if (raw === "repo" || raw.startsWith("repo")) {
        setMentionMode("repo");
        setMentionQuery("");
      } else if (raw.startsWith("folder:")) {
        setMentionMode("folder");
        setMentionQuery(raw.replace("folder:", ""));
      } else {
        setMentionMode("file");
        setMentionQuery(raw);
      }
      setMentionOpen(true);
      return;
    }
    setMentionQuery("");
    setMentionOpen(false);
    setMentionMode("file");
  };

  const selectMention = (path) => {
    const value = draft;
    const cursor = inputRef.current?.selectionStart ?? value.length;
    const before = value.slice(0, cursor);
    const after = value.slice(cursor);
    const nextBefore = before.replace(/@([A-Za-z0-9_./:-]*)$/, `@${path} `);
    const next = `${nextBefore}${after}`;
    setDraft(next);
    setMentionOpen(false);
    setMentionQuery("");
    setMentionMode("file");
    setFileMentions((prev) => (prev.includes(path) ? prev : [...prev, path]));
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const selectSlashCommand = (command) => {
    const value = draft;
    const cursor = inputRef.current?.selectionStart ?? value.length;
    const before = value.slice(0, cursor);
    const after = value.slice(cursor);
    const nextBefore = before.replace(/(^|\s)\/([A-Za-z_]*)$/, `$1${command} `);
    setDraft(`${nextBefore}${after}`);
    setCommandMenuOpen(false);
    setCommandQuery("");
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const removePill = (id) => {
    if (id === "current-file") {
      setIncludeCurrentFile(false);
      return;
    }
    if (id === "selection") {
      setIncludeSelection(false);
      return;
    }
    if (id.startsWith("mention:")) {
      const path = id.replace("mention:", "");
      setFileMentions((prev) => prev.filter((item) => item !== path));
    }
  };

  const contextPills = useMemo(() => {
    const pills = [];
    if (activeWorkspace) {
      pills.push({ id: "workspace", label: activeWorkspace.name, removable: false });
    }
    if (activeFile?.path && includeCurrentFile) {
      pills.push({ id: "current-file", label: activeFile.path, removable: true });
    }
    if (activeFile?.path && selectedRange && includeSelection) {
      pills.push({
        id: "selection",
        label: `${activeFile.path}:${selectedRange.start_line}-${selectedRange.end_line}`,
        removable: true
      });
    }
    fileMentions.forEach((path) => {
      const label =
        path === "repo"
          ? "@repo"
          : path.startsWith("folder:")
            ? `@${path}`
            : `@${path}`;
      pills.push({ id: `mention:${path}`, label, removable: true });
    });
    return pills;
  }, [
    activeWorkspace,
    activeFile?.path,
    includeCurrentFile,
    selectedRange,
    includeSelection,
    fileMentions
  ]);

  const sendMessage = async (event) => {
    event.preventDefault();
    if (!activeWorkspaceId) {
      pushToast({
        title: "Workspace required",
        message: "Open a local folder before sending prompts",
        variant: "error"
      });
      return;
    }
    if (!threadsLoaded) return;
    const text = draft.trim();
    if (!text || !activeThread) return;

    const inlineMentions = Array.from(
      new Set(
        (text.match(/@([A-Za-z0-9_./:-]+)/g) || []).map((item) => item.slice(1))
      )
    );
    const mergedMentions = Array.from(new Set([...fileMentions, ...inlineMentions]));

    const userMessage = {
      id: crypto.randomUUID(),
      type: "chat",
      role: "user",
      content: text,
      createdAt: new Date().toISOString()
    };

    const currentMessages = Array.isArray(activeThread.messages)
      ? activeThread.messages
      : [];
    const withUser = [...currentMessages, userMessage];
    const autoTitle =
      activeThread.title === EMPTY_THREAD_TITLE
        ? text.replace(/^\/[a-z_]+\s*/i, "").trim().slice(0, 56) ||
          EMPTY_THREAD_TITLE
        : null;
    await appendMessages(withUser, autoTitle);
    setDraft("");
    setMentionOpen(false);
    setMentionQuery("");
    setIsThinking(true);

    const threadHistory = withUser
      .filter((msg) => msg.type === "chat")
      .slice(-historyWindow)
      .map((msg) => ({ role: msg.role, content: msg.content }));

    const context = {
      file_path: includeCurrentFile ? activeFile?.path : undefined,
      selection:
        includeCurrentFile && includeSelection && selectedRange
          ? selectedRange
          : undefined,
      open_files: activeFile?.path ? [activeFile.path] : [],
      file_mentions: mergedMentions
    };

    const llmParams = {
      model: provider === "openai" ? model : undefined,
      reasoning_level: reasoningLevel,
      temperature,
    };
    if (provider === "azure" && azureConfig.deployment) {
      llmParams.model = azureConfig.deployment;
    }

    if (/^\/wiki\b/i.test(text)) {
      try {
        const wiki = await apiPost("/api/wiki/explain", {
          workspace_id: activeWorkspaceId,
          provider,
          llm_params: llmParams,
          azure_config: provider === "azure" ? azureConfig : undefined
        });
        setWikiDoc(wiki);
        setMainStageMode("wiki");
        const additions = [
          {
            id: crypto.randomUUID(),
            type: "chat",
            role: "assistant",
            content: wiki.markdown || "",
            citations: parseInlineCitations(wiki.markdown || ""),
            createdAt: new Date().toISOString()
          },
          {
            id: crypto.randomUUID(),
            type: "event",
            role: "assistant",
            content: "Generated repository wiki.",
            createdAt: new Date().toISOString()
          }
        ];
        await appendMessages([...withUser, ...additions]);
        addActivity("Generated repository wiki");
      } catch (err) {
        pushToast({
          title: "Wiki failed",
          message: err.message || "Could not generate wiki",
          variant: "error"
        });
      } finally {
        setIsThinking(false);
      }
      return;
    }

    if (/^\/file_summary\b/i.test(text) && activeFile?.path) {
      try {
        const summary = await apiPost("/api/repo/file-summary", {
          workspace_id: activeWorkspaceId,
          provider,
          path: activeFile.path,
          llm_params: llmParams,
          azure_config: provider === "azure" ? azureConfig : undefined
        });
        const additions = [
          {
            id: crypto.randomUUID(),
            type: "chat",
            role: "assistant",
            content: summary.summary_markdown || "",
            citations: parseInlineCitations(summary.summary_markdown || ""),
            createdAt: new Date().toISOString()
          }
        ];
        await appendMessages([...withUser, ...additions]);
        addActivity(`Summarized ${activeFile.path}`);
      } catch (err) {
        pushToast({
          title: "File summary failed",
          message: err.message || "Could not summarize file",
          variant: "error"
        });
      } finally {
        setIsThinking(false);
      }
      return;
    }

    try {
      const payload = {
        thread_id: activeThread.thread_id,
        workspace_id: activeWorkspaceId,
        provider,
        messages: threadHistory,
        context,
        llm_params: llmParams,
        azure_config: provider === "azure" ? azureConfig : undefined
      };
      const data = await apiPost("/api/chat", payload);

      const additions = [
        {
          id: crypto.randomUUID(),
          type: "chat",
          role: "assistant",
          content: data.assistant_message?.content || "",
          citations: Array.isArray(data.citations)
            ? data.citations
            : parseInlineCitations(data.assistant_message?.content || ""),
          createdAt: new Date().toISOString()
        }
      ];

      if (Array.isArray(data.step_events)) {
        data.step_events.forEach((eventItem) => {
          additions.push({
            id: crypto.randomUUID(),
            type: "event",
            role: "assistant",
            content: eventItem.message,
            createdAt: new Date().toISOString()
          });
          addActivity(eventItem.message);
        });
      }

      if (data.proposed_changes?.patch) {
        const filesChanged = parsePatchChangedFiles(
          data.proposed_changes.patch,
          data.proposed_changes.files_changed || []
        );
        additions.push({
          id: crypto.randomUUID(),
          type: "review",
          role: "assistant",
          content: "Review changes",
          patch: data.proposed_changes.patch,
          filesChanged,
          createdAt: new Date().toISOString()
        });
        setPendingPatch(data.proposed_changes.patch);
        setPendingFiles(filesChanged);
      }

      await appendMessages([...withUser, ...additions]);

      if (data.queued_ms) {
        pushToast({
          title: "Queued",
          message: `Delayed ${data.queued_ms}ms due to rate limits`,
          variant: "default"
        });
      }
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        pushToast({
          title: "Rate limited",
          message: err.data.retry_after_seconds
            ? `Retry in ${err.data.retry_after_seconds}s`
            : err.message,
          variant: "error"
        });
        addActivity("Rate limit reached; request deferred");
      } else {
        pushToast({
          title: "Chat failed",
          message: err.message || "Unknown error",
          variant: "error"
        });
        addActivity("Chat request failed");
      }
    } finally {
      setIsThinking(false);
    }
  };

  const activeMessages = activeThread?.messages || [];

  return (
    <Box sx={{ height: "100vh", bgcolor: "background.default", color: "text.primary" }}>
      <Box sx={{ display: "flex", height: "100%" }}>
        {!sidebarCollapsed ? (
          <Sidebar
            threads={filteredThreads}
            activeThreadId={activeThreadId}
            onThreadSelect={(threadId) => {
              setActiveThreadId(threadId);
              setThreadMenuId("");
              setRenamingThreadId("");
              setMainStageMode("thread");
            }}
            onNewThread={createThread}
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            onWorkspaceChange={setActiveWorkspaceId}
            onOpenFolder={() => setIsModalOpen(true)}
            threadMenuId={threadMenuId}
            onThreadMenu={setThreadMenuId}
            renamingThreadId={renamingThreadId}
            renameDraft={renameDraft}
            onRenameDraft={setRenameDraft}
            onRenameStart={(thread) => {
              setThreadMenuId("");
              setRenamingThreadId(thread.thread_id);
              setRenameDraft(thread.title || EMPTY_THREAD_TITLE);
            }}
            onRenameSubmit={async (threadId) => {
              const nextTitle = renameDraft.trim() || EMPTY_THREAD_TITLE;
              try {
                await patchThread(threadId, { title: nextTitle });
                setRenamingThreadId("");
                setRenameDraft("");
              } catch (err) {
                pushToast({
                  title: "Rename failed",
                  message: err.message || "Could not rename thread",
                  variant: "error"
                });
              }
            }}
            onRenameCancel={() => {
              setRenamingThreadId("");
              setRenameDraft("");
            }}
            onTogglePin={async (thread) => {
              setThreadMenuId("");
              try {
                await patchThread(thread.thread_id, { pinned: !thread.pinned });
              } catch (err) {
                pushToast({
                  title: "Update failed",
                  message: err.message || "Could not update thread",
                  variant: "error"
                });
              }
            }}
            onDeleteThread={(thread) => {
              setThreadMenuId("");
              setThreadToDelete(thread);
            }}
            threadSearch={searchQuery}
            onThreadSearch={setSearchQuery}
            tree={repoTree}
            fileList={fileList}
            fileSearch={fileSearch}
            onFileSearch={setFileSearch}
            expandedPaths={expandedPaths}
            onTogglePath={togglePath}
            onOpenFile={(path) => {
              openFile(path);
              setMainStageMode("editor");
            }}
            activeFilePath={activeFile?.path}
          />
        ) : null}

        <Box sx={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column" }}>
          <header className="flex h-12 items-center justify-between border-b border-slate-200 bg-white px-4">
            <div className="flex min-w-0 items-center gap-3">
              <AntButton
                size="small"
                icon={sidebarCollapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
                onClick={() => setSidebarCollapsed((prev) => !prev)}
                title={sidebarCollapsed ? "Open left panel" : "Close left panel"}
              />
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-900">
                  {activeWorkspace?.name || "No workspace selected"}
                </p>
                <p className="truncate text-xs text-slate-500">
                  {activeWorkspace?.path || "Open a local repository to start"}
                </p>
              </div>
              <Tag color="green">{provider === "azure" ? "Azure OpenAI" : "OpenAI"}</Tag>
            </div>
            <div className="flex items-center gap-2 pr-2">
              <AntButton
                size="small"
                icon={rightPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                onClick={() => setRightPanelOpen((prev) => !prev)}
                title={rightPanelOpen ? "Close inspector" : "Open inspector"}
              />
            </div>
          </header>

          <Box sx={{ display: "flex", minHeight: 0, flex: 1, position: "relative" }}>
            <main className="flex min-h-0 flex-1 flex-col">
              {mainStageMode === "wiki" && wikiDoc ? (
                <div className="min-h-0 flex-1 overflow-hidden px-4 py-4">
                  <div className="h-full overflow-hidden rounded-sm border border-slate-200 shadow-sm">
                    <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Wiki mode
                      </p>
                      <ButtonSecondary
                        type="button"
                        className="h-8 px-2"
                        onClick={() => setMainStageMode("thread")}
                      >
                        Back to thread
                      </ButtonSecondary>
                    </div>
                    <div className="h-[calc(100%-41px)]">
                      <WikiDocumentView
                        wikiDoc={wikiDoc}
                        wikiLoading={wikiLoading}
                        onRefreshWiki={refreshWiki}
                        onOpenFile={openFile}
                        fileSet={fileSet}
                      />
                    </div>
                  </div>
                </div>
              ) : mainStageMode === "editor" ? (
                <div className="min-h-0 flex-1 overflow-hidden px-4 py-4">
                  <div className="flex h-full flex-col overflow-hidden rounded-sm border border-slate-200 bg-white shadow-sm">
                    <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-slate-800">
                          {activeFile?.path || "Editor"}
                        </p>
                        <p className="truncate text-[11px] text-slate-500">
                          Local file editor view
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {activeFile?.path ? (
                          <AntButton
                            size="small"
                            icon={<Copy size={14} />}
                            onClick={() => navigator.clipboard?.writeText(activeFile.path)}
                          />
                        ) : null}
                        {activeFile?.path ? (
                          <AntButton
                            size="small"
                            type={editorEditable ? "default" : "primary"}
                            onClick={() => setEditorEditable((prev) => !prev)}
                          >
                            {editorEditable ? "Read only" : "Edit"}
                          </AntButton>
                        ) : null}
                        {activeFile?.path && editorEditable ? (
                          <AntButton
                            size="small"
                            type="primary"
                            loading={savingFile}
                            onClick={saveActiveFile}
                          >
                            Save
                          </AntButton>
                        ) : null}
                        <AntButton
                          size="small"
                          onClick={() => setMainStageMode("thread")}
                        >
                          Back to chat
                        </AntButton>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 overflow-x-auto border-b border-slate-200 bg-white px-2 py-1">
                      {openFileTabs.length ? (
                        openFileTabs.map((path) => {
                          const isActive = activeFile?.path === path;
                          const name = path.split("/").pop() || path;
                          return (
                            <div
                              key={path}
                              className={[
                                "group inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs",
                                isActive
                                  ? "border-primary-300 bg-primary-50 text-primary-700"
                                  : "border-slate-200 text-slate-700 hover:border-slate-300",
                              ].join(" ")}
                            >
                              <button
                                type="button"
                                className="max-w-44 truncate"
                                onClick={() => openFile(path)}
                                title={path}
                              >
                                {name}
                              </button>
                              <button
                                type="button"
                                className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  closeFileTab(path);
                                }}
                                title={`Close ${name}`}
                              >
                                <X size={12} />
                              </button>
                            </div>
                          );
                        })
                      ) : (
                        <p className="px-1 text-xs text-slate-500">
                          Select a file from the workspace panel.
                        </p>
                      )}
                    </div>
                    <div className="min-h-0 flex-1">
                      <FileViewer
                        file={activeFile}
                        loading={loadingFile}
                        editable={editorEditable}
                        content={editorDraft}
                        onContentChange={setEditorDraft}
                        onSelectionChange={(range) => {
                          setSelectedRange(range);
                          setIncludeSelection(true);
                        }}
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <div
                  ref={timelineRef}
                  className="min-h-0 flex-1 overflow-auto px-6 py-4"
                >
                  <div className="mx-auto w-full max-w-4xl space-y-3">
                    {!activeMessages.length ? (
                      <div className="rounded-sm border border-slate-200 bg-white px-6 py-8 text-center shadow-sm">
                        <p className="text-base font-semibold text-slate-900">
                          Let&apos;s build {activeWorkspace?.name || "your workspace"}
                        </p>
                        <p className="mt-2 text-sm text-slate-600">
                          Chat is the control surface. Try <code>/wiki</code>,{" "}
                          <code>/fix</code>, or mention files with <code>@</code>.
                        </p>
                      </div>
                    ) : null}

                    <div className="divide-y divide-slate-100 rounded-sm">
                      {activeMessages.map((message, index) => (
                        <div
                          key={message.id || `${message.type || "message"}-${index}`}
                          className="py-3 first:pt-0"
                        >
                          <MessageBubble
                            message={message}
                            fileSet={fileSet}
                            onOpenFile={openFile}
                            onReviewChanges={reviewChanges}
                            onOpenCitation={openCitation}
                          />
                        </div>
                      ))}
                    </div>

                    {isThinking ? (
                      <div className="rounded-sm border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">Thinking</span>
                          <span className="thinking-dots" aria-hidden>
                            <span />
                            <span />
                            <span />
                          </span>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              )}

              {pendingPatch ? (
                <div className="border-t border-slate-200 bg-slate-50 px-6 py-2">
                  <div className="mx-auto flex w-full max-w-4xl items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-slate-600">
                      Pending changes ready for review
                    </p>
                    <div className="flex items-center gap-2">
                      <ButtonSecondary
                        type="button"
                        className="h-8 px-2"
                        onClick={() => {
                          setRightPanelOpen(true);
                          setRightPanelTab("diffs");
                        }}
                      >
                        View changes
                      </ButtonSecondary>
                      <ButtonSecondary
                        type="button"
                        className="h-8 px-2"
                        onClick={discardPatch}
                        disabled={applying}
                      >
                        Discard
                      </ButtonSecondary>
                      <ButtonPrimary
                        type="button"
                        className="h-8 px-2"
                        onClick={applyPatch}
                        disabled={applying}
                      >
                        {applying ? "Applying..." : "Apply"}
                      </ButtonPrimary>
                    </div>
                  </div>
                </div>
              ) : null}

              <form className="bg-white/96 px-6 pb-4 pt-2" onSubmit={sendMessage}>
                <div className="mx-auto w-full max-w-4xl space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {contextPills.map((pill) => (
                      <Tag
                        key={pill.id}
                        closable={pill.removable}
                        onClose={(event) => {
                          event.preventDefault();
                          removePill(pill.id);
                        }}
                        color={pill.id === "workspace" ? "green" : "default"}
                      >
                        {pill.label}
                      </Tag>
                    ))}
                  </div>

                  <div className="rounded-2xl border border-slate-200/70 bg-white p-2 shadow-[0_4px_16px_rgba(15,23,42,0.05)]">
                    <div className="mb-2 flex items-center justify-between">
                      <AntButton
                        size="small"
                        shape="circle"
                        icon={<Plus size={14} />}
                        onClick={() => {
                          if (activeFile?.path) {
                            pushMentionFromEditor(activeFile.path);
                          } else {
                            setFileMentions((prev) =>
                              prev.includes("repo") ? prev : [...prev, "repo"]
                            );
                          }
                          setRightPanelOpen(true);
                          setRightPanelTab("context");
                        }}
                        title="Add context"
                      />
                      <div className="flex items-center gap-2">
                        <Tag>{provider === "azure" ? "Azure" : "OpenAI"}</Tag>
                        <Tag>{provider === "azure" ? azureConfig.deployment || "dep" : model}</Tag>
                      </div>
                    </div>

                    <AntInput.TextArea
                      ref={inputRef}
                      autoSize={{ minRows: 2, maxRows: 7 }}
                      value={draft}
                      onChange={(event) => handleDraftChange(event.target.value)}
                      placeholder="Ask Codex anything. Use / commands and @mentions."
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          sendMessage(event);
                        }
                      }}
                    />

                    {commandMenuOpen && slashSuggestions.length ? (
                      <div className="relative mt-1">
                        <div className="absolute z-20 max-h-52 w-full overflow-auto rounded-sm border border-slate-200 bg-white shadow-lg">
                          {slashSuggestions.map((item) => (
                            <button
                              key={item}
                              type="button"
                              className="block w-full border-b border-slate-100 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
                              onClick={() => selectSlashCommand(item)}
                            >
                              {item}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {mentionOpen && mentionSuggestions.length ? (
                      <div className="relative mt-1">
                        <div className="absolute z-20 max-h-52 w-full overflow-auto rounded-sm border border-slate-200 bg-white shadow-lg">
                          {mentionSuggestions.map((item) => (
                            <button
                              key={item}
                              type="button"
                              className="block w-full border-b border-slate-100 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
                              onClick={() => selectMention(item)}
                            >
                              @{item}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    <div className="mt-2 flex items-center justify-end">
                      <AntButton
                        type="primary"
                        htmlType="submit"
                        shape="circle"
                        icon={<Send size={14} />}
                        loading={isThinking}
                        title="Send"
                      />
                    </div>
                  </div>
                </div>
              </form>
            </main>

            <ContextualPanel
              open={rightPanelOpen}
              tab={rightPanelTab}
              onTabChange={setRightPanelTab}
              patch={pendingPatch}
              changedFiles={pendingFiles}
              onApply={applyPatch}
              onDiscard={discardPatch}
              onOpenFile={openFile}
              applying={applying}
              contextPills={contextPills}
              onRemovePill={removePill}
              activityEvents={activity}
              provider={provider}
              onProviderChange={(next) => {
                setProvider(next);
                if (next === "openai") {
                  setModel((prev) => prev || DEFAULT_MODEL_BY_PROVIDER.openai);
                } else if (next === "azure") {
                  setModel((prev) => prev || azureConfig.deployment || "");
                }
              }}
              model={model}
              onModelChange={setModel}
              temperature={temperature}
              onTemperatureChange={setTemperature}
              reasoningLevel={reasoningLevel}
              onReasoningLevelChange={setReasoningLevel}
              deployments={deployments}
              azureConfig={azureConfig}
              onAzureConfigChange={(key, value) => {
                setAzureConfig((prev) => ({ ...prev, [key]: value }));
                if (key === "deployment") {
                  setModel(value);
                }
              }}
              historyWindow={historyWindow}
              onHistoryWindowChange={setHistoryWindow}
              width={inspectorWidth}
              onWidthChange={setInspectorWidth}
            />
          </Box>
        </Box>
      </Box>

      {threadToDelete ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-sm rounded-sm border border-slate-200 bg-white p-4 shadow-xl">
            <p className="text-sm font-semibold text-slate-900">Delete thread</p>
            <p className="mt-2 text-sm text-slate-600">
              Delete &quot;{summarizeThreadTitle(threadToDelete)}&quot;? This cannot be
              undone.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <ButtonSecondary type="button" onClick={() => setThreadToDelete(null)}>
                Cancel
              </ButtonSecondary>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-sm bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700"
                onClick={async () => {
                  try {
                    await apiDelete(`/api/threads/${threadToDelete.thread_id}`);
                    const remaining = threads.filter(
                      (item) => item.thread_id !== threadToDelete.thread_id
                    );
                    if (remaining.length) {
                      const sorted = sortThreads(remaining);
                      setThreads(sorted);
                      if (activeThreadId === threadToDelete.thread_id) {
                        setActiveThreadId(sorted[0].thread_id);
                      }
                    } else {
                      const created = await apiPost("/api/threads", {
                        title: EMPTY_THREAD_TITLE,
                        workspace_id: activeWorkspaceId || null,
                      });
                      const normalized = normalizeThread(created);
                      setThreads([normalized]);
                      setActiveThreadId(normalized.thread_id);
                    }
                    setThreadToDelete(null);
                  } catch (err) {
                    pushToast({
                      title: "Delete failed",
                      message: err.message || "Could not delete thread",
                      variant: "error"
                    });
                  }
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {isModalOpen ? <WorkspaceModal /> : null}
    </Box>
  );
}
