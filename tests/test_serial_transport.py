"""Exercise the candidate using fake Epson endpoints in HA's installed Python."""

import asyncio
import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

ROOT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "epson_projector_link"
)
spec = importlib.util.spec_from_file_location(
    "epson_serial_candidate",
    ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)],
)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
driver = importlib.import_module("epson_serial_candidate.projector.projector")
flow_module = importlib.import_module("epson_serial_candidate.config_flow")
const = importlib.import_module("epson_serial_candidate.projector.const")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []
        self.writers = []
        self.servers = []

    async def asyncTearDown(self):
        for client in self.clients:
            client.close()
        for writer in self.writers:
            writer.close()
        await asyncio.sleep(0)
        for server in self.servers:
            server.close()
            await server.wait_closed()
        await asyncio.sleep(0)

    async def endpoint(self, handler, raw=True):
        async def accepted(reader, writer):
            self.writers.append(writer)
            try:
                await handler(reader, writer)
            except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(accepted, "127.0.0.1", 0)
        self.servers.append(server)
        port = server.sockets[0].getsockname()[1]
        client = driver.Projector(
            f"socket://127.0.0.1:{port}" if raw else "127.0.0.1", port=port
        )
        self.clients.append(client)
        return client

    async def test_raw_queries_fragmentation_queue_and_errors(self):
        commands = []

        async def epson(reader, writer):
            self.assertEqual(await reader.readexactly(1), b"\r")
            writer.write(b":")
            await writer.drain()
            replies = {
                b"PWR?\r": b"PWR=01\r:",
                b"CMODE?\r": b"CMODE=15\r:",
                b"DYNRANGE?\r": b"DYNRANGE=01\r:",
                b"BAD?\r": b"ERR\r:",
            }
            while True:
                command = await reader.readuntil(b"\r")
                commands.append(command)
                reply = replies[command]
                writer.write(reply[:3])
                await writer.drain()
                await asyncio.sleep(0.001)
                writer.write(reply[3:])
                await writer.drain()

        client = await self.endpoint(epson)
        values = await asyncio.gather(
            client.get_property("PWR"),
            client.get_property("CMODE"),
            client.send_command("DYNRANGE?"),
        )
        self.assertEqual(values, ["on", "Cinema", "01"])
        self.assertEqual(commands[:3], [b"PWR?\r", b"CMODE?\r", b"DYNRANGE?\r"])
        with self.assertRaises(driver.ProjectorErrorResponse):
            await client.send_command("BAD?")
        self.assertEqual(await client.get_property("PWR"), "on")
        self.assertEqual(len(client._request_queue), 0)

    async def test_concurrent_reads_share_one_connection_and_request(self):
        connections = 0
        requests = []

        async def epson(reader, writer):
            nonlocal connections
            connections += 1
            self.assertEqual(await reader.readexactly(1), b"\r")
            await asyncio.sleep(0.01)
            writer.write(b":")
            await writer.drain()
            while True:
                requests.append(await reader.readuntil(b"\r"))
                await asyncio.sleep(0.01)
                writer.write(b"PWR=01\r:")
                await writer.drain()

        client = await self.endpoint(epson)
        results = await asyncio.gather(*[client.get_property("PWR") for _ in range(5)])
        self.assertEqual(results, ["on"] * 5)
        self.assertEqual(connections, 1)
        self.assertEqual(requests, [b"PWR?\r"])

    async def test_raw_rejects_wrong_prompt_and_closes_connection(self):
        closed = asyncio.Event()

        async def epson(reader, writer):
            await reader.readexactly(1)
            writer.write(b"ERR\r:")
            await writer.drain()
            self.assertEqual(await reader.read(), b"")
            closed.set()

        client = await self.endpoint(epson)
        with self.assertRaises(ValueError):
            await client.connect()
        await asyncio.wait_for(closed.wait(), 1)
        self.assertFalse(client._is_open)

    async def test_raw_timeout_closes_connection(self):
        closed = asyncio.Event()

        async def epson(reader, writer):
            await reader.readexactly(1)
            self.assertEqual(await reader.read(), b"")
            closed.set()

        client = await self.endpoint(epson)
        with patch.object(driver, "TIMEOUT_CONNECT", 0.03):
            with self.assertRaises(asyncio.TimeoutError):
                await client.connect()
        await asyncio.wait_for(closed.wait(), 1)
        self.assertFalse(client._is_open)

    async def test_existing_network_handshake_remains_supported(self):
        async def epson(reader, writer):
            self.assertEqual(
                await reader.readexactly(16), const.ESCVPNET_CONNECT_COMMAND.encode()
            )
            reply = const.ESCVPNETNAME.encode() + bytes([16, 3, 0, 0, 32, 0])
            writer.write(reply[:7])
            await writer.drain()
            await asyncio.sleep(0.001)
            writer.write(reply[7:])
            await writer.drain()
            self.assertEqual(await reader.readuntil(b"\r"), b"PWR?\r")
            writer.write(b"PWR=01\r:")
            await writer.drain()
            await reader.read()

        client = await self.endpoint(epson, raw=False)
        self.assertEqual(await client.get_property("PWR"), "on")

    async def test_network_rejects_bad_status_and_closes(self):
        closed = asyncio.Event()

        async def epson(reader, writer):
            await reader.readexactly(16)
            writer.write(const.ESCVPNETNAME.encode() + bytes([16, 3, 0, 0, 64, 0]))
            await writer.drain()
            self.assertEqual(await reader.read(), b"")
            closed.set()

        client = await self.endpoint(epson, raw=False)
        with self.assertRaises(ValueError):
            await client.connect()
        await asyncio.wait_for(closed.wait(), 1)

    async def test_proxy_disconnect_reconnects_without_new_power_command(self):
        connections = 0
        reconnected = asyncio.Event()

        async def epson(reader, writer):
            nonlocal connections
            connections += 1
            number = connections
            self.assertEqual(await reader.readexactly(1), b"\r")
            writer.write(b":")
            await writer.drain()
            if number > 1:
                reconnected.set()
            self.assertEqual(await reader.readuntil(b"\r"), b"PWR?\r")
            writer.write(b"PWR=01\r:")
            await writer.drain()
            if number > 1:
                await reader.read()

        client = await self.endpoint(epson)
        self.assertEqual(await client.get_property("PWR"), "on")
        await asyncio.wait_for(reconnected.wait(), 1)
        self.assertEqual(await client.get_property("PWR"), "on")
        self.assertEqual(connections, 2)

    async def test_raw_power_transition_ack_and_fresh_state(self):
        commands = []
        powered = False

        async def epson(reader, writer):
            nonlocal powered
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            while True:
                command = await reader.readuntil(b"\r")
                commands.append(command)
                if command == b"PWR ON\r":
                    powered = True
                    reply = b":"
                else:
                    reply = b"PWR=01\r:" if powered else b"PWR=00\r:"
                writer.write(reply)
                await writer.drain()

        client = await self.endpoint(epson)
        self.assertEqual(await client.get_property("PWR"), "off")
        await client.set_property("PWR", "ON")
        self.assertEqual(await asyncio.wait_for(client.get_property("PWR"), 1), "on")
        self.assertEqual(commands, [b"PWR?\r", b"PWR ON\r", b"PWR?\r"])

    def test_endpoint_validation(self):
        for address in (
            "socket://",
            "socket://host",
            "socket://user:password@host:1234",
            "socket://host:1234/path",
            "socket://host:1234?x=1",
            "socket://host:1234#fragment",
            "socket://host:70000",
            "socket://host:0",
            "http://host",
        ):
            with self.subTest(address=address), self.assertRaises(ValueError):
                driver.Projector(address)


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    def flow(self):
        flow = flow_module.EpsonProjectorLinkOptionsFlowHandler(
            SimpleNamespace(
                data={
                    "host": "old-host",
                    "scan_interval": 600,
                    "poll_properties_scan_interval": 60,
                    "poll_properties": ["CMODE"],
                },
                unique_id="existing-serial",
            )
        )
        flow.async_show_form = Mock(side_effect=lambda **kw: {"type": "form", **kw})
        flow.async_create_entry = Mock(
            side_effect=lambda **kw: {"type": "create_entry", **kw}
        )
        return flow

    async def test_options_expose_endpoint(self):
        form = await self.flow().async_step_init()
        validated = form["data_schema"]({"host": "socket://example.invalid:1234"})
        self.assertEqual(validated["host"], "socket://example.invalid:1234")

    async def test_wrong_projector_refused_and_probe_closed(self):
        client = SimpleNamespace(
            get_property=AsyncMock(return_value="wrong-serial"), close=Mock()
        )
        with patch.object(flow_module, "create_projector", return_value=client):
            flow = self.flow()
            result = await flow.async_step_init(
                {"host": "socket://example.invalid:1234"}
            )
        self.assertEqual(result["errors"], {"base": "wrong_projector"})
        flow.async_create_entry.assert_not_called()
        client.close.assert_called_once()

    async def test_valid_endpoint_preserves_identity_and_options(self):
        client = SimpleNamespace(
            get_property=AsyncMock(side_effect=["existing-serial", "on", "Cinema"]),
            close=Mock(),
        )
        with patch.object(flow_module, "create_projector", return_value=client):
            result = await self.flow().async_step_init(
                {
                    "host": "socket://example.invalid:1234",
                    "scan_interval": 5,
                }
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"],
            {
                "host": "socket://example.invalid:1234",
                "scan_interval": 5,
                "poll_properties_scan_interval": 60,
                "poll_properties": ["CMODE"],
            },
        )
        client.close.assert_called_once()

    async def test_unreachable_endpoint_not_saved(self):
        client = SimpleNamespace(
            get_property=AsyncMock(side_effect=asyncio.TimeoutError),
            close=Mock(),
        )
        with patch.object(flow_module, "create_projector", return_value=client):
            flow = self.flow()
            result = await flow.async_step_init(
                {"host": "socket://example.invalid:1234"}
            )
        self.assertEqual(result["type"], "form")
        flow.async_create_entry.assert_not_called()
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
