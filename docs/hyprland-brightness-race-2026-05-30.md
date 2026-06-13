# Hyprland Brightness Race Report - 2026-05-30

## Objective

Investigate and fix the screen brightness spike/flicker when Ben starts using
the laptop right as the Hyprland idle fade begins.

## Root Cause

`scripts/hypr-brightness-fade.sh` had no cancellation between concurrent fade
operations.

The 300-second `hypridle` listener starts:

```text
hypr-brightness-fade.sh dim 10 1.6
```

If the laptop is used while that fade-down process is still running, Hypridle
also starts:

```text
hypr-brightness-fade.sh restore 1.0
```

Before the fix, both shell processes could continue writing brightness values
at the same time: one ramping down toward 10%, the other ramping back up to the
saved level. That explains the apparent brightness "tweak out" right at the
idle boundary.

There was a second smaller race: `restore` deleted the saved-brightness file
before the restore fade finished. The Rust auto-brightness daemon uses that
file as its "idle dim is active" guard, so removing it early could allow another
brightness writer to resume before the restore completed.

## Fix

Changed `scripts/hypr-brightness-fade.sh` to use an operation token:

- every `dim`, `restore`, or `fade` writes a new `active-op` token
- each fade step checks that its token is still current
- older fade processes stop as soon as a newer operation starts
- `restore` now keeps `saved-brightness` until the restore fade completes
- stale/canceled operations do not delete another operation's token

## Follow-Up: First Wake Key Was Typing Into Apps

After the fade race was fixed, Ben reported that pressing a key to wake the
dimmed screen typed that key into the focused text field before he could see the
screen.

Fix:

- `config/hypr/hyprland.conf` defines an `idle-wake` submap.
- The submap has a `catchall` bind that resets to the normal submap and consumes
  the first key press.
- The battery-only 300-second `hypridle` dim hook enters `idle-wake` before
  dimming.
- The `hypridle` resume hook always resets the submap, so mouse wake cannot
  leave the session stuck in `idle-wake`.

Verification:

```text
Hyprland --verify-config -c /home/ben/.config/hypr/hyprland.conf
hyprctl binds -j shows catch_all=true in submap idle-wake
hyprctl dispatch submap idle-wake; hyprctl dispatch submap reset
hypridle restarted and is running
```

## Follow-Up: Waybar Tooltips

Ben reported that Waybar tooltips looked broken and wanted the time tooltip to
drop down a 12-hour clock.

Fix:

- `config/waybar/style.css` tooltip styling was simplified to an opaque dark
  tooltip with a normal border, 8px radius, and no text shadow/glass effects.
- `waybar-helper clock24` still displays 24-hour text in the bar, but its
  tooltip is now the 12-hour clock.
- The rebuilt helper was installed to `/home/ben/.local/bin/waybar-helper`.
- `waybar.service` was reloaded successfully.

Verification:

```text
/home/ben/.local/bin/waybar-helper clock24
{"text": "14:59", "tooltip": "2:59:21 pm"}
```

## Verification

Static check:

```text
bash -n scripts/hypr-brightness-fade.sh config/hypr/hypridle.conf
```

Controlled live race test:

```text
start_raw=158 start_pct=37 target_pct=33
start slow dim, wait 0.20s, run restore while dim is active
end_raw=158 delta=0
state_files_after=
```

No `saved-brightness` or `active-op` files were left behind after the test.

## Changed Files

```text
config/hypr/hypridle.conf
config/hypr/hyprland.conf
config/waybar/style.css
scripts/hypr-brightness-fade.sh
waybar-helper/src/main.rs
```

## Handoff Prompt

```text
/goal Read /home/ben/dotfiles/docs/hyprland-brightness-race-2026-05-30.md in full before doing anything. Verify the current brightness state, idle wake submap, and Waybar tooltip state first. The intended behavior is: only the newest brightness fade operation writes, the first keyboard wake from idle dim is consumed, and the 24-hour clock tooltip shows a 12-hour clock.
```
