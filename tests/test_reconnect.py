"""Regression tests for the connection supervisor (reconnect behaviour).

These tests run the real driver against a fake TCP gateway. They cover the
two empirically reproduced production scenarios:

- Gateway offline at startup: the old code never scheduled a retry after a
  failed initial connect, leaving the integration dead until reload.
- Connection lost at runtime (peer close): the old code reconnected only if
  the first reconnect attempt succeeded.
"""

import asyncio

import pytest

from custom_components.foxtron_dali.driver import (
    DaliCommandEvent,
    FoxtronDaliDriver,
    FoxtronMessage,
    MSG_TYPE_DALI_EVENT_NO_ANSWER,
    MSG_TYPE_QUERY_CONFIG_ITEM,
)


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """Lift the socket block from pytest-homeassistant-custom-component.

    These tests exercise real localhost TCP connections. The fixture (rather
    than the enable_socket marker) is used because fixtures run after all
    pytest_runtest_setup hooks, so it wins regardless of plugin hook order.
    """
    yield


class FakeGateway:
    """Minimal TCP server standing in for a Foxtron DALI gateway."""

    def __init__(self) -> None:
        self.server: asyncio.Server | None = None
        self.port: int | None = None
        self.connections = 0
        self.clients: list[asyncio.StreamWriter] = []
        self.received = b""

    async def start(self, port: int | None = None) -> None:
        self.server = await asyncio.start_server(
            self._on_client, "127.0.0.1", port or 0
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self.clients.append(writer)
        while True:
            data = await reader.read(1024)
            if not data:
                return
            self.received += data

    async def stop(self) -> None:
        for writer in self.clients:
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def _free_port() -> int:
    """Reserve and release a local TCP port."""
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    return port


async def _wait_for(condition, timeout: float = 5.0) -> bool:
    """Poll ``condition`` until it is true or ``timeout`` elapses."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


@pytest.mark.asyncio
async def test_connects_and_reports_state():
    """Baseline: driver connects to a running gateway."""
    gateway = FakeGateway()
    await gateway.start()
    driver = FoxtronDaliDriver("127.0.0.1", gateway.port, reconnect_delay=0.05)
    try:
        await driver.connect()
        assert await driver.wait_connected(5)
        assert driver.is_connected
        assert gateway.connections == 1
    finally:
        await driver.disconnect()
        await gateway.stop()


@pytest.mark.asyncio
async def test_retries_until_gateway_appears():
    """Regression: gateway offline at startup must not kill the connection.

    The supervisor keeps retrying (multiple failed attempts) and connects
    as soon as the gateway comes online — the power-outage recovery case.
    """
    port = await _free_port()
    driver = FoxtronDaliDriver("127.0.0.1", port, reconnect_delay=0.05)
    gateway = FakeGateway()
    try:
        await driver.connect()
        # Several connection attempts fail while the gateway is offline.
        assert not await driver.wait_connected(0.3)

        await gateway.start(port)
        assert await driver.wait_connected(5)
        assert driver.is_connected
    finally:
        await driver.disconnect()
        await gateway.stop()


@pytest.mark.asyncio
async def test_reconnects_after_peer_close():
    """Regression: a dropped connection must be re-established."""
    gateway = FakeGateway()
    await gateway.start()
    driver = FoxtronDaliDriver("127.0.0.1", gateway.port, reconnect_delay=0.05)
    try:
        await driver.connect()
        assert await driver.wait_connected(5)

        # Gateway drops the TCP connection (e.g. gateway reboot).
        gateway.clients[0].close()

        assert await _wait_for(lambda: gateway.connections >= 2 and driver.is_connected)
    finally:
        await driver.disconnect()
        await gateway.stop()


@pytest.mark.asyncio
async def test_disconnect_stops_supervisor():
    """disconnect() must stop the retry loop for good (config flow probe)."""
    port = await _free_port()
    driver = FoxtronDaliDriver("127.0.0.1", port, reconnect_delay=0.05)
    await driver.connect()
    await asyncio.sleep(0.15)
    await driver.disconnect()
    assert driver._connection._supervisor_task is None
    assert not driver.is_connected


@pytest.mark.asyncio
async def test_send_while_disconnected_raises():
    """Sending without a connection fails fast with ConnectionError."""
    driver = FoxtronDaliDriver("127.0.0.1", 9, reconnect_delay=0.05)
    with pytest.raises(ConnectionError):
        await driver.send_dali_command(0xFF, 0x00)


@pytest.mark.asyncio
async def test_events_delivered_over_tcp():
    """Frames from the gateway reach registered event listeners in order."""
    gateway = FakeGateway()
    await gateway.start()
    driver = FoxtronDaliDriver("127.0.0.1", gateway.port, reconnect_delay=0.05)
    received: list = []
    driver.add_event_listener(received.append)
    try:
        await driver.connect()
        assert await driver.wait_connected(5)

        # A 16-bit DALI command event (address 0x01, opcode 0x02)
        payload = bytes([MSG_TYPE_DALI_EVENT_NO_ANSWER, 0x10, 0x01, 0x02])
        gateway.clients[0].write(FoxtronMessage.build_frame(payload))
        await gateway.clients[0].drain()

        assert await _wait_for(lambda: len(received) == 1)
        event = received[0]
        assert isinstance(event, DaliCommandEvent)
        assert event.address_byte == 0x01
        assert event.opcode_byte == 0x02
    finally:
        await driver.disconnect()
        await gateway.stop()


@pytest.mark.asyncio
async def test_keep_alive_sent():
    """The keep-alive frame is sent periodically over the connection."""
    gateway = FakeGateway()
    await gateway.start()
    driver = FoxtronDaliDriver(
        "127.0.0.1", gateway.port, keep_alive_interval=0.05, reconnect_delay=0.05
    )
    keep_alive_frame = FoxtronMessage.build_frame(
        bytes([MSG_TYPE_QUERY_CONFIG_ITEM, 0x02])
    )
    try:
        await driver.connect()
        assert await driver.wait_connected(5)
        assert await _wait_for(lambda: keep_alive_frame in gateway.received)
    finally:
        await driver.disconnect()
        await gateway.stop()
