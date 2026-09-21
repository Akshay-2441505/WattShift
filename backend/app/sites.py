"""Onboarding and access for customer sites: companies, sites, per-site keys, and the two operator switches."""
import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.models import Company, Site, SiteKey

KEY_PREFIX = "wsk_"
MODES = ("shadow", "autonomous")


def hash_key(key: str) -> str:
    # Keys are 256-bit random tokens, so a plain SHA-256 is enough (no salt or slow hash needed).
    return hashlib.sha256(key.encode()).hexdigest()


def issue_key(session: Session, site: Site) -> str:
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    session.add(SiteKey(site_id=site.id, key_hash=hash_key(key)))
    session.flush()
    return key


def revoke_keys(session: Session, site: Site, now: datetime) -> None:
    session.execute(update(SiteKey).where(SiteKey.site_id == site.id, SiteKey.revoked_at.is_(None)).values(revoked_at=now))


def site_for_key(session: Session, key: str) -> Site | None:
    if not key:
        return None
    return session.scalar(
        select(Site).join(SiteKey, SiteKey.site_id == Site.id).where(SiteKey.key_hash == hash_key(key), SiteKey.revoked_at.is_(None))
    )


def create_site(
    session: Session, company_name: str, site_name: str, *, gpus: int, power_limit_kw: float | None = None,
    kw_per_gpu: float = 1.25, shift_capacity_share: float = 0.5, utility: str = "MSEDCL",
    tariff_category: str = "HT-I(A)", mode: str = "shadow",
) -> tuple[Site, str]:
    """Create (or reuse) the company, add the site, issue its first key. The plaintext key is returned once."""
    company = session.scalar(select(Company).where(Company.name == company_name))
    if company is None:
        company = Company(name=company_name)
        session.add(company)
        session.flush()
    site = Site(
        company_id=company.id, name=site_name, gpus=gpus, power_limit_kw=power_limit_kw, kw_per_gpu=kw_per_gpu,
        shift_capacity_share=shift_capacity_share, utility=utility, tariff_category=tariff_category, mode=mode,
    )
    session.add(site)
    session.flush()
    return site, issue_key(session, site)


def set_mode(session: Session, site: Site, mode: str, now: datetime, actor: str = "operator") -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if site.mode != mode:
        audit.log(session, site.id, now, actor, "mode_changed", previous=site.mode, mode=mode)
        site.mode = mode
        session.flush()


def set_release_all(session: Session, site: Site, on: bool, now: datetime, actor: str = "operator") -> None:
    """The kill switch: while on, the cloud plans nothing and tells the agent to release every job it deferred."""
    if site.release_all != on:
        audit.log(session, site.id, now, actor, "release_all_on" if on else "release_all_off")
        site.release_all = on
        session.flush()
