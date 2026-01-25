import { useState } from "react";

export default function AdvancedParamsSection({ values, setValues, errors }) {
  const [open, setOpen] = useState(false);
  const updateField = (field) => (event) => {
    const value = event.target.value;
    setValues((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="card">
      <button
        className="button-secondary w-full justify-between"
        type="button"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span>Advanced (optional)</span>
        <span>{open ? "Hide" : "Show"}</span>
      </button>
      {open ? (
        <div className="mt-5 grid gap-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="label" htmlFor="model">
                Model override
              </label>
              <input
                id="model"
                className="input"
                value={values.model}
                onChange={updateField("model")}
                placeholder="gemini/gemini-2.0-flash"
              />
            </div>
            <div>
              <label className="label" htmlFor="reasoning_level">
                Reasoning level
              </label>
              <select
                id="reasoning_level"
                className="select"
                value={values.reasoning_level}
                onChange={updateField("reasoning_level")}
              >
                <option value="">default</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="extra_high">extra_high</option>
              </select>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="temperature">
                Temperature
              </label>
              <input
                id="temperature"
                className="input"
                value={values.temperature}
                onChange={updateField("temperature")}
                placeholder="0.2"
              />
              {errors.temperature ? (
                <p className="mt-1 text-xs text-red-600">{errors.temperature}</p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="top_p">
                Top p
              </label>
              <input
                id="top_p"
                className="input"
                value={values.top_p}
                onChange={updateField("top_p")}
                placeholder="0.95"
              />
              {errors.top_p ? (
                <p className="mt-1 text-xs text-red-600">{errors.top_p}</p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="max_output_tokens">
                Max output tokens
              </label>
              <input
                id="max_output_tokens"
                className="input"
                value={values.max_output_tokens}
                onChange={updateField("max_output_tokens")}
                placeholder="1024"
              />
              {errors.max_output_tokens ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.max_output_tokens}
                </p>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <label className="label" htmlFor="num_retries">
                Retries
              </label>
              <input
                id="num_retries"
                className="input"
                value={values.num_retries}
                onChange={updateField("num_retries")}
                placeholder="5"
              />
              {errors.num_retries ? (
                <p className="mt-1 text-xs text-red-600">{errors.num_retries}</p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="retry_min_wait">
                Retry min wait (s)
              </label>
              <input
                id="retry_min_wait"
                className="input"
                value={values.retry_min_wait}
                onChange={updateField("retry_min_wait")}
                placeholder="8"
              />
              {errors.retry_min_wait ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.retry_min_wait}
                </p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="retry_max_wait">
                Retry max wait (s)
              </label>
              <input
                id="retry_max_wait"
                className="input"
                value={values.retry_max_wait}
                onChange={updateField("retry_max_wait")}
                placeholder="64"
              />
              {errors.retry_max_wait ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.retry_max_wait}
                </p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="retry_multiplier">
                Retry multiplier
              </label>
              <input
                id="retry_multiplier"
                className="input"
                value={values.retry_multiplier}
                onChange={updateField("retry_multiplier")}
                placeholder="8.0"
              />
              {errors.retry_multiplier ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.retry_multiplier}
                </p>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="label" htmlFor="presence_penalty">
                Presence penalty
              </label>
              <input
                id="presence_penalty"
                className="input"
                value={values.presence_penalty}
                onChange={updateField("presence_penalty")}
                placeholder="0.0"
              />
              {errors.presence_penalty ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.presence_penalty}
                </p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="frequency_penalty">
                Frequency penalty
              </label>
              <input
                id="frequency_penalty"
                className="input"
                value={values.frequency_penalty}
                onChange={updateField("frequency_penalty")}
                placeholder="0.0"
              />
              {errors.frequency_penalty ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.frequency_penalty}
                </p>
              ) : null}
            </div>
          </div>

          <div>
            <label className="label" htmlFor="system_prompt">
              System prompt (optional)
            </label>
            <textarea
              id="system_prompt"
              className="textarea min-h-[140px]"
              value={values.system_prompt}
              onChange={updateField("system_prompt")}
              placeholder="Add extra system instructions for the agent..."
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="label" htmlFor="extra_headers">
                Extra headers (JSON)
              </label>
              <textarea
                id="extra_headers"
                className="textarea min-h-[120px]"
                value={values.extra_headers}
                onChange={updateField("extra_headers")}
                placeholder='{"X-Trace-Id": "codex"}'
              />
              {errors.extra_headers ? (
                <p className="mt-1 text-xs text-red-600">
                  {errors.extra_headers}
                </p>
              ) : null}
            </div>
            <div>
              <label className="label" htmlFor="extra_body">
                Extra body (JSON)
              </label>
              <textarea
                id="extra_body"
                className="textarea min-h-[120px]"
                value={values.extra_body}
                onChange={updateField("extra_body")}
                placeholder='{"routing": "fast"}'
              />
              {errors.extra_body ? (
                <p className="mt-1 text-xs text-red-600">{errors.extra_body}</p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
