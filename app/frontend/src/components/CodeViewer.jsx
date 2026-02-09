import { useEffect, useMemo } from "react";
import Prism from "prismjs";

import "prismjs/themes/prism.css";
import "prismjs/components/prism-markup";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-python";
import "prismjs/components/prism-json";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-yaml";

const guessLanguage = (path) => {
  if (!path) return "markup";
  const lower = path.toLowerCase();
  if (lower.endsWith(".py")) return "python";
  if (lower.endsWith(".ts") || lower.endsWith(".tsx")) return "typescript";
  if (lower.endsWith(".js") || lower.endsWith(".jsx")) return "javascript";
  if (lower.endsWith(".json")) return "json";
  if (lower.endsWith(".yml") || lower.endsWith(".yaml")) return "yaml";
  if (lower.endsWith(".sh")) return "bash";
  return "markup";
};

export default function CodeViewer({
  file,
  onLineClick,
  onSelectRange,
  selectedRange,
  diffHighlights,
  loading
}) {
  const language = guessLanguage(file?.path);
  const code = file?.content || "";

  const highlighted = useMemo(() => {
    const grammar = Prism.languages[language] || Prism.languages.markup;
    return Prism.highlight(code, grammar, language);
  }, [code, language]);

  useEffect(() => {
    Prism.highlightAll();
  }, [highlighted]);

  if (!file && loading) {
    return (
      <div className="space-y-2 p-6">
        {Array.from({ length: 12 }).map((_, idx) => (
          <div key={idx} className="skeleton h-4" />
        ))}
      </div>
    );
  }

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        <div className="text-center">
          <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-slate-200 bg-white" />
          <p className="text-sm font-semibold text-slate-700">No file selected</p>
          <p className="mt-1 text-xs text-slate-500">
            Choose a file from the explorer to preview it here.
          </p>
        </div>
      </div>
    );
  }

  if (file.is_binary) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        Binary file preview is not available.
      </div>
    );
  }

  const lines = highlighted.split("\n");
  return (
    <div className="h-full overflow-auto font-mono text-xs text-slate-800">
      {lines.map((line, idx) => (
        <div
          key={`${file.path}-${idx}`}
          className={`flex border-b border-slate-100 ${
            selectedRange &&
            idx + 1 >= selectedRange.start_line &&
            idx + 1 <= selectedRange.end_line
              ? "bg-primary-50"
              : diffHighlights?.added?.has(idx + 1)
                ? "bg-green-50"
                : diffHighlights?.removed?.has(idx + 1)
                  ? "bg-red-50"
              : "hover:bg-slate-50"
          }`}
          onClick={(event) => {
            onLineClick?.(file.path, idx + 1);
            onSelectRange?.(idx + 1, event.shiftKey);
          }}
          role="button"
          tabIndex={0}
          onKeyDown={() => {}}
        >
          <span
            className={`w-12 select-none border-r px-4 py-1 text-right text-[11px] ${
              diffHighlights?.added?.has(idx + 1)
                ? "border-green-300 text-green-600"
                : diffHighlights?.removed?.has(idx + 1)
                  ? "border-red-300 text-red-600"
                  : "border-slate-100 text-slate-400"
            }`}
          >
            {idx + 1}
          </span>
          <span
            className="flex-1 px-4 py-1"
            dangerouslySetInnerHTML={{ __html: line || " " }}
          />
        </div>
      ))}
    </div>
  );
}
