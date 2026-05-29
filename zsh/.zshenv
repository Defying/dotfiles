# Ensure user-local tools are available in every zsh shell, including
# non-login shells used by wrappers, SSH commands, and launchers.
typeset -U path PATH
path=(${path:#/opt/homebrew/opt/node/bin})
path=(${path:#$HOME/.local/lib/node_modules/@openai/codex*})
[[ -d /opt/homebrew/bin ]] || path=(${path:#/opt/homebrew/bin})
[[ -d /opt/homebrew/sbin ]] || path=(${path:#/opt/homebrew/sbin})
path=($HOME/.local/bin $HOME/.bun/bin $path)
[[ -d /opt/homebrew/bin ]] && path=(/opt/homebrew/bin $path)
[[ -d /opt/homebrew/sbin ]] && path=(/opt/homebrew/sbin $path)
export PATH
