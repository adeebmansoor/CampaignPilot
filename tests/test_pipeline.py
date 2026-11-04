import csv
import shutil
from pathlib import Path

from sqlalchemy import func, select

from campaignpilot.config import get_settings
from campaignpilot.db.models import CampaignMetrics, Customer
from campaignpilot.db.session import get_session
from campaignpilot.pipeline.etl import extract_and_validate, run_etl


def test_pipeline_loads_all_tables(warehouse):
    assert warehouse["etl"]["loaded"]["customers"] == 2000
    assert warehouse["etl"]["campaign_metrics"] == 12
    assert warehouse["indexed_chunks"] > 5


def test_metrics_are_valid_rates():
    with get_session() as s:
        for m in s.scalars(select(CampaignMetrics)):
            assert m.sent > 0
            assert 0 <= m.open_rate <= 1 and 0 <= m.ctr <= 1 and 0 <= m.conversion_rate <= 1
            assert m.opens >= m.clicks >= m.conversions


def test_etl_is_idempotent():
    raw = get_settings().raw_data_dir
    first = run_etl(raw)
    second = run_etl(raw)
    assert first["loaded"] == second["loaded"]
    with get_session() as s:
        assert s.scalar(select(func.count()).select_from(Customer)) == first["loaded"]["customers"]


def test_bad_rows_are_rejected(tmp_path):
    raw = Path(get_settings().raw_data_dir)
    for f in raw.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    with open(tmp_path / "customers.csv", "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([99999, "bad@example.com", "Bad", "India", "Pune", 30, "SaaS", "platinum",
                         "2026-01-01", "2026-01-02", 0, 1])  # invalid plan
    with open(tmp_path / "campaign_events.csv", "a", newline="") as f:
        csv.writer(f).writerow([10**9, 1, 123456789, "open", "2026-01-01 10:00:00", 0])  # unknown customer
    _, rejected = extract_and_validate(str(tmp_path))
    assert rejected["customers"] == 1
    assert rejected["events"] == 1
