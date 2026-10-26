#!/usr/bin/env bash
# What `orbit cloud up` puts on a target (Ubuntu): root on a DO box, or a sudo user on a host. Idempotent, re-run on
# every `up`. zsh + oh-my-zsh, node 22+, python 3.13+, uv, build tools. $WORK (from cloud.mjs) = the pack's parent dir.
# The login shell is changed only for root (a throwaway box); on a host `orbit cloud ssh` opens zsh without it.
# The pack itself (~/$WORK/orbit-sp1) is put there by cloud.mjs afterwards: it needs this Mac's agent and working tree.
set -euo pipefail
WORK=${WORK:-orbit-cloud}
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)
apt() { $SUDO env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o DPkg::Lock::Timeout=300 -qq "$@" >/dev/null 2>&1; }

if command -v cloud-init >/dev/null; then cloud-init status --wait >/dev/null 2>&1 || true; fi   # apt is busy until first boot is done
apt update
apt install -y zsh git rsync curl jq ripgrep tmux build-essential unzip ca-certificates python3 python3-venv

major() { node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/'; }
[ "$(major || echo 0)" -ge 22 ] 2>/dev/null || apt install -y nodejs npm || true
[ "$(major || echo 0)" -ge 22 ] 2>/dev/null || { curl -fsSL https://deb.nodesource.com/setup_22.x | $SUDO bash - >/dev/null; apt install -y nodejs; }

[ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' || command -v python3.13 >/dev/null || [ -x ~/.local/bin/python3.13 ] ||
  ~/.local/bin/uv python install 3.13 >/dev/null 2>&1

[ -d ~/.oh-my-zsh ] || RUNZSH=no CHSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended >/dev/null 2>&1
[ "$(id -u)" != 0 ] || [ "$(getent passwd root | cut -d: -f7)" = "$(command -v zsh)" ] || chsh -s "$(command -v zsh)" root
grep -q orbit-cloud ~/.zshenv 2>/dev/null || cat >>~/.zshenv <<EOF
# orbit-cloud
export PATH="\$HOME/.local/bin:\$HOME/$WORK/orbit-sp1/bin:\$PATH"
export ORBIT_HOME="\$HOME/$WORK/orbit-sp1"
EOF
grep -q orbit-cloud ~/.zshrc || echo 'PROMPT="%F{magenta}%m%f $PROMPT"  # orbit-cloud: show which box' >>~/.zshrc

# agent CLIs; auth stays a person's step (claude: /login, codex: codex login --device-auth), reported below
command -v claude >/dev/null || [ -x ~/.local/bin/claude ] || curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1
command -v codex >/dev/null || [ -x ~/.local/bin/codex ] || npm install -g --prefix ~/.local @openai/codex >/dev/null 2>&1

mkdir -p ~/"$WORK" ~/.claude/skills ~/.ssh
grep -q '^github.com' ~/.ssh/known_hosts 2>/dev/null || ssh-keyscan -t ed25519 github.com >>~/.ssh/known_hosts 2>/dev/null
py=$(command -v python3.13 || ls ~/.local/bin/python3.13 2>/dev/null || command -v python3)
echo "setup: $(zsh --version | cut -d' ' -f1-2), node $(node -v), $($py --version), uv $(~/.local/bin/uv --version | cut -d' ' -f2)"
export PATH="$HOME/.local/bin:$PATH"
ca=$(claude auth status 2>/dev/null | grep -q '"loggedIn": true' && echo "logged in" || echo "NOT logged in (ssh -t <target> claude, then /login)")
xa=$(codex login status >/dev/null 2>&1 && echo "logged in" || echo "NOT logged in (codex login --device-auth)")
echo "auth: claude $ca; codex $xa"
