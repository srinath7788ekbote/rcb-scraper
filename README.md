# RCB Availability Monitor + Checkout Flow

Selenium-based availability monitor for the RCB shop. Uses **dynamic page analysis** to detect purchase buttons, product options, forms, and payment fields automatically — no hardcoded selectors for the main flow. Works across merch pages, ticket pages, and any future page layout changes.

**This is for personal educational/testing use only. No automated purchase is completed — the script pauses at UPI request so you confirm payment manually.**

## How It Works

The core of the script is a `PageAnalyzer` that scans every visible button, link, and input on the page, scoring them by semantic keyword match:

- **Purchase buttons**: scored by keywords like "add to bag", "add to cart", "buy now", "book tickets", "reserve", etc. Highest-scoring clickable element wins.
- **Product options**: detects labeled groups (Size, Category, Color) by scanning text near interactive elements, then selects based on your `.env` preferences (or picks the first option).
- **Checkout navigation**: finds "proceed", "continue", "checkout", "view cart" buttons dynamically.
- **Form fields**: detects name/email/phone inputs by their placeholder, label, and attribute patterns.
- **UPI payment**: finds UPI tabs/radios and VPA input fields by keyword matching.
- **Negative signals**: recognizes "sold out", "coming soon", "notify me" etc. and correctly reports unavailability.

This means if the site changes button text from "ADD TO BAG" to "BUY NOW" or adds new option selectors, the script adapts automatically.

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

Copy the example and fill in your values:

```powershell
Copy-Item .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `MONITOR_MODE` | `test-merch` | `test-merch` or `live-tickets` (only affects default URL) |
| `TARGET_URL` | (auto per mode) | Page to monitor |
| `CHECK_INTERVAL` | `120` | Seconds between polls |
| `QUANTITY` | `4` | Number of items/tickets |
| `NAMES` | (4 names) | Comma-separated attendee/guest names |
| `UPI_VPA` | (your VPA) | UPI VPA for payment |
| `CONTACT_EMAIL` | (empty) | Email for guest forms |
| `CONTACT_PHONE` | (empty) | Phone for guest forms & login OTP |
| `MERCH_SIZE` | `L` | Preferred size (S/M/L/XL/XXL) |
| `MERCH_CATEGORY` | (empty) | Preferred category (Women/Men/blank) |
| `ENABLE_NOTIFICATIONS` | `1` | Desktop notifications (`1`/`0`) |
| `MANUAL_SEAT_TIMEOUT` | `60` | Seconds to wait for manual seat pick |
| `OTP_TIMEOUT` | `90` | Seconds to wait for OTP entry |
| `MAX_RETRIES` | `3` | Checkout retry attempts |
| `CHROME_PROFILE_DIR` | `chrome_profile` | Chrome profile for session persistence |
| `SCREENSHOT_DIR` | `screenshots` | Screenshot output folder |
| `LOG_FILE` | `monitor.log` | Log file path |

## Usage

```powershell
python monitor.py
```

The script opens Chrome, navigates to `TARGET_URL`, and polls every `CHECK_INTERVAL` seconds. When a purchase button appears:

1. Detects and selects product options (size, category, etc.)
2. Sets quantity
3. Clicks the purchase button
4. Handles seat maps if present (auto-select adjacent seats, manual fallback)
5. Navigates to cart
6. Proceeds to checkout
7. Fills name/email/phone forms
8. Selects UPI and enters VPA
9. Plays a loud siren alert and waits for you to confirm

### Login handling

If the site redirects to a login page, the script:
- Enters your `CONTACT_PHONE` automatically
- Sends OTP and plays an alert beep
- Waits for you to enter the OTP in the browser (or type it manually within `OTP_TIMEOUT` seconds)
- Session persists via Chrome profile, so you only login once

### Seat map support

If a venue/stadium seat map is detected:
1. Auto-selects adjacent available seats (groups by parent element, picks consecutive runs)
2. Falls back to manual selection with an alert if auto-select can't fill `QUANTITY`
3. Continues to checkout after seat selection

## Artifacts

- `monitor.log` — full run log.
- `screenshots/*.png` — timestamped screenshots at every step.

## Notes

- `winsound` is a Windows-only stdlib module used for siren alerts.
- The script **does not complete payment** — it sends the UPI collect request then waits for ENTER.
- Login selectors are the only semi-hardcoded XPaths (login pages are consistent). All purchase/checkout/form detection is fully dynamic.
