"""Train, test and score the nightly models for one office.

- missed_visit: chance a booked visit ends as a no-show or late cancel
- recall_self_book: chance an overdue patient books a cleaning on their own within 60 days, with nothing more
  than the office's usual reminders. Low scores are the patients outreach should go to first

Each model is XGBoost on point-in-time features, tested on the most recent months it never saw (train on the
past, early-stop on the next stretch, test on the latest), then used to score what is open today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from xgboost import XGBClassifier

from sadhana.benefits.rules import add_months
from sadhana.models import features as F

VERSION = "1"
PARAMS = {
    "n_estimators": 800,
    "learning_rate": 0.03,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "eval_metric": "logloss",
    "early_stopping_rounds": 50,
    "random_state": 42,
    "n_jobs": 4,
}


@dataclass
class ModelResult:
    model: str
    version: str
    n_train: int
    n_test: int
    base_rate: float
    auc: float
    brier: float
    log_loss: float
    calibration_error: float  # mean gap between predicted and observed rates across ten bins
    top_features: list[tuple[str, float]]
    scores: pd.DataFrame = field(repr=False)  # entity_id, patient_id, score

    def metrics(self) -> dict:
        return {
            "n_train": self.n_train,
            "n_test": self.n_test,
            "base_rate": round(self.base_rate, 4),
            "auc": round(self.auc, 4),
            "brier": round(self.brier, 4),
            "log_loss": round(self.log_loss, 4),
            "calibration_error": round(self.calibration_error, 4),
            "top_features": [[n, round(v, 4)] for n, v in self.top_features],
        }


def _fit_and_test(
    model_name: str,
    labeled: pd.DataFrame,
    time_col: str,
    target: str,
    feats: list[str],
    test_from: pd.Timestamp,
    valid_from: pd.Timestamp,
) -> tuple[XGBClassifier, ModelResult]:
    train = labeled[labeled[time_col] < valid_from]
    valid = labeled[(labeled[time_col] >= valid_from) & (labeled[time_col] < test_from)]
    test = labeled[labeled[time_col] >= test_from]
    if min(len(train), len(valid), len(test)) == 0 or train[target].nunique() < 2:
        raise ValueError(f"{model_name}: not enough history to train and test")
    clf = XGBClassifier(**PARAMS)
    clf.fit(train[feats], train[target], eval_set=[(valid[feats], valid[target])], verbose=False)
    p = clf.predict_proba(test[feats])[:, 1]
    y = test[target].to_numpy()
    obs, pred = calibration_curve(y, p, n_bins=10, strategy="quantile")
    gain = clf.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1.0
    top = sorted(((k, v / total) for k, v in gain.items()), key=lambda kv: -kv[1])[:8]
    result = ModelResult(
        model_name,
        VERSION,
        len(train),
        len(test),
        float(y.mean()),
        float(roc_auc_score(y, p)) if len(set(y)) > 1 else float("nan"),
        float(brier_score_loss(y, p)),
        float(log_loss(y, p, labels=[0, 1])),
        float(np.mean(np.abs(obs - pred))),
        top,
        pd.DataFrame(),
    )
    return clf, result


def train_missed_visit(data: dict[str, pd.DataFrame], today: date) -> ModelResult:
    rows = F.with_payer_dummies(F.missed_visit_frame(data))
    t = pd.Timestamp(today)
    labeled = rows[rows["missed"].notna() & (rows["date"] < t)]
    test_from = pd.Timestamp(add_months(today, -4))
    valid_from = pd.Timestamp(add_months(today, -6))
    clf, result = _fit_and_test("missed_visit", labeled, "date", "missed", F.MISSED_FEATURES, test_from, valid_from)
    upcoming = rows[rows["status"].eq("scheduled") & (rows["date"] >= t)]
    result.scores = pd.DataFrame(
        {
            "entity_id": upcoming["appt_id"].to_numpy(),
            "patient_id": upcoming["patient_id"].to_numpy(),
            "score": clf.predict_proba(upcoming[F.MISSED_FEATURES])[:, 1] if len(upcoming) else [],
        }
    )
    return result


def train_recall_self_book(data: dict[str, pd.DataFrame], today: date) -> ModelResult:
    first = data["appointments"]["date"].min().date()
    snaps = []
    d = date(first.year, first.month, 1)
    d = add_months(d, 7)  # enough history for anyone to be overdue
    while d <= today:
        snaps.append(d)
        d = add_months(d, 1)
    if snaps[-1] != today:
        snaps.append(today)
    rows = F.with_payer_dummies(F.recall_frame(data, snaps))
    labeled = rows[rows["booked"].notna()]
    last_labeled = labeled["snapshot"].max()
    test_from = last_labeled - pd.DateOffset(months=3)
    valid_from = last_labeled - pd.DateOffset(months=5)
    clf, result = _fit_and_test(
        "recall_self_book", labeled, "snapshot", "booked", F.RECALL_FEATURES, test_from, valid_from
    )
    now = rows[rows["snapshot"].eq(pd.Timestamp(today))]
    result.scores = pd.DataFrame(
        {
            "entity_id": now["patient_id"].to_numpy(),
            "patient_id": now["patient_id"].to_numpy(),
            "score": clf.predict_proba(now[F.RECALL_FEATURES])[:, 1] if len(now) else [],
        }
    )
    return result
