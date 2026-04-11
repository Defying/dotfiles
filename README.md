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

## auto sync

Use `scripts/auto-sync.sh` to keep this repo archived to GitHub.

What it does:

- stages updates, deletions, and new non-ignored files
- blocks common secret file types and obvious token/key material
- creates a timestamped `chore: sync dotfiles (...)` commit
- pushes to `origin main`

If it sees something secret-looking, it aborts instead of pushing.

## local overrides

Your live machine-specific shell helpers stay in `~/.zshrc.local`.
A starter example lives at `zsh/.zshrc.local.example`.
