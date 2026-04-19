from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import httpx

from app.services.regression import (
    build_lci_real_proposal_fixture,
    build_real_proposal_regression_report,
    evaluate_real_proposal_gate,
)


def _unwrap(response: httpx.Response) -> Any:
    response.raise_for_status()
    payload = response.json()
    return payload["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run product-driven regression against real proposal samples.")
    parser.add_argument("--base-url", default=os.environ.get("PRODUCT_REGRESSION_API_BASE", "http://127.0.0.1:8000/api/v1"))
    parser.add_argument("--report", default=None)
    parser.add_argument("--keep-project", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    report_path = Path(args.report) if args.report else repo_root / "output" / "product-driven-real-proposal-regression.md"
    fixture = build_lci_real_proposal_fixture(repo_root)
    missing_local = [str(path) for path in fixture.local_fixture_paths if not path.exists()]
    if missing_local:
        raise FileNotFoundError("Missing regression fixtures: " + ", ".join(missing_local))

    created_project_id: str | None = None
    deleted_after_run = False
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=90.0) as client:
        try:
            project = _unwrap(
                client.post(
                    "/projects",
                    json={
                        "name": fixture.project_name,
                        "product_line": fixture.product_line,
                        "industry": fixture.industry,
                        "description": fixture.description,
                    },
                )
            )
            created_project_id = str(project["id"])

            client.post(f"/projects/{created_project_id}/extract-requirement", json={}).raise_for_status()
            requirement_card = _unwrap(client.get(f"/projects/{created_project_id}/requirement-card/latest"))

            client.post(
                f"/projects/{created_project_id}/retrieve-evidence",
                json={"top_k": 6, "doc_type": "historical_proposal"},
            ).raise_for_status()
            evidence_bundle = _unwrap(client.get(f"/projects/{created_project_id}/evidence-bundles/latest"))

            solution_snapshot = _unwrap(
                client.post(
                    f"/projects/{created_project_id}/design-solution",
                    json={"force_refresh": True},
                )
            )

            gate_results = evaluate_real_proposal_gate(
                fixture=fixture,
                requirement_card=requirement_card,
                evidence_bundle=evidence_bundle,
                solution_snapshot=solution_snapshot,
            )
            report = build_real_proposal_regression_report(
                fixture=fixture,
                requirement_card=requirement_card,
                evidence_bundle=evidence_bundle,
                solution_snapshot=solution_snapshot,
                gate_results=gate_results,
                project_id=created_project_id,
                deleted_after_run=not args.keep_project,
            )
            report_path.write_text(report, encoding="utf-8")

            failed = [gate for gate in gate_results if not gate.get("passed")]
            print(f"report={report_path}")
            print(f"project_id={created_project_id}")
            print(f"gates_passed={len(gate_results) - len(failed)}/{len(gate_results)}")
            if failed:
                for gate in failed:
                    print(f"failed_gate={gate['gate']} detail={gate['detail']}")
                return 1
            return 0
        finally:
            if created_project_id and not args.keep_project:
                try:
                    client.delete(f"/projects/{created_project_id}")
                    deleted_after_run = True
                except httpx.HTTPError:
                    deleted_after_run = False


if __name__ == "__main__":
    raise SystemExit(main())
