# Added by Antigravity
export PATH="$HOME/.antigravity/antigravity/bin:$PATH"

# Machine-local helpers, tokens, and tunnel aliases live here instead of in git.
if [ -f "$HOME/.zshrc.local" ]; then
  source "$HOME/.zshrc.local"
fi

# Added by LM Studio CLI (lms)
export PATH="$PATH:$HOME/.lmstudio/bin"
# End of LM Studio CLI section

# Hermes Agent, ensure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"
alias sd-orange="$HOME/.local/bin/backup-orangepi-sd-card.sh"
alias sd-raspberry="$HOME/.local/bin/backup-raspberrypi-sd-card.sh"
alias claude-danger='claude --dangerously-skip-permissions'
alias codex-danger='codex --dangerously-bypass-approvals-and-sandbox'

ssh() {
  if [[ "${TERM:-}" == "xterm-ghostty" ]]; then
    TERM=xterm-256color command ssh "$@"
  else
    command ssh "$@"
  fi
}

# OpenClaw Completion
if [ -f "$HOME/.openclaw/completions/openclaw.zsh" ]; then
  source "$HOME/.openclaw/completions/openclaw.zsh"
fi

# Agnoster-style powerline prompt without pulling in a prompt framework.
autoload -Uz add-zsh-hook colors vcs_info
colors
setopt prompt_subst

zstyle ':vcs_info:*' enable git
zstyle ':vcs_info:git:*' check-for-changes true
zstyle ':vcs_info:git:*' stagedstr '+'
zstyle ':vcs_info:git:*' unstagedstr '!'
zstyle ':vcs_info:git:*' formats '%b%c%u'
zstyle ':vcs_info:git:*' actionformats '%b:%a%c%u'

_doves_prompt_segment() {
  local next_bg="$1"
  local fg="$2"
  local text="$3"

  if [[ -n "${DOVES_PROMPT_BG:-}" && "${DOVES_PROMPT_BG}" != "$next_bg" ]]; then
    DOVES_PROMPT+="%K{${next_bg}}%F{${DOVES_PROMPT_BG}}"
  else
    DOVES_PROMPT+="%K{${next_bg}}"
  fi

  DOVES_PROMPT+="%F{${fg}} ${text} "
  DOVES_PROMPT_BG="$next_bg"
}

_doves_prompt_end() {
  if [[ -n "${DOVES_PROMPT_BG:-}" ]]; then
    DOVES_PROMPT+="%k%F{${DOVES_PROMPT_BG}}%f "
  fi
}

_doves_prompt_precmd() {
  local exit_code=$?
  vcs_info

  DOVES_PROMPT=""
  DOVES_PROMPT_BG=""

  if [[ $exit_code -ne 0 ]]; then
    _doves_prompt_segment 160 231 "✘ ${exit_code}"
  fi

  if [[ $EUID -eq 0 ]]; then
    _doves_prompt_segment 220 16 "%n@%m"
  elif [[ -n "${SSH_CONNECTION:-}" || -n "${SSH_TTY:-}" ]]; then
    _doves_prompt_segment 31 231 "%n@%m"
  fi

  _doves_prompt_segment 33 231 "%~"

  if [[ -n "${vcs_info_msg_0_}" ]]; then
    local git_bg=70
    if [[ "${vcs_info_msg_0_}" == *[\!\+]* ]]; then
      git_bg=178
    fi
    _doves_prompt_segment "$git_bg" 16 " ${vcs_info_msg_0_}"
  fi

  _doves_prompt_end
  PROMPT="$DOVES_PROMPT"
}

add-zsh-hook precmd _doves_prompt_precmd

PROMPT='%K{33}%F{231} %~ %k%F{33}%f '
RPROMPT=''

# Login banner — interactive shells outside tmux only, so it shows once per
# terminal window instead of in every tmux pane. Reads cached state only
# (sysfs, the waybar usage cache, one local git call); no network or heavy
# spawns, so it stays near-instant.
if [[ -o interactive && -z "${TMUX:-}" ]]; then
  "$HOME/dotfiles/scripts/zsh-motd.sh" 2>/dev/null
fi

# bun completions
[ -s "/home/ben/.bun/_bun" ] && source "/home/ben/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"
