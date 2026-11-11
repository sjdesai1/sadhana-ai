"""Point-in-time features from the canonical tables, for one office.

Every feature for an event uses only what the office knew before that event. Two rules from the spec:
- language is used to pick the message language, never as a scoring input
- no zip code, neighborhood or sex, so scores cannot stand in for income, race or gender
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import Connection, text

HYGIENE_KINDS = ("new_patient", "recall_adult", "recall_child", "perio_maintenance")
MISSED = ("no_show", "late_cancel")


def _frame(conn: Connection, sql: str, **params) -> pd.DataFrame:
    result = conn.execute(text(sql), params)
    return pd.DataFrame(result.fetchall(), columns=list(result.keys()))


def load(conn: Connection) -> dict[str, pd.DataFrame]:
    """The tables the models read, already scoped to one office by the session."""
    patients = _frame(
        conn,
        """
        SELECT p.patient_id, p.birth_date, p.payer, p.secondary_payer, p.phone_type, p.email_on_file,
               p.sms_consent, p.guardian_id, coalesce(pe.maintenance, false) AS perio_maintenance
        FROM patients p LEFT JOIN perio pe USING (patient_id)
        """,
    )
    appts = _frame(
        conn,
        """
        SELECT appt_id, patient_id, provider_id, "column", date, start, kind, booked_on, status, status_on, rebook_of
        FROM appointments
        """,
    )
    for col in ("birth_date",):
        patients[col] = pd.to_datetime(patients[col])
    for col in ("date", "booked_on", "status_on"):
        appts[col] = pd.to_datetime(appts[col])
    appts["hour"] = appts["start"].map(lambda t: t.hour if t is not None else np.nan)
    return {"patients": patients, "appointments": appts}


def _payer_type(payer: pd.Series) -> pd.Series:
    return np.select(
        [payer.eq("Cash"), payer.str.contains("Medicaid", na=False)], ["cash", "medicaid"], default="insured"
    )


def _patient_static(patients: pd.DataFrame) -> pd.DataFrame:
    out = patients[["patient_id", "birth_date", "perio_maintenance"]].copy()
    out["payer_type"] = _payer_type(patients["payer"])
    out["has_secondary"] = patients["secondary_payer"].fillna("").ne("").astype(int)
    out["cell_phone"] = patients["phone_type"].eq("cell").astype(int)
    out["email"] = patients["email_on_file"].fillna(False).astype(int)
    out["texts_ok"] = patients["sms_consent"].fillna(False).astype(int)
    out["is_child_record"] = patients["guardian_id"].fillna("").ne("").astype(int)
    out["perio_maintenance"] = out["perio_maintenance"].astype(int)
    return out


def _history(appts: pd.DataFrame, events: pd.DataFrame, at_col: str) -> pd.DataFrame:
    """For each event (patient_id, at), counts of that patient's visits resolved strictly before `at`."""
    past = appts[appts["status"].isin(("completed", *MISSED))][["patient_id", "date", "status"]]
    ev = pd.DataFrame({"patient_id": events["patient_id"].to_numpy(), "at": events[at_col].to_numpy()})
    ev["_row"] = np.arange(len(ev))
    merged = ev.merge(past, on="patient_id", how="inner")
    merged = merged[merged["date"] < merged["at"]]
    merged = merged.assign(
        completed=merged["status"].eq("completed"),
        no_show=merged["status"].eq("no_show"),
        late_cancel=merged["status"].eq("late_cancel"),
    )
    agg = merged.groupby("_row").agg(
        prior_visits=("status", "size"),
        prior_completed=("completed", "sum"),
        prior_no_shows=("no_show", "sum"),
        prior_late_cancels=("late_cancel", "sum"),
        last=("date", "max"),
        first=("date", "min"),
    )
    out = agg.reindex(np.arange(len(ev)))
    counts = ["prior_visits", "prior_completed", "prior_no_shows", "prior_late_cancels"]
    out[counts] = out[counts].fillna(0)
    at = pd.Series(ev["at"].to_numpy(), index=out.index)
    out["days_since_last_visit"] = (at - out["last"]).dt.days
    out["tenure_days"] = (at - out["first"]).dt.days
    # smoothed miss rate: two imaginary kept visits keep new patients from looking extreme
    out["prior_miss_rate"] = (out["prior_no_shows"] + out["prior_late_cancels"]) / (out["prior_visits"] + 2)
    out = out.drop(columns=["last", "first"])
    out.index = events.index
    return out


def missed_visit_frame(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per appointment with point-in-time features; `missed` is set for visits already resolved."""
    appts = data["appointments"]
    rows = appts[appts["status"].isin(("completed", "scheduled", *MISSED))].copy()
    rows["missed"] = np.where(rows["status"].isin(MISSED), 1.0, np.where(rows["status"].eq("completed"), 0.0, np.nan))
    static = _patient_static(data["patients"])
    rows = rows.merge(static, on="patient_id", how="left")
    hist = _history(appts, rows, "booked_on")
    rows = pd.concat([rows, hist], axis=1)
    rows["age"] = (rows["date"] - rows["birth_date"]).dt.days / 365.25
    rows["lead_days"] = (rows["date"] - rows["booked_on"]).dt.days
    rows["weekday"] = rows["date"].dt.weekday
    rows["month"] = rows["date"].dt.month
    rows["rebooked"] = rows["rebook_of"].fillna("").ne("").astype(int)
    rows["is_hygiene"] = rows["kind"].isin(HYGIENE_KINDS).astype(int)
    rows["is_new_patient"] = rows["kind"].eq("new_patient").astype(int)
    rows["is_emergency"] = rows["kind"].eq("emergency").astype(int)
    rows["is_treatment"] = rows["kind"].isin(("treatment", "srp", "followup")).astype(int)
    return rows


MISSED_FEATURES = [
    "age", "has_secondary", "cell_phone", "email", "texts_ok", "is_child_record", "perio_maintenance",
    "prior_visits", "prior_completed", "prior_no_shows", "prior_late_cancels", "prior_miss_rate",
    "days_since_last_visit", "tenure_days", "lead_days", "weekday", "month", "hour", "rebooked",
    "is_hygiene", "is_new_patient", "is_emergency", "is_treatment", "payer_cash", "payer_medicaid",
]  # fmt: skip


def recall_frame(data: dict[str, pd.DataFrame], snapshots: list[date], horizon_days: int = 60) -> pd.DataFrame:
    """One row per (patient, snapshot) where the patient is overdue for a cleaning with nothing booked.
    `booked` = they booked a cleaning within `horizon_days` on their own (NaN when that window is not over)."""
    appts = data["appointments"]
    hyg = appts[appts["kind"].isin(HYGIENE_KINDS)]
    done = hyg[hyg["status"].eq("completed")]
    static = _patient_static(data["patients"])
    frames = []
    for snap in snapshots:
        t = pd.Timestamp(snap)
        last = done[done["date"] < t].groupby("patient_id")["date"].max()
        live = hyg[(hyg["booked_on"] < t) & (hyg["date"] >= t)]
        live = live[~(live["status"].eq("cancelled") & (live["status_on"] < t))]
        has_booking = set(live["patient_id"])
        snap_df = static.merge(last.rename("last_cleaning"), left_on="patient_id", right_index=True)
        cycle = np.where(snap_df["perio_maintenance"].eq(1), 3, 6)
        due = snap_df["last_cleaning"] + pd.to_timedelta(cycle * 30.4 + 30, unit="D")
        snap_df = snap_df[(due < t) & ~snap_df["patient_id"].isin(has_booking)]
        snap_df = snap_df[(t - snap_df["last_cleaning"]).dt.days < 548]  # still an active patient
        snap_df = snap_df.assign(snapshot=t)
        future = hyg[(hyg["booked_on"] >= t) & (hyg["booked_on"] < t + pd.Timedelta(days=horizon_days))]
        booked = set(future["patient_id"])
        window_over = t + pd.Timedelta(days=horizon_days) <= appts["booked_on"].max()
        snap_df["booked"] = snap_df["patient_id"].isin(booked).astype(float) if window_over else np.nan
        frames.append(snap_df)
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if rows.empty:
        return rows
    hist = _history(appts, rows, "snapshot")
    rows = pd.concat([rows, hist], axis=1)
    rows["age"] = (rows["snapshot"] - rows["birth_date"]).dt.days / 365.25
    rows["days_overdue"] = (rows["snapshot"] - rows["last_cleaning"]).dt.days
    rows["month"] = rows["snapshot"].dt.month
    return rows


RECALL_FEATURES = [
    "age", "has_secondary", "cell_phone", "email", "texts_ok", "is_child_record", "perio_maintenance",
    "prior_visits", "prior_completed", "prior_no_shows", "prior_late_cancels", "prior_miss_rate",
    "days_since_last_visit", "tenure_days", "days_overdue", "month", "payer_cash", "payer_medicaid",
]  # fmt: skip


def with_payer_dummies(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    rows["payer_cash"] = rows["payer_type"].eq("cash").astype(int)
    rows["payer_medicaid"] = rows["payer_type"].eq("medicaid").astype(int)
    return rows
