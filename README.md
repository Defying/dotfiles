# dotfiles

Ben's local shell and prompt setup, kept in git.

## current layout

- `zsh/` -> links into `$HOME`
- `git/` -> links into `$HOME`
- `config/` -> links into `$HOME/.config`
- `home/` -> links into `$HOME` for anything else you want to archive later

## not tracked

Keep machine-only and secret stuff out of the repo.

- `~/.zshrc.local`
- `.env*`
- private keys, cert bundles, keychains, sqlite/db files
- `.cloudflared/`
- anything with tokens or credentials in it

## install

```bash
./install.sh
exec zsh -l
```

The installer backs up existing non-symlink files into `backups/<timestamp>/` before linking the repo into `$HOME`.

## Hyprland on Fedora Asahi

The Hyprland setup keeps Plasma installed as the default fallback and documents
the rollback path for this MacBook Air M1:

- `docs/hyprland-quickstart.md` has the current login, keybinding, and rollback card.
- `docs/2026-05-22-hyprland-asahi-migration.md` records the backup/snapshot anchors.
- `hypr-readiness` is the read-only audit before any real login-screen attempt.
- `Hyprland (Recovery Terminal)` is the terminal-first test session.
- `Plasma (Rollback Hyprland)` disables `~/.config/hypr` from the login screen.

Hyprland implementation rule: anything newly built for this desktop should be
Rust-based by default. Use shell/Python/Lua only as glue around existing tools,
for one-off migrations, or where the runtime must be embedded in another
program.

## auto sync

Use `scripts/auto-sync.sh` to keep this repo archived to GitHub.

What it does:

- stages updates, deletions, and new non-ignored files
- blocks common secret file types and obvious token/key material
- creates a timestamped `chore: sync dotfiles (...)` commit
- pushes to `origin main`

If it sees something secret-looking, it aborts instead of pushing.

## agent handoff

Use `handoff` before a Codex or Claude Code session runs low on context or
limits. It writes a Markdown report under `docs/`, then prints a compact
`/goal` prompt that tells the next agent to read that file first.

See `docs/agent-context-handoff.md`.

## local overrides

Your live machine-specific shell helpers stay in `~/.zshrc.local`.
A starter example lives at `zsh/.zshrc.local.example`.
