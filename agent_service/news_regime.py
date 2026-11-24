"""News blackout calendar and optional AI macro-regime filter (veto-only)."""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class NewsEvent:
    title: str
    currency: str
    impact: str  # "HIGH", "MEDIUM", "LOW"
    timestamp_utc: datetime


def load_news_events(path: Path) -> List[NewsEvent]:
    """Loads scheduled economic events from a JSON file."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    events: List[NewsEvent] = []
    for item in raw:
        dt = datetime.fromisoformat(item["timestamp_utc"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        events.append(
            NewsEvent(
                title=str(item.get("title", "Unknown Event")),
                currency=str(item.get("currency", "USD")).upper(),
                impact=str(item.get("impact", "HIGH")).upper(),
                timestamp_utc=dt.astimezone(timezone.utc),
            )
        )
    return events


def check_news_blackout(
    now_utc: datetime,
    events: List[NewsEvent],
    before_mins: int = 15,
    after_mins: int = 10,
    currencies: Tuple[str, ...] = ("USD",),
) -> Tuple[bool, str]:
    """Returns (is_blackout, reason) if now_utc falls near a high-impact event."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    for ev in events:
        if ev.impact != "HIGH" or ev.currency not in currencies:
            continue
        start = ev.timestamp_utc - timedelta(minutes=before_mins)
        end = ev.timestamp_utc + timedelta(minutes=after_mins)
        if start <= now_utc <= end:
            return True, f"High-impact {ev.currency} news blackout: {ev.title}"
    return False, "No high-impact news blackout"


def query_gemini_regime_veto(
    market_summary: str,
    api_key: Optional[str] = None,
    model: str = "gemini-2.5-flash",
    timeout_secs: float = 5.0,
) -> Dict[str, object]:
    """
    Optional AI regime check. Returns {"allow": bool, "regime": str, "reason": str}.
    The AI can only VETO trading during erratic/geopolitical shock conditions;
    it cannot override hard drawdown or news guardrails.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"allow": True, "regime": "deterministic", "reason": "GEMINI_API_KEY not set; AI veto skipped"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    prompt = (
        "You are a conservative risk officer for a $2,500 The5ers prop-firm Gold (XAUUSD) bot. "
        "Decide whether market conditions are SAFE ('ALLOW') or UNSAFE ('HALT' - e.g. unscheduled "
        "geopolitical shock, flash crash, or central bank emergency). "
        "Respond strictly with JSON: {\"allow\": true/false, \"regime\": \"normal|volatile|shock\", \"reason\": \"short reason\"}.\n\n"
        f"Market context:\n{market_summary}"
    )
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
        return {
            "allow": bool(parsed.get("allow", True)),
            "regime": str(parsed.get("regime", "normal")),
            "reason": str(parsed.get("reason", "AI check passed")),
        }
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as exc:
        return {"allow": True, "regime": "fallback", "reason": f"AI check skipped ({exc})"}
