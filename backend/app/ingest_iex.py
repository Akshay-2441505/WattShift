"""IEX day-ahead market (DAM) prices. IEX has no documented API, but its market-snapshot page is
server-rendered from query params, so a plain GET returns the full 15-min-block table."""
import re
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PriceSignal
from app.tariff import IST

URL = "https://www.iexindia.com/market-data/day-ahead-market/market-snapshot"
TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
DATE = re.compile(r"^(\d\d)-(\d\d)-(\d{4})$")
BLOCK = re.compile(r"^(\d\d):(\d\d) - \d\d:\d\d$")


def parse_market_snapshot(html: str) -> list[tuple[datetime, float]]:
    """[(block_start_ist, mcp_rs_per_mwh)] in page order. The date cell appears only on a day's first row."""
    out, day = [], None
    for tr in TR.findall(html):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in TD.findall(tr)]
        for c in cells:
            if m := DATE.match(c):
                day = datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=IST)
        block = next((m for c in cells if (m := BLOCK.match(c))), None)
        if block and day and cells:
            out.append((day + timedelta(hours=int(block[1]), minutes=int(block[2])), float(cells[-1])))
    if not out:
        raise ValueError("no IEX price rows found (not published yet, or the page layout changed)")
    return out


def fetch_dam(dp: str = "TODAY") -> list[tuple[datetime, float]]:
    """dp: YESTERDAY | TODAY | TOMORROW. Tomorrow's prices appear once IEX publishes them (~midday)."""
    r = httpx.get(
        URL,
        params={"interval": "ONE_FOURTH_HOUR", "dp": dp, "showGraph": "false", "toDate": "1", "fromDate": "1"},
        headers={"User-Agent": "Mozilla/5.0 (Wattshift research)"},
        timeout=30,
        follow_redirects=True,
    )
    r.raise_for_status()
    return parse_market_snapshot(r.text)


def fetch_dam_date(day: date) -> list[tuple[datetime, float]]:
    """One specific past (or published) day, for building price history. IEX's date-range view only returns the
    first day of a range, so ask for a single day at a time."""
    d = day.strftime("%d-%m-%Y")
    r = httpx.get(
        URL,
        params={"interval": "ONE_FOURTH_HOUR", "dp": "SELECT_RANGE", "showGraph": "false", "fromDate": d, "toDate": d},
        headers={"User-Agent": "Mozilla/5.0 (Wattshift research)"},
        timeout=60,
        follow_redirects=True,
    )
    r.raise_for_status()
    rows = parse_market_snapshot(r.text)
    if rows[0][0].date() != day:
        raise ValueError(f"asked IEX for {day.isoformat()} but the page shows {rows[0][0].date().isoformat()}")
    return rows


def ingest(session: Session, days: tuple[str, ...] = ("TODAY", "TOMORROW")) -> int:
    """Daily job. TOMORROW may not be published yet; that is not an error, the next run picks it up."""
    n = 0
    for dp in days:
        try:
            n += upsert_prices(session, fetch_dam(dp))
        except (ValueError, httpx.HTTPError) as e:
            print(f"ingest {dp}: skipped ({type(e).__name__}: {str(e)[:100]})")
    return n


def upsert_prices(session: Session, rows: list[tuple[datetime, float]], source: str = "iex_dam") -> int:
    """Idempotent: re-ingesting the same day updates prices in place."""
    stmt = insert(PriceSignal).values(
        [{"ts": ts, "price_rs_per_mwh": p, "source": source} for ts, p in rows]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["ts", "source"], set_={"price_rs_per_mwh": stmt.excluded.price_rs_per_mwh}
        )
    )
    return len(rows)
