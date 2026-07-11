from __future__ import annotations

import builtins
from pathlib import Path

from app.api.container import build_container
from app.domain.enums import Severity
from app.domain.models import Candidate
from app.persistence.repositories import FeedbackRepository, FindingRepository
from app.services.ranking_service import RankingService
from tests import harness


def _candidate(finding_type: str, idx: int, *, confidence: float) -> Candidate:
    """Synthetic candidate where ONLY finding_type carries label signal (severity/field_name/
    confidence distributions are identical across classes) - isolates the type=<t> one-hot
    feature as the learnable signal for a robust, deterministic test.
    """
    return Candidate(
        object_name="Opportunity",
        record_id=f"00Q{idx:05d}",
        record_name=f"Deal {idx}",
        finding_type=finding_type,
        field_name=None,
        severity=Severity.MEDIUM,
        confidence=confidence,
        priority=0.5,
        title=f"{finding_type} candidate {idx}",
        evidence={"why": "synthetic"},
    )


def _signal_specs(n_pairs: int = 50) -> list[tuple[str, str, float]]:
    """Interleaved (finding_type, label, confidence) specs, oldest -> newest, so a chronological
    time-based split sees both classes in both the train and holdout slices.
    """
    specs: list[tuple[str, str, float]] = []
    for i in range(n_pairs):
        conf = 0.7 + 0.01 * (i % 5)
        specs.append(("required_missing", "real_issue", conf))
        specs.append(("ml_anomaly", "false_positive", conf))
    return specs


def _seed_labeled_findings(container, *, scan_id: str, specs: list[tuple[str, str, float]]) -> list[str]:
    """Insert a finding + a feedback row per spec, in order, on ONE connection (so feedback.rowid
    order matches insertion order for the deterministic time-based split). Returns finding ids.
    """
    findings = FindingRepository()
    feedback = FeedbackRepository()
    ids: list[str] = []
    with container.database.connect() as conn:
        for i, (finding_type, label, confidence) in enumerate(specs):
            cand = _candidate(finding_type, i, confidence=confidence)
            findings.upsert_many(
                conn, scan_id=scan_id, candidates=[cand],
                source_hash="h", scan_created_at="2026-01-01T00:00:00+00:00",
                source_key="src:test",
            )
            row = conn.execute(
                "SELECT id FROM findings WHERE record_id=? AND finding_type=? ORDER BY rowid DESC LIMIT 1",
                (cand.record_id, finding_type),
            ).fetchone()
            feedback.insert(conn, finding_id=row["id"], label=label, note="", reviewer="synthetic")
            ids.append(row["id"])
    return ids


def _fresh_finding(finding_id: str, finding_type: str, *, priority: float = 0.5) -> dict:
    return {
        "id": finding_id,
        "finding_type": finding_type,
        "severity": "medium",
        "confidence": 0.72,
        "priority": priority,
        "field_name": None,
        "evidence": {},
    }


def test_model_learns_the_synthetic_signal(tmp_path: Path):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    _seed_labeled_findings(container, scan_id="s-signal", specs=_signal_specs(50))

    result = container.learning_service.train()

    assert result["trained"] is True
    assert result["n_labels"] == 100
    assert result["metrics"]["accuracy"] >= 0.8

    preds = container.learning_service.predict_acceptance([
        _fresh_finding("fresh-req", "required_missing"),
        _fresh_finding("fresh-ml", "ml_anomaly"),
    ])
    assert preds is not None
    assert preds["fresh-req"]["p_accept"] > preds["fresh-ml"]["p_accept"]


def test_cold_start_guard_blocks_training_and_prediction(tmp_path: Path):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    specs = _signal_specs(6)  # 6 pairs = 12 labels total, well below min_labels=40
    _seed_labeled_findings(container, scan_id="s-cold", specs=specs)

    result = container.learning_service.train()

    assert result["trained"] is False
    assert "insufficient" in result["reason"]
    assert result["n_labels"] == 12

    preds = container.learning_service.predict_acceptance([_fresh_finding("x1", "required_missing")])
    assert preds is None

    with container.database.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM learned_models").fetchone()[0]
    assert count == 0


def test_ranking_uses_model_then_falls_back_to_heuristic(tmp_path: Path):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    _seed_labeled_findings(container, scan_id="s-rank", specs=_signal_specs(50))
    assert container.learning_service.train()["trained"] is True

    findings_to_rank = [
        _fresh_finding("r1", "required_missing"),
        _fresh_finding("r2", "ml_anomaly"),
    ]
    ranker = RankingService(
        database=container.database, feedback=FeedbackRepository(), learner=container.learning_service,
    )
    ranked = ranker.rank(findings_to_rank)

    assert ranked[0]["id"] == "r1"
    assert ranked[0]["ranking_basis"] == "model"
    assert ranked[1]["ranking_basis"] == "model"
    assert "model_p_accept" in ranked[0] and "model_factors" in ranked[0]

    # A fresh deployment with no trained model must fall back to the exact heuristic behavior.
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    fresh_container = build_container(data_dir=fresh_dir, root=harness.ROOT)
    fresh_container.database.setup()
    fresh_findings = [
        _fresh_finding("r1", "required_missing"),
        _fresh_finding("r2", "ml_anomaly"),
    ]
    fresh_ranker = RankingService(
        database=fresh_container.database, feedback=FeedbackRepository(), learner=fresh_container.learning_service,
    )
    fresh_ranked = fresh_ranker.rank(fresh_findings)
    for row in fresh_ranked:
        assert row["ranking_basis"] == "heuristic"
        assert row["rank_score"] == row["priority"]
        assert "model_p_accept" not in row


def test_predict_acceptance_works_without_sklearn(tmp_path: Path, monkeypatch):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    _seed_labeled_findings(container, scan_id="s-noskl", specs=_signal_specs(50))
    assert container.learning_service.train()["trained"] is True

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError("sklearn forcibly disabled for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    preds = container.learning_service.predict_acceptance([
        _fresh_finding("x1", "required_missing"),
        _fresh_finding("x2", "ml_anomaly"),
    ])

    assert preds is not None
    assert 0.0 <= preds["x1"]["p_accept"] <= 1.0
    assert 0.0 <= preds["x2"]["p_accept"] <= 1.0


def test_predict_acceptance_top_factors_are_explainable(tmp_path: Path):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    _seed_labeled_findings(container, scan_id="s-explain", specs=_signal_specs(50))
    assert container.learning_service.train()["trained"] is True

    preds = container.learning_service.predict_acceptance([_fresh_finding("x1", "required_missing")])
    top_factors = preds["x1"]["top_factors"]

    assert top_factors
    assert all({"feature", "contribution"} <= set(f.keys()) for f in top_factors)
    assert any(f["feature"] == "type=required_missing" for f in top_factors)


def test_learning_status_and_train_api(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        status = client.get("/api/learning/status").json()
        assert status["ready"] is False
        assert status["n_labels"] == 0
        assert status["model"] is None

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    _seed_labeled_findings(container, scan_id="s-api", specs=_signal_specs(50))

    with harness.api_client(tmp_path) as client:
        train_resp = client.post("/api/learning/train")
        assert train_resp.status_code == 200
        assert train_resp.json()["trained"] is True

        status2 = client.get("/api/learning/status").json()
        assert status2["ready"] is True
        assert status2["model"] is not None
        assert "metrics" in status2["model"]
