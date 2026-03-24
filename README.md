# RCB Availability Monitor + Checkout Flow

Selenium-based availability monitor for the RCB shop with optional **Scrapling stealth browser** support for anti-detection. Uses **dynamic page analysis** to detect purchase buttons, product options, forms, and payment fields automatically — no hardcoded selectors for the main flow. Works across merch pages, ticket pages, and any future page layout changes.

**This is for personal educational/testing use only. No automated purchase is completed — the script pauses at UPI request so you confirm payment manually.**

## How It Works

The core of the script is a `PageAnalyzer` that scans every visible button, link, and input on the page, scoring them by semantic keyword match:

- **Purchase buttons**: scored by keywords like "add to bag", "add to cart", "buy now", "book tickets", "reserve", etc. Highest-scoring clickable element wins.
- **Product options**: detects labeled groups (Size, Category, Color) by scanning text near interactive elements, then selects based on your `.env` preferences (or picks the first option).
- **Checkout navigation**: finds "proceed", "continue", "checkout", "view cart" buttons dynamically.
- **Form fields**: detects name/email/phone inputs by their placeholder, label, and attribute patterns.
- **UPI payment**: finds UPI tabs/radios and VPA input fields by keyword matching.
- **Negative signals**: recognizes "sold out", "coming soon", "notify me" etc. and correctly reports unavailability.
- **Adaptive fallback** (optional): when keyword scoring finds nothing, [Scrapling](https://github.com/D4Vinci/Scrapling)'s `Adaptor` parses the page HTML and uses similarity-based element relocation as a last resort, then maps the result back to a clickable element.
- **Stealth browser workers** (optional): parallel booking workers can use Scrapling's `StealthyFetcher` (Camoufox/Playwright) instead of raw Selenium, making bot detection much harder.

This means if the site changes button text from "ADD TO BAG" to "BUY NOW" or adds new option selectors, the script adapts automatically.

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

This installs Scrapling with Camoufox support automatically. If you don't need the stealth browser features, everything still works without it — Scrapling is imported optionally.

## Configuration

Copy the example and fill in your values:

```powershell
Copy-Item .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `MONITOR_MODE` | `test-merch` | `test-merch` or `live-tickets` (only affects default URL) |
| `TARGET_URL` | (auto per mode) | `test-merch` → merch product page · `live-tickets` → `https://www.royalchallengers.com/fixtures` |
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
| `USE_STEALTH_BROWSER` | `0` | Use Scrapling/Camoufox stealth browser for parallel workers (`1`/`0`) |

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

### Ticket mode (`live-tickets`)

Set `MONITOR_MODE=live-tickets` and the bot targets the **RCB fixtures page** (`https://www.royalchallengers.com/fixtures`) by default. Each poll it:

1. Scans all links on the page, scoring them by ticket/booking keywords and known partner domains (BookMyShow, Paytm Insider, insider.in, etc.)
2. If a same-domain ticket page is found (e.g. `/tickets/rcb-vs-mi`) — navigates there and checks for a purchase button automatically
3. If the best link leads to a **3rd party partner** (BookMyShow etc.) — plays an alert, opens the page, and hands control to you for manual booking
4. If no ticket links found yet — logs "not available" and waits for the next poll

Set a custom `TARGET_URL` in `.env` if RCB releases a dedicated ticket page URL before you run the bot.



- `winsound` is a Windows-only stdlib module used for siren alerts.
- The script **does not complete payment** — it sends the UPI collect request then waits for ENTER.
- Login selectors are the only semi-hardcoded XPaths (login pages are consistent). All purchase/checkout/form detection is fully dynamic.

## Scrapling Integration (Optional)

The script optionally uses [Scrapling](https://github.com/D4Vinci/Scrapling) for two features:

### Stealth Browser for Parallel Workers

Set `USE_STEALTH_BROWSER=1` to have parallel booking workers (workers 1–6 in `live-tickets` mode) use Scrapling's `StealthyFetcher` backed by Camoufox instead of raw Selenium+ChromeDriver. This patches canvas/WebGL fingerprints, navigator properties, and WebDriver detection flags.

- **Worker 0** (main) always uses Selenium for maximum compatibility
- **Workers 1–N** use the stealth browser when enabled
- Falls back to Selenium automatically if stealth setup fails
- No Chrome profile copies needed for stealth workers (lighter on disk/CPU)

### Adaptive Element Fallback

Always active when Scrapling is installed (no env var needed). When the keyword-scoring engine in `PageAnalyzer` can't find a purchase or checkout button:

1. Scrapling's `Adaptor` parses the full page HTML
2. Searches for buttons/links matching purchase/checkout keywords via CSS selectors
3. Maps the best match back to a real WebElement by ID or XPath
4. Returns it so the normal click/checkout flow continues

This is purely a **fallback** — the existing keyword scorer always runs first. If Scrapling isn't installed, the fallback is silently skipped.
