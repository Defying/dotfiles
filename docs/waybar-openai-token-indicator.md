# Waybar Codex Usage Indicator

## Current Implementation

The left side of Waybar includes `custom/codex-tokens`, backed by:

- `config/waybar/config.jsonc`
- `scripts/waybar-openai-tokens.py`
- `scripts/quick-settings-panel.py`

The bubble is now a Codex subscription usage indicator. It does not read Codex
logs, Codex SQLite databases, shell history, local usage files, OpenAI API keys,
or `~/.config/openai/*`.

The script checks local Codex login status:

```sh
codex login status
```

Then it asks the installed Codex CLI app-server for the ChatGPT subscription
rate-limit snapshot:

```text
account/rateLimits/read
```

Display states:

- `codex N%`: current Codex subscription window usage.
- `codex limit`: Codex reports a limit or credit exhaustion state.
- `codex login`: Codex CLI does not report a usable login status.
- `codex ?`: Codex is logged in but the rate-limit request failed.

Clicking the bubble opens the quick settings panel.

## Subscription Usage

Codex subscription usage is part of the ChatGPT/Codex plan surface, not the
OpenAI platform Usage API. OpenAI's Codex Help Center article says Codex usage
limits depend on the ChatGPT plan and should be checked in the Codex usage page
or the limit banner. The Codex pricing page links the current usage dashboard at:

```text
https://chatgpt.com/codex/settings/usage
```

Quick settings has a Codex Usage section with:

- `Usage`: opens the Codex usage dashboard.
- `Codex`: opens Codex web.
- `Pricing`: opens Codex pricing.
- `Login`: starts `codex login` in a terminal so Codex can run its browser login
  flow.
- `Status`: refreshes the panel's Codex usage status.

There is no API-key prompt, no clipboard key saver, and no local token dialog.

## Why The API Usage Version Was Removed

The previous version called:

```text
https://api.openai.com/v1/organization/usage/completions
```

That endpoint reports platform API usage. It is useful for API billing and
project/key accounting, but it is not the ChatGPT/Codex subscription usage
counter the Waybar bubble is meant to show. It also required API/admin keys,
which is the wrong auth surface for this setup.

The working implementation uses Codex's own local app-server protocol instead.
That keeps the widget on the same ChatGPT-authenticated subscription surface as
the CLI, instead of mixing subscription usage with platform API billing.

## Calendar Plan

The middle clock now has a `cal -3` tooltip and opens `korganizer` on click. A
fuller calendar indicator should be a separate popup script, not packed into the
clock text:

- Read events from Akonadi/KOrganizer or an ICS file.
- Show the next 3 events in the tooltip.
- On click, open a small fuzzel/GTK agenda menu with Today, Tomorrow, and This
  Week.
- Keep Waybar text short so the center bubble stays stable.

## Quick Settings Panel

The left gear icon launches `scripts/waybar-quick-settings.sh`, which opens
`scripts/quick-settings-panel.py` as a GTK layer-shell panel. It currently
supports:

- Wi-Fi toggle
- Bluetooth toggle
- Output mute toggle
- Brightness slider
- Volume slider
- Codex usage/login links
- Audio device picker
- Network settings
- Sound settings
- Waybar reload
- Hyprland reload
- Lock

If GTK layer-shell fails, the launcher falls back to a fuzzel menu with the same
core actions.

Next useful upgrades:

- Add microphone mute, idle inhibit, VPN, and power profile controls.
- Add status rows for current Wi-Fi SSID, Bluetooth device, and active audio
  output.
- Add the same liquid-glass shader treatment used by the weather popup.
