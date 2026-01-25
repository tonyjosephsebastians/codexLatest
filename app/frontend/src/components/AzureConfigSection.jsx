export default function AzureConfigSection({
  values,
  setValues,
  deployments,
  defaults
}) {
  const updateField = (field) => (event) => {
    setValues((prev) => ({ ...prev, [field]: event.target.value }));
  };

  return (
    <div className="mt-4 rounded-2xl border border-border-soft bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-800">
        Azure OpenAI settings
      </h3>
      <p className="mt-1 text-xs text-slate-600">
        These override server defaults for this request. Deployment must be the
        Azure deployment name.
      </p>

      {deployments.length ? (
        <div className="mt-4">
          <label className="label" htmlFor="deployment_preset">
            Deployment presets
          </label>
          <select
            id="deployment_preset"
            className="select"
            value=""
            onChange={(event) => {
              const value = event.target.value;
              if (value) {
                setValues((prev) => ({ ...prev, deployment: value }));
              }
            }}
          >
            <option value="">Select a deployment</option>
            {deployments.map((deployment) => (
              <option key={deployment} value={deployment}>
                {deployment}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <label className="label" htmlFor="azure_endpoint">
            AZURE_OPENAI_ENDPOINT
          </label>
          <input
            id="azure_endpoint"
            className="input"
            value={values.endpoint}
            onChange={updateField("endpoint")}
            placeholder={defaults?.endpoint || "https://<resource>.openai.azure.com/"}
          />
        </div>
        <div>
          <label className="label" htmlFor="azure_deployment">
            AZURE_OPENAI_DEPLOYMENT
          </label>
          <input
            id="azure_deployment"
            className="input"
            value={values.deployment}
            onChange={updateField("deployment")}
            placeholder={defaults?.deployment || "deployment-name"}
          />
        </div>
        <div>
          <label className="label" htmlFor="azure_api_version">
            AZURE_OPENAI_API_VERSION
          </label>
          <input
            id="azure_api_version"
            className="input"
            value={values.api_version}
            onChange={updateField("api_version")}
            placeholder={defaults?.api_version || "2024-12-01-preview"}
          />
        </div>
        <div>
          <label className="label" htmlFor="azure_mi_client_id">
            AZURE_MANAGED_IDENTITY_CLIENT_ID
          </label>
          <input
            id="azure_mi_client_id"
            className="input"
            value={values.managed_identity_client_id}
            onChange={updateField("managed_identity_client_id")}
            placeholder={defaults?.managed_identity_client_id || "(optional)"}
          />
        </div>
      </div>
    </div>
  );
}
