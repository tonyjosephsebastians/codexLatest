import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import mermaid from "mermaid";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { apiPost } from "../lib/api.js";
import { useWorkspace } from "../context/WorkspaceContext.jsx";
import { useToast } from "../context/ToastContext.jsx";
import {
  Badge,
  ButtonPrimary,
  Card,
  PanelHeader,
  Select,
  Textarea
} from "../components/ui.jsx";

mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  themeVariables: {
    primaryColor: "#ffffff",
    primaryTextColor: "#1f2328",
    primaryBorderColor: "#d0d7de",
    lineColor: "#57606a",
    secondaryColor: "#f6f8fa",
    tertiaryColor: "#f6f8fa",
    noteBkgColor: "#f6f8fa",
    clusterBkg: "#f6f8fa",
    clusterBorder: "#d0d7de",
    edgeLabelBackground: "#ffffff",
    fontFamily: "Manrope"
  }
});

const slugify = (text) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");

const extractText = (value) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(extractText).join(" ");
  if (value && typeof value === "object" && value.props?.children) {
    return extractText(value.props.children);
  }
  return "";
};

export default function Wiki() {
  const { activeWorkspace, activeWorkspaceId } = useWorkspace();
  const { pushToast } = useToast();
  const [provider, setProvider] = useState(
    localStorage.getItem("provider") || "gemini"
  );
  const [summary, setSummary] = useState("");
  const [generatedAt, setGeneratedAt] = useState("");
  const [cached, setCached] = useState(false);
  const [queuedMs, setQueuedMs] = useState(null);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeHeading, setActiveHeading] = useState("");
  const docsRef = useRef(null);

  const toc = useMemo(() => {
    const lines = summary.split("\n");
    const headings = [];
    lines.forEach((line) => {
      const match = line.match(/^(#{1,4})\s+(.*)$/);
      if (match) {
        headings.push({
          level: match[1].length,
          text: match[2],
          id: slugify(match[2])
        });
      }
    });
    return headings;
  }, [summary]);

  const MermaidBlock = ({ code }) => {
    const [svg, setSvg] = useState("");
    const idRef = useRef(
      `mermaid-${Math.random().toString(36).slice(2, 9)}`
    );
    useEffect(() => {
      let active = true;
      const render = async () => {
        try {
          const { svg } = await mermaid.render(idRef.current, code);
          if (active) setSvg(svg);
        } catch (err) {
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
        <pre className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
          {code}
        </pre>
      );
    }
    return (
      <div
        className="overflow-auto"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  };

  useEffect(() => {
    if (!docsRef.current) return;
    if (!toc.length) {
      setActiveHeading("");
      return;
    }
    const headings = docsRef.current.querySelectorAll("h1, h2, h3, h4");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setActiveHeading(entry.target.id);
          }
        });
      },
      { root: docsRef.current, rootMargin: "-20% 0px -70% 0px" }
    );

    headings.forEach((heading) => observer.observe(heading));
    return () => observer.disconnect();
  }, [summary, toc]);

  const refreshDocs = async () => {
    if (!activeWorkspaceId) {
      setError("Open a workspace to generate docs.");
      return;
    }
    setError("");
    setLoadingDocs(true);
    try {
      const data = await apiPost("/api/wiki/explain", {
        workspace_id: activeWorkspaceId,
        provider
      });
      setSummary(data.markdown || "");
      setCached(Boolean(data.cached));
      setQueuedMs(data.queued_ms ?? null);
      if (data.generated_at) {
        setGeneratedAt(new Date(data.generated_at).toLocaleString());
      } else {
        setGeneratedAt(new Date().toLocaleString());
      }
      localStorage.setItem("provider", provider);
      pushToast({
        title: "Docs refreshed",
        message: activeWorkspace?.name || "Repository",
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
        setError(err.message || "Failed to generate docs");
      }
    } finally {
      setLoadingDocs(false);
    }
  };

  useEffect(() => {
    if (activeWorkspaceId) {
      refreshDocs();
    }
    if (!activeWorkspaceId) {
      setSummary("");
      setGeneratedAt("");
      setCached(false);
      setQueuedMs(null);
      setChatMessages([]);
    }
  }, [activeWorkspaceId]);

  const submitChat = async (event) => {
    event.preventDefault();
    if (!chatInput.trim()) return;
    if (!activeWorkspaceId) {
      setError("Open a workspace to chat.");
      return;
    }
    const nextMessages = [
      ...chatMessages,
      { role: "user", content: chatInput.trim() }
    ];
    setChatMessages(nextMessages);
    setChatInput("");
    setChatLoading(true);
    setError("");
    try {
      const data = await apiPost("/api/wiki/chat", {
        workspace_id: activeWorkspaceId,
        provider,
        messages: nextMessages
      });
      setChatMessages([...nextMessages, data.message]);
    } catch (err) {
      setError(err.message || "Chat failed");
    } finally {
      setChatLoading(false);
    }
  };

  const handleTocClick = (event, id) => {
    event.preventDefault();
    const target = docsRef.current?.querySelector(`#${id}`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 px-4 py-4 lg:grid-cols-[260px_minmax(0,1fr)_360px]">
      <aside className="hidden min-h-0 flex-col lg:flex">
        <Card className="flex h-full min-h-0 flex-col">
          <PanelHeader title="On this page" subtitle="Auto-generated" />
          <nav className="flex-1 overflow-auto px-4 py-2 text-sm text-slate-600">
            {toc.length ? (
              toc.map((item) => (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  onClick={(event) => handleTocClick(event, item.id)}
                  className={`block rounded-md px-2 py-2 text-sm transition ${
                    activeHeading === item.id
                      ? "bg-primary-50 text-primary-700"
                      : "text-slate-600 hover:bg-slate-100"
                  }`}
                  style={{ paddingLeft: `${(item.level - 1) * 12}px` }}
                >
                  {item.text}
                </a>
              ))
            ) : (
              <p className="text-xs text-slate-400">No headings yet.</p>
            )}
          </nav>
        </Card>
      </aside>

      <section className="min-h-0">
        <Card className="flex h-full min-h-0 flex-col">
          <PanelHeader
            title={activeWorkspace ? activeWorkspace.name : "Repository Docs"}
            subtitle={`Generated ${generatedAt || "-"} | Workspace: ${
              activeWorkspace ? activeWorkspace.name : "None"
            }`}
            action={
              <>
                <Badge>{provider}</Badge>
                {cached ? <Badge>Cached</Badge> : null}
                {queuedMs ? <Badge>Queued {queuedMs}ms</Badge> : null}
                <Select
                  className="h-8 min-w-[140px]"
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                >
                  <option value="gemini">gemini</option>
                  <option value="azure_mi">azure_mi</option>
                </Select>
                <ButtonPrimary
                  type="button"
                  onClick={refreshDocs}
                  disabled={loadingDocs}
                >
                  {loadingDocs ? "Refreshing" : "Refresh docs"}
                </ButtonPrimary>
              </>
            }
          />
          {error ? (
            <p className="px-4 pt-2 text-sm text-red-600">{error}</p>
          ) : null}
          <div className="flex-1 min-h-0 overflow-auto" ref={docsRef}>
            <div className="mx-auto max-w-3xl px-4 py-4">
              {loadingDocs ? (
                <div className="space-y-2">
                  {Array.from({ length: 10 }).map((_, idx) => (
                    <div key={idx} className="skeleton h-4" />
                  ))}
                </div>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeSanitize]}
                  className="prose prose-sm prose-slate"
                  components={{
                    h1: ({ node, ...props }) => (
                      <h1 id={slugify(extractText(props.children))} {...props} />
                    ),
                    h2: ({ node, ...props }) => (
                      <h2 id={slugify(extractText(props.children))} {...props} />
                    ),
                    h3: ({ node, ...props }) => (
                      <h3 id={slugify(extractText(props.children))} {...props} />
                    ),
                    h4: ({ node, ...props }) => (
                      <h4 id={slugify(extractText(props.children))} {...props} />
                    ),
                    code: ({ inline, className, children }) => {
                      if (
                        !inline &&
                        className &&
                        className.includes("language-mermaid")
                      ) {
                        return <MermaidBlock code={String(children)} />;
                      }
                      return (
                        <code className={className}>
                          {children}
                        </code>
                      );
                    }
                  }}
                >
                  {summary || "Open a workspace and refresh docs."}
                </ReactMarkdown>
              )}
            </div>
          </div>
        </Card>
      </section>

      <aside className="hidden min-h-0 flex-col lg:flex">
        <Card className="flex h-full min-h-0 flex-col">
          <PanelHeader
            title="Ask about this repository"
            subtitle="Grounded in your local workspace"
          />
          <div className="flex-1 min-h-0 overflow-auto bg-slate-50/60 px-4 py-2">
            {chatMessages.length ? (
              <div className="space-y-2">
                {chatMessages.map((message, idx) => (
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
                {chatLoading ? (
                  <div className="flex justify-start">
                    <div className="skeleton h-10 w-40" />
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-200 bg-white p-4 text-sm text-slate-500">
                Ask a question about architecture, flows, or key files.
              </div>
            )}
          </div>
          <form
            className="border-t border-slate-200 p-4"
            onSubmit={submitChat}
          >
            <Textarea
              className="min-h-[96px]"
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              placeholder="Ask about architecture, flows, or key files..."
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submitChat(event);
                }
              }}
            />
            <div className="mt-2 flex items-center justify-between">
              <p className="text-xs text-slate-500">
                Shift + Enter for a new line
              </p>
              <ButtonPrimary type="submit" disabled={chatLoading}>
                {chatLoading ? "Thinking" : "Send"}
              </ButtonPrimary>
            </div>
          </form>
        </Card>
      </aside>
    </div>
  );
}
