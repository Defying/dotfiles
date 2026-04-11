# dotfiles

Ben's local shell and prompt setup, kept in git.

## tracked

- `~/.zshenv`
- `~/.zprofile`
- `~/.zshrc`
- `~/.gitconfig`
- `~/.config/motd/omens-motd.sh`

## not tracked

Keep machine-only and secret stuff out of the repo.

- `~/.zshrc.local`
- tokens
- private hostnames and tunnels you do not want synced
- anything else that is specific to one box

## install

```bash
./install.sh
exec zsh -l
```

The installer backs up existing non-symlink files into `backups/<timestamp>/` before linking the repo into `$HOME`.

## local overrides

Your live machine-specific shell helpers stay in `~/.zshrc.local`.
A starter example lives at `zsh/.zshrc.local.example`.
