import argparse
import json

from dotenv import load_dotenv

from app.backend.runner import run_task_sync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex agent tasks")
    parser.add_argument("--workspace", required=True, help="Workspace path")
    parser.add_argument("--task", required=True, help="Task prompt")
    parser.add_argument(
        "--provider",
        required=True,
        choices=["gemini", "azure_mi"],
        help="LLM provider",
    )
    parser.add_argument(
        "--validate",
        default=None,
        help='Validate command allowlist, e.g. "pytest;git status"',
    )
    parser.add_argument(
        "--outdir",
        default=None,
        help="Output directory (default: .openhands_runs/<task_id>)",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    record = run_task_sync(
        workspace=args.workspace,
        task=args.task,
        provider=args.provider,
        validate=args.validate,
        out_dir=args.outdir,
    )

    out_dir = record.out_dir
    summary_file = out_dir / "summary.json"
    patch_file = out_dir / "changes.patch"

    summary_text = ""
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            summary_text = summary.get("summary", "")
        except json.JSONDecodeError:
            summary_text = ""

    print(f"task_id: {record.task_id}")
    print(f"out_dir: {out_dir}")
    print(f"patch: {patch_file}")
    if summary_text:
        print(f"summary: {summary_text[:200]}")
    if record.error:
        print(f"error: {record.error}")


if __name__ == "__main__":
    main()
