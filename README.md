# L'eau d'Ile-de-France (SEDIF) for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=ha_eauidf&owner=timoptr)

Home Assistant custom integration for [L'eau d'Ile-de-France](https://connexion.leaudiledefrance.fr) (SEDIF) water consumption monitoring.

Fetches your water meter data from the SEDIF customer portal and exposes it as sensors in Home Assistant. Historical consumption is imported as **external statistics** with correct timestamps, making it fully compatible with the **Energy dashboard** for water tracking.

## Disclaimer

This integration relies on scraping the SEDIF customer portal. It is not based on an official API, so any change to the website's structure or authentication flow may break it without notice.

This integration was built with the help of [Claude](https://claude.ai) (Anthropic).

## Installation

### HACS (recommended)

Click the button to open your Home Assistant instance with this repository pre-filled:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=ha_eauidf&owner=timoptr)

Or add it manually:

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) > **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "L'eau d'Ile-de-France" and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/eauidf` folder into your Home Assistant `config/custom_components/` directory and restart.

## Removal

1. Go to **Settings > Devices & Services**
2. Find **L'eau d'Ile-de-France** and click the three dots menu
3. Click **Delete**
4. Restart Home Assistant

If installed via HACS, you can also uninstall the integration from HACS after removing the config entry.

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **L'eau d'Ile-de-France**
3. Enter your SEDIF portal email and password (the same credentials you use at [connexion.leaudiledefrance.fr](https://connexion.leaudiledefrance.fr))
4. The integration automatically discovers all contracts linked to your account

### Parameters

| Parameter | Required | Description |
|---|---|---|
| **Email** | Yes | The email address used to log in to the SEDIF customer portal at [connexion.leaudiledefrance.fr](https://connexion.leaudiledefrance.fr). |
| **Password** | Yes | The password for your SEDIF portal account. Stored locally in Home Assistant and only sent to the SEDIF portal for authentication. |

Each contract appears as a separate device named **SEDIF Contract {number}** (e.g. "SEDIF Contract XXXXXXX"), where the number matches your SEDIF contract reference.

## Entities

Each contract creates three sensors:

### Meter Reading (`sensor.sedif_contract_*_meter_reading`)

The cumulative water meter index, as read by your physical meter.

| Property | Value |
|---|---|
| **Unit** | m³ (cubic meters) |
| **Device class** | Water |
| **State class** | Total increasing |
| **Icon** | mdi:counter |

This value only goes up over time and represents the total water that has passed through your meter since installation. It corresponds to the number displayed on your physical water meter.

### Daily Consumption (`sensor.sedif_contract_*_daily_consumption`)

The amount of water consumed during the last reported day.

| Property | Value |
|---|---|
| **Unit** | L (liters) |
| **State class** | Measurement |
| **Icon** | mdi:water |

The water usage for the most recent day available from the SEDIF portal. This value is replaced each time new data is published (typically daily with a 1-2 day delay). A typical household uses between 100 and 300 liters per day.

### Last Reading Date (`sensor.sedif_contract_*_last_reading_date`)

The date of the most recent data available from the SEDIF portal. This is the date of the data itself, not when the integration last polled.

| Property | Value |
|---|---|
| **Device class** | Date |
| **Entity category** | Diagnostic |
| **Icon** | mdi:calendar-clock |

Useful to verify that the portal is providing fresh data. If this date stops advancing, it may indicate an issue on the SEDIF side.

### Extra attributes

The meter reading and daily consumption sensors expose the following additional attributes:

| Attribute | Description |
|---|---|
| `last_reading_date` | Date of the most recent reading (YYYY-MM-DD) |
| `is_estimated` | `true` if the value is an estimate rather than an actual meter reading |

## Energy Dashboard

The integration imports historical water consumption data as **external statistics** with correct timestamps, so the Energy dashboard attributes usage to the right day (not when the integration polled).

On first setup, up to **90 days** of history are imported. After that, only the last 7 days are fetched on each update cycle, keeping the statistics up to date without redundant API calls. Estimated readings are excluded from statistics to avoid double-counting when the actual value is published later.

Two external statistics are created per contract:

| Statistic | Unit | Description |
|---|---|---|
| **SEDIF {contract_number} water consumption** | m³ | Cumulative water consumption (for Energy Dashboard water tracking) |
| **SEDIF {contract_number} water cost** | EUR | Cumulative water cost based on the average price per m³ reported by the SEDIF portal |

To add water tracking:

1. Go to **Settings > Dashboards > Energy**
2. In the **Water consumption** section, click **Add water source**
3. Search for **SEDIF {contract_number} water consumption** — this is the external statistic created by the integration

The water cost statistic can be used in custom cards or automations. It uses the average price per cubic meter provided by the SEDIF portal to compute daily costs.

> **Important:** Use the external statistic, not the sensor entities. The **Meter Reading** sensor updates every 6 hours and timestamps data at poll time, which causes consumption to appear on the wrong day. The external statistic uses the actual date reported by SEDIF, so the Energy dashboard shows accurate daily breakdowns.

## Data updates

The integration polls the SEDIF portal every **6 hours**. Each update fetches daily consumption records and:

- Updates the sensor entities with the latest values
- Imports the records as external statistics into the recorder (used by the Energy dashboard)

Water consumption data on the portal typically updates once per day with a 1-2 day delay, so more frequent polling is unnecessary.

If your credentials expire, Home Assistant will prompt you to re-authenticate through the integration's configuration page.

## Requirements

- A SEDIF customer account at [connexion.leaudiledefrance.fr](https://connexion.leaudiledefrance.fr)
- Home Assistant 2026.3.0 or newer (might work on older version but not tested)
- The [pyeauidf](https://github.com/TimoPtr/pyeauidf) Python library ([PyPI](https://pypi.org/project/pyeauidf/), installed automatically)

## Multi-contract support

If your account has multiple active contracts (e.g. multiple properties), each contract gets its own device with its own set of sensors. All contracts are fetched in a single update cycle. If one contract fails to fetch, the others will still update — check **Settings > System > Logs** and filter by `eauidf` for details.

## Troubleshooting

- **"Invalid credentials"** — Verify you can log in at [connexion.leaudiledefrance.fr](https://connexion.leaudiledefrance.fr) with the same email/password
- **"No active contracts"** — Your account exists but has no active water contracts associated with it
- **"Unable to connect"** — The SEDIF portal may be temporarily down; check again later
- **Sensors show "unavailable"** — A temporary API error occurred; the integration will retry on the next 6-hour cycle
