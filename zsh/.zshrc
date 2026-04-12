export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node/bin:$PATH"

# Added by Antigravity
export PATH="$HOME/.antigravity/antigravity/bin:$PATH"

# Machine-local helpers, tokens, and tunnel aliases live here instead of in git.
if [ -f "$HOME/.zshrc.local" ]; then
  source "$HOME/.zshrc.local"
fi

# Added by LM Studio CLI (lms)
export PATH="$PATH:$HOME/.lmstudio/bin"
# End of LM Studio CLI section

. "$HOME/.local/bin/env"

# Hermes Agent, ensure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"
alias sd-orange="$HOME/.local/bin/backup-orangepi-sd-card.sh"
alias sd-raspberry="$HOME/.local/bin/backup-raspberrypi-sd-card.sh"

# Omen login banner
if [ -f "$HOME/.config/motd/omens-motd.sh" ]; then
  source "$HOME/.config/motd/omens-motd.sh"
fi

# OpenClaw Completion
if [ -f "$HOME/.openclaw/completions/openclaw.zsh" ]; then
  source "$HOME/.openclaw/completions/openclaw.zsh"
fi

# Minimal prompt, readable git info, no heavy theme machinery.
autoload -Uz add-zsh-hook colors vcs_info
colors
setopt prompt_subst

zstyle ':vcs_info:*' enable git
zstyle ':vcs_info:git:*' check-for-changes true
zstyle ':vcs_info:git:*' stagedstr ' +'
zstyle ':vcs_info:git:*' unstagedstr ' !'
zstyle ':vcs_info:git:*' formats ' %F{244}on%f %F{81}%b%f%c%u'
zstyle ':vcs_info:git:*' actionformats ' %F{244}on%f %F{81}%b|%a%f%c%u'

_doves_prompt_precmd() {
  vcs_info
}

add-zsh-hook precmd _doves_prompt_precmd

PROMPT='%F{39}%2~%f${vcs_info_msg_0_}
%(?.%F{70}›%f.%F{160}›%f) '
RPROMPT='%(?..%F{160}exit %?%f )%F{244}%*%f'
