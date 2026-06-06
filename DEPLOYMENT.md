# Next-Gen PlanFact — Production Deployment Guide

## CI/CD Pipeline Overview

Automated deployment via GitHub Actions:
1. **Lint & Test** — Python syntax check + pytest
2. **Build & Push** — Docker image to GitHub Container Registry
3. **Deploy** — SSH to VPS, pull image, run migrations, health check
4. **Notifications** — Slack alerts on success/failure

## GitHub Secrets Configuration

Set these secrets in Repository Settings → Secrets and variables → Actions:

### Docker Registry (GitHub Container Registry)
- `GITHUB_TOKEN` — Automatically available, no setup needed

### VPS Deployment
```
VPS_HOST           = your-vps.example.com
VPS_USER           = deploy-user
VPS_SSH_KEY        = (private SSH key for deployment user)
VPS_PORT           = 22 (or custom SSH port)
```

### Notifications (Optional)
```
SLACK_WEBHOOK_URL  = https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

## Local VPS Setup

### 1. Create deployment user
```bash
sudo useradd -m -s /bin/bash deploy-user
sudo usermod -aG docker deploy-user
```

### 2. Generate SSH key for GitHub Actions
```bash
ssh-keygen -t ed25519 -C "github-actions" -N "" -f /tmp/github-deploy-key
# Copy output of: cat /tmp/github-deploy-key
# Add to: Repository → Settings → Secrets → VPS_SSH_KEY
```

### 3. Add public key to VPS
```bash
mkdir -p ~/.ssh
cat /tmp/github-deploy-key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### 4. Prepare application directory on VPS
```bash
sudo mkdir -p /app/planfact
sudo chown deploy-user:deploy-user /app/planfact
cd /app/planfact

# Initialize git repo or clone
git clone https://github.com/your-org/next-gen-planfact.git .

# Create .env.prod with production secrets
cat > .env.prod <<EOF
ENVIRONMENT=production
DATABASE_URL=postgresql://postgres:PASSWORD@db:5432/planfact
POSTGRES_DB=planfact
POSTGRES_USER=postgres
POSTGRES_PASSWORD=PASSWORD
SECRET_KEY=your-secret-key
SENTRY_DSN=https://...
CORS_ORIGINS=https://yourdomain.com
EOF
chmod 600 .env.prod
```

### 5. Create backup directory
```bash
mkdir -p var/backups logs
docker-compose -f docker-compose.prod.yml run --rm db mkdir -p /backups
```

### 6. Initial deployment (manual)
```bash
cd /app/planfact
docker-compose -f docker-compose.prod.yml up -d
docker-compose -f docker-compose.prod.yml exec web alembic upgrade head
```

## Automated Deployment Process

### Trigger: Push to `main` branch
```bash
git push origin main
```

### Pipeline execution (GitHub Actions):
1. **Test Job** (5-10 min)
   - Checks out code
   - Sets up Python 3.11
   - Runs linting (flake8)
   - Runs tests (pytest) if available
   - Artifacts: Build log, test results

2. **Build & Push Job** (10-15 min, needs test success)
   - Builds Docker image with buildx
   - Pushes to ghcr.io with tags: `latest`, `main-SHA`, `git-hash`
   - Caches layers for faster future builds
   - Artifacts: Image URL, metadata

3. **Deploy Job** (5-10 min, needs build success)
   - SSH to VPS
   - Pulls latest code: `git pull origin main`
   - Logs into registry: `docker login ghcr.io`
   - Pulls latest image: `docker pull ghcr.io/.../next-gen-planfact:latest`
   - Starts services: `docker-compose up -d --build web`
   - **Applies migrations:** `docker exec planfact-api alembic upgrade head`
   - Health check: `curl http://localhost:8000/health`
   - Notifies Slack on success/failure

## Monitoring Deployment

### View workflow status
- GitHub: Repository → Actions → Latest run
- Real-time logs: Click on job → View logs

### View application logs on VPS
```bash
# Web service logs
docker-compose -f docker-compose.prod.yml logs -f web

# Database logs
docker-compose -f docker-compose.prod.yml logs -f db

# Combined
docker-compose -f docker-compose.prod.yml logs -f
```

## Rollback Procedure

If deployment fails or introduces bugs:

### Quick rollback (previous Docker image)
```bash
cd /app/planfact

# Get previous image tag
docker images | grep ghcr.io/next-gen-planfact

# Switch to previous version
docker tag ghcr.io/next-gen-planfact:previous ghcr.io/next-gen-planfact:current
docker-compose -f docker-compose.prod.yml up -d web

# Verify
docker-compose -f docker-compose.prod.yml logs -f web
```

### Database rollback (if migrations failed)
```bash
# List available migrations
docker exec planfact-api alembic history

# Downgrade to specific revision
docker exec planfact-api alembic downgrade base

# Re-apply specific migration
docker exec planfact-api alembic upgrade <revision-id>
```

## Security Best Practices

1. **SSH Key Rotation** — Rotate VPS_SSH_KEY quarterly
2. **Secrets Scanning** — Enable GitHub secret scanning
3. **Protected Branch** — Require reviews before merge to main
4. **Audit Log** — Monitor GitHub Actions audit events
5. **Monitoring** — Alert on failed deployments (Slack webhooks)
6. **Backups** — Daily automated backups (see scripts/backup_db.sh)

## Environment Variables Reference

### Production (.env.prod on VPS)
```
ENVIRONMENT=production
DATABASE_URL=postgresql://postgres:PASSWORD@db:5432/planfact
POSTGRES_DB=planfact
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<secure-password>
POSTGRES_HOST=db
POSTGRES_PORT=5432
SECRET_KEY=<random-string-32+chars>
SENTRY_DSN=https://key@sentry.io/project-id
CORS_ORIGINS=https://yourdomain.com,https://api.yourdomain.com
```

## Troubleshooting

### "Docker login failed"
- Verify `GITHUB_TOKEN` has `packages:write` permission
- Check GitHub Actions permissions in org/repo settings

### "Connection refused" after deploy
- Check health endpoint: `curl http://VPS:8000/health`
- View logs: `docker-compose logs web`
- Verify .env.prod DATABASE_URL is correct

### "Migration failed"
- Check DB connectivity: `docker-compose exec db psql -U postgres -c "SELECT 1"`
- View migration history: `docker exec planfact-api alembic history`
- Check migration files: `ls app/migrations/versions/`

### "SSH key permission denied"
- Verify authorized_keys: `cat ~/.ssh/authorized_keys`
- Check permissions: `chmod 600 ~/.ssh/authorized_keys`
- Test manually: `ssh -i key.pem deploy-user@VPS_HOST`

## Next Steps

1. Configure GitHub Secrets (see section above)
2. Set up VPS (see Local VPS Setup)
3. Push to `main` branch to trigger first deployment
4. Monitor in GitHub Actions → Workflows
5. Verify on VPS: `curl https://yourdomain.com/health`
