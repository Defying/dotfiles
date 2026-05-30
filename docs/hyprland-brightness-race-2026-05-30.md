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
scripts/hypr-brightness-fade.sh
```

## Handoff Prompt

```text
/goal Read /home/ben/dotfiles/docs/hyprland-brightness-race-2026-05-30.md in full before doing anything. Verify the current brightness state and recent idle logs first. The intended fix is that only the newest brightness fade operation writes, and restore keeps saved-brightness until the restore fade finishes.
```
