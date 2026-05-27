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

# Hermes Agent, ensure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"
alias sd-orange="$HOME/.local/bin/backup-orangepi-sd-card.sh"
alias sd-raspberry="$HOME/.local/bin/backup-raspberrypi-sd-card.sh"
alias claude-danger='claude --dangerously-skip-permissions'
alias codex-danger='codex --dangerously-bypass-approvals-and-sandbox'

# OpenClaw Completion
if [ -f "$HOME/.openclaw/completions/openclaw.zsh" ]; then
  source "$HOME/.openclaw/completions/openclaw.zsh"
fi

# Fancy but lightweight prompt.
autoload -Uz add-zsh-hook colors vcs_info
colors
setopt prompt_subst

zstyle ':vcs_info:*' enable git
zstyle ':vcs_info:git:*' check-for-changes true
zstyle ':vcs_info:git:*' stagedstr '●'
zstyle ':vcs_info:git:*' unstagedstr '✚'
zstyle ':vcs_info:git:*' formats '%F{183}⎇ %b%f %F{214}%c%u%f'
zstyle ':vcs_info:git:*' actionformats '%F{183}⎇ %b|%a%f %F{214}%c%u%f'

_doves_prompt_precmd() {
  local exit_code=$?
  vcs_info

  if [[ $exit_code -eq 0 ]]; then
    export DOVES_PROMPT_STATUS=""
    export DOVES_PROMPT_ARROW="%F{78}❯%f"
  else
    export DOVES_PROMPT_STATUS="%F{160}exit ${exit_code}%f"
    export DOVES_PROMPT_ARROW="%F{160}❯%f"
  fi

  local jobs_count
  jobs_count=$(jobs -p 2>/dev/null | wc -l | tr -d ' ')
  if [[ -n "$jobs_count" && "$jobs_count" != "0" ]]; then
    export DOVES_PROMPT_JOBS=" %F{215}⚙ ${jobs_count}%f"
  else
    export DOVES_PROMPT_JOBS=""
  fi

  if [[ -n "${vcs_info_msg_0_}" ]]; then
    export DOVES_PROMPT_GIT=" ${vcs_info_msg_0_}"
  else
    export DOVES_PROMPT_GIT=""
  fi
}

add-zsh-hook precmd _doves_prompt_precmd

PROMPT='%F{183}╭─%f %B%F{225}%n%f%b %B%F{189}%~%f%b${DOVES_PROMPT_GIT}${DOVES_PROMPT_JOBS}
%F{183}╰─%f${DOVES_PROMPT_STATUS:+ ${DOVES_PROMPT_STATUS}} ${DOVES_PROMPT_ARROW} '
RPROMPT=''
