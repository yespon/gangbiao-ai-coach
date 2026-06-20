#!/usr/bin/env bash
# backup_db.sh — PostgreSQL full backup + file backup with 7-day rotation
# Usage: ./scripts/backup_db.sh [--no-rotate]
# Cron example: 0 3 * * * /app/scripts/backup_db.sh >> /app/logs/backup.log 2>&1

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"

# Parse DATABASE_URL from .env
# Format: postgresql+asyncpg://user:password@host:port/dbname
# pg_dump needs: postgresql://user:password@host:port/dbname
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC1090
    DATABASE_URL="$(grep -E '^DATABASE_URL=' "$PROJECT_DIR/.env" | head -1 | cut -d= -f2-)"
fi
DATABASE_URL="${DATABASE_URL:-}"

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not found in .env and not set in environment" >&2
    exit 1
fi

# Convert asyncpg URL to standard psycopg2 URL for pg_dump
PG_URL="$(echo "$DATABASE_URL" | sed 's|+asyncpg||')"

# Extract components for pg_dump (avoid exposing full URL in ps output)
PGHOST="$(echo "$PG_URL" | sed -E 's|.*@([^:/]+).*|\1|')"
PGPORT="$(echo "$PG_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')"
PGUSER="$(echo "$PG_URL" | sed -E 's|.*://([^:]+):.*|\1|')"
PGDB="$(echo "$PG_URL" | sed -E 's|.*/([^?]+).*|\1|')"
PGPASSWORD="$(echo "$PG_URL" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')"

export PGPASSWORD

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
TODAY_DIR="$BACKUP_DIR/$(date +%Y%m%d)"

mkdir -p "$TODAY_DIR"

# ── 1. Database dump ──────────────────────────────────────────────────────────
echo "[$(date -Iseconds)] Starting database backup: $PGDB @ $PGHOST:$PGPORT"

DUMP_FILE="$TODAY_DIR/db_${TIMESTAMP}.dump"

if pg_dump \
    --host="$PGHOST" \
    --port="${PGPORT:-5432}" \
    --username="$PGUSER" \
    --dbname="$PGDB" \
    --format=custom \
    --verbose \
    --file="$DUMP_FILE"; then

    SIZE="$(du -h "$DUMP_FILE" | cut -f1)"
    echo "[$(date -Iseconds)] Database backup OK: $DUMP_FILE ($SIZE)"
else
    echo "[$(date -Iseconds)] ERROR: pg_dump failed" >&2
    rm -f "$DUMP_FILE"
    exit 1
fi

# ── 2. File backups ───────────────────────────────────────────────────────────
# Uploads directory
if [ -d "$PROJECT_DIR/uploads" ]; then
    UPLOADS_ARCHIVE="$TODAY_DIR/uploads_${TIMESTAMP}.tar.gz"
    echo "[$(date -Iseconds)] Backing up uploads/..."
    tar czf "$UPLOADS_ARCHIVE" -C "$PROJECT_DIR" uploads/
    SIZE="$(du -h "$UPLOADS_ARCHIVE" | cut -f1)"
    echo "[$(date -Iseconds)] Uploads backup OK: $UPLOADS_ARCHIVE ($SIZE)"
fi

# TLS certificates
if [ -d "$PROJECT_DIR/deploy/nginx/certs" ]; then
    CERTS_ARCHIVE="$TODAY_DIR/certs_${TIMESTAMP}.tar.gz"
    echo "[$(date -Iseconds)] Backing up TLS certificates..."
    tar czf "$CERTS_ARCHIVE" -C "$PROJECT_DIR/deploy/nginx" certs/
    SIZE="$(du -h "$CERTS_ARCHIVE" | cut -f1)"
    echo "[$(date -Iseconds)] Certs backup OK: $CERTS_ARCHIVE ($SIZE)"
fi

# .env configuration
if [ -f "$PROJECT_DIR/.env" ]; then
    ENV_BACKUP="$TODAY_DIR/env_${TIMESTAMP}.bak"
    cp "$PROJECT_DIR/.env" "$ENV_BACKUP"
    chmod 600 "$ENV_BACKUP"
    echo "[$(date -Iseconds)] .env backup OK: $ENV_BACKUP"
fi

# ── 3. Rotation (delete backups older than RETAIN_DAYS) ───────────────────────
if [ "${1:-}" != "--no-rotate" ]; then
    DELETED_COUNT=0
    while IFS= read -r dir; do
        if [ -d "$dir" ]; then
            echo "[$(date -Iseconds)] Rotating: $dir"
            rm -rf "$dir"
            DELETED_COUNT=$((DELETED_COUNT + 1))
        fi
    done < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -mtime +"$RETAIN_DAYS")

    if [ "$DELETED_COUNT" -gt 0 ]; then
        echo "[$(date -Iseconds)] Rotated $DELETED_COUNT old backup(s)"
    else
        echo "[$(date -Iseconds)] No old backups to rotate"
    fi
fi

# ── 4. Summary ────────────────────────────────────────────────────────────────
echo "[$(date -Iseconds)] Backup complete. Contents of $TODAY_DIR:"
ls -lh "$TODAY_DIR/" 2>/dev/null || true

unset PGPASSWORD
