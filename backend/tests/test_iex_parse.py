from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.ingest_iex import IST, parse_market_snapshot, upsert_prices
from app.models import PriceSignal

# Mirrors the real page: the date cell appears only on a day's first row; the hour cell only on each
# hour's first row; the MCP is always the last cell.
def _row(cells):
    return "<tr>" + "".join(f'<td class="MuiTableCell-root">{c}</td>' for c in cells) + "</tr>"


def _day_html(date, mcps):
    rows = []
    for i, mcp in enumerate(mcps):
        h, q = divmod(i, 4)
        block = f"{h:02d}:{q*15:02d} - {(h*60+q*15+15)//60 % 24:02d}:{(q*15+15) % 60:02d}"
        cells = [block, "1.0", "1.0", "1.0", "1.0", f"{mcp:.2f}"]
        if q == 0:
            cells.insert(0, str(h + 1))
        if i == 0:
            cells.insert(0, date)
        rows.append(_row(cells))
    return "<table><tr><th>Date</th><th>MCP (Rs/MWh) *</th></tr>" + "".join(rows) + "</table>"


def test_parses_96_blocks_with_ist_timestamps():
    html = _day_html("18-09-2026", [1000 + i for i in range(96)])
    out = parse_market_snapshot(html)
    assert len(out) == 96
    assert out[0] == (datetime(2026, 9, 18, 0, 0, tzinfo=IST), 1000.0)
    assert out[95] == (datetime(2026, 9, 18, 23, 45, tzinfo=IST), 1095.0)
    assert out[1][0] - out[0][0] == timedelta(minutes=15)


def test_parses_multiple_days_in_one_page():
    html = _day_html("18-09-2026", [5.0] * 96).replace("</table>", "") + _day_html("19-09-2026", [7.0] * 96).split("</tr>", 1)[1]
    out = parse_market_snapshot(html)
    assert len(out) == 192
    assert out[96][0] == datetime(2026, 9, 19, 0, 0, tzinfo=IST)
    assert out[96][1] == 7.0


def test_real_page_fixture():
    """Regression test against the real IEX markup captured 2026-09-18."""
    html = (Path(__file__).parent / "fixtures" / "iex_snapshot_2026-09-18.html").read_text(encoding="utf-8")
    out = parse_market_snapshot(html)
    assert len(out) == 96
    assert out[0] == (datetime(2026, 9, 18, 0, 0, tzinfo=IST), 10000.0)
    assert min(p for _, p in out) == 2609.54
    assert len({ts for ts, _ in out}) == 96  # no duplicate blocks


def test_upsert_is_idempotent_and_updates_in_place(session):
    rows = [(datetime(2026, 9, 18, 0, 0, tzinfo=IST) + timedelta(minutes=15 * i), 1000.0 + i) for i in range(96)]
    upsert_prices(session, rows)
    upsert_prices(session, rows)
    assert session.query(PriceSignal).count() == 96
    upsert_prices(session, [(rows[0][0], 42.0)])
    assert session.query(PriceSignal).count() == 96
    assert float(session.query(PriceSignal).filter_by(ts=rows[0][0]).one().price_rs_per_mwh) == 42.0


def _fake_get(monkeypatch, html):
    seen = {}

    class R:
        text = html

        def raise_for_status(self):
            pass

    def get(url, params=None, **kw):
        seen.update(params)
        return R()

    monkeypatch.setattr("app.ingest_iex.httpx.get", get)
    return seen


def test_fetch_dam_date_asks_for_exactly_that_day(monkeypatch):
    from datetime import date

    from app.ingest_iex import fetch_dam_date

    seen = _fake_get(monkeypatch, _day_html("17-08-2026", [1.0] * 96))
    rows = fetch_dam_date(date(2026, 8, 17))
    assert seen["dp"] == "SELECT_RANGE" and seen["fromDate"] == seen["toDate"] == "17-08-2026"
    assert len(rows) == 96 and rows[0][0] == datetime(2026, 8, 17, 0, 0, tzinfo=IST)


def test_fetch_dam_date_refuses_a_page_for_a_different_day(monkeypatch):
    """If IEX falls back to another day, a report must not silently mix dates."""
    from datetime import date

    from app.ingest_iex import fetch_dam_date

    _fake_get(monkeypatch, _day_html("18-09-2026", [1.0] * 96))
    with pytest.raises(ValueError, match="17-08-2026|2026-08-17"):
        fetch_dam_date(date(2026, 8, 17))


def test_rejects_page_without_prices():
    with pytest.raises(ValueError):
        parse_market_snapshot("<html><body>no table here</body></html>")
