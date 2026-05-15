# L'eau d'Ile-de-France (SEDIF) for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=ha_eauidf&owner=timoptr)

Home Assistant custom integration for [L'eau d'Ile-de-France](https://connexion.leaudiledefrance.fr) (SEDIF) water consumption monitoring.

Fetches your water meter data from the SEDIF customer portal and exposes it as sensors in Home Assistant — compatible with the **Energy dashboard** for water tracking.

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

Each contract appears as a separate device named **SEDIF Contract {number}** (e.g. "SEDIF Contract 9235380"), where the number matches your SEDIF contract reference.

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

For long-term water tracking and the Energy dashboard, use the **Meter Reading** sensor instead — it accumulates over time and HA computes the differences automatically.

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

Both sensors are compatible with the Home Assistant Energy dashboard:

1. Go to **Settings > Dashboards > Energy**
2. In the **Water consumption** section, click **Add water source**
3. Select the **Meter Reading** sensor (recommended — it uses `total_increasing` which works best with the Energy dashboard's statistics)

## Data updates

The integration polls the SEDIF portal every **6 hours**. Water consumption data on the portal typically updates once per day with a 1-2 day delay, so more frequent polling is unnecessary.

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
