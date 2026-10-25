"""Native controls use reported feedback and preserve external ownership."""

import asyncio
import importlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import create_autospec
from unittest.mock import patch

from homeassistant.core import Context
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import EntityPlatform
import test_serial_transport

controls = importlib.import_module("epson_serial_candidate.controls")
selects = importlib.import_module("epson_serial_candidate.select")
numbers = importlib.import_module("epson_serial_candidate.number")
switches = importlib.import_module("epson_serial_candidate.switch")


class NativeControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SimpleNamespace(
            async_load=AsyncMock(return_value=None), async_save=AsyncMock()
        )
        self.entry = SimpleNamespace(
            entry_id="test-entry",
            unique_id="test-serial",
            title="Projector",
            data={
                "model_profile": "ls500",
                "hdr_auto_modes": ["Bright Cinema"],
                "delegated_properties": [],
            },
        )
        self.projector = SimpleNamespace(
            add_callback=Mock(return_value=Mock()),
            set_property=AsyncMock(),
            get_property=AsyncMock(return_value="01"),
        )
        self.hass = SimpleNamespace(async_create_task=asyncio.create_task)
        with patch.object(controls, "Store", return_value=self.store):
            self.hub = controls.EpsonControls(self.hass, self.entry, self.projector)
        self.hub.notify = Mock()
        self.hub.connected = True
        self.hub.values = {
            "PWR": "on",
            "CMODE": "Cinema",
            "DYNRANGE": "01",
            "BRIGHT": 126,
            "MUTE": False,
        }

    async def asyncTearDown(self):
        self.hub.close()
        await asyncio.sleep(0)

    async def test_same_value_requests_delegate_but_feedback_never_does(self):
        self.entry.data["delegated_properties"] = ["CMODE"]
        action = SimpleNamespace(async_run=AsyncMock())
        self.hub._action = action
        for _ in range(2):
            await self.hub.async_set("CMODE", "Cinema")
        self.hub.update("CMODE", "Cinema")
        self.assertEqual(action.async_run.await_count, 2)
        self.projector.set_property.assert_not_awaited()
        self.assertEqual(
            action.async_run.call_args.args[0], {"property": "CMODE", "value": "Cinema"}
        )

    async def test_delegated_control_never_falls_back_to_direct_write(self):
        self.entry.data["delegated_properties"] = ["CMODE"]
        with self.assertRaises(controls.HomeAssistantError):
            await self.hub.async_set("CMODE", "Game")
        self.projector.set_property.assert_not_awaited()

    async def test_stored_action_templates_render_through_real_ha_script(self):
        """Exercise selector JSON through HA, not a mocked Script.async_run."""
        with tempfile.TemporaryDirectory() as config_dir:
            hass = HomeAssistant(config_dir)
            received = []

            async def receive(call):
                received.append(dict(call.data))

            hass.services.async_register("test_receiver", "set", receive)
            self.entry.data["control_action"] = [
                {
                    "action": "test_receiver.set",
                    "data": {
                        "capability_id": (
                            "{{ {'CMODE': 'picture', 'BRIGHT': 'brightness',"
                            " 'MUTE': 'blank'}[property] }}"
                        ),
                        "value": "{{ value }}",
                    },
                }
            ]
            self.entry.data["delegated_properties"] = ["MUTE", "BRIGHT", "CMODE"]
            values = self.hub.values.copy()
            self.hub.close()
            with patch.object(controls, "Store", return_value=self.store):
                self.hub = controls.EpsonControls(hass, self.entry, self.projector)
            self.hub.notify = Mock()
            self.hub.connected = True
            self.hub.values = values
            await self.hub.async_load()
            self.hub.auto_hdr = False
            for prop, value in [("MUTE", False), ("BRIGHT", 126), ("CMODE", "Cinema")]:
                await self.hub.async_set(prop, value, Context())
            self.assertEqual(
                received,
                [
                    {"capability_id": "blank", "value": False},
                    {"capability_id": "brightness", "value": 126},
                    {"capability_id": "picture", "value": "Cinema"},
                ],
            )
            self.projector.set_property.assert_not_awaited()

    async def test_pending_feedback_after_unload_cannot_start_new_policy_task(self):
        self.hub.close()
        self.hub.auto_hdr = True
        self.hub.update("CMODE", "Bright Cinema")
        await asyncio.sleep(0)
        self.projector.set_property.assert_not_awaited()

    async def test_source_parser_exposes_all_ls500_inputs(self):
        parsed = test_serial_transport.driver._parse_source_list(
            "30 HDMI1 53 LAN A0 HDMI2 C0 HDMI3"
        )
        self.assertEqual(parsed, ["HDMI1", "LAN", "HDMI2", "HDMI3"])

    async def test_source_polling_reads_current_input_as_well_as_input_list(self):
        entity, projector, unregister = (
            test_serial_transport.AvailabilityTests().entity(None)
        )
        entity._poll_properties = ["SOURCE"]
        entity._attr_available = True
        entity._attr_state = "on"
        tasks = []
        entity.hass = SimpleNamespace(
            create_task=lambda coro: tasks.append(asyncio.create_task(coro))
        )
        entity.update_additional_attributes()
        await asyncio.gather(*tasks)
        self.assertEqual(
            {c.args[0] for c in projector.get_property.await_args_list},
            {"SOURCE", "SOURCELIST"},
        )
        entity._callback("SOURCE", "HDMI3")
        self.assertEqual(entity.source, "HDMI3")
        entity.unload()

    async def test_entity_services_match_current_home_assistant_api(self):
        platform = create_autospec(EntityPlatform, instance=True)
        module = test_serial_transport.entity_module
        with patch.object(
            module.entity_platform, "async_get_current_platform", return_value=platform
        ):
            module._setup_services()
        calls = platform.async_register_entity_service.call_args_list
        command = next(call for call in calls if call.args[0] == "send_command")
        self.assertEqual(
            command.kwargs["supports_response"], module.SupportsResponse.OPTIONAL
        )

    async def test_commands_use_documented_codes_and_read_back(self):
        for prop, value, wire in [
            ("CMODE", "Game", "0D"),
            ("DYNRANGE", "HDR10", "20"),
            ("DYNRANGE", "HLG", "30"),
            ("BRIGHT", 0, 0),
            ("MUTE", False, "OFF"),
        ]:
            await self.hub.async_set(prop, value)
            self.projector.set_property.assert_awaited_with(prop, wire)
            self.projector.get_property.assert_awaited_with(prop)

    async def test_brightness_keeps_reported_state_until_readback_arrives(self):
        number = numbers.EpsonBrightness(self.hub)
        await number.async_set_native_value(100)
        self.assertEqual(number.native_value, 126)
        self.hub.update("BRIGHT", 100)
        self.assertEqual(number.native_value, 100)
        with self.assertRaises(ValueError):
            await number.async_set_native_value(256)
        with self.assertRaises(ValueError):
            await number.async_set_native_value(1.5)

    async def test_manual_hdr_disables_and_persists_automatic_policy(self):
        self.hub.auto_hdr = True
        await self.hub.async_set("DYNRANGE", "SDR")
        self.assertFalse(self.hub.auto_hdr)
        self.store.async_save.assert_awaited_with({"auto_hdr": False})
        self.projector.set_property.reset_mock()
        await self.hub.async_apply_auto_hdr()
        self.projector.set_property.assert_not_awaited()

    async def test_manual_override_is_restored_before_first_report(self):
        self.store.async_load.return_value = {"auto_hdr": False}
        await self.hub.async_load()
        self.hub.update("CMODE", "Bright Cinema")
        await asyncio.sleep(0)
        self.assertFalse(self.hub.auto_hdr)
        self.projector.set_property.assert_not_awaited()

    async def test_auto_policy_uses_actual_mode_and_skips_matching_writes(self):
        self.hub.auto_hdr = True
        await self.hub.async_apply_auto_hdr()
        self.projector.set_property.assert_not_awaited()
        self.hub.values["CMODE"] = "Bright Cinema"
        await self.hub.async_apply_auto_hdr()
        self.projector.set_property.assert_awaited_with("DYNRANGE", "00")
        self.hub.values["PWR"] = "off"
        self.projector.set_property.reset_mock()
        await self.hub.async_apply_auto_hdr()
        self.projector.set_property.assert_not_awaited()

    async def test_auto_policy_does_not_use_previous_power_session_mode(self):
        self.hub.auto_hdr = True
        self.hub.update("PWR", "off")
        self.hub.update("PWR", "on")
        await self.hub.async_apply_auto_hdr()
        self.projector.set_property.assert_not_awaited()
        self.assertNotIn("CMODE", self.hub.values)

    async def test_lost_connection_invalidates_all_controls(self):
        number = numbers.EpsonBrightness(self.hub)
        self.assertTrue(number.available)
        self.hub.set_connected(False)
        self.assertFalse(number.available)
        self.assertEqual(self.hub.values, {})

    async def test_native_entities_share_device_and_keep_actual_hdr(self):
        picture = selects.EpsonModeSelect(
            self.hub, "color_mode", "Picture mode", "CMODE", controls.LS500_COLOR_MODES
        )
        hdr = selects.EpsonModeSelect(
            self.hub, "dynamic_range", "HDR mode", "DYNRANGE", list(controls.HDR_MODES)
        )
        blank = switches.EpsonBlankSwitch(self.hub)
        self.assertEqual(picture.device_info, blank.device_info)
        self.assertEqual(picture.current_option, "Cinema")
        self.assertEqual(hdr.current_option, "SDR")
        self.assertFalse(blank.is_on)
        self.assertEqual(picture.unique_id, "test-serial_color_mode")


class ZeroSettingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_serial_transport.TransportTests.asyncSetUp
    asyncTearDown = test_serial_transport.TransportTests.asyncTearDown
    endpoint = test_serial_transport.TransportTests.endpoint

    async def test_numeric_zero_is_transmitted(self):
        async def epson(reader, writer):
            self.assertEqual(await reader.readexactly(1), b"\r")
            writer.write(b":")
            await writer.drain()
            self.assertEqual(await reader.readuntil(b"\r"), b"BRIGHT 0\r")
            writer.write(b":")
            await writer.drain()
            await reader.read()

        client = await self.endpoint(epson)
        self.assertEqual(await client.set_property("BRIGHT", 0), 0)
