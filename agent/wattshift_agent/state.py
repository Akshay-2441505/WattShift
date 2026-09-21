"""The agent's local memory (SQLite): what it has seen and applied. Needed to detect a user's own change to a job and
to retry reports the cloud has not acknowledged. Losing the file is safe: jobs still start when Slurm says."""
import sqlite3
from dataclasses import asdict, dataclass, fields

SCHEMA = """
create table if not exists jobs (
  ref text primary key, first_seen integer not null, max_wait_min integer not null, state text not null,
  submit integer, gpus integer, time_limit_min integer, predicted_start integer, skipped_reason text,
  applied_start integer, released integer not null default 0, override integer not null default 0,
  start_time integer, end_time integer, final_sent integer not null default 0
);
create table if not exists outbox_applied (ref text primary key, start_at integer not null, ok integer not null, error text);
create table if not exists outbox_released (ref text primary key);
create table if not exists meta (key text primary key, value text);
"""
_BOOLS = ("released", "override", "final_sent")


@dataclass
class Tracked:
    ref: str
    first_seen: int
    max_wait_min: int
    state: str  # PENDING | RUNNING | COMPLETED | FAILED | CANCELLED | TIMEOUT | OTHER
    submit: int | None = None
    gpus: int | None = None  # captured at first sight; sacct may not have it later
    time_limit_min: int | None = None
    predicted_start: int | None = None
    skipped_reason: str | None = None  # array | dependency | requeue: reported, never planned
    applied_start: int | None = None  # the start time WE set (the basis for detecting a user's change)
    released: bool = False
    override: bool = False  # the owner changed, held or cancelled it after we deferred it
    start_time: int | None = None
    end_time: int | None = None
    final_sent: bool = False


_COLS = [f.name for f in fields(Tracked)]


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    @staticmethod
    def _tracked(row) -> Tracked:
        d = dict(row)
        for b in _BOOLS:
            d[b] = bool(d[b])
        return Tracked(**d)

    def get(self, ref: str) -> Tracked | None:
        row = self.db.execute("select * from jobs where ref = ?", (ref,)).fetchone()
        return self._tracked(row) if row else None

    def save(self, t: Tracked) -> None:
        d = asdict(t)
        vals = [int(d[c]) if isinstance(d[c], bool) else d[c] for c in _COLS]
        self.db.execute(f"insert or replace into jobs ({','.join(_COLS)}) values ({','.join('?' * len(_COLS))})", vals)
        self.db.commit()

    def active(self) -> list[Tracked]:
        return [self._tracked(r) for r in self.db.execute("select * from jobs where final_sent = 0 order by ref")]

    def mark_sent(self, refs) -> None:
        self.db.executemany("update jobs set final_sent = 1 where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def put_applied(self, ref: str, start_at: int, ok: bool, error: str | None) -> None:
        self.db.execute("insert or replace into outbox_applied values (?, ?, ?, ?)", (ref, start_at, int(ok), error))
        self.db.commit()

    def applied_outbox(self) -> list[tuple]:
        return [(r["ref"], r["start_at"], bool(r["ok"]), r["error"]) for r in self.db.execute("select * from outbox_applied order by ref")]

    def clear_applied(self, refs) -> None:
        self.db.executemany("delete from outbox_applied where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def add_released(self, ref: str) -> None:
        self.db.execute("insert or ignore into outbox_released values (?)", (ref,))
        self.db.commit()

    def released_outbox(self) -> list[str]:
        return [r["ref"] for r in self.db.execute("select ref from outbox_released order by ref")]

    def clear_released(self, refs) -> None:
        self.db.executemany("delete from outbox_released where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def get_meta(self, key: str, default=None):
        row = self.db.execute("select value from meta where key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("insert or replace into meta values (?, ?)", (key, value))
        self.db.commit()

    def prune(self, older_than: int) -> None:
        """Forget jobs whose final report was sent before `older_than` (epoch seconds)."""
        self.db.execute("delete from jobs where final_sent = 1 and coalesce(end_time, first_seen) < ?", (older_than,))
        self.db.commit()
