from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from super_dev.host_commands import (
    DEFAULT_BASE_URL,
    HostGateError,
    run_project_replay_gate,
    run_quality_smoke,
    run_release_gate,
)


def _resolve_external_binary() -> str | None:
    candidates = [
        Path.home() / ".local" / "bin" / "super-dev",
        Path("/usr/local/bin/super-dev"),
        Path("/opt/homebrew/bin/super-dev"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _build_host_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-dev host")
    subparsers = parser.add_subparsers(dest="host_command", required=True)

    def add_project_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--project-id",
            default="",
            help="Target project UUID. Defaults to the latest replay report project when available.",
        )
        command_parser.add_argument(
            "--base-url",
            default=DEFAULT_BASE_URL,
            help="Runtime API base URL.",
        )
        command_parser.add_argument(
            "--draft-version",
            type=int,
            default=0,
            help="Draft version to inspect. Defaults to the current draft when omitted or 0.",
        )
        command_parser.add_argument(
            "--output",
            default="",
            help="Optional markdown output path for replay evaluation.",
        )
        command_parser.add_argument(
            "--json-output",
            default="",
            help="Optional JSON output path for replay evaluation.",
        )
        command_parser.add_argument(
            "--history-dir",
            default="",
            help="Optional history directory override for replay evaluation.",
        )
        command_parser.add_argument(
            "--use-recommended-thresholds",
            action="store_true",
            help="Load recommended retrieval thresholds when explicit gate flags are unset.",
        )
        command_parser.add_argument(
            "--recommended-thresholds-json",
            default="",
            help="Optional recommendation JSON override used by --use-recommended-thresholds.",
        )

    release_gate = subparsers.add_parser("release-gate", help="Run repo-local replay gate and quality smoke.")
    add_project_options(release_gate)
    release_gate.add_argument(
        "--skip-project-replay",
        action="store_true",
        help="Skip the project replay step.",
    )
    release_gate.add_argument(
        "--skip-quality-smoke",
        action="store_true",
        help="Skip the quality smoke step.",
    )

    project_replay = subparsers.add_parser("project-replay-gate", help="Run the project replay gate only.")
    add_project_options(project_replay)

    subparsers.add_parser("quality-smoke", help="Run repo-local quality smoke checks.")
    return parser


def _run_local_host_command(argv: list[str]) -> int:
    namespace = _build_host_parser().parse_args(argv[1:])
    try:
        if namespace.host_command == "release-gate":
            return run_release_gate(
                project_id=namespace.project_id,
                base_url=namespace.base_url,
                draft_version=namespace.draft_version,
                output_path=namespace.output,
                json_output_path=namespace.json_output,
                history_dir=namespace.history_dir,
                skip_project_replay=bool(namespace.skip_project_replay),
                skip_quality_smoke=bool(namespace.skip_quality_smoke),
                use_recommended_thresholds=bool(namespace.use_recommended_thresholds),
                recommended_thresholds_json=namespace.recommended_thresholds_json,
            )
        if namespace.host_command == "project-replay-gate":
            return run_project_replay_gate(
                project_id=namespace.project_id,
                base_url=namespace.base_url,
                draft_version=namespace.draft_version,
                output_path=namespace.output,
                json_output_path=namespace.json_output,
                history_dir=namespace.history_dir,
                use_recommended_thresholds=bool(namespace.use_recommended_thresholds),
                recommended_thresholds_json=namespace.recommended_thresholds_json,
            )
        if namespace.host_command == "quality-smoke":
            run_quality_smoke()
            return 0
    except HostGateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "host":
        return _run_local_host_command(args)
    external = _resolve_external_binary()
    if not external:
        print(
            "External super-dev executable not found. Install it with `pip install -U super-dev` or `uv tool install super-dev`.",
            file=sys.stderr,
        )
        return 1
    completed = subprocess.run([external, *args], check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
