#!/bin/sh
# Nightly SQLite snapshot of the parlay-tracker DB (named volume parlay_data).
# Run on the botserver host (cron):  15 3 * * *  ~/parlay-tracker/scripts/backup_db.sh
set -e
STAMP="$(date +%Y%m%d-%H%M%S)"
docker exec parlay-tracker python3 -c "
import sqlite3
s = sqlite3.connect('/data/parlay.db')
d = sqlite3.connect('/data/backups-parlay-$STAMP.db')
s.backup(d); d.close(); s.close()
print('backup written: /data/backups-parlay-$STAMP.db')
"
# prune backups older than 14 days inside the volume
docker exec parlay-tracker sh -c "find /data -name 'backups-parlay-*.db' -mtime +14 -delete" || true
