"""Conservative per-host request scheduling shared by local worker processes.

These intervals are this platform's policy, not published source quotas or a
hard per-IP limit. A reservation only schedules a caller; this module performs
no HTTP requests. Clients must reserve immediately before their own request.
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

# Exact configured official hosts only; never wildcard third-party destinations.
INTERVALS = {
    'clinicaltrials.gov': 3.0,
    'www.nasdaqtrader.com': 2.0,
    'www.bse.cn': 0.5,
    'www.sec.gov': 1.0,
    'www.hkex.com.hk': 1.0,
    'www.szse.cn': 1.0,
    'query.sse.com.cn': 1.0,
    'www.hengrui.com': 1.0,
    'www.pfizer.com': 1.0,
    'investor.innoventbio.com': 1.0,
}
MAX_WAIT = 60.0
POLL_SECONDS = 0.1
DATABASE_NAME = 'source_rate.sqlite3'


def _source(url):
    if not isinstance(url, str) or not url or len(url) > 12000 or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        raise ValueError('Source rate URL must be a bounded HTTPS URL')
    parsed = urlsplit(url)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname in INTERVALS
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Source rate URL is outside the fixed official-host allowlist')
    return parsed.hostname, INTERVALS[parsed.hostname]


def _state_path(state_dir):
    root = Path(state_dir).expanduser().resolve()
    if root.is_relative_to('/Volumes'):
        raise ValueError('Source rate state must be on local disk, never /Volumes')
    root.mkdir(parents=True, exist_ok=True)
    path = root / DATABASE_NAME
    # SQLite may create sibling journal files. Do not follow a database-file
    # symlink into a different volume or directory even when state_dir is local.
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Source rate database must remain inside its local state directory')
    return path


def _timestamp(clock):
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Source rate clock must return a finite nonnegative timestamp')
    return float(value)


def wait_slot(state_dir, url, cancelled=lambda: False, *, clock=time.time, sleep=time.sleep):
    """Reserve a shared host slot, wait cancellably, and return None.

 ``clock`` and ``sleep`` are injectable for deterministic fast tests; production
 callers use Unix time so independently started processes share reservations.
 The SQLite write transaction ends before waiting. A cancelled reservation stays
 consumed, since reclaiming it could overlap reservations made by other callers.
 Debt over 60 seconds fails closed without extending the debt. Persisted invalid
 clock/debt values also fail closed; no requests or unthrottled fallback occur.
    """
    host, interval = _source(url)
    if cancelled():
        raise InterruptedError('Source request cancelled before slot reservation')
    path = _state_path(state_dir)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS source_request_slots (
          host TEXT PRIMARY KEY, next_at REAL NOT NULL, last_reserved REAL NOT NULL,
          interval_seconds REAL NOT NULL)''')
        db.execute('BEGIN IMMEDIATE')
        if cancelled():
            raise InterruptedError('Source request cancelled before slot reservation')
        current = _timestamp(clock)
        row = db.execute('SELECT next_at FROM source_request_slots WHERE host=?', (host,)).fetchone()
        next_at = row[0] if row else current
        if isinstance(next_at, bool) or not isinstance(next_at, (int, float)) or not math.isfinite(next_at) or next_at < 0:
            raise ValueError('Source rate persisted debt is invalid; reservation rejected')
        slot = max(current, next_at)
        if slot - current > MAX_WAIT:
            raise TimeoutError('Source rate debt exceeds 60 seconds; reservation rejected')
        db.execute('''INSERT INTO source_request_slots VALUES (?,?,?,?)
          ON CONFLICT(host) DO UPDATE SET next_at=excluded.next_at,
            last_reserved=excluded.last_reserved,interval_seconds=excluded.interval_seconds''',
                   (host, slot + interval, slot, interval))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    requested_wait, previous = 0.0, current
    while True:
        if cancelled():
            raise InterruptedError('Source request cancelled while waiting for its reserved slot')
        current = _timestamp(clock)
        if current < previous:
            raise TimeoutError('Source rate clock moved backwards; request remains blocked')
        previous = current
        remaining = slot - current
        # Ignore sub-microsecond floating-point residue at an exact boundary.
        if remaining <= 1e-6:
            return None
        # A moved/frozen test or system clock must never create an unbounded
        # wait. Sleeping outside SQLite leaves other hosts/processes unblocked.
        if remaining > MAX_WAIT or requested_wait >= MAX_WAIT:
            raise TimeoutError('Source rate wait exceeded 60 seconds; request remains blocked')
        step = min(POLL_SECONDS, remaining, MAX_WAIT - requested_wait)
        sleep(step)
        requested_wait += step
