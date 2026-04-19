# 🚀 TAG Implementation - Docker Setup & Domain Switching Guide

**Last Updated**: 2026-04-19  
**Status**: Production Ready

---

## 📋 Quick Start

```bash
# 1. Navigate to project
cd /home/deepakrajb/Desktop/MD/TAG-Implementation

# 2. Copy environment template
cp .env.example .env

# 3. Update .env with your values (see Configuration section)

# 4. Start all services
docker-compose up -d

# 5. Verify services
docker-compose ps
```

---

## ⚙️ How .env File Works (IMPORTANT)

### Current Setup

The `docker-compose.yml` **EXPLICITLY** reads from `.env` file:

```yaml
tag_backend:
  env_file:
    - .env              # ← Loads ALL variables from .env
  environment:
    DATABASE_URL: ${DATABASE_URL_DOCKER}    # ← Uses variables from .env
    FITS_DATABASE_URL_DOCKER: ${FITS_DATABASE_URL_DOCKER}
    VTS_DATABASE_URL_DOCKER: ${VTS_DATABASE_URL_DOCKER}
    DOMAIN: ${DOMAIN}   # ← This comes from .env!
```

### What This Means

✅ **YES**, Docker is reading from `.env`  
✅ **YES**, all variables are injected into containers  
✅ **YES**, `.env` is the single source of truth  
✅ **NO**, you don't hardcode values in Dockerfile  
✅ **NO**, you don't hardcode values in docker-compose.yml  

---

## 🔄 Switching from VTS to FITS

### Step 1: Update `.env` File

**Current (VTS):**
```bash
# Domain Configuration
DOMAIN=vts
DEFAULT_CHAT_APP_ID=vts
APPS_CONFIG_PATH=./config/apps.local.yaml
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot
```

**Change to (FITS):**
```bash
# Domain Configuration
DOMAIN=fits_dev_march_9           # ← CHANGE THIS
DEFAULT_CHAT_APP_ID=fits_dev_march_9  # ← CHANGE THIS
APPS_CONFIG_PATH=./config/apps.local.yaml
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot
```

### Step 2: Rebuild Docker Image (CRITICAL!)

```bash
# STOP all running containers
docker-compose down

# REBUILD with new environment
docker-compose up --build -d

# Verify rebuild
docker-compose logs -f tag_backend | head -50
```

### Step 3: Verify FITS is Running

```bash
# Check logs for FITS domain loaded
docker-compose logs tag_backend | grep -i "fits"

# Expected output:
# [INFO] Loading domain: fits_dev_march_9
# [INFO] Database URL: mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot
```

### Step 4: Test FITS Chatbot

```bash
# Wait for services to be healthy (check Status column)
docker-compose ps

# Test API endpoint
curl -X GET http://localhost:8012/health

# Test FITS query
curl -X POST http://localhost:8012/chat/query \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Show pending tasks",
    "domain": "fits_dev_march_9"
  }'
```

---

## 📁 Configuration Files

### `.env` (Main Configuration)

Location: `/home/deepakrajb/Desktop/MD/TAG-Implementation/.env`

**Key Variables:**

```bash
# ========== DOMAIN SELECTION ==========
DOMAIN=fits_dev_march_9              # Switch between: vts | fits_dev_march_9 | ims
DEFAULT_CHAT_APP_ID=fits_dev_march_9 # Should match DOMAIN

# ========== DATABASE URLS ==========
# Local Development (when running outside Docker)
FITS_DATABASE_URL=mysql+aiomysql://root:12345@localhost:3306/remp-chat-bot
VTS_DATABASE_URL=mysql+aiomysql://debian-sys-maint:password@localhost:3306/VTS

# Docker Development (when running inside Docker)
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot
VTS_DATABASE_URL_DOCKER=mysql+aiomysql://debian-sys-maint:password@host.docker.internal:3306/VTS

# ========== DOCKER PORTS ==========
TAG_BACKEND_PORT=8012               # Backend: http://localhost:8012
CHATBOT_DEMO_PORT=5174              # Frontend: http://localhost:5174
REDIS_HOST_PORT=6384                # Redis: localhost:6384

# ========== LLM CONFIGURATION ==========
LLM_BASE_URL_DOCKER=http://host.docker.internal:11434/v1
LLM_MODEL=llama-3.3-70b-versatile

# ========== REDIS ==========
REDIS_URL=redis://redis:6379/0

# ========== APP MODE ==========
ASSISTANT_FLOW_MODE=yaml            # Options: mutation | hybrid | yaml
LOG_LEVEL=INFO
```

### `config/apps.local.yaml` (Domain Definitions)

Location: `/home/deepakrajb/Desktop/MD/TAG-Implementation/config/apps.local.yaml`

Defines all available domains:
- `vts` - Vehicle Tracking System
- `fits_dev_march_9` - Facility & Asset Management

**Each domain has:**
- Database URL template
- Allowed tables
- Mutation permissions
- Company ID filters

---

## 🐳 Docker Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   docker-compose.yml                    │
│  Reads from: .env (via env_file: - .env)               │
└─────────────────────────────────────────────────────────┘
         │
         ├─── tag_backend (Python/FastAPI)
         │    └─ Loads .env variables
         │    └─ Initializes domain: ${DOMAIN}
         │    └─ Connects to: ${FITS_DATABASE_URL_DOCKER}
         │    └─ Listens on: :${TAG_BACKEND_PORT}
         │
         ├─── chatbot_demo (React/Vite)
         │    └─ Frontend for testing
         │    └─ Ports on: ${CHATBOT_DEMO_PORT}
         │
         └─── redis (Cache/Sessions)
              └─ Cache server for backend
              └─ Ports on: ${REDIS_HOST_PORT}
```

---

## 🔍 Troubleshooting

### Issue: Backend still using VTS after changing .env

**Solution:**
```bash
# Just changing .env is NOT enough
# You MUST rebuild the Docker image

docker-compose down          # Stop containers
docker-compose up --build -d # Rebuild + Start
```

### Issue: Backend can't connect to database

**Check your .env:**
```bash
# If running Docker locally:
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot

# If MySQL is in Docker network:
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@mysql:3306/remp-chat-bot
```

### Issue: Wrong domain loaded

**Verify in logs:**
```bash
docker-compose logs tag_backend | grep -i domain

# Should show: "Loading domain: fits_dev_march_9"
```

---

## ✅ Complete Demo Setup

### Prerequisites

```bash
# 1. Local MySQL running
mysql -u root -p12345 -h localhost -e "SHOW DATABASES LIKE 'remp-chat-bot';"

# 2. LLM Server running (Ollama or similar)
curl http://localhost:11434/api/tags

# 3. Docker & Docker Compose installed
docker --version
docker-compose --version
```

### Full Setup Process

```bash
# 1. Clone/navigate to repo
cd /home/deepakrajb/Desktop/MD/TAG-Implementation

# 2. Copy .env template
cp .env.example .env

# 3. Edit .env for FITS
cat > .env << 'EOF'
DOMAIN=fits_dev_march_9
DEFAULT_CHAT_APP_ID=fits_dev_march_9
FITS_DATABASE_URL_DOCKER=mysql+aiomysql://root:12345@host.docker.internal:3306/remp-chat-bot
TAG_BACKEND_PORT=8012
CHATBOT_DEMO_PORT=5174
REDIS_HOST_PORT=6384
LLM_BASE_URL_DOCKER=http://host.docker.internal:11434/v1
LOG_LEVEL=INFO
EOF

# 4. Rebuild & start
docker-compose down
docker-compose up --build -d

# 5. Wait for services
sleep 30

# 6. Verify health
docker-compose ps
curl http://localhost:8012/health

# 7. Test FITS queries
curl -X POST http://localhost:8012/chat/query \
  -H "Content-Type: application/json" \
  -d '{"message":"Show pending tasks"}'

# 8. Open frontend
open http://localhost:5174
```

---

## 📊 Environment Variable Reference

| Variable | Purpose | Docker Value | Example |
|----------|---------|--------------|---------|
| `DOMAIN` | Active domain | fits_dev_march_9 | vts \| fits_dev_march_9 \| ims |
| `DEFAULT_CHAT_APP_ID` | Default app | fits_dev_march_9 | Must match DOMAIN |
| `FITS_DATABASE_URL_DOCKER` | FITS DB connection | Docker network | mysql+aiomysql://... |
| `VTS_DATABASE_URL_DOCKER` | VTS DB connection | Docker network | mysql+aiomysql://... |
| `TAG_BACKEND_PORT` | Backend port | 8012 | Any available port |
| `CHATBOT_DEMO_PORT` | Frontend port | 5174 | Any available port |
| `REDIS_HOST_PORT` | Redis port | 6384 | Any available port |
| `APPS_CONFIG_PATH` | Config file | ./config/apps.local.yaml | Fixed path |
| `LOG_LEVEL` | Logging verbosity | INFO | DEBUG \| INFO \| WARNING |

---

## 🔄 Switching Back to VTS

```bash
# 1. Update .env
sed -i 's/DOMAIN=.*/DOMAIN=vts/g' .env
sed -i 's/DEFAULT_CHAT_APP_ID=.*/DEFAULT_CHAT_APP_ID=vts/g' .env

# 2. Rebuild
docker-compose down
docker-compose up --build -d

# 3. Verify
docker-compose logs tag_backend | grep -i "Loading domain"
```

---

## 🧪 Test Queries

### For FITS Domain

```bash
# After switching to FITS and rebuilding

# 1. Pending tasks
curl -X POST http://localhost:8012/chat/query \
  -d '{"message":"Show pending tasks"}'

# 2. Overdue tasks
curl -X POST http://localhost:8012/chat/query \
  -d '{"message":"How many tasks are overdue?"}'

# 3. Facility workload
curl -X POST http://localhost:8012/chat/query \
  -d '{"message":"Which facility has most tasks?"}'
```

### For VTS Domain

```bash
# After switching to VTS and rebuilding

# 1. Active trips
curl -X POST http://localhost:8012/chat/query \
  -d '{"message":"Show active trips"}'

# 2. Driver workload
curl -X POST http://localhost:8012/chat/query \
  -d '{"message":"List drivers with trips"}'
```

---

## 🚨 Important Notes

### ⚠️ ALWAYS Rebuild After Changing `.env`

```bash
# WRONG - just restarting (will use old values)
docker-compose restart

# CORRECT - rebuild with new env vars
docker-compose up --build -d
```

### ⚠️ Check logs for successful domain load

```bash
# Always verify which domain loaded
docker-compose logs tag_backend | head -50

# Look for:
# [INFO] Initializing TAG with domain: fits_dev_march_9
# [INFO] Database connected to: mysql://...remp-chat-bot
```

### ⚠️ Database Connection Must Work

```bash
# Test connection before docker-compose up
mysql -h host.docker.internal -u root -p12345 remp-chat-bot -e "SELECT COUNT(*) FROM task_transaction;"
```

---

## 📝 Summary - VTS to FITS Workflow

| Step | Action | Command |
|------|--------|---------|
| 1 | Edit .env | `sed -i 's/DOMAIN=vts/DOMAIN=fits_dev_march_9/g' .env` |
| 2 | Stop containers | `docker-compose down` |
| 3 | Rebuild images | `docker-compose up --build -d` |
| 4 | Wait for health | `sleep 30 && docker-compose ps` |
| 5 | Check logs | `docker-compose logs tag_backend \| grep -i fits` |
| 6 | Test endpoint | `curl http://localhost:8012/health` |
| 7 | Use chatbot | Open http://localhost:5174 |

---

## 🎯 Ready to Deploy

✅ All environment variables properly configured  
✅ Docker correctly reads from .env  
✅ Domain switching requires rebuild (documented)  
✅ FITS metadata complete and tested  
✅ No last-minute surprises!

**Happy demoing! 🚀**
