"""Tests for resource_adaptive engine."""
import time
from unittest.mock import MagicMock, patch

import pytest

from xagent.core.resource_adaptive import (
    AdaptiveSettings,
    ResourceMonitor,
    SystemProfile,
    SystemProfiler,
    Throttler,
    apply_adaptive_config,
)


class TestSystemProfile:
    def test_tier_low_memory(self):
        p = SystemProfile(cpu_count=4, memory_gb=2.0)
        assert p.tier() == "low"

    def test_tier_low_cpu(self):
        p = SystemProfile(cpu_count=1, memory_gb=8.0)
        assert p.tier() == "low"

    def test_tier_low_explicit(self):
        p = SystemProfile(cpu_count=8, memory_gb=16.0, is_low_end=True)
        assert p.tier() == "low"

    def test_tier_high(self):
        p = SystemProfile(cpu_count=8, memory_gb=16.0)
        assert p.tier() == "high"

    def test_tier_high_more_cores(self):
        p = SystemProfile(cpu_count=16, memory_gb=32.0)
        assert p.tier() == "high"

    def test_tier_mid(self):
        p = SystemProfile(cpu_count=4, memory_gb=8.0)
        assert p.tier() == "mid"


class TestSystemProfilerRecommend:
    def test_recommend_low(self):
        profile = SystemProfile(cpu_count=2, memory_gb=2.0, is_low_end=True)
        s = SystemProfiler.recommend(profile)
        assert s.max_tokens == 2048
        assert s.cache_mode == "never"
        assert s.session_persist is False
        assert s.enable_thought_harvest is False
        assert s.max_questions_per_task == 2

    def test_recommend_mid(self):
        profile = SystemProfile(cpu_count=4, memory_gb=8.0)
        s = SystemProfiler.recommend(profile)
        assert s.max_tokens == 4096
        assert s.cache_mode == "auto"
        assert s.session_persist is True

    def test_recommend_high(self):
        profile = SystemProfile(cpu_count=16, memory_gb=32.0)
        s = SystemProfiler.recommend(profile)
        assert s.max_tokens == 8192
        assert s.cache_mode == "auto"
        assert s.session_persist is True
        assert s.enable_thought_harvest is True
        assert s.max_questions_per_task == 5

    def test_recommend_none_defaults_to_detect(self):
        with patch.object(SystemProfiler, "detect", return_value=SystemProfile(cpu_count=2, memory_gb=2.0, is_low_end=True)):
            s = SystemProfiler.recommend(None)
            assert s.max_tokens == 2048


class TestSystemProfilerReport:
    def test_report_contains_tier(self):
        report = SystemProfiler.report()
        assert "系统档位" in report

    def test_report_virtual_flag(self):
        with patch.object(SystemProfiler, "detect", return_value=SystemProfile(cpu_count=2, memory_gb=4.0, is_virtual=True)):
            report = SystemProfiler.report()
            assert "虚拟机" in report


class TestResourceMonitor:
    def test_start_stop(self):
        m = ResourceMonitor(interval=0.1)
        m.start()
        assert m._running is True
        assert m._thread is not None
        m.stop()
        assert m._running is False

    def test_current_load_defaults(self):
        m = ResourceMonitor()
        load = m.current_load()
        assert "cpu" in load
        assert "memory" in load
        assert load["cpu"] == 0.0
        assert load["memory"] == 0.0

    def test_is_overloaded_false_by_default(self):
        m = ResourceMonitor()
        assert m.is_overloaded() is False

    def test_is_overloaded_when_cpu_high(self):
        m = ResourceMonitor(cpu_threshold=50.0)
        m._cpu_percent = 60.0
        assert m.is_overloaded() is True

    def test_should_throttle_preventive(self):
        m = ResourceMonitor()
        m._cpu_percent = 80.0
        assert m.should_throttle() is True

    def test_should_throttle_false_when_idle(self):
        m = ResourceMonitor()
        m._cpu_percent = 10.0
        m._memory_percent = 20.0
        assert m.should_throttle() is False


class TestThrottler:
    def test_check_allows_when_no_monitor(self):
        t = Throttler(monitor=None)
        assert t.check("test") is True

    def test_check_allows_when_healthy(self):
        monitor = ResourceMonitor()
        monitor._cpu_percent = 10.0
        monitor._memory_percent = 20.0
        t = Throttler(monitor=monitor)
        assert t.check("test") is True

    def test_check_denies_when_overloaded(self):
        monitor = ResourceMonitor()
        monitor._cpu_percent = 80.0
        monitor._memory_percent = 20.0
        t = Throttler(monitor=monitor)
        assert t.check("test") is False
        assert t._throttle_count == 1

    def test_wait_if_needed_skips_when_healthy(self):
        monitor = ResourceMonitor()
        monitor._cpu_percent = 10.0
        t = Throttler(monitor=monitor)
        start = time.time()
        t.wait_if_needed(base_delay=0.5)
        assert time.time() - start < 0.1

    def test_wait_if_needed_waits_when_overloaded(self):
        monitor = ResourceMonitor()
        monitor._cpu_percent = 80.0
        t = Throttler(monitor=monitor)
        start = time.time()
        t.wait_if_needed(base_delay=0.1)
        assert time.time() - start >= 0.08

    def test_status(self):
        monitor = ResourceMonitor()
        monitor._cpu_percent = 50.0
        monitor._memory_percent = 60.0
        t = Throttler(monitor=monitor)
        t._throttle_count = 3
        status = t.status()
        assert "CPU" in status
        assert "Memory" in status
        assert "3" in status


class TestApplyAdaptiveConfig:
    def test_applies_low_end(self):
        config = {}
        profile = SystemProfile(cpu_count=2, memory_gb=2.0, is_low_end=True)
        result = apply_adaptive_config(config, profile)
        assert result["model"]["max_tokens"] == 2048
        assert result["cache"]["mode"] == "never"
        assert result["cache"]["session_persist"] is False
        assert result["_adaptive"]["tier"] == "low"
        assert "applied_at" in result["_adaptive"]

    def test_applies_high_end(self):
        config = {}
        profile = SystemProfile(cpu_count=16, memory_gb=32.0)
        result = apply_adaptive_config(config, profile)
        assert result["model"]["max_tokens"] == 8192
        assert result["cache"]["mode"] == "auto"
        assert result["_adaptive"]["tier"] == "high"

    def test_preserves_existing_keys(self):
        config = {"model": {"temperature": 0.7}, "extra": "value"}
        profile = SystemProfile(cpu_count=4, memory_gb=8.0)
        result = apply_adaptive_config(config, profile)
        assert result["model"]["temperature"] == 0.7
        assert result["extra"] == "value"

    def test_auto_detect_when_profile_none(self):
        config = {}
        with patch.object(SystemProfiler, "detect", return_value=SystemProfile(cpu_count=2, memory_gb=2.0, is_low_end=True)):
            result = apply_adaptive_config(config, None)
            assert result["_adaptive"]["tier"] == "low"
