#!/usr/bin/env bash
#
# Installs (or removes) the daily backup as a launchd agent (#51).
#
#   backend/scripts/install-backup-schedule.sh --mirror ~/some/synced/folder
#   backend/scripts/install-backup-schedule.sh --uninstall
#
# launchd rather than the application's own scheduler: a backup that stops when
# the app stops is not much of a backup, and this has to survive a reboot and an
# afternoon when nobody started the stack.
set -euo pipefail

LABEL="com.trademind.backup"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$REPO_ROOT/backend/.venv/bin/python"
MIRROR="${BACKUP_MIRROR_DIR:-}"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL. Nothing takes a backup now; scripts/backup.py still works by hand."
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mirror) MIRROR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$PYTHON" ]] || { echo "No interpreter at $PYTHON — create the venv first." >&2; exit 1; }

if [[ -z "$MIRROR" ]]; then
  echo "WARNING: no --mirror given, so dumps will exist only on this machine."
  echo "         Losing the disk loses the backups with it (#51, criterion 4)."
else
  mkdir -p "$MIRROR"
  [[ -d "$MIRROR" ]] || { echo "Mirror $MIRROR is not a directory." >&2; exit 1; }
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_ROOT/backups"

# PATH is spelled out because launchd gives an agent almost none, and the first
# thing this script does is call docker. That omission is the classic way one of
# these appears installed and has never once run.
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO_ROOT/backend/scripts/backup.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
$( [[ -n "$MIRROR" ]] && printf '    <key>BACKUP_MIRROR_DIR</key><string>%s</string>\n' "$MIRROR" )
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
  <!-- A laptop is asleep at 03:00 more often than not, and a job that simply
       missed its window is how a backup silently never happens. -->
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$REPO_ROOT/backups/launchd.log</string>
  <key>StandardErrorPath</key><string>$REPO_ROOT/backups/launchd.log</string>
</dict>
</plist>
PLIST_END

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"

echo "Installed $LABEL — daily at 03:00."
echo
echo "Check it:      launchctl print gui/$UID/$LABEL | head -20"
echo "Run it now:    launchctl kickstart -p gui/$UID/$LABEL"
echo "Watch it:      tail -f $REPO_ROOT/backups/backup.log"
echo "Remove it:     backend/scripts/install-backup-schedule.sh --uninstall"
