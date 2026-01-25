import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

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

export default function Home() {
  const navigate = useNavigate();
  const [workspace, setWorkspace] = useState(
    localStorage.getItem("workspace") || ""
  );
  const [provider, setProvider] = useState(
    localStorage.getItem("provider") || "gemini"
  );
  const [task, setTask] = useState("");
  const [validate, setValidate] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [qualityMode, setQualityMode] = useState(
    localStorage.getItem("qualityMode") || "quality"
  );
  const [cooldownSeconds, setCooldownSeconds] = useState(0);
  const [llmParams, setLlmParams] = useState(defaultLlmParams);
  const [llmErrors, setLlmErrors] = useState({});
  const [llmPayload, setLlmPayload] = useState({});
  const [repoScope, setRepoScope] = useState(defaultRepoScope);
  const [azureConfig, setAzureConfig] = useState(defaultAzureConfig);
  const [deploymentPresets, setDeploymentPresets] = useState([]);
  const [azureDefaults, setAzureDefaults] = useState(null);

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

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (Object.keys(llmErrors).length) {
      setError("Fix the advanced LLM settings before starting.");
      return;
    }
    setIsSubmitting(true);
    try {
      const payload = {
        workspace,
        task,
        provider,
        validate: validate.trim() ? validate : null,
        llm_params: Object.keys(llmPayload).length ? llmPayload : null,
        repo_scope: buildRepoScopePayload(repoScope),
        quality_mode: qualityMode,
        azure_config:
          provider === "azure_mi" ? buildAzureConfigPayload(azureConfig) : null
      };
      const data = await apiPost("/api/tasks", payload);
      localStorage.setItem("workspace", workspace);
      localStorage.setItem("provider", provider);
      localStorage.setItem("qualityMode", qualityMode);
      navigate(`/tasks/${data.task_id}`);
    } catch (err) {
      if (err.data?.error === "rate_limited") {
        const retryAfter = err.data.retry_after_seconds || 0;
        setCooldownSeconds(retryAfter);
        setError(
          `Rate limited by ${err.data.provider}. Retry in ${retryAfter}s.`
        );
      } else {
        setError(err.message || "Failed to start task");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="grid gap-6">
      <section className="card">
        <h2 className="card-title">Start a Codex run</h2>
        <p className="card-subtitle">
          Provide a workspace path, pick a provider, and send a task. Logs and
          diffs stream to the task page.
        </p>
        <form className="mt-6 grid gap-4" onSubmit={submit}>
          <div>
            <label className="label" htmlFor="workspace">
              Workspace path
            </label>
            <input
              id="workspace"
              className="input"
              value={workspace}
              onChange={(event) => setWorkspace(event.target.value)}
              placeholder="C:\\Projects\\my-repo"
              required
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
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
            <div>
              <label className="label" htmlFor="validate">
                Validate commands (optional)
              </label>
              <input
                id="validate"
                className="input"
                value={validate}
                onChange={(event) => setValidate(event.target.value)}
                placeholder="pytest;git status"
              />
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
          <div>
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
            <p className="muted">
              Deep mode may hit provider rate limits on busy workloads.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="task">
              Task prompt
            </label>
            <textarea
              id="task"
              className="textarea min-h-[160px]"
              value={task}
              onChange={(event) => setTask(event.target.value)}
              placeholder="Describe the change you want in this repo..."
              required
            />
          </div>
          {error ? <p className="text-sm text-red-600">{error}</p> : null}
          <div className="flex flex-wrap gap-3">
            <button
              className="button-primary"
              type="submit"
              disabled={
                isSubmitting ||
                Object.keys(llmErrors).length > 0 ||
                cooldownSeconds > 0
              }
            >
              {isSubmitting
                ? "Starting..."
                : cooldownSeconds > 0
                  ? `Retry in ${cooldownSeconds}s`
                  : "Run Task"}
            </button>
            <button
              className="button-secondary"
              type="button"
              onClick={() => navigate("/tools")}
            >
              Repo Tools
            </button>
          </div>
        </form>
      </section>

      <AdvancedParamsSection
        values={llmParams}
        setValues={setLlmParams}
        errors={llmErrors}
      />

      <RepoScopeSection values={repoScope} setValues={setRepoScope} />
    </div>
  );
}
