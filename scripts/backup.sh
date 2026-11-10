#!/usr/bin/env bash
# Back up CNTRL's durable state with rclone — a thin wrapper over
# `python -m backend.cli backup` (backend/backup.py does the work: rclone sync of
# memory/projects/agents/skills, a consistent SQLite snapshot, and secrets only
# through an rclone crypt layer). Run by cntrl-backup.timer, or by hand.
# Configure the remote in the GUI (PUT /api/backup/config) or with
# CNTRL_BACKUP_REMOTE in ~/.config/cntrl/env; unconfigured = a quiet no-op.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m backend.cli backup --if-configured "$@"
