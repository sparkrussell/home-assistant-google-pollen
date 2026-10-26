"""Isolated real HA runtime; no live Home Assistant or credentials."""

import socket

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntries
from homeassistant.util import dt as dt_util


@pytest.fixture(autouse=True)
def only_loopback_network(monkeypatch):
    """A forgotten mock must not send the synthetic key to Google."""
    original = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1"), "External test network forbidden"
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest_asyncio.fixture
async def hass(tmp_path):
    # pytest's temp root is explicitly placed in the approved scratch directory.
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    instance.config.time_zone = "America/Denver"
    dt_util.set_default_time_zone(dt_util.get_time_zone("America/Denver"))
    from homeassistant.helpers import entity_registry, device_registry

    device_registry.async_setup(instance)
    await device_registry.async_load(instance)
    await entity_registry.async_load(instance)
    yield instance
    await instance.async_stop()
    dt_util.set_default_time_zone(dt_util.UTC)
