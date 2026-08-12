# Foxtron DALI Home Assistant Integration

This custom component integrates Foxtron DALI gateways (DALInet, DALI2net) into Home Assistant: DALI lights become `light` entities, DALI-2 buttons (e.g. Foxtron DALI4SW) fire events and device triggers, and physical wall switches can be paired as first-class Home Assistant devices.

It communicates with the gateway over its proprietary ASCII/TCP protocol. The protocol is documented in the [`docs`](custom_components/foxtron_dali/docs) directory, with a corrected summary in [`protocol_spec.md`](custom_components/foxtron_dali/docs/protocol_spec.md).

## Features

*   **Light discovery & control** — scans the bus at startup, exposes each control gear as a brightness-capable `light` entity (its own HA device, assignable to an area), and adds new lights on demand via the `scan_for_lights` service.
*   **Per-light fade time** — each light gets a `Fade time` select (config category) that mirrors the fade time stored in the ballast NVM and writes it only on an explicit change, verified by readback.
*   **Push-based state updates** — light entities decode 16-bit frames observed on the bus (DAPC levels, OFF, RECALL MAX, broadcast DAPC) and update without polling. Note: this only applies to frames sent by *other* DALI masters; the gateway reports the integration's own commands separately.
*   **Button events & gestures** — DALI4SW modules send only raw `pressed`/`released` notifications; the integration reconstructs `short_press`, `double_press`, `triple_press` and `long_press_start/repeat/stop` in software with configurable timing.
*   **Switch pairing & native device triggers** — a 5-minute pairing mode turns physical rocker switches into Home Assistant devices with `upper`/`lower` × press-type device triggers usable directly in the automation UI.
*   **Robust connection handling** — a supervisor task owns each TCP connection: exponential-backoff reconnect (1 s → 60 s), `ConfigEntryNotReady` when the gateway is offline at startup (HA retries setup on its own), and a keep-alive watchdog that detects silently dead connections (unplugged cable) within ~1 minute. Lights show as `unavailable` while their gateway is unreachable and refresh their state on reconnect.
*   **Multi-gateway / multi-bus** — one config entry per DALI bus; DALI2net exposes two buses (TCP ports 23 and 24).

## Supported Hardware

*   **Foxtron DALInet** — single DALI bus to Ethernet gateway.
*   **Foxtron DALI2net** — dual DALI bus to Ethernet gateway.
*   **Foxtron DALI4SW** — 4-channel DALI-2 button interface.
*   Other DALI control gear and DALI-2 input devices should work; only the hardware above is verified.

## Installation

1.  Add this repository to [HACS](https://hacs.xyz/) as a custom repository (type: Integration). HACS installs the latest GitHub Release; updates and version rollbacks are done through HACS ("Redownload" lets you pick a version).
2.  Alternatively, copy `custom_components/foxtron_dali` into `<config>/custom_components/` manually.
3.  Restart Home Assistant.
4.  **Settings → Devices & Services → Add Integration**, search for "Foxtron DALI".
5.  Enter the gateway IP and port — `23` for the first DALI bus, `24` for the second (DALI2net). Repeat for each bus.

## Lights

Lights are discovered automatically when the integration starts and named `light.dali_light_<address>` (rename them freely — entity IDs and unique IDs are stable). Since 0.8.0 every light is its own HA device (linked to the bus device): assign each to a room area for area-based control. After upgrading from an older release the new devices start without an area — assign them once.

If you add new gear to the bus later, call `foxtron_dali.scan_for_lights` — each bus is rescanned and only newly found addresses are added.

While a gateway is unreachable its lights are `unavailable`; they recover automatically (including a fresh level query) when the connection returns.

### Fade time

Each light device carries a **Fade time** select with the 16 DALI fade codes (`No fade`, `0.7 s`, … `90.5 s`). The value lives in the ballast NVM — it survives HA restarts and power outages on its own. The entity only *mirrors* it: it reads the ballast on startup and reconnect, writes on an explicit change (verified by readback; a mismatch raises an error and shows the actual value), and never pushes state on restart. Values set by external commissioning tools are preserved (and picked up on the next restart/reconnect).

Recommended values: lights dimmed by held buttons ≤ `0.7 s` (a longer fade fights the button repeat interval and feels rubbery); scene/navigation lights `1.4–2.8 s`. Each write is one NVM cycle in the ballast — fine for daily automation profiles, not for per-motion-event changes.

## Buttons

Every DALI-2 input notification is exposed on a per-bus `DALI Button Events` `event` entity, and simultaneously fired on the HA event bus as `foxtron_dali_button_event`:

```yaml
automation:
  - alias: "Turn on kitchen light with DALI button"
    trigger:
      - platform: event
        event_type: foxtron_dali_button_event
        event_data:
          bus_id: "192.168.1.50_23"
          address: 56
          instance_number: 1
          press_type: "short_press"
    action:
      - service: light.toggle
        target:
          entity_id: light.kitchen_light
```

`press_type` is one of: `button_pressed`, `button_released`, `short_press`, `double_press`, `triple_press`, `long_press_start`, `long_press_repeat`, `long_press_stop`. Gesture timing (long-press threshold, repeat interval, multi-press window) is configurable in the integration options.

## Switch Pairing (DALI4SW)

Physical rocker switches can be registered as Home Assistant devices:

1.  Open the integration options (**CONFIGURE**) → **Start Button Pairing**. Pairing mode runs for 5 minutes (a persistent notification shows the state).
2.  On the physical switch, press **Upper** and then **Lower** within 5 seconds. The integration pairs the two instances of that DALI address and creates a device.
3.  The new device offers native **device triggers**: `upper`/`lower` × `short_press`, `double_press`, `triple_press`, `long_press_start`, `long_press_repeat`, `long_press_stop` — pick them directly in the automation editor. Each trigger also fires `foxtron_dali_button_action` on the event bus with `device_id`, `flap` and `press_type`.
4.  To remove a paired switch, call `foxtron_dali.remove_paired_switch` with its device ID.

## Services

All services are global (they act on every configured bus).

| Service | Description |
|---------|-------------|
| `foxtron_dali.scan_for_lights` | Rescan all buses and add newly discovered lights. |
| `foxtron_dali.remove_paired_switch` | Remove a paired DALI switch device (`device_id`). |

> **Removed in 0.7.3:** `broadcast_on`, `broadcast_off` and `set_fade_time`.
> Use `light.turn_on`/`light.turn_off` targeting an area or group instead of
> the broadcasts. Fade time is a per-light setting since 0.8.0 (see above).
> Earlier releases wrote the DALI *fade rate* instead of the fade time (wrong
> opcode); on first start after upgrading, the integration restores the fade
> rate of all ballasts to the DALI default (7) once.

## Options

Opened via **CONFIGURE** on any entry; options are applied to **all** configured buses:

*   **Start Button Pairing** — see above.
*   **Reload All Buses** — reload every config entry at once.
*   **Set Event Timing** — long-press threshold, long-press repeat interval and multi-press window (seconds).

## Reliability Notes

*   Gateway offline at HA startup (e.g. power-outage recovery where HA boots faster than the gateway): setup raises `ConfigEntryNotReady` and HA keeps retrying until the gateway appears — no manual reload needed.
*   Connection lost at runtime: the supervisor reconnects with exponential backoff. A silently dead connection (cable pulled — TCP black hole) is detected by the keep-alive watchdog within ~50–70 s.
*   The DALI2net accepts only **one TCP master per port** — don't run other control software against the same bus port while HA is connected.

## Development

```bash
uv run --group test pytest      # test suite (incl. fake-gateway reconnect tests)
uv run --group dev pre-commit run --all-files
```

Releases follow a strict flow: bump `manifest.json` version → tag `vX.Y.Z` → GitHub Release (HACS deploys only from releases). Release notes include verification steps and the rollback target.

## Contributing

This integration is developed for a single household installation, but issues and pull requests are welcome.
