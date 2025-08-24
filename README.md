# Foxtron DALI Home Assistant Integration

This custom component integrates Foxtron DALI gateways (DALInet, DALI2net) into Home Assistant, allowing you to control DALI lights and react to DALI-2 button events.

It communicates with the Foxtron DALI gateways using a proprietary ASCII-based protocol over TCP/IP. The protocol is documented in the [`docs`](custom_components/foxtron_dali/docs) directory of this repository, with a detailed summary in [`protocol_spec.md`](custom_components/foxtron_dali/docs/protocol_spec.md).

## Features

*   **Light Discovery & Control:** Automatically discovers DALI control gear (lights) on the bus. They appear as standard Home Assistant `light` entities, supporting brightness control.
*   **Push-Based State Updates:** Light entities listen for DALI commands on the bus and update instantly without polling.
*   **Button Event Discovery & Integration:** Discovers DALI-2 input devices (like the Foxtron DALI4SW) when they are pressed. These are exposed as `event` entities for use in automations.
*   **Global Services:** Provides services to send broadcast commands (`broadcast_on`, `broadcast_off`) to all lights on a DALI bus.
*   **Configurable Fade Time:** Allows setting the DALI fade time via the integration options or a service call.
*   **Multi-Gateway Support:** Supports both single-bus (DALInet) and dual-bus (DALI2net) gateways.

## Supported Hardware

*   **Foxtron DALInet:** Single DALI bus to Ethernet gateway.
*   **Foxtron DALI2net:** Dual DALI bus to Ethernet gateway.
*   **Foxtron DALI4SW:** 4-channel DALI-2 button interface.
*   Other DALI and DALI-2 compliant control gear (lights) and input devices (buttons, sensors).

## Installation

1.  Ensure you have a working Home Assistant installation.
2.  Copy the `custom_components/foxtron_dali` directory from this repository into your `<config>/custom_components/` directory, or add this repository to [HACS](https://hacs.xyz/) as a custom repository.
3.  Restart Home Assistant.
4.  Go to **Settings > Devices & Services** and click the **+ ADD INTEGRATION** button.
5.  Search for "Foxtron DALI" and select it.

## Configuration

### Initial Setup

When you first add the integration, you will be prompted for the following:

*   **Host:** The IP address of your Foxtron DALI gateway.
*   **Port:** The TCP port for the DALI bus you want to connect to. This is typically `23` for the first DALI bus and `24` for the second bus on a DALI2net.

### Options

After setup, you can adjust additional settings by clicking **CONFIGURE** on the integration card:

*   **Default Fade Time:** Sets the default DALI fade time (0-15) for all lights on this bus.
*   **Discovered Buttons:** This section is used to manage and add newly discovered DALI buttons.

## Usage

### Controlling Lights

DALI lights are discovered automatically when the integration starts. They will appear as standard `light` entities in Home Assistant, named like `light.dali_light_0`, `light.dali_light_1`, etc., where the number is the DALI short address.

You can control these lights like any other Home Assistant light entity in your dashboard, scenes, and automations.

The integration listens for brightness commands on the DALI bus. When a light level changes—either directly or via broadcast—the corresponding `light` entity updates immediately. No polling or additional configuration is required.

### Using DALI Buttons

Unlike lights, DALI buttons are not actively scanned. They are discovered when they send an event. The process is as follows:

**1. Discovery**

To discover a new button, you must physically press it. When you do, the button sends an "Input Notification" event on the DALI bus, which the integration detects.

**2. Adopting the Button**

Once a new button is detected, a persistent notification will appear in your Home Assistant dashboard.

> **New DALI buttons discovered**
> The Foxtron DALI integration has discovered new buttons. Please go to the integration's configuration to add them.

To add the button:
1.  Go to **Settings > Devices & Services**.
2.  Find the Foxtron DALI integration card and click **CONFIGURE**.
3.  A list of newly discovered button IDs (formatted as `address-instance`) will be shown.
4.  Select the button(s) you wish to add and click **Submit**.

**3. Using the Button in Automations**

Adding a button creates an `event` entity in Home Assistant. This entity does not have a state (like on/off) but acts as a source for events in your automations.

You can trigger automations using the `dali_event` event type.

Here is an example automation that turns on a light with a short press of a DALI button:

```yaml
automation:
  - alias: "Turn on kitchen light with DALI button"
    trigger:
      - platform: event
        event_type: dali_event
        event_data:
          # Unique ID of the integration entry and button identifier
          unique_id: "YOUR_CONFIG_ENTRY_ID"
          button_id: "56-1"
          # This is the specific button action you want to react to.
          event_type: "short_press"
    action:
      - service: light.toggle
        target:
          entity_id: light.kitchen_light
```

The `event_type` in the trigger can be any of the following standard DALI-2 event names:
*   `button_pressed`
*   `button_released`
*   `short_press`
*   `double_press`
*   `triple_press`
*   `long_press_start`
*   `long_press_repeat`
*   `long_press_stop`

Foxtron DALI4SW devices are configured to send only the `button_pressed` and
`button_released` notifications on the bus. The integration reconstructs all
other button events locally based on timing. These thresholds (long press
delay, repeat interval, and multi-press window) can be adjusted in the
integration's configuration options. Any other button events from the bus are
ignored.

## Services

This integration provides several global services to control all lights on a DALI bus simultaneously.

#### `foxtron_dali.broadcast_on`

Turns on all lights on all configured DALI buses to their maximum level.

#### `foxtron_dali.broadcast_off`

Turns off all lights on all configured DALI buses.

#### `foxtron_dali.set_fade_time`

Sets the DALI fade time for all devices on all configured DALI buses.

| Field       | Description                             | Example |
|-------------|-----------------------------------------|---------|
| `fade_time` | A DALI fade code from 0 to 15.          | `7`     |

#### `foxtron_dali.export_names`

Exports a mapping of DALI addresses to entity names, areas, and device
identifiers. The mapping is written to a JSON file that can be restored
later.

**Domain:** `foxtron_dali`  
**Service:** `export_names`

**Parameters**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | No | File path relative to the Home Assistant configuration directory. Defaults to `foxtron_dali_names.json`. |

**Example**

```yaml
service: foxtron_dali.export_names
data:
  path: foxtron_dali_names.json
```

#### `foxtron_dali.import_names`

Reads a previously exported JSON file and restores entity names, areas, and
device names.

**Domain:** `foxtron_dali`  
**Service:** `import_names`

**Parameters**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | No | File path of the JSON mapping relative to the Home Assistant configuration directory. Defaults to `foxtron_dali_names.json`. |

**Example**

```yaml
service: foxtron_dali.import_names
data:
  path: foxtron_dali_names.json
```

## Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request if you have any improvements or bug fixes.
