"""Check the manifest against Home Assistant's actual serial attribution API."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

from homeassistant.components.usb.consumers import async_get_serial_port_consumers
from homeassistant.config_entries import ConfigEntryState

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "custom_components/epson_projector_link/manifest.json"
    ).read_text()
)


class SerialInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def consumers(self, host):
        """Run HA's inventory using this integration's real manifest metadata."""
        entry = SimpleNamespace(
            domain=MANIFEST["domain"],
            entry_id="test-projector",
            title="Projector",
            state=ConfigEntryState.LOADED,
            data={"host": host},
            options={"host": host},
        )
        integration = SimpleNamespace(
            dependencies=MANIFEST.get("dependencies", []),
            after_dependencies=MANIFEST.get("after_dependencies", []),
        )

        async def executor(function, *args):
            return function(*args)

        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_entries=Mock(return_value=[entry])),
            async_add_executor_job=executor,
        )
        with (
            patch(
                "homeassistant.components.usb.consumers.async_get_integrations",
                AsyncMock(return_value={entry.domain: integration}),
            ),
            patch(
                "homeassistant.components.usb.consumers.is_hassio", return_value=False
            ),
        ):
            return await async_get_serial_port_consumers(hass, [])

    async def test_raw_socket_appears_once_with_active_projector_consumer(self):
        endpoint = "socket://192.0.2.10:1234"
        ports = await self.consumers(endpoint)
        self.assertEqual(list(ports), [endpoint])
        self.assertEqual(len(ports[endpoint]), 1)
        consumer = ports[endpoint][0]
        self.assertEqual(consumer.config_entry_id, "test-projector")
        self.assertEqual(consumer.domain, MANIFEST["domain"])
        self.assertEqual(consumer.title, "Projector")
        self.assertTrue(consumer.active)

    async def test_native_network_host_is_not_advertised_as_serial(self):
        self.assertEqual(await self.consumers("192.0.2.10"), {})
