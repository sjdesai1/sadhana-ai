"""The canonical data model: what Sadhana stores for every office, whatever PMS it came from.

One Postgres schema per office (`office_<id>`), the same tables in each. Every table carries `office_id` and a
row-level security policy, so a query can only see rows for the office its session is set to (decision 0002).

Only observed data lives here: what a PMS would hold. The simulator's hidden truth never enters these tables
(decision 0006); it stays in the simulator outputs, where evals read it.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Time,
    func,
)

metadata = MetaData()  # office tables: created once per office schema
meta = MetaData(schema="sadhana_meta")  # shared bookkeeping: offices and sync runs

MONEY = Numeric(12, 2)


def _office() -> Column:
    return Column("office_id", String(64), nullable=False)


staff = Table(
    "staff",
    metadata,
    _office(),
    Column("staff_id", String(16), primary_key=True),
    Column("name", Text),
    Column("role", String(32), nullable=False),
    Column("tier", String(16)),
    Column("specialty", String(32)),
    Column("certs", JSON),
    Column("days", JSON),
    Column("languages", JSON),
    Column("speed", Numeric(4, 2)),
    Column("frequency", String(32)),
    Column("comp", JSON),
)

patients = Table(
    "patients",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("household_id", String(16), index=True),
    Column("role", String(16)),
    Column("guardian_id", String(16)),
    Column("birth_date", Date),
    Column("sex", String(1)),
    Column("zip", String(10)),
    Column("neighborhood", Text),
    Column("language", String(32)),
    Column("payer", String(64)),
    Column("subscriber_id", String(16)),
    Column("secondary_payer", String(64)),
    Column("phone_type", String(16)),
    Column("email_on_file", Boolean),
    Column("sms_consent", Boolean),
    Column("marketing_consent", Boolean),
    Column("preferred_time", String(16)),
    Column("smoker", Boolean),
    Column("diabetes", Boolean),
    Column("blood_thinner", Boolean),
    Column("pregnant", Boolean),
)

patient_status = Table(
    "patient_status",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("first_visit", Date),
    Column("last_visit", Date),
    Column("last_cleaning", Date),
    Column("next_appointment", Date),
    Column("next_cleaning", Date),
    Column("completed_visits", Integer),
    Column("no_shows", Integer),
    Column("late_cancels", Integer),
    Column("status", String(16), index=True),
)

appointments = Table(
    "appointments",
    metadata,
    _office(),
    Column("appt_id", String(16), primary_key=True),
    Column("patient_id", String(16), nullable=False, index=True),
    Column("provider_id", String(16), nullable=False),
    Column("column", String(16)),
    Column("date", Date, nullable=False, index=True),
    Column("start", Time),
    Column("end", Time),
    Column("kind", String(32)),
    Column("codes", Text),
    Column("booked_on", Date),
    Column("status", String(16), index=True),
    Column("status_on", Date),
    Column("rebook_of", String(16)),
)

procedures = Table(
    "procedures",
    metadata,
    _office(),
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("appt_id", String(16), index=True),
    Column("patient_id", String(16), nullable=False, index=True),
    Column("date", Date, nullable=False),
    Column("provider_id", String(16)),
    Column("code", String(8), nullable=False),
    Column("tooth", String(4)),
    Column("plan_id", String(16)),
)

teeth = Table(
    "teeth",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("tooth", String(4), primary_key=True),
    Column("kind", String(16)),
    Column("present", Boolean),
    Column("missing_reason", String(16)),
    Column("restoration", String(16)),
    Column("filling_surfaces", Integer),
    Column("restoration_age_years", Integer),
    Column("root_canal", Boolean),
)

conditions = Table(  # diagnosed problems only; what an exam charted
    "conditions",
    metadata,
    _office(),
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("patient_id", String(16), nullable=False, index=True),
    Column("tooth", String(4)),
    Column("condition", String(32)),
    Column("severity", String(16)),
    Column("found_on", Date),
    Column("treated_on", Date),
)

perio = Table(
    "perio",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("charted_stage", String(16)),
    Column("bleeding", Boolean),
    Column("grinding_wear", Boolean),
    Column("maintenance", Boolean),
)

treatment_plan = Table(
    "treatment_plan",
    metadata,
    _office(),
    Column("plan_id", String(16), primary_key=True),
    Column("patient_id", String(16), nullable=False, index=True),
    Column("tooth", String(4)),
    Column("code", String(8), nullable=False),
    Column("reason", Text),
    Column("priority", String(16)),
    Column("status", String(16), index=True),
    Column("diagnosed_on", Date),
    Column("status_on", Date),
    Column("appt_id", String(16)),
    Column("times_presented", Integer),
)

ledger = Table(
    "ledger",
    metadata,
    _office(),
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("appt_id", String(16), index=True),
    Column("patient_id", String(16), nullable=False, index=True),
    Column("guarantor_id", String(16)),
    Column("date", Date, nullable=False),
    Column("code", String(8)),
    Column("tooth", String(4)),
    Column("provider_id", String(16)),
    Column("payer", String(64)),
    Column("fee", MONEY),
    Column("allowed", MONEY),
    Column("primary_paid", MONEY),
    Column("secondary_paid", MONEY),
    Column("patient_portion", MONEY),
    Column("write_off", MONEY),
    Column("note", Text),
)

claims = Table(
    "claims",
    metadata,
    _office(),
    Column("claim_id", String(16), primary_key=True),
    Column("appt_id", String(16), index=True),
    Column("patient_id", String(16), nullable=False),
    Column("payer", String(64)),
    Column("rank", String(16)),
    Column("submitted_on", Date),
    Column("status", String(32), index=True),
    Column("paid_on", Date),
    Column("billed", MONEY),
    Column("expected", MONEY),
    Column("paid", MONEY),
    Column("denial_reason", Text),
)

payments = Table(
    "payments",
    metadata,
    _office(),
    Column("payment_id", String(16), primary_key=True),
    Column("date", Date, nullable=False),
    Column("guarantor_id", String(16)),
    Column("patient_id", String(16)),
    Column("source", String(32)),
    Column("amount", MONEY),
    Column("ref", String(16)),
)

benefits = Table(
    "benefits",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("year", Integer, primary_key=True),
    Column("payer", String(64), primary_key=True),
    Column("rank", String(16), primary_key=True),
    Column("annual_max", MONEY),
    Column("used", MONEY),
    Column("remaining", MONEY),
    Column("deductible", MONEY),
    Column("deductible_met", MONEY),
)

balances = Table(
    "balances",
    metadata,
    _office(),
    Column("guarantor_id", String(16), primary_key=True),
    Column("balance", MONEY),
    Column("current", MONEY),
    Column("days_31_60", MONEY),
    Column("days_61_90", MONEY),
    Column("over_90", MONEY),
    Column("insurance_pending", MONEY),
    Column("on_payment_plan", Boolean),
)

scores = Table(  # written by the nightly models, not by the sync
    "scores",
    metadata,
    _office(),
    Column("model", String(32), primary_key=True),
    Column("entity_id", String(16), primary_key=True),  # appointment or patient, depending on the model
    Column("patient_id", String(16), index=True),
    Column("score", Numeric(6, 4), nullable=False),
    Column("model_version", String(16)),
    Column("scored_at", DateTime(timezone=True), server_default=func.now()),
)

next_best = Table(  # the one open item most worth raising with each patient, refreshed nightly
    "next_best",
    metadata,
    _office(),
    Column("patient_id", String(16), primary_key=True),
    Column("plan_id", String(16)),
    Column("code", String(8)),
    Column("tooth", String(4)),
    Column("p_book", Numeric(6, 4)),
    Column("value", MONEY),
    Column("expected_value", MONEY),
    Column("benefits_this_year", MONEY),
    Column("reason", Text),
    Column("scored_at", DateTime(timezone=True), server_default=func.now()),
)

offices = Table(
    "offices",
    meta,
    Column("office_id", String(64), primary_key=True),
    Column("name", Text),
    Column("schema_name", String(80), nullable=False),
    Column("source", String(32)),  # simulator, opendental, ...
    Column("config", JSON),
    Column("last_synced_at", DateTime(timezone=True)),
)

model_runs = Table(
    "model_runs",
    meta,
    Column("run_id", BigInteger, primary_key=True, autoincrement=True),
    Column("office_id", String(64), nullable=False, index=True),
    Column("model", String(32), nullable=False),
    Column("model_version", String(16)),
    Column("trained_at", DateTime(timezone=True), server_default=func.now()),
    Column("as_of", Date),
    Column("metrics", JSON),
)

sync_runs = Table(
    "sync_runs",
    meta,
    Column("run_id", BigInteger, primary_key=True, autoincrement=True),
    Column("office_id", String(64), nullable=False, index=True),
    Column("source", String(32)),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(16)),
    Column("rows", JSON),
    Column("error", Text),
)

# tables the sync replaces; there are no foreign keys, a full refresh reloads everything
OFFICE_TABLES = [
    staff,
    patients,
    patient_status,
    appointments,
    procedures,
    teeth,
    conditions,
    perio,
    treatment_plan,
    ledger,
    claims,
    payments,
    benefits,
    balances,
]
