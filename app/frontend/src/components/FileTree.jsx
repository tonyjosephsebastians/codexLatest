import { useState } from "react";

const getLabel = (path) => {
  if (!path) return "root";
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
};

export default function FileTree({
  tree,
  onSelectFile,
  filter,
  selectedPath
}) {
  const [expanded, setExpanded] = useState(new Set(["", "/"]));

  const toggleFolder = (path) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  if (!tree) {
    return (
      <div className="text-sm text-slate-500">Open a workspace first.</div>
    );
  }

  const warning = tree.truncated ? (
    <p className="mb-2 text-xs text-slate-500">
      File tree truncated. Narrow your workspace or filters.
    </p>
  ) : null;

  const matchesFilter = (node) => {
    if (!filter) return true;
    const text = (node.path || "").toLowerCase();
    if (text.includes(filter.toLowerCase())) return true;
    if (node.type === "dir" && node.children) {
      return node.children.some(matchesFilter);
    }
    return false;
  };

  const renderNode = (node, depth) => {
    if (!matchesFilter(node)) return null;
    const isDir = node.type === "dir";
    const isExpanded = expanded.has(node.path);
    const isSelected = selectedPath && selectedPath === node.path;
    const label = getLabel(node.path);
    const chevron = isDir ? (isExpanded ? "v" : ">") : "";

    if (node.path === "" && isDir && node.children) {
      return node.children.map((child) => renderNode(child, depth));
    }

    return (
      <div key={node.path || "root"}>
        <button
          className={`flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition ${
            isSelected
              ? "bg-primary-50 text-primary-800"
              : "text-slate-700 hover:bg-slate-100"
          }`}
          style={{ paddingLeft: `${depth * 16}px` }}
          type="button"
          onClick={() => {
            if (isDir) {
              toggleFolder(node.path);
            } else {
              onSelectFile(node.path);
            }
          }}
        >
          <span className="w-4 text-xs text-slate-400">{chevron}</span>
          <span className="text-[10px] font-semibold text-slate-400">
            {isDir ? "D" : "F"}
          </span>
          <span className="truncate">{label}</span>
        </button>
        {isDir && isExpanded && node.children
          ? node.children.map((child) => renderNode(child, depth + 1))
          : null}
      </div>
    );
  };

  return (
    <div className="space-y-2">
      {warning}
      {renderNode(tree, 0)}
    </div>
  );
}
