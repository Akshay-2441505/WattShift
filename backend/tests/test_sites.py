from datetime import datetime, timedelta, timezone

import pytest

from app import audit, sites
from app.models import AuditLog, SiteKey

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def test_create_site_returns_a_key_that_finds_it(session):
    site, key = sites.create_site(session, "Acme", "Pune-1", gpus=64)
    assert key.startswith("wsk_") and len(key) > 30
    assert sites.site_for_key(session, key).id == site.id
    assert site.mode == "shadow" and site.gpus == 64


def test_only_the_hash_is_stored(session):
    _, key = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    stored = session.query(SiteKey).one().key_hash
    assert stored != key and key not in stored and len(stored) == 64


def test_unknown_or_empty_key_finds_nothing(session):
    sites.create_site(session, "Acme", "Pune-1", gpus=8)
    assert sites.site_for_key(session, "wsk_nope") is None
    assert sites.site_for_key(session, "") is None


def test_revoked_key_stops_working_and_a_new_one_works(session):
    site, old = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    sites.revoke_keys(session, site, NOW)
    assert sites.site_for_key(session, old) is None
    assert sites.site_for_key(session, sites.issue_key(session, site)).id == site.id


def test_second_site_reuses_the_company(session):
    a, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    b, _ = sites.create_site(session, "Acme", "Nagpur-1", gpus=8)
    assert a.company_id == b.company_id


def test_set_mode_and_release_all_are_audited(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    sites.set_mode(session, site, "autonomous", NOW)
    sites.set_release_all(session, site, True, NOW)
    assert site.mode == "autonomous" and site.release_all is True
    events = [(a.actor, a.event) for a in session.query(AuditLog).order_by(AuditLog.id)]
    assert events == [("operator", "mode_changed"), ("operator", "release_all_on")]


def test_bad_mode_is_rejected(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    with pytest.raises(ValueError):
        sites.set_mode(session, site, "yolo", NOW)


def test_log_throttled_skips_a_repeat_inside_the_window(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    audit.log_throttled(session, site.id, NOW, "planner", "no_tariff")
    audit.log_throttled(session, site.id, NOW + timedelta(minutes=30), "planner", "no_tariff")
    audit.log_throttled(session, site.id, NOW + timedelta(minutes=61), "planner", "no_tariff")
    assert session.query(AuditLog).count() == 2
