"""Create a company and site and print its key ONCE.  Usage:
python -m scripts.onboard_site --company Acme --site Pune-1 --gpus 64 [--power-limit-kw 400] [--kw-per-gpu 1.25] [--mode shadow]"""
import argparse

from app import db, models, sites  # noqa: F401  (models registers every table on db.Base before create_all)
from app.config import settings


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--company", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--gpus", type=int, required=True)
    p.add_argument("--power-limit-kw", type=float)
    p.add_argument("--kw-per-gpu", type=float, default=1.25)
    p.add_argument("--mode", choices=sites.MODES, default="shadow")
    a = p.parse_args()
    assert settings.database_url, "DATABASE_URL not set (see ~/.wattshift/.env)"
    engine = db.make_engine(settings.database_url)
    db.Base.metadata.create_all(engine)
    with db.make_session_factory(engine)() as s, s.begin():
        site, key = sites.create_site(
            s, a.company, a.site, gpus=a.gpus, power_limit_kw=a.power_limit_kw, kw_per_gpu=a.kw_per_gpu, mode=a.mode
        )
        print(f"site {site.id} created in {site.mode} mode.")
        print(f"agent key (shown once, store it in the agent config): {key}")


if __name__ == "__main__":
    main()
