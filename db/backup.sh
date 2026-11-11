#!/bin/sh
# pg_dump of the app database: once when the container starts, then every 24 hours.
# Keeps 14 days of dumps in /backups (the ./backups folder on the host, git-ignored).
# Restore: pg_restore --clean --if-exists -d "$PGDATABASE" /backups/<file>.dump
set -u
while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  file="/backups/tradehub_${stamp}.dump"
  if pg_dump -Fc -f "${file}.tmp"; then
    mv "${file}.tmp" "${file}"
    echo "backup ok: ${file}"
    find /backups -name 'tradehub_*.dump' -mtime +14 -delete
  else
    rm -f "${file}.tmp"
    echo "BACKUP FAILED at ${stamp}" >&2
  fi
  sleep 86400
done
