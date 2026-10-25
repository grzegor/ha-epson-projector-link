# Epson Projector Link — serial-proxy fork

This is a fork of [amosyuen/ha-epson-projector-link](https://github.com/amosyuen/ha-epson-projector-link), based on upstream commit `18f1b79c7b8b3c22d00b95de9e7bc0b266d86636` (integration version 1.2.3). The original integration and its authors retain their credit. The Apache-2.0 [LICENSE](LICENSE) is unchanged.

## Read this first

**The changes in this fork are 100% AI-generated / “vibecoded”, without human code review or anyone committing to ongoing maintenance.** Automated tests are included, but they are not a substitute for human review or a promise of reliability.

**These changes are not being submitted upstream to the original maintainer for that reason.** The original maintainer does not review, support or maintain this fork. This repository is an experimental personal fork, not an official upstream release.

## Why this fork exists

Upstream opens an Epson ESC/VP.net network connection. A USB-to-RS-232 adapter attached to a serial device server instead carries plain ESC/VP21 commands over TCP, so the network handshake does not work there.

This fork adds explicit `socket://host:port` support while keeping the integration's command queue, warmup/cooldown handling, existing entity domain and custom actions. Its purpose is to use a projector's RS-232 connection when its own network interface is slow to return after a power cycle. Startup timing and standby behavior remain dependent on the projector and have not been guaranteed by the software tests.

Bare hostnames/IP addresses continue to use the original ESC/VP.net transport. The entity remains registered when the endpoint is temporarily unreachable, so its power polling can recover without an integration reload. This fork does not add USB/IP, local USB-device forwarding or RFC2217 configuration. Set baud rate, framing and flow control on the serial server itself.

On Home Assistant versions with Settings → Connectivity → Serial, the configured `socket://` endpoint appears with this integration as its consumer. The manifest declares the optional `usb` dependency so Home Assistant can attribute the configured connection using its native serial inventory. That page reports configuration ownership; it does not open a second connection or verify that the projector is responding. Bare ESC/VP.net hosts are not serial ports and do not appear there.

## Install with HACS

1. Back up the existing integration and its configuration.
2. In HACS, add `https://github.com/grzegor/ha-epson-projector-link` as a custom **Integration** repository.
3. If upstream is already installed, replace its HACS installation with this fork. Both use the `epson_projector_link` domain and must not be installed as separate integrations at the same time. Keep the existing Home Assistant integration entry and entity registry.
4. Download a tagged fork release and restart Home Assistant.
5. Open the existing Epson Projector Link entry's options. Set Host to the serial proxy URL, for example `socket://192.0.2.10:1234`.
6. Set the power-state polling interval to about **5 seconds**. Serial proxies are not assumed to deliver the network power notifications. Additional-property polling remains separately configurable.

When changing an existing connection, the options flow verifies the projector's serial number and refuses a different projector. Turn the projector on while configuring or validating additional properties. A failed validation is not saved.

HACS must track this fork rather than upstream. Installing upstream over this fork removes serial-proxy support; the serial URL would then stop working. Update only from this repository's published releases. There is no automatic upstream merge or dependency-update maintenance in this fork.

## Preserved functionality

The `epson_projector_link` domain, configured unique IDs, entities and existing actions remain the same. Supported capabilities depend on the projector:

- Power, warmup and cooldown state, with serialized commands.
- Input, color mode, brightness, audio and power-consumption controls.
- Lens/picture memory and optional lamp-hour/error attributes.
- `epson_projector_link.send_command` for ESC/VP21 commands.

Serial settings belong to the device server. Typical Epson RS-232 settings are 9600 baud, 8 data bits, no parity, one stop bit and no flow control; check the particular projector.

## Native LS500 controls

Choose the `ls500` model profile in the integration options to add:

| Control                                     | Native Home Assistant entity            |
| ------------------------------------------- | --------------------------------------- |
| HDMI1, HDMI2, HDMI3 and LAN inputs          | Existing media player's source selector |
| Cinema, Bright Cinema, Game, Dynamic        | Picture mode select                     |
| Auto, SDR, HDR10, HLG                       | HDR mode select                         |
| Picture brightness / black level            | Number slider, raw Epson scale 0–255    |
| A/V screen blanking                         | Switch                                  |
| Bright Cinema / Cinema shortcut             | Switch                                  |
| Automatic HDR following actual picture mode | Switch                                  |
| Light-source usage                          | Diagnostic sensor in hours              |

All entities share the projector's existing serial identity and TCP connection. Polling and explicit readback update the controls from reported values. Picture brightness is **not laser output**. The LS500 command profile and ranges come from [Epson's LS500 control-code documentation](https://epson.com/Support/Projectors/LS-Series/Epson-LS500B/s/SPT_V11H956620).

Configure which picture modes allow Auto HDR in the options. When the automatic HDR switch is on, those modes use Auto and the other known LS500 picture modes use SDR. Unknown modes and an off/unreachable projector cause no write. Selecting an HDR mode manually turns automatic HDR off and persists that choice across reloads/restarts. Enable the automatic switch to return to that policy. This affects projector dynamic range; it does not configure external HDMI devices or EDIDs.

An optional control action can delegate picture, brightness and blanking requests to another controller. Select the delegated properties and supply a Home Assistant action sequence using the variables `property` (`CMODE`, `BRIGHT`, `MUTE`) and `value` (mode name, integer or boolean). Only explicit control requests run that action; feedback updates do not. Equal-value requests still run it, allowing a controller to cancel an in-flight opposite request. Existing low-level Epson actions remain direct transports so a controller can use them without recursion. No household controller IDs or scripts are bundled here.

`epson_projector_link.send_command` optionally returns the parsed device reply when called with response data requested. Existing calls that do not request a response keep working.

## Development and tests

The fork's additional regression tests use simulated raw serial proxies and ESC/VP.net servers. They cover fragmented replies, concurrency, queue recovery, invalid prompts, timeouts, reconnects, power-command acknowledgement, endpoint validation and wrong-projector protection. Fake endpoints never operate physical equipment.

Use Python 3.14 with the requirements in `requirements_test.txt`, then run:

```sh
python -m unittest discover -s tests -v
black --check custom_components tests
isort --check-only custom_components tests
flake8 custom_components tests
```

GitHub Actions also runs HACS and Hassfest checks. Passing automation does not establish long-term stability, mains-cycle recovery, standby wake or compatibility with every serial server.

## Rollback

Keep a backup of the original integration and config entry. Before returning to upstream, restore the projector's original network address and suitable polling settings. Replace the fork with the upstream HACS repository, restart Home Assistant and verify the existing entity and custom actions. Do not replace the full Home Assistant config-entry registry with an older snapshot.

## Provenance

Original project: [amosyuen/ha-epson-projector-link](https://github.com/amosyuen/ha-epson-projector-link). The upstream README credits [pszafer/epson_projector](https://github.com/pszafer/epson_projector), the [Home Assistant custom-component cookiecutter](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component) and [integration_blueprint](https://github.com/custom-components/integration_blueprint).
