# Ensure Homebrew and user-local tools are available in every zsh shell,
# including non-login shells used by wrappers, SSH commands, and launchers.
typeset -U path PATH
path=(/opt/homebrew/bin /opt/homebrew/sbin /opt/homebrew/opt/node/bin $HOME/.local/bin $path)
export PATH
