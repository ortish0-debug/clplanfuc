#!/bin/bash
# PostgreSQL Backup Script with Auto-Rotation
# Backs up database to gzip archive and deletes backups older than 7 days

set -e

# Load .env if exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Configuration
DB_NAME="${POSTGRES_DB:-planfact}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASSWORD="${POSTGRES_PASSWORD}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="var/backups"
RETENTION_DAYS=7

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Generate timestamp for backup file
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/backup_planfact_${TIMESTAMP}.sql.gz"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting backup of database: $DB_NAME"

# Perform backup using pg_dump
PGPASSWORD="$DB_PASSWORD" pg_dump \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-password \
  | gzip > "$BACKUP_FILE"

# Verify backup file created and has content
if [ -s "$BACKUP_FILE" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup successful: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Backup file is empty or missing"
  exit 1
fi

# Rotate backups: delete files older than RETENTION_DAYS
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleaning up backups older than $RETENTION_DAYS days..."
DELETED_COUNT=$(find "$BACKUP_DIR" -name "backup_planfact_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
if [ "$DELETED_COUNT" -gt 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deleted $DELETED_COUNT old backup(s)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup completed successfully"
