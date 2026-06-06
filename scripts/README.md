# Database Backup & Disaster Recovery

## Overview

This directory contains automated backup scripts for PostgreSQL disaster recovery.

## Backup Script: `backup_db.sh`

### Features
- Automated daily backups using `pg_dump` with gzip compression
- Configurable backup retention (default: 7 days)
- Automatic rotation: deletes backups older than retention period
- Timestamped backup files: `backup_planfact_YYYYMMDD_HHMMSS.sql.gz`
- Stores backups in `var/backups/` directory
- Detailed logging with timestamps

### Configuration

Backups read database credentials from environment variables:
- `POSTGRES_DB` — Database name (default: `planfact`)
- `POSTGRES_USER` — Database user (default: `postgres`)
- `POSTGRES_PASSWORD` — Database password (required)
- `POSTGRES_HOST` — Database host (default: `localhost`)
- `POSTGRES_PORT` — Database port (default: `5432`)

These can be set in `.env` file or as system environment variables.

### Manual Backup

```bash
cd /path/to/project
bash scripts/backup_db.sh
```

### Automated Backups (Cron)

To schedule daily backups at 03:00 AM:

```bash
crontab -e
# Add line:
0 3 * * * cd /app && bash scripts/backup_db.sh >> logs/backup.log 2>&1
```

Verify installation:
```bash
crontab -l
```

## Disaster Recovery: Restore Database

### One-Command Restore

To restore database from a backup file:

```bash
gunzip -c var/backups/backup_planfact_YYYYMMDD_HHMMSS.sql.gz | psql -h localhost -U postgres -d planfact
```

Replace placeholders:
- `YYYYMMDD_HHMMSS` — Backup timestamp
- `-h localhost` — Database host
- `-U postgres` — Database user
- `-d planfact` — Database name

### Step-by-Step Restore

1. **Verify backup file exists:**
   ```bash
   ls -lh var/backups/backup_planfact_*.sql.gz
   ```

2. **Drop existing database (⚠️ WARNING: irreversible):**
   ```bash
   psql -h localhost -U postgres -c "DROP DATABASE planfact;"
   ```

3. **Create fresh database:**
   ```bash
   psql -h localhost -U postgres -c "CREATE DATABASE planfact;"
   ```

4. **Restore from backup:**
   ```bash
   gunzip -c var/backups/backup_planfact_YYYYMMDD_HHMMSS.sql.gz | psql -h localhost -U postgres -d planfact
   ```

5. **Verify restore:**
   ```bash
   psql -h localhost -U postgres -d planfact -c "SELECT COUNT(*) FROM users;"
   ```

## Backup Retention

Backups older than 7 days are automatically deleted during each backup run.

To view current backups:
```bash
ls -lh var/backups/
```

To manually remove old backups:
```bash
find var/backups -name "backup_planfact_*.sql.gz" -mtime +7 -delete
```

## Monitoring

Check backup logs (if cron configured):
```bash
tail -f logs/backup.log
```

Expected log output:
```
[2026-05-31 03:00:01] Starting backup of database: planfact
[2026-05-31 03:00:15] Backup successful: var/backups/backup_planfact_20260531_030015.sql.gz (2.4M)
[2026-05-31 03:00:15] Cleaning up backups older than 7 days...
[2026-05-31 03:00:15] Backup completed successfully
```

## Troubleshooting

### "pg_dump: command not found"
Install PostgreSQL client tools:
```bash
apt-get install postgresql-client-15  # Ubuntu/Debian
brew install postgresql                # macOS
```

### "FATAL: password authentication failed"
Verify `POSTGRES_PASSWORD` is set correctly in `.env` or environment.

### "psql: ERROR: database planfact already exists"
Drop existing database first or restore to different database name.

## Best Practices

1. **Test restores regularly** — Verify backups work before disaster
2. **Monitor disk space** — Ensure `var/backups/` has sufficient free space
3. **Offsite backups** — Copy backups to S3/GCS/Azure for geographic redundancy
4. **Alert on failures** — Monitor cron logs for backup failures
5. **Document credentials** — Keep `.env` with database credentials secure
