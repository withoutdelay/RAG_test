import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_reuse_first_artifacts_exist() -> None:
    required = [
        ROOT / "output" / "reuse-first-prd.md",
        ROOT / "output" / "reuse-first-architecture.md",
        ROOT / "output" / "reuse-first-uiux.md",
        ROOT / ".super-dev" / "changes" / "reuse-first" / "specs" / "reuse-first" / "spec.md",
        ROOT / ".super-dev" / "changes" / "reuse-first" / "plan.md",
        ROOT / ".super-dev" / "changes" / "reuse-first" / "checklist.md",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"missing governance artifacts: {missing}"


def test_runtime_contract_artifacts_exist() -> None:
    required = [
        ROOT / "output" / "my-project-ui-contract.json",
        ROOT / "output" / "frontend" / "index.html",
        ROOT / "output" / "frontend" / "styles.css",
        ROOT / "output" / "frontend" / "design-tokens.css",
        ROOT / "output" / "frontend" / "app.js",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"missing runtime artifacts: {missing}"


def test_project_replay_regression_guard_is_healthy() -> None:
    markdown_path = ROOT / "output" / "RAG_test-project-replay-eval.md"
    json_path = ROOT / "output" / "RAG_test-project-replay-eval.json"
    history_dir = ROOT / "output" / "project-replay-history"
    required = [markdown_path, json_path, history_dir]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"missing project replay artifacts: {missing}"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    comparison_summary = comparison.get("summary") if isinstance(comparison.get("summary"), dict) else {}

    assert snapshot, "project replay report is missing snapshot payload"
    assert summary.get("health") == "healthy", f"unexpected replay health: {summary.get('health')}"

    total_sections = int(summary.get("total_sections") or 0)
    generated_sections = int((summary.get("status_counts") or {}).get("generated") or 0)
    passed_sections = int((summary.get("quality_counts") or {}).get("passed") or 0)
    assert total_sections > 0, "project replay report must cover at least one section"
    assert generated_sections == total_sections, f"generated sections mismatch: {generated_sections}/{total_sections}"
    assert passed_sections == total_sections, f"passed sections mismatch: {passed_sections}/{total_sections}"
    assert int(summary.get("open_blocking_review_task_count") or 0) == 0, "blocking review tasks remain open"

    assert comparison, "project replay report must include baseline comparison"
    assert int(comparison_summary.get("regression_count") or 0) == 0, f"detected regressions: {comparison_summary}"

    baseline_path = Path(str(comparison.get("baseline_path") or ""))
    assert baseline_path.is_file(), f"baseline snapshot missing: {baseline_path}"
    history_files = sorted(history_dir.glob("*.json"))
    assert history_files, "project replay history is empty"


def test_project_replay_governance_artifacts_are_wired() -> None:
    required = [
        ROOT / "output" / "RAG_test-project-replay-refresh.json",
        ROOT / "output" / "RAG_test-project-replay-thresholds.json",
        ROOT / "output" / "RAG_test-release-gate.json",
        ROOT / "output" / "RAG_test-proof-pack.json",
        ROOT / "output" / "RAG_test-release-readiness.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"missing replay governance artifacts: {missing}"

    refresh_payload = json.loads((ROOT / "output" / "RAG_test-project-replay-refresh.json").read_text(encoding="utf-8"))
    threshold_payload = json.loads((ROOT / "output" / "RAG_test-project-replay-thresholds.json").read_text(encoding="utf-8"))
    release_gate_payload = json.loads((ROOT / "output" / "RAG_test-release-gate.json").read_text(encoding="utf-8"))
    proof_pack_payload = json.loads((ROOT / "output" / "RAG_test-proof-pack.json").read_text(encoding="utf-8"))
    readiness_payload = json.loads((ROOT / "output" / "RAG_test-release-readiness.json").read_text(encoding="utf-8"))

    refresh_summary = release_gate_payload.get("project_replay_refresh_summary")
    threshold_summary = release_gate_payload.get("project_replay_threshold_summary")
    assert isinstance(refresh_summary, dict) and refresh_summary, "release gate is missing replay refresh advisory"
    assert isinstance(threshold_summary, dict) and threshold_summary, "release gate is missing replay threshold advisory"
    assert int(refresh_summary.get("discovered_projects") or 0) == int((refresh_payload.get("summary") or {}).get("discovered_projects") or 0)
    assert int(threshold_summary.get("eligible_healthy_snapshots") or 0) == int((threshold_payload.get("summary") or {}).get("eligible_healthy_snapshots") or 0)

    artifact_names = [item.get("name") for item in (proof_pack_payload.get("key_artifacts") or []) if isinstance(item, dict)]
    assert "Project Replay Refresh" in artifact_names, "proof pack missing Project Replay Refresh artifact"
    assert "Project Replay Threshold Recommendation" in artifact_names, "proof pack missing replay threshold artifact"

    readiness_checks = {
        item.get("name"): item for item in (readiness_payload.get("checks") or []) if isinstance(item, dict)
    }
    assert "Governance: Project Replay Refresh" in readiness_checks, "release readiness missing replay refresh check"
    assert "Governance: Project Replay Threshold Recommendation" in readiness_checks, "release readiness missing replay threshold check"
    assert "recommendation_requested=" in str(readiness_checks["Governance: Project Replay Refresh"].get("detail") or "")
    assert "recommended=" not in str(readiness_checks["Governance: Project Replay Refresh"].get("detail") or "")


def test_ai_wiki_prior_governance_artifacts_are_wired() -> None:
    required = [
        ROOT / "output" / "RAG_test-layer4-ai-wiki-prior-eval.json",
        ROOT / "output" / "RAG_test-release-gate.json",
        ROOT / "output" / "RAG_test-proof-pack.json",
        ROOT / "output" / "RAG_test-release-readiness.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert not missing, f"missing AI wiki governance artifacts: {missing}"

    ai_wiki_payload = json.loads((ROOT / "output" / "RAG_test-layer4-ai-wiki-prior-eval.json").read_text(encoding="utf-8"))
    release_gate_payload = json.loads((ROOT / "output" / "RAG_test-release-gate.json").read_text(encoding="utf-8"))
    proof_pack_payload = json.loads((ROOT / "output" / "RAG_test-proof-pack.json").read_text(encoding="utf-8"))
    readiness_payload = json.loads((ROOT / "output" / "RAG_test-release-readiness.json").read_text(encoding="utf-8"))

    ai_wiki_summary = release_gate_payload.get("knowledge_wiki_prior_summary")
    assert isinstance(ai_wiki_summary, dict) and ai_wiki_summary, "release gate is missing AI wiki prior advisory"
    assert int(ai_wiki_summary.get("total_sections") or 0) == int((ai_wiki_payload.get("summary") or {}).get("total_sections") or 0)

    artifact_names = [item.get("name") for item in (proof_pack_payload.get("key_artifacts") or []) if isinstance(item, dict)]
    assert "AI Wiki Prior Evaluation" in artifact_names, "proof pack missing AI wiki prior artifact"

    readiness_checks = {
        item.get("name"): item for item in (readiness_payload.get("checks") or []) if isinstance(item, dict)
    }
    assert "Governance: AI Wiki Prior Evaluation" in readiness_checks, "release readiness missing AI wiki prior check"
    detail = str(readiness_checks["Governance: AI Wiki Prior Evaluation"].get("detail") or "")
    assert "sections=" in detail
    assert "evaluation=" in detail
