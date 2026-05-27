"""Shared pytest fixtures for OpenNeuro tests"""

import sys
import os

os.environ["DEEPSEEK_API_KEY"] = "test"
os.environ["SILICONFLOW_API_KEY"] = "test"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_s1_client():
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.generate = AsyncMock(
        return_value=MagicMock(
            parsed=MagicMock(
                token=MagicMock(value="GREETING"), direction="", confidence=0.8
            )
        )
    )
    return client


@pytest.fixture
def mock_s2_client():
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.generate = AsyncMock(
        return_value=MagicMock(content="测试回复", thinking="", total_ms=500, error="")
    )
    return client


@pytest.fixture
def event_bus():
    from src.events.bus import EventBus

    return EventBus(log_dir="data/test_events")
