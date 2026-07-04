from __future__ import annotations

import math
from typing import Any

from ..learning.features import FEATURE_VERSION, feature_names, vectorize
from ..persistence.database import Database
from ..persistence.repositories import FindingRepository, LearnedModelRepository


def _sigmoid(z: float) -> float:
    """Numerically stable logistic sigmoid."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


class LearningService:
    """Trains + serves a supervised (logistic regression) P(human accepts) ranking model from
    reviewer feedback. Cold-start guarded: never trains, saves, or serves a model below label
    thresholds - callers must fall back to the existing heuristic in that case.
    """

    SCOPE = "global"

    def __init__(
        self,
        *,
        database: Database,
        findings: FindingRepository,
        models: LearnedModelRepository,
        min_labels: int = 40,
        min_per_class: int = 8,
        holdout_frac: float = 0.2,
        random_state: int = 42,
    ) -> None:
        self.database = database
        self.findings = findings
        self.models = models
        self.min_labels = min_labels
        self.min_per_class = min_per_class
        self.holdout_frac = holdout_frac
        self.random_state = random_state

    def _label_counts(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        n = len(rows)
        n_pos = sum(1 for row in rows if row["label"] == "real_issue")
        return n, n_pos, n - n_pos

    def train(self) -> dict[str, Any]:
        self.database.setup()
        with self.database.connect() as conn:
            rows = self.findings.labeled_findings(conn)

        n, n_pos, n_neg = self._label_counts(rows)
        if n < self.min_labels or n_pos < self.min_per_class or n_neg < self.min_per_class:
            return {
                "trained": False,
                "reason": (
                    f"insufficient labels (n={n}, pos={n_pos}, neg={n_neg}; need >= {self.min_labels} "
                    f"total and >= {self.min_per_class} per class)"
                ),
                "n_labels": n,
            }

        try:
            import numpy as np
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import precision_score, recall_score, roc_auc_score
        except Exception:
            return {"trained": False, "reason": "sklearn unavailable", "n_labels": n}

        names = feature_names()
        y = [1 if row["label"] == "real_issue" else 0 for row in rows]
        X_raw = [vectorize(row) for row in rows]

        n_holdout = max(1, math.ceil(self.holdout_frac * n))
        n_train = n - n_holdout
        X_train_raw, X_holdout_raw = X_raw[:n_train], X_raw[n_train:]
        y_train, y_holdout = y[:n_train], y[n_train:]

        means = [sum(col) / n_train for col in zip(*X_train_raw)]
        stds = [
            math.sqrt(sum((v - means[i]) ** 2 for v in col) / n_train) or 1.0
            for i, col in enumerate(zip(*X_train_raw))
        ]

        def _standardize(rows_raw: list[list[float]]) -> list[list[float]]:
            return [[(row[i] - means[i]) / stds[i] for i in range(len(names))] for row in rows_raw]

        X_train = np.array(_standardize(X_train_raw))
        clf = LogisticRegression(max_iter=1000, random_state=self.random_state)
        clf.fit(X_train, y_train)

        holdout_note = None
        if not y_holdout:
            accuracy = precision = recall = roc_auc = None
            holdout_note = "holdout is empty"
        elif len(set(y_holdout)) < 2:
            accuracy = precision = recall = roc_auc = None
            holdout_note = "holdout has a single class; metrics not computed"
        else:
            X_holdout = np.array(_standardize(X_holdout_raw))
            preds = clf.predict(X_holdout)
            probs = clf.predict_proba(X_holdout)[:, 1]
            accuracy = float((preds == np.array(y_holdout)).mean())
            precision = float(precision_score(y_holdout, preds, zero_division=0))
            recall = float(recall_score(y_holdout, preds, zero_division=0))
            roc_auc = float(roc_auc_score(y_holdout, probs))

        model = {
            "feature_names": names,
            "means": means,
            "stds": stds,
            "coef": [float(c) for c in clf.coef_[0]],
            "intercept": float(clf.intercept_[0]),
            "feature_version": FEATURE_VERSION,
        }
        metrics: dict[str, Any] = {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "roc_auc": roc_auc,
            "n_train": n_train,
            "n_holdout": n_holdout,
        }
        if holdout_note:
            metrics["note"] = holdout_note

        with self.database.connect() as conn:
            self.models.save(
                conn,
                scope=self.SCOPE,
                feature_version=FEATURE_VERSION,
                model=model,
                n_labels=n,
                metrics=metrics,
            )

        return {"trained": True, "n_labels": n, "metrics": metrics}

    def predict_acceptance(self, findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
        """Pure dot-product -> sigmoid inference from the stored model JSON (no sklearn required)."""
        self.database.setup()
        with self.database.connect() as conn:
            stored = self.models.get_latest(conn, scope=self.SCOPE)
        if stored is None or stored["feature_version"] != FEATURE_VERSION:
            return None

        model = stored["model"]
        names = model["feature_names"]
        means = model["means"]
        stds = model["stds"]
        coef = model["coef"]
        intercept = model["intercept"]

        results: dict[str, dict[str, Any]] = {}
        for finding in findings:
            x = vectorize(finding)
            z = intercept
            contributions: list[tuple[str, float]] = []
            for i, name in enumerate(names):
                std = stds[i] if stds[i] else 1.0
                xstd = (x[i] - means[i]) / std
                contribution = coef[i] * xstd
                z += contribution
                contributions.append((name, contribution))
            p = _sigmoid(z)
            contributions.sort(key=lambda c: abs(c[1]), reverse=True)
            top_factors = [
                {"feature": name, "contribution": round(value, 4)} for name, value in contributions[:3]
            ]
            results[finding["id"]] = {"p_accept": round(p, 4), "top_factors": top_factors}
        return results

    def status(self) -> dict[str, Any]:
        self.database.setup()
        with self.database.connect() as conn:
            stored = self.models.get_latest(conn, scope=self.SCOPE)
            rows = self.findings.labeled_findings(conn)

        n, n_pos, n_neg = self._label_counts(rows)
        ready = n >= self.min_labels and n_pos >= self.min_per_class and n_neg >= self.min_per_class
        model_summary = None
        if stored is not None:
            model_summary = {
                "id": stored["id"],
                "trained_at": stored["trained_at"],
                "n_labels": stored["n_labels"],
                "feature_version": stored["feature_version"],
                "metrics": stored["metrics"],
            }
        return {
            "model": model_summary,
            "n_labels": n,
            "n_pos": n_pos,
            "n_neg": n_neg,
            "ready": ready,
        }
