from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Company, Decision, ManagedJob, Site, SiteKey, TariffCatalogue

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def make_site(session, **kw):
    c = Company(name="Acme")
    session.add(c)
    session.flush()
    s = Site(company_id=c.id, name="Pune-1", gpus=8, **kw)
    session.add(s)
    session.flush()
    return s


def make_job(session, site, ref="1", **kw):
    j = ManagedJob(site_id=site.id, ref=ref, state="PENDING", first_seen=NOW, submit_time=NOW, **kw)
    session.add(j)
    session.flush()
    return j


def test_site_defaults(session):
    s = make_site(session)
    session.refresh(s)
    assert s.mode == "shadow" and s.release_all is False
    assert float(s.kw_per_gpu) == 1.25 and float(s.shift_capacity_share) == 0.5
    assert s.start_margin_s == 120 and s.utility == "MSEDCL" and s.tariff_category == "HT-I(A)"
    assert s.power_limit_kw is None and s.last_seen_at is None


def test_managed_job_defaults(session):
    j = make_job(session, make_site(session))
    session.refresh(j)
    assert j.id is not None and j.plan_status == "pending" and j.applied_start is None


def test_managed_job_is_unique_per_site_and_ref(session):
    site = make_site(session)
    make_job(session, site, "48211")
    session.add(ManagedJob(site_id=site.id, ref="48211", state="PENDING", first_seen=NOW, submit_time=NOW))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("kw", [{"mode": "weird"}, {"gpus": 0}])
def test_site_constraints(session, kw):
    c = Company(name="Acme")
    session.add(c)
    session.flush()
    session.add(Site(**{"company_id": c.id, "name": "X", "gpus": 8, **kw}))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("kw", [{"state": "WEIRD"}, {"plan_status": "weird"}])
def test_managed_job_constraints(session, kw):
    site = make_site(session)
    base = {"site_id": site.id, "ref": "1", "state": "PENDING", "first_seen": NOW, "submit_time": NOW}
    session.add(ManagedJob(**{**base, **kw}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_other_tables_round_trip(session):
    site = make_site(session)
    job = make_job(session, site)
    session.add_all([
        SiteKey(site_id=site.id, key_hash="abc"),
        TariffCatalogue(utility="MSEDCL", category="HT-I(A)", valid_from=NOW.date(), valid_until=NOW.date(),
                        rules=[{"zone": "baseline"}], base_rate=8.44, verified=True, source="test"),
        Decision(managed_job_id=job.id, site_id=site.id, created_at=NOW, start_at=NOW, mode="shadow"),
        AuditLog(site_id=site.id, at=NOW, actor="planner", event="decision", ref="1", detail={"a": 1}),
    ])
    session.flush()
    assert session.query(AuditLog).one().detail == {"a": 1}
