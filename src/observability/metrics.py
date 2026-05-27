"""可观测性 — Prometheus 指标 + 告警规则"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable


@dataclass
class MetricEntry:
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """轻量指标采集器 (Phase 5: 后续升级 Prometheus client)"""

    def __init__(self):
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histories: Dict[str, List[MetricEntry]] = defaultdict(list)
        self._alert_rules: Dict[str, Callable[[], Optional[str]]] = {}
        self._started_at: float = time.time()

    # ── Counter ────────────────────────────────────────

    def inc(self, name: str, value: float = 1.0, labels: Dict[str, str] = None) -> None:
        key = self._key(name, labels)
        self._counters[key] += value

    def get_counter(self, name: str, labels: Dict[str, str] = None) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    # ── Gauge ──────────────────────────────────────────

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    # ── History ────────────────────────────────────────

    def record(self, name: str, value: float, labels: Dict[str, str] = None) -> None:
        key = self._key(name, labels)
        self._histories[key].append(MetricEntry(value=value, labels=labels or {}))
        if len(self._histories[key]) > 1000:
            self._histories[key] = self._histories[key][-500:]

    def get_p50(self, name: str) -> float:
        values = sorted(e.value for e in self._histories.get(name, []))
        if not values:
            return 0.0
        n = len(values)
        if n % 2 == 0:
            return (values[n//2 - 1] + values[n//2]) / 2
        return values[n//2]

    def get_p95(self, name: str) -> float:
        values = sorted(e.value for e in self._histories.get(name, []))
        if not values:
            return 0.0
        return values[int(len(values) * 0.95)]

    def get_avg(self, name: str) -> float:
        values = [e.value for e in self._histories.get(name, [])]
        if not values:
            return 0.0
        return sum(values) / len(values)

    # ── 告警 ──────────────────────────────────────────

    def add_alert(self, name: str, rule: Callable[[], Optional[str]]) -> None:
        self._alert_rules[name] = rule

    def check_alerts(self) -> List[Dict]:
        alerts = []
        for name, rule in self._alert_rules.items():
            try:
                msg = rule()
                if msg:
                    alerts.append({"name": name, "message": msg, "timestamp": time.time()})
            except Exception:
                pass
        return alerts

    # ── 摘要 ─────────────────────────────────────────

    def summary(self) -> Dict:
        return {
            "uptime_seconds": time.time() - self._started_at,
            "s1_decisions": self.get_counter("s1.decisions.total"),
            "s2_calls": self.get_counter("s2.calls.total"),
            "s2_errors": self.get_counter("s2.errors.total"),
            "replies_sent": self.get_counter("replies.sent"),
            "s2_latency_p50": self.get_p50("s2.latency_ms"),
            "s2_latency_p95": self.get_p95("s2.latency_ms"),
            "degradation_level": int(self.get_gauge("degradation.level")),
        }

    # ── 预设告警 ──────────────────────────────────────

    def setup_default_alerts(self) -> None:
        self.add_alert("high_s2_error_rate", lambda: (
            f"S2错误率过高: {self.get_counter('s2.errors.total')}/{self.get_counter('s2.calls.total')}"
            if self.get_counter("s2.calls.total") > 10
            and self.get_counter("s2.errors.total") / max(self.get_counter("s2.calls.total"), 1) > 0.3
            else None
        ))
        self.add_alert("high_s2_latency", lambda: (
            f"S2延迟过高: p95={self.get_p95('s2.latency_ms'):.0f}ms"
            if self.get_p95("s2.latency_ms") > 5000
            else None
        ))
        self.add_alert("low_reply_rate", lambda: (
            f"回复率过低: {self.get_counter('replies.sent')}/{self.get_counter('s1.decisions.total')}"
            if self.get_counter('s1.decisions.total') > 20
            and self.get_counter('replies.sent') / max(self.get_counter('s1.decisions.total'), 1) < 0.1
            else None
        ))

    @staticmethod
    def _key(name: str, labels: Dict[str, str] = None) -> str:
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{name}{{{label_str}}}"
        return name


# 全局单例
metrics = MetricsCollector()
metrics.setup_default_alerts()
