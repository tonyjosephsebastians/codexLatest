import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import mermaid from "mermaid";

import AdvancedParamsSection from "../components/AdvancedParamsSection.jsx";
import AzureConfigSection from "../components/AzureConfigSection.jsx";
import RepoScopeSection from "../components/RepoScopeSection.jsx";
import { apiGet, apiPost } from "../lib/api.js";
import {
  applyAzureDefaults,
  buildAzureConfigPayload,
  defaultAzureConfig
} from "../lib/azureConfig.js";
import { defaultLlmParams, parseLlmParams } from "../lib/llmParams.js";
import { buildRepoScopePayload, defaultRepoScope } from "../lib/repoScope.js";

mermaid.initialize({
  startOnLoad: false,
  theme: "base",
  themeVariables: {
    primaryColor: "#22c55e",
    primaryTextColor: "#0f172a",
    primaryBorderColor: "#16a34a",
    lineColor: "#16a34a",
    fontFamily: "Manrope"
  }
});

export default function Tools() {
  const [workspace, setWorkspace] = useState(
    localStorage.getItem("workspace") || ""
  );
  const [provider, setProvider] = useState(
    localStorage.getItem("provider") || "gemini"
  );
  const [summary, setSummary] = useState("");
  const [keyFiles, setKeyFiles] = useState([]);
  const [diagramType, setDiagramType] = useState("mermaid_flow");
  const [mermaidText, setMermaidText] = useState("");
  const [mermaidSvg, setMermaidSvg] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [qualityMode, setQualityMode] = useState(
    localStorage.getItem("qualityMode") || "quality"
  );
  const [isExplaining, setIsExplaining] = useState(false);
  const [isDiagramming, setIsDiagramming] = useState(false);
  const [queuedMs, setQueuedMs] = useState(null);
  const [cooldownSeconds, setCooldownSeconds] = useState(0);
  const [llmParams, setLlmParams] = useState(defaultLlmParams);
  const [llmErrors, setLlmErrors] = useState({});
  const [llmPayload, setLlmPayload] = useState({});
  const [repoScope, setRepoScope] = useState(defaultRepoScope);
  const [azureConfig, setAzureConfig] = useState(defaultAzureConfig);
  const [deploymentPresets, setDeploymentPresets] = useState([]);
  const [azureDefaults, setAzureDefaults] = useState(null);

  useEffect(() => {
    if (!mermaidText) {
      setMermaidSvg("");
      return;
    }
    const render = async () => {
      try {
        const { svg } = await mermaid.render("repo-diagram", mermaidText);
        setMermaidSvg(svg);
      } catch (err) {
        setMermaidSvg("");
      }
    };
    render();
  }, [mermaidText]);

  useEffect(() => {
    const { errors, parsed } = parseLlmParams(llmParams);
    setLlmErrors(errors);
    setLlmPayload(parsed);
  }, [llmParams]);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const data = await apiGet("/api/config/deployments");
        setDeploymentPresets(data.deployments || []);
        setAzureDefaults(data.defaults || null);
      } catch (err) {
        setDeploymentPresets([]);
        setAzureDefaults(null);
      }
    };
    loadConfig();
  }, []);

  useEffect(() => {
    if (!azureDefaults) return;
    setAzureConfig((prev) => applyAzureDefaults(prev, azureDefaults));
  }, [azureDefaults]);

  useEffect(() => {
    if (cooldownSeconds <= 0) return;
    const timer = setInterval(() => {
      setCooldownSeconds((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [cooldownSeconds]);

  const explainRepo = async () => {
    setError("");
    setQueuedMs(null);
    if (Object.keys(llmErrors).length) {
      setError("Fix the advanced LLM settings before running tools.");
      return;
    }
    if (cooldownSeconds > 0) {
      setError(`Rate limited. Retry in ${cooldownSeconds}s.`);
      return;
    }
    setIsExplaining(true);
    try {
      const data = await apiPost("/api/analyze/explain-repo", {
        workspace,
        provider,
        llm_params: Object.keys(llmPayload).length ? llmPayload : null,
        repo_scope: buildRepoScopePayload(repoScope),
        quality_mode: qualityMode,
        azure_config:
          provider === "azure_mi" ? buildAzureConfigPayload(azureConfig) : null
      });
      const summaryText =
        data.summary_markdown || data.summary || data.message || "";
      setSummary(summaryText);
      setKeyFiles(data.key_files || []);
      setQueuedMs(data.queued_ms ?? null);
      localStorage.setItem("qualityMode", qualityMode);
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        const retryAfter = err.data.retry_after_seconds || 0;
        setCooldownSeconds(retryAfter);
        setError(
          `Rate limited by ${err.data.provider}. Retry in ${retryAfter}s.`
        );
      } else {
        setError(err.message || "Failed to explain repo");
      }
    } finally {
      setIsExplaining(false);
    }
  };

  const generateDiagram = async () => {
    setError("");
    setQueuedMs(null);
    if (Object.keys(llmErrors).length) {
      setError("Fix the advanced LLM settings before running tools.");
      return;
    }
    if (cooldownSeconds > 0) {
      setError(`Rate limited. Retry in ${cooldownSeconds}s.`);
      return;
    }
    setIsDiagramming(true);
    try {
      const data = await apiPost("/api/analyze/architecture-diagram", {
        workspace,
        provider,
        diagram_type: diagramType,
        llm_params: Object.keys(llmPayload).length ? llmPayload : null,
        repo_scope: buildRepoScopePayload(repoScope),
        quality_mode: qualityMode,
        azure_config:
          provider === "azure_mi" ? buildAzureConfigPayload(azureConfig) : null
      });
      setMermaidText(data.mermaid || "");
      setNotes(data.notes_markdown || "");
      setQueuedMs(data.queued_ms ?? null);
      localStorage.setItem("qualityMode", qualityMode);
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        const retryAfter = err.data.retry_after_seconds || 0;
        setCooldownSeconds(retryAfter);
        setError(
          `Rate limited by ${err.data.provider}. Retry in ${retryAfter}s.`
        );
      } else {
        setError(err.message || "Failed to generate diagram");
      }
    } finally {
      setIsDiagramming(false);
    }
  };

  return (
    <div className="grid gap-6">
      <section className="card">
        <h2 className="card-title">Repo tools</h2>
        <p className="card-subtitle">
          Use Codex to explain this repo or draft a diagram.
        </p>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="workspace">
              Workspace path
            </label>
            <input
              id="workspace"
              className="input"
              value={workspace}
              onChange={(event) => setWorkspace(event.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="provider">
              Provider
            </label>
            <select
              id="provider"
              className="select"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              <option value="gemini">gemini</option>
              <option value="azure_mi">azure_mi</option>
            </select>
          </div>
        </div>
        {provider === "azure_mi" ? (
          <AzureConfigSection
            values={azureConfig}
            setValues={setAzureConfig}
            deployments={deploymentPresets}
            defaults={azureDefaults}
          />
        ) : null}
        <div className="mt-4">
          <label className="label" htmlFor="quality_mode">
            Quality vs speed
          </label>
          <select
            id="quality_mode"
            className="select"
            value={qualityMode}
            onChange={(event) => setQualityMode(event.target.value)}
          >
            <option value="speed">speed (fewer turns)</option>
            <option value="quality">quality (default)</option>
            <option value="deep">deep (more turns)</option>
          </select>
          <p className="muted">Deep mode may hit rate limits on busy workloads.</p>
        </div>
        <div className="mt-5 flex flex-wrap items-end gap-3">
          <button
            className="button-primary"
            type="button"
            onClick={explainRepo}
            disabled={
              Object.keys(llmErrors).length > 0 ||
              isExplaining ||
              cooldownSeconds > 0
            }
          >
            {isExplaining
              ? "Explaining..."
              : cooldownSeconds > 0
                ? `Retry in ${cooldownSeconds}s`
                : "Explain Repo"}
          </button>
          <div>
            <label className="label" htmlFor="diagram">
              Diagram type
            </label>
            <select
              id="diagram"
              className="select"
              value={diagramType}
              onChange={(event) => setDiagramType(event.target.value)}
            >
              <option value="mermaid_flow">mermaid_flow</option>
              <option value="mermaid_c4">mermaid_c4</option>
              <option value="sequence">sequence</option>
            </select>
          </div>
          <button
            className="button-secondary"
            type="button"
            onClick={generateDiagram}
            disabled={
              Object.keys(llmErrors).length > 0 ||
              isDiagramming ||
              cooldownSeconds > 0
            }
          >
            {isDiagramming
              ? "Generating..."
              : cooldownSeconds > 0
                ? `Retry in ${cooldownSeconds}s`
                : "Generate Diagram"}
          </button>
        </div>
        {queuedMs ? (
          <p className="mt-3 text-sm text-slate-600">
            Queued due to rate limit for {Math.round(queuedMs / 1000)}s.
          </p>
        ) : null}
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </section>

      <AdvancedParamsSection
        values={llmParams}
        setValues={setLlmParams}
        errors={llmErrors}
      />

      <RepoScopeSection values={repoScope} setValues={setRepoScope} />

      <section className="card">
        <h3 className="card-title">Summary</h3>
        <div className="mt-4 text-sm text-slate-700">
          <ReactMarkdown>{summary || "No summary yet."}</ReactMarkdown>
        </div>
        {keyFiles.length ? (
          <div className="mt-5">
            <h4 className="text-sm font-semibold text-slate-800">Key files</h4>
            <ul className="mt-2 space-y-2 text-sm text-slate-700">
              {keyFiles.map((file, idx) => (
                <li key={idx}>
                  <span className="font-semibold">{file.path}</span>: {file.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="card-title">Architecture diagram</h3>
          <button
            className="button-muted"
            type="button"
            disabled={!mermaidText}
            onClick={() => navigator.clipboard.writeText(mermaidText)}
          >
            Copy Mermaid
          </button>
        </div>
        <div className="mt-4 rounded-xl border border-border-soft bg-white p-4 shadow-sm">
          {mermaidSvg ? (
            <div
              className="max-h-[420px] overflow-auto"
              dangerouslySetInnerHTML={{ __html: mermaidSvg }}
            />
          ) : (
            <p className="text-sm text-slate-600">
              Generate a diagram to preview it here.
            </p>
          )}
        </div>
        {notes ? (
          <div className="mt-4 text-sm text-slate-700">
            <ReactMarkdown>{notes}</ReactMarkdown>
          </div>
        ) : null}
        {mermaidText ? (
          <div className="mt-4">
            <div className="mono-panel max-h-[240px] overflow-auto whitespace-pre">
              {mermaidText}
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
