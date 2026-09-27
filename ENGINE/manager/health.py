"""
ENGINE/manager/health.py — AI provider intelligence brain.

Tracks rich per-provider signals across rolling time windows and produces
multi-dimensional scores that explain *why* a provider ranks where it does.

Dimensions:
    reliability   — success rate, latency percentiles, error code patterns,
                    consecutive failure streaks
    availability  — quality breadth (how many distinct quality options returned),
                    stream type (m3u8 > mp4 > iframe), subtitle presence, TTL health
    context_fit   — per content-category hit rates (anime, asian, bollywood,
                    movie, tv) so a provider that specialises in anime ranks
                    higher for anime requests even if overall rate is average

Rolling windows (1 h / 24 h / 7 d):
    Recent performance matters more than historical totals. Each record()
    call appends a lightweight event to a deque capped at MAX_EVENTS. Window
    statistics are computed on demand from those events, no background task
    needed.

Circuit breaker (unchanged behaviour, same thresholds):
    N consecutive failures → skip provider for COOLDOWN seconds.
    After cooldown: one half-open probe allowed through.

Persists to disk so stats survive restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass, asdict, field
from typing import Literal, Optional

from config import get_settings

_s = get_settings()

Outcome = Literal["found", "empty", "failed"]
ContentCategory = Literal["anime", "asian", "bollywood", "movie", "tv", "unknown"]

# How many raw events we keep per provider (covers ~7 days at 1 req/min)
MAX_EVENTS = 10_000

# Rolling window sizes in seconds
WIN_1H  = 3_600
WIN_24H = 86_400
WIN_7D  = 604_800


# ── Raw event ─────────────────────────────────────────────────────────────────

@dataclass
class _Event:
    ts: float               # monotonic-ish (wall clock, good enough for windows)
    outcome: Outcome
    ms: float
    http_status: int        # 0 = unknown / timeout
    quality_count: int      # distinct quality options returned
    has_m3u8: bool
    has_mp4: bool
    has_iframe: bool
    has_subtitles: bool
    ttl_seconds: int        # shortest TTL in the result batch (0 = unknown)
    category: ContentCategory


# ── Persisted state ──────────────────────────────────────────────────────────

@dataclass
class _Stat:
    # Circuit breaker (unchanged)
    consecutive_failures: int = 0
    circuit_until: float = 0.0
    last: Optional[str] = None

    # Lifetime totals (legacy — kept for backward compat with /health/providers)
    success: int = 0
    failure: int = 0
    empty: int = 0
    total_ms: float = 0.0
    runs: int = 0

    # Rolling event log — stored as list[dict] for JSON serialisation
    events: list = field(default_factory=list)   # list of _Event as dicts


# ── Window helpers ────────────────────────────────────────────────────────────

def _window(events: list[_Event], seconds: int) -> list[_Event]:
    cutoff = time.time() - seconds
    return [e for e in events if e.ts >= cutoff]


def _reliability(events: list[_Event]) -> dict:
    """Success rate, latency percentiles, error code breakdown."""
    if not events:
        return {
            "success_rate": 1.0,
            "avg_ms": 0,
            "p95_ms": 0,
            "error_rate_4xx": 0.0,
            "error_rate_5xx": 0.0,
            "sample": 0,
        }
    total = len(events)
    found = [e for e in events if e.outcome == "found"]
    success_rate = round(len(found) / total, 3)
    latencies = sorted(e.ms for e in found) if found else [0]
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    avg_ms = round(sum(latencies) / len(latencies))
    p95_ms = round(latencies[p95_idx])
    errors = [e for e in events if e.http_status >= 400]
    err_4xx = round(len([e for e in errors if 400 <= e.http_status < 500]) / total, 3)
    err_5xx = round(len([e for e in errors if e.http_status >= 500]) / total, 3)
    return {
        "success_rate": success_rate,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "error_rate_4xx": err_4xx,
        "error_rate_5xx": err_5xx,
        "sample": total,
    }


def _availability(events: list[_Event]) -> dict:
    """Quality breadth, stream type mix, subtitle rate, TTL health."""
    if not events:
        return {
            "avg_quality_count": 0.0,
            "max_quality_count": 0,
            "m3u8_rate": 0.0,
            "mp4_rate": 0.0,
            "iframe_rate": 0.0,
            "subtitle_rate": 0.0,
            "avg_ttl_seconds": 0,
            "sample": 0,
        }
    found = [e for e in events if e.outcome == "found"]
    if not found:
        return {
            "avg_quality_count": 0.0,
            "max_quality_count": 0,
            "m3u8_rate": 0.0,
            "mp4_rate": 0.0,
            "iframe_rate": 0.0,
            "subtitle_rate": 0.0,
            "avg_ttl_seconds": 0,
            "sample": len(events),
        }
    n = len(found)
    return {
        "avg_quality_count": round(sum(e.quality_count for e in found) / n, 2),
        "max_quality_count": max(e.quality_count for e in found),
        "m3u8_rate": round(sum(1 for e in found if e.has_m3u8) / n, 3),
        "mp4_rate":  round(sum(1 for e in found if e.has_mp4)  / n, 3),
        "iframe_rate": round(sum(1 for e in found if e.has_iframe) / n, 3),
        "subtitle_rate": round(sum(1 for e in found if e.has_subtitles) / n, 3),
        "avg_ttl_seconds": round(sum(e.ttl_seconds for e in found if e.ttl_seconds > 0) /
                                  max(1, sum(1 for e in found if e.ttl_seconds > 0))),
        "sample": n,
    }


def _context_fit(events: list[_Event]) -> dict:
    """Per-category hit rates. Reveals specialisation vs general capability."""
    cats: dict[str, dict] = {}
    for cat in ("anime", "asian", "bollywood", "movie", "tv"):
        cat_events = [e for e in events if e.category == cat]
        if not cat_events:
            continue
        found_count = sum(1 for e in cat_events if e.outcome == "found")
        cats[cat] = {
            "success_rate": round(found_count / len(cat_events), 3),
            "sample": len(cat_events),
        }
    return cats


def _score_provider(
    events_1h: list[_Event],
    events_24h: list[_Event],
    category: Optional[ContentCategory],
) -> dict:
    """
    Compute composite score (0–100) and per-dimension breakdown.

    Reliability  (40 pts max)
        success_rate × 28
        + latency bonus: 12 pts scaled (0 ms = 12, 5000 ms = 0)
        − 4xx penalty × 6
        − 5xx penalty × 8

    Availability (40 pts max)
        quality_count score: min(count, 5) / 5 × 20
        + m3u8_rate × 10
        + subtitle_rate × 6
        + ttl bonus: min(ttl, 3600) / 3600 × 4

    Context fit (20 pts max)
        If category is known and provider has ≥3 samples for it:
            category_success_rate × 20
        Else:
            overall success_rate × 16  (penalised — unknown fit)
    """
    rel = _reliability(events_24h)
    avail = _availability(events_24h)
    ctx = _context_fit(events_24h)

    # Reliability
    latency_bonus = max(0.0, 12.0 - (rel["avg_ms"] / 5000) * 12) if rel["avg_ms"] else 12.0
    r_score = (
        rel["success_rate"] * 28
        + latency_bonus
        - rel["error_rate_4xx"] * 6
        - rel["error_rate_5xx"] * 8
    )

    # Availability
    quality_score = min(avail["avg_quality_count"], 5) / 5 * 20
    a_score = (
        quality_score
        + avail["m3u8_rate"] * 10
        + avail["subtitle_rate"] * 6
        + min(avail["avg_ttl_seconds"], 3600) / 3600 * 4
    )

    # Context fit
    cat_key = str(category) if category else None
    cat_data = ctx.get(cat_key, {}) if cat_key else {}
    if cat_data and cat_data.get("sample", 0) >= 3:
        c_score = cat_data["success_rate"] * 20
    else:
        c_score = rel["success_rate"] * 16  # penalised unknown

    composite = round(min(100.0, r_score + a_score + c_score), 1)

    return {
        "composite": composite,
        "reliability": round(min(40.0, r_score), 1),
        "availability": round(min(40.0, a_score), 1),
        "context_fit": round(min(20.0, c_score), 1),
    }


def _explain(
    pid: str,
    name: str,
    score: dict,
    rel: dict,
    avail: dict,
    ctx: dict,
    category: Optional[ContentCategory],
    circuit_broken: bool,
) -> str:
    """
    Return a human-readable one-liner explaining why a provider ranked where it did.
    This is the 'BMW vs Mercedes' reasoning layer.
    """
    parts: list[str] = []

    if circuit_broken:
        return f"{name} ({pid}): circuit open — skipped until recovered"

    # Headline score
    parts.append(f"score {score['composite']}/100")

    # Reliability highlight
    sr = rel.get("success_rate", 0)
    if sr >= 0.95:
        parts.append(f"very reliable ({sr*100:.0f}%)")
    elif sr >= 0.75:
        parts.append(f"reliable ({sr*100:.0f}%)")
    elif sr < 0.5:
        parts.append(f"unreliable ({sr*100:.0f}% success)")

    # Latency
    avg = rel.get("avg_ms", 0)
    if avg and avg < 1500:
        parts.append(f"fast ({avg} ms avg)")
    elif avg and avg > 4000:
        parts.append(f"slow ({avg} ms avg)")

    # Availability highlight
    qc = avail.get("avg_quality_count", 0)
    if qc >= 3:
        parts.append(f"{qc:.1f} qualities avg")
    elif qc == 1:
        parts.append("single quality only")

    if avail.get("m3u8_rate", 0) >= 0.9:
        parts.append("HLS streams")
    elif avail.get("iframe_rate", 0) >= 0.9:
        parts.append("iframe only")

    if avail.get("subtitle_rate", 0) >= 0.5:
        parts.append("subtitles available")

    # Context fit
    cat_key = str(category) if category else None
    cat_data = ctx.get(cat_key, {}) if cat_key else {}
    if cat_data and cat_data.get("sample", 0) >= 3:
        cat_sr = cat_data["success_rate"]
        if cat_sr >= 0.9:
            parts.append(f"excellent for {cat_key} ({cat_sr*100:.0f}%)")
        elif cat_sr < 0.5:
            parts.append(f"poor for {cat_key} ({cat_sr*100:.0f}%)")

    # Error flags
    if rel.get("error_rate_4xx", 0) >= 0.2:
        parts.append("high auth errors (4xx)")
    if rel.get("error_rate_5xx", 0) >= 0.2:
        parts.append("high server errors (5xx)")

    return f"{name} ({pid}): " + ", ".join(parts)


# ── Main tracker ──────────────────────────────────────────────────────────────

class HealthTracker:

    def __init__(self) -> None:
        self._stats: dict[str, _Stat] = {}
        self._events: dict[str, deque[_Event]] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(_s.provider_stats_file, "r") as f:
                raw = json.load(f)
            for pid, d in raw.items():
                # Load _Stat fields (drop unknown keys for forward compat)
                stat_fields = {k: v for k, v in d.items()
                               if k in _Stat.__dataclass_fields__ and k != "events"}
                self._stats[pid] = _Stat(**stat_fields)
                # Reset circuit_until values from old monotonic-epoch saves.
                # Valid wall-clock values are always > (now - 1 h); anything
                # smaller is a stale monotonic offset from a previous process.
                s = self._stats[pid]
                if s.circuit_until > 0 and s.circuit_until < (time.time() - 3600):
                    s.circuit_until = 0.0
                    s.consecutive_failures = 0
                # Rebuild event deque
                dq: deque[_Event] = deque(maxlen=MAX_EVENTS)
                for ev in d.get("events", []):
                    try:
                        dq.append(_Event(**{k: v for k, v in ev.items()
                                            if k in _Event.__dataclass_fields__}))
                    except Exception:
                        pass
                self._events[pid] = dq
        except Exception:
            pass

    async def _save(self) -> None:
        async with self._lock:
            data: dict = {}
            for pid, stat in self._stats.items():
                d = asdict(stat)
                d.pop("events", None)
                d["events"] = [asdict(e) for e in self._events.get(pid, deque())]
                data[pid] = d
        try:
            tmp = _s.provider_stats_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _s.provider_stats_file)
        except Exception:
            pass

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _stat(self, pid: str) -> _Stat:
        if pid not in self._stats:
            self._stats[pid] = _Stat()
        return self._stats[pid]

    def _evq(self, pid: str) -> deque[_Event]:
        if pid not in self._events:
            self._events[pid] = deque(maxlen=MAX_EVENTS)
        return self._events[pid]

    # ── Public API ────────────────────────────────────────────────────────────

    async def record(
        self,
        pid: str,
        outcome: Outcome,
        ms: float,
        *,
        http_status: int = 0,
        quality_count: int = 0,
        has_m3u8: bool = False,
        has_mp4: bool = False,
        has_iframe: bool = False,
        has_subtitles: bool = False,
        ttl_seconds: int = 0,
        category: ContentCategory = "unknown",
    ) -> None:
        async with self._lock:
            s = self._stat(pid)
            s.last = outcome

            # Circuit breaker counters
            if outcome == "found":
                s.success += 1
                s.consecutive_failures = 0
                s.circuit_until = 0.0
                s.total_ms += ms
                s.runs += 1
            elif outcome == "failed":
                s.failure += 1
                s.consecutive_failures += 1
                if s.consecutive_failures >= _s.cb_fail_threshold:
                    s.circuit_until = time.time() + _s.cb_cooldown_seconds
            else:
                s.empty += 1
                s.consecutive_failures = 0

            # Append rich event
            self._evq(pid).append(_Event(
                ts=time.time(),
                outcome=outcome,
                ms=ms,
                http_status=http_status,
                quality_count=quality_count,
                has_m3u8=has_m3u8,
                has_mp4=has_mp4,
                has_iframe=has_iframe,
                has_subtitles=has_subtitles,
                ttl_seconds=ttl_seconds,
                category=category,
            ))

        asyncio.ensure_future(self._save())

    async def should_run(self, pid: str) -> bool:
        async with self._lock:
            s = self._stats.get(pid)
            if not s or s.circuit_until == 0.0:
                return True
            now = time.time()
            if now < s.circuit_until:
                return False
            # half-open probe
            s.circuit_until = now + _s.cb_cooldown_seconds
            return True

    async def get_stats(self, pid: str) -> dict:
        """Legacy-compatible stats dict + new rich fields."""
        async with self._lock:
            s = self._stats.get(pid)
            evq = list(self._events.get(pid, deque()))

        if not s:
            return {
                "circuit_broken": False,
                "success_rate": 1.0,
                "avg_ms": 0,
                "success": 0,
                "failure": 0,
                "last": None,
                "windows": {},
                "score": {},
                "explanation": "No data yet",
            }

        circuit_broken = s.circuit_until > time.time()
        total = s.success + s.failure
        events_1h  = _window(evq, WIN_1H)
        events_24h = _window(evq, WIN_24H)
        events_7d  = _window(evq, WIN_7D)

        score = _score_provider(events_1h, events_24h, None)
        rel   = _reliability(events_24h)
        avail = _availability(events_24h)
        ctx   = _context_fit(events_24h)

        return {
            # Legacy fields
            "circuit_broken": circuit_broken,
            "success_rate": round(s.success / total, 2) if total else 1.0,
            "avg_ms": round(s.total_ms / s.runs) if s.runs else 0,
            "success": s.success,
            "failure": s.failure,
            "consecutive_failures": s.consecutive_failures,
            "last": s.last,
            # New fields
            "windows": {
                "1h":  {"reliability": _reliability(events_1h),  "availability": _availability(events_1h)},
                "24h": {"reliability": rel,                        "availability": avail},
                "7d":  {"reliability": _reliability(events_7d),  "availability": _availability(events_7d)},
            },
            "context_fit": ctx,
            "score": score,
        }

    async def get_insights(
        self,
        pid: str,
        name: str,
        category: Optional[ContentCategory] = None,
    ) -> dict:
        """Full intelligence report for one provider including explanation."""
        async with self._lock:
            s = self._stats.get(pid)
            evq = list(self._events.get(pid, deque()))

        if not s:
            return {
                "id": pid,
                "name": name,
                "score": {"composite": 50.0, "reliability": 20.0, "availability": 20.0, "context_fit": 10.0},
                "explanation": f"{name} ({pid}): No data yet — using neutral defaults",
                "anomalies": [],
            }

        circuit_broken = s.circuit_until > time.time()
        events_1h  = _window(evq, WIN_1H)
        events_24h = _window(evq, WIN_24H)
        events_7d  = _window(evq, WIN_7D)

        score = _score_provider(events_1h, events_24h, category)
        rel_1h  = _reliability(events_1h)
        rel_24h = _reliability(events_24h)
        rel_7d  = _reliability(events_7d)
        avail   = _availability(events_24h)
        ctx     = _context_fit(events_24h)

        explanation = _explain(pid, name, score, rel_24h, avail, ctx, category, circuit_broken)

        # Anomaly detection: compare 1h window vs 7d baseline
        anomalies: list[str] = []
        if circuit_broken:
            anomalies.append("Circuit breaker open — provider skipped until probe succeeds")
        if rel_7d["sample"] >= 10:
            # Latency regression
            if rel_1h["avg_ms"] and rel_7d["avg_ms"]:
                ratio = rel_1h["avg_ms"] / max(rel_7d["avg_ms"], 1)
                if ratio >= 2.0:
                    anomalies.append(
                        f"Latency spike: {rel_1h['avg_ms']} ms now vs "
                        f"{rel_7d['avg_ms']} ms 7-day avg ({ratio:.1f}×)"
                    )
            # Success rate drop
            if rel_7d["success_rate"] > 0 and rel_1h["sample"] >= 3:
                drop = rel_7d["success_rate"] - rel_1h["success_rate"]
                if drop >= 0.25:
                    anomalies.append(
                        f"Success rate dropped: {rel_1h['success_rate']*100:.0f}% (1h) "
                        f"vs {rel_7d['success_rate']*100:.0f}% (7d)"
                    )
            # Auth error surge
            if rel_1h["error_rate_4xx"] - rel_7d["error_rate_4xx"] >= 0.3:
                anomalies.append(
                    f"Auth error surge: {rel_1h['error_rate_4xx']*100:.0f}% 4xx "
                    f"(was {rel_7d['error_rate_4xx']*100:.0f}%)"
                )

        return {
            "id": pid,
            "name": name,
            "circuit_broken": circuit_broken,
            "score": score,
            "windows": {
                "1h":  {"reliability": rel_1h,  "availability": _availability(events_1h)},
                "24h": {"reliability": rel_24h, "availability": avail},
                "7d":  {"reliability": rel_7d,  "availability": _availability(events_7d)},
            },
            "context_fit": ctx,
            "explanation": explanation,
            "anomalies": anomalies,
        }

    async def score_for_ranking(
        self,
        pid: str,
        category: Optional[ContentCategory] = None,
    ) -> float:
        """
        Fast path used by stream.py _score() replacement.
        Returns composite float (0–100). Called per provider per request.
        """
        async with self._lock:
            evq = list(self._events.get(pid, deque()))
        events_1h  = _window(evq, WIN_1H)
        events_24h = _window(evq, WIN_24H)
        s = _score_provider(events_1h, events_24h, category)
        return s["composite"]

    async def reset(self, pid: str) -> None:
        async with self._lock:
            self._stats.pop(pid, None)
            self._events.pop(pid, None)
        await self._save()


# ── Singleton ─────────────────────────────────────────────────────────────────
_tracker = HealthTracker()


# ── Public functions (unchanged signatures where possible) ────────────────────

async def get_stats(pid: str) -> dict:
    return await _tracker.get_stats(pid)


async def record(
    pid: str,
    outcome: Outcome,
    ms: float,
    *,
    http_status: int = 0,
    quality_count: int = 0,
    has_m3u8: bool = False,
    has_mp4: bool = False,
    has_iframe: bool = False,
    has_subtitles: bool = False,
    ttl_seconds: int = 0,
    category: ContentCategory = "unknown",
) -> None:
    await _tracker.record(
        pid, outcome, ms,
        http_status=http_status,
        quality_count=quality_count,
        has_m3u8=has_m3u8,
        has_mp4=has_mp4,
        has_iframe=has_iframe,
        has_subtitles=has_subtitles,
        ttl_seconds=ttl_seconds,
        category=category,
    )


async def should_run(pid: str) -> bool:
    return await _tracker.should_run(pid)


async def reset(pid: str) -> None:
    await _tracker.reset(pid)


async def get_insights(pid: str, name: str, category: Optional[ContentCategory] = None) -> dict:
    return await _tracker.get_insights(pid, name, category)


async def score_for_ranking(pid: str, category: Optional[ContentCategory] = None) -> float:
    return await _tracker.score_for_ranking(pid, category)


async def is_circuit_open(pid: str) -> bool:
    """Return True if the circuit breaker is currently tripped for this provider."""
    s = _tracker._stats.get(pid)
    if not s:
        return False
    return s.circuit_until > time.time()


# Expose raw stats dict for admin analytics (read-only — admin never writes here)
# Each value is a dict with an "events" list of _Event dicts.
def _get_raw_stats() -> dict:
    result = {}
    for pid, stat in _tracker._stats.items():
        evs = [asdict(e) for e in _tracker._events.get(pid, deque())]
        result[pid] = {"events": evs, **asdict(stat)}
    return result

# Alias used by admin.py for direct iteration
_stats: dict = {}  # populated lazily — admin reads via _get_raw_stats()
