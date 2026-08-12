#!/usr/bin/env bash
# Guided setup. Everything optional is asked, nothing is assumed.
#
#   bash setup.sh
#
# Safe to re-run — it skips whatever is already done.

set -uo pipefail
cd "$(dirname "$0")"

B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; O=$'\033[0m'
ok()   { echo "  ${G}✓${O} $1"; }
warn() { echo "  ${Y}!${O} $1"; }
step() { echo; echo "${B}$1${O}"; }

echo
echo "${B}Hermes fleet — setup${O}"
echo "${D}Nothing here costs money except the Anthropic API key, which is pennies.${O}"

# ---------------------------------------------------------------- python

step "1. Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "  ${R}✗${O} No python3 found."
  echo "    Chromebook: enable Linux (Settings → Advanced → Developers)."
  echo "    Or use Google Cloud Shell, which already has it: shell.cloud.google.com"
  exit 1
fi
ok "$(python3 --version)"

# ---------------------------------------------------------------- deps

step "2. Installing what it needs"
if [ ! -d .venv ]; then
  python3 -m venv .venv 2>/dev/null || {
    echo "  ${R}✗${O} Could not create a virtualenv. Try: sudo apt install python3-venv"
    exit 1; }
  ok "created .venv"
else
  ok ".venv already there"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python3 -m pip install --quiet --upgrade pip
if python3 -m pip install --quiet -r requirements.txt; then
  ok "packages installed"
else
  warn "some packages failed — the calendar bits may not work, the rest will"
fi

# ---------------------------------------------------------------- api key

step "3. Anthropic API key"
echo "${D}  This is the only thing that costs money. A conversation is a few cents.${O}"
echo "${D}  Get one at: console.anthropic.com → API keys${O}"

if [ -f .env ] && grep -q ANTHROPIC_API_KEY .env; then
  ok "already saved in .env"
else
  echo
  read -rsp "  Paste your key (hidden, then press Enter): " KEY
  echo
  if [ -z "${KEY}" ]; then
    warn "skipped — Bailey and Danbury need this to talk. Re-run setup.sh later."
  else
    printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > .env
    chmod 600 .env
    ok "saved to .env (this file is gitignored)"
  fi
fi
[ -f .env ] && set -a && . ./.env && set +a

# ---------------------------------------------------------------- check

step "4. Checking the guardrails"
echo "${D}  No API key needed for this — it proves the safety rules work.${O}"
echo
python3 -m hermes drill 2>&1 | tail -20

# ---------------------------------------------------------------- calendar

step "5. Google Calendar — optional, skip it for now"
echo "  Bailey (what to work on) and Danbury (does this fit the plan) work"
echo "  without it. Only Holt needs it, and setting it up takes ~30 minutes"
echo "  of clicking through Google Cloud."
echo
echo "${D}  When you want it: docs/PHASE1.md${O}"

# ---------------------------------------------------------------- done

step "Ready"
if [ -f .env ]; then
  cat <<EOF
  Start talking to them:

      ${B}source .venv/bin/activate && python3 -m hermes chat${O}

  Try:
      bailey, I have 40 minutes — what should I do?
      bailey, I have 3 hours and good energy
      danbury, Farm School wants another workshop for \$900 — worth it?
      danbury, should I take on another Tardus responsibility?

  ${D}Holt will say his calendar is fixtures until you do step 5.${O}
EOF
else
  echo "  Add an API key (re-run ${B}bash setup.sh${O}) to talk to them."
  echo "  Meanwhile ${B}python3 -m hermes board${O} shows your tasks."
fi
echo
