import { useState } from "react";

export default function RepoScopeSection({ values, setValues }) {
  const [open, setOpen] = useState(false);
  const updateField = (field) => (event) => {
    const value = event.target.type === "checkbox"
      ? event.target.checked
      : event.target.value;
    setValues((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="card">
      <button
        className="button-secondary w-full justify-between"
        type="button"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span>Repo scope (optional)</span>
        <span>{open ? "Hide" : "Show"}</span>
      </button>
      {open ? (
        <div className="mt-5 grid gap-4">
          <div>
            <label className="label" htmlFor="include_globs">
              Include globs
            </label>
            <input
              id="include_globs"
              className="input"
              value={values.include_globs}
              onChange={updateField("include_globs")}
              placeholder="src/**,README.md"
            />
            <p className="muted">
              Leave empty to allow all files (subject to excludes).
            </p>
          </div>
          <div>
            <label className="label" htmlFor="exclude_globs">
              Exclude globs
            </label>
            <input
              id="exclude_globs"
              className="input"
              value={values.exclude_globs}
              onChange={updateField("exclude_globs")}
            />
          </div>
          <div>
            <label className="label" htmlFor="focus_files">
              Focus files (one per line)
            </label>
            <textarea
              id="focus_files"
              className="textarea min-h-[120px]"
              value={values.focus_files}
              onChange={updateField("focus_files")}
              placeholder="src/main.py\nREADME.md"
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="flex items-center gap-3 text-sm font-semibold text-slate-700">
              <input
                type="checkbox"
                checked={values.allow_write}
                onChange={updateField("allow_write")}
              />
              Allow write operations
            </label>
            <label className="flex items-center gap-3 text-sm font-semibold text-slate-700">
              <input
                type="checkbox"
                checked={values.allow_apply_patch}
                onChange={updateField("allow_apply_patch")}
              />
              Allow apply patch
            </label>
          </div>
        </div>
      ) : null}
    </div>
  );
}
