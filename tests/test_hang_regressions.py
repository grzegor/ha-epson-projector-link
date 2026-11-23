"""Failures and real reply callbacks must not poison or feed the command queue."""

import asyncio
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch

import test_serial_transport

driver = test_serial_transport.driver
controls = importlib.import_module("epson_serial_candidate.controls")


class HangRegressionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_serial_transport.TransportTests.asyncSetUp
    asyncTearDown = test_serial_transport.TransportTests.asyncTearDown
    endpoint = test_serial_transport.TransportTests.endpoint

    async def test_timeout_releases_all_waiters_and_next_poll_reconnects(self):
        connections = 0
        received = asyncio.Event()
        commands = []

        async def epson(reader, writer):
            nonlocal connections
            connections += 1
            number = connections
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            command = await reader.readuntil(b"\r")
            commands.append((number, command))
            received.set()
            if number > 1:
                writer.write(b"PWR=01\r:")
                await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        with patch.object(driver, "TIMEOUT_REQUEST", 0.04):
            first = asyncio.create_task(client.get_property("PWR"))
            await received.wait()
            waiters = [
                asyncio.create_task(client.get_property(prop))
                for prop in ("CMODE", "DYNRANGE")
            ]
            results = await asyncio.wait_for(
                asyncio.gather(first, *waiters, return_exceptions=True), 1
            )
        self.assertIsInstance(results[0], TimeoutError)
        self.assertTrue(
            all(isinstance(result, ConnectionError) for result in results[1:])
        )
        self.assertFalse(client._request_queue)
        self.assertEqual(await asyncio.wait_for(client.get_property("PWR"), 1), "on")
        self.assertEqual(commands, [(1, b"PWR?\r"), (2, b"PWR?\r")])

    async def test_cancelled_queued_request_does_not_cancel_or_overtake_active(self):
        received, release = asyncio.Event(), asyncio.Event()
        commands = []

        async def epson(reader, writer):
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            commands.append(await reader.readuntil(b"\r"))
            received.set()
            await release.wait()
            writer.write(b"PWR=01\r:")
            await writer.drain()
            commands.append(await reader.readuntil(b"\r"))
            writer.write(b"DYNRANGE=01\r:")
            await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        first = asyncio.create_task(client.get_property("PWR"))
        await received.wait()
        cancelled = asyncio.create_task(client.get_property("CMODE"))
        last = asyncio.create_task(client.get_property("DYNRANGE"))
        await asyncio.sleep(0)
        cancelled.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            await asyncio.sleep(0.01)
            self.assertEqual(commands, [b"PWR?\r"])
            self.assertFalse(first.done())
            self.assertFalse(last.done())
            release.set()
            self.assertEqual(await asyncio.wait_for(first, 1), "on")
            self.assertEqual(await asyncio.wait_for(last, 1), "01")
            self.assertEqual(commands, [b"PWR?\r", b"DYNRANGE?\r"])
            self.assertFalse(client._request_queue)
        finally:
            release.set()
            client.close()
            await asyncio.gather(first, cancelled, last, return_exceptions=True)

    async def test_cancelling_duplicate_leaves_original_request_running(self):
        received, release = asyncio.Event(), asyncio.Event()
        commands = []

        async def epson(reader, writer):
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            commands.append(await reader.readuntil(b"\r"))
            received.set()
            await release.wait()
            writer.write(b"PWR=01\r:")
            await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        first = asyncio.create_task(client.get_property("PWR"))
        await received.wait()
        duplicate = asyncio.create_task(client.get_property("PWR"))
        await asyncio.sleep(0)
        duplicate.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await duplicate
            self.assertFalse(first.done())
            release.set()
            self.assertEqual(await asyncio.wait_for(first, 1), "on")
            self.assertEqual(commands, [b"PWR?\r"])
        finally:
            release.set()
            client.close()
            await asyncio.gather(first, duplicate, return_exceptions=True)

    async def test_cancelling_active_request_discards_stream_and_unsent_commands(self):
        connections = 0
        received = asyncio.Event()
        commands = []

        async def epson(reader, writer):
            nonlocal connections
            connections += 1
            number = connections
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            commands.append((number, await reader.readuntil(b"\r")))
            received.set()
            if number > 1:
                writer.write(b"CMODE=15\r:")
                await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        first = asyncio.create_task(client.get_property("PWR"))
        await received.wait()
        queued = asyncio.create_task(client.set_property("PWR", "ON"))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(queued, 1)
        self.assertEqual(
            await asyncio.wait_for(client.get_property("CMODE"), 1), "Cinema"
        )
        self.assertEqual(commands, [(1, b"PWR?\r"), (2, b"CMODE?\r")])
        self.assertFalse(client._request_queue)

    async def test_late_reply_cannot_complete_next_transaction(self):
        connections = 0
        new_command = asyncio.Event()
        late_reply_attempted = asyncio.Event()

        async def epson(reader, writer):
            nonlocal connections
            connections += 1
            number = connections
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            await reader.readuntil(b"\r")
            if number == 1:
                await new_command.wait()
                writer.write(b"PWR=00\r:")
                late_reply_attempted.set()
                await writer.drain()
            else:
                new_command.set()
                await late_reply_attempted.wait()
                await asyncio.sleep(0.01)
                writer.write(b"CMODE=15\r:")
                await writer.drain()
                self.assertEqual(await reader.readuntil(b"\r"), b"DYNRANGE?\r")
                writer.write(b"DYNRANGE=01\r:")
                await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        with patch.object(driver, "TIMEOUT_REQUEST", 0.04):
            with self.assertRaises(TimeoutError):
                await client.get_property("PWR")
        self.assertEqual(
            await asyncio.wait_for(client.get_property("CMODE"), 1), "Cinema"
        )
        self.assertEqual(
            await asyncio.wait_for(client.get_property("DYNRANGE"), 1), "01"
        )
        self.assertEqual(connections, 2)

    async def test_close_during_handshake_cannot_reopen_connection(self):
        received, release = asyncio.Event(), asyncio.Event()

        async def epson(reader, writer):
            await reader.readexactly(1)
            received.set()
            await release.wait()
            writer.write(b":")
            await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        pending = asyncio.create_task(client.get_property("PWR"))
        await received.wait()
        client.close()
        release.set()
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(pending, 1)
        self.assertFalse(client._is_open)
        self.assertFalse(client._request_queue)
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(client.get_property("PWR"), 1)

    async def test_automatic_hdr_real_replies_settle_without_feedback_poll_loop(self):
        commands = []
        values = {"PWR": "01", "CMODE": "15", "DYNRANGE": "01"}

        async def epson(reader, writer):
            await reader.readexactly(1)
            writer.write(b":")
            await writer.drain()
            while True:
                command = (await reader.readuntil(b"\r")).decode().strip()
                commands.append(command)
                if command.endswith("?"):
                    prop = command[:-1]
                    writer.write(f"{prop}={values[prop]}\r:".encode())
                else:
                    prop, value = command.split(" ", 1)
                    values[prop] = value
                    writer.write(b":")
                await writer.drain()

        client = await self.endpoint(epson)
        entry = SimpleNamespace(
            entry_id="test-entry", data={"hdr_auto_modes": ["Bright Cinema"]}
        )
        with patch.object(controls, "Store", return_value=Mock()):
            hub = controls.EpsonControls(
                SimpleNamespace(async_create_task=asyncio.create_task), entry, client
            )
        hub.notify = Mock()
        hub.auto_hdr = True
        try:
            await client.get_property("PWR")
            await client.get_property("CMODE")
            await asyncio.sleep(0.03)
            self.assertEqual(commands, ["PWR?", "CMODE?", "DYNRANGE?"])
            self.assertEqual(hub.values["DYNRANGE"], "01")
            # A real subsequent mode report still applies the required policy,
            # and that write's readback must also settle.
            values["CMODE"] = "0C"
            await client.get_property("CMODE")
            await asyncio.sleep(0.03)
            self.assertEqual(commands[-3:], ["CMODE?", "DYNRANGE 00", "DYNRANGE?"])
            self.assertEqual(len(commands), 6)
            self.assertEqual(hub.values["DYNRANGE"], "00")
            self.assertTrue(hub._hdr_task.done())
        finally:
            hub.close()


if __name__ == "__main__":
    unittest.main()
