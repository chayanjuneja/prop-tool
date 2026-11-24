"""Lightweight HTTP regime & kill-switch bridge for MetaTrader 5 WebRequest."""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from .config import PropFirmConfig
from .guardrails import PropGuardrailEngine
from .news_regime import check_news_blackout, load_news_events, query_gemini_regime_veto


class BridgeState:
    def __init__(self, config: Optional[PropFirmConfig] = None, news_file: Optional[Path] = None) -> None:
        self.cfg = config or PropFirmConfig()
        self.guardrails = PropGuardrailEngine(self.cfg)
        self.news_file = news_file or (Path(__file__).parent / "news_events.json")
        self.market_context = "Normal London/NY session Gold trading."
        self.last_equity = self.cfg.initial_balance
        self.lock = threading.Lock()

    def evaluate_regime(self, use_ai: bool = True) -> Dict[str, Any]:
        with self.lock:
            now = datetime.now(timezone.utc)
            self.guardrails.rollover_day(now.date(), self.last_equity, self.last_equity)
            gr_ok, gr_reason = self.guardrails.evaluate(self.last_equity)

            events = load_news_events(self.news_file)
            is_news, news_reason = check_news_blackout(
                now,
                events,
                before_mins=self.cfg.news_blackout_before_mins,
                after_mins=self.cfg.news_blackout_after_mins,
            )

            ai_res = (
                query_gemini_regime_veto(self.market_context)
                if use_ai
                else {"allow": True, "regime": "deterministic", "reason": "AI check disabled"}
            )

            allow = bool(gr_ok and (not is_news) and ai_res.get("allow", True))
            reasons = [gr_reason, news_reason, str(ai_res.get("reason", ""))]
            return {
                "allow": allow,
                "timestamp_utc": now.isoformat(),
                "guardrails_ok": gr_ok,
                "news_blackout": is_news,
                "regime": ai_res.get("regime", "normal"),
                "reason": " | ".join(r for r in reasons if r),
            }


def make_handler(bridge_state: BridgeState):
    class RegimeHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                self._send_json(200, {"status": "ok"})
            elif self.path.startswith("/regime"):
                self._send_json(200, bridge_state.evaluate_regime(use_ai=True))
            else:
                self._send_json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            with bridge_state.lock:
                if self.path.startswith("/kill"):
                    bridge_state.guardrails.state.manual_kill_switch = bool(data.get("halt", True))
                    self._send_json(
                        200,
                        {"manual_kill_switch": bridge_state.guardrails.state.manual_kill_switch},
                    )
                elif self.path.startswith("/context"):
                    if "market_context" in data:
                        bridge_state.market_context = str(data["market_context"])
                    if "equity" in data:
                        bridge_state.last_equity = float(data["equity"])
                    self._send_json(200, {"updated": True})
                else:
                    self._send_json(404, {"error": "Not found"})

        def log_message(self, format: str, *args: Any) -> None:
            return  # Keep console clean; enable if verbose HTTP logging is needed

    return RegimeHandler


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    state = BridgeState()
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"GoldPropGuard bridge listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down bridge server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
