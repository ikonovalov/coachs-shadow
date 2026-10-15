#!/bin/sh
# Консистентная копия SQLite-БД бота (безопасна при работающем процессе); хранит последние 14 копий.
set -eu
APP_DIR="${APP_DIR:-/opt/coachs-shadow}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/coachs-shadow}"
mkdir -p "$BACKUP_DIR"
sqlite3 "$APP_DIR/coachs_shadow.db" ".backup '$BACKUP_DIR/coachs_shadow-$(date +%F).db'"
ls -1t "$BACKUP_DIR"/coachs_shadow-*.db | tail -n +15 | xargs -r rm --
