# 🚀 FITS Demo - Quick Cheatsheet (Keep this open tomorrow!)

**Date**: 2026-04-20  
**Domain**: fits_dev_march_9  
**Duration**: ~5 minutes to get running

---

## ⚡ 3-Step Quick Start

### Step 1: Update .env (Single Change)
```bash
cd /home/deepakrajb/Desktop/MD/TAG-Implementation
# Change this line:
DOMAIN=fits_dev_march_9
DEFAULT_CHAT_APP_ID=fits_dev_march_9
```

### Step 2: Rebuild Docker (Not Restart!)
```bash
docker-compose down
docker-compose up --build -d
sleep 30
```

### Step 3: Verify & Demo
```bash
# Check status
docker-compose ps

# Test API
curl http://localhost:8012/health

# Open frontend
open http://localhost:5174
```

---

## ✅ Pre-Demo Checklist

- [ ] MySQL running: `mysql -h localhost -u root -p12345 remp-chat-bot -e "SELECT COUNT(*) FROM task_transaction;"`
- [ ] LLM running: `curl http://localhost:11434/api/tags`
- [ ] Docker installed: `docker --version`
- [ ] .env updated with FITS settings
- [ ] Docker rebuilt (NOT just restarted)
- [ ] Services healthy: `docker-compose ps` (all Status: Up)

---

## 🎯 Demo Queries to Run

Copy-paste these in the chatbot UI at http://localhost:5174:

### Task Management
```
1. "Show me all pending maintenance tasks"
2. "How many tasks are overdue?"
3. "List critical priority tasks for today"
4. "Show tasks assigned to me"
```

### Facility Analysis
```
5. "Which facility has the most open tasks?"
6. "Show completed tasks from last week"
7. "What's the workload distribution across facilities?"
```

### Reports
```
8. "Generate a priority breakdown report"
9. "Show checklist compliance status"
10. "List assets with maintenance history"
```

---

## 🔧 If Something Goes Wrong

### Problem: Still showing VTS domain

**Solution:**
```bash
docker-compose down
docker-compose up --build -d  # REBUILD not restart!
docker-compose logs tag_backend | head -20  # Check logs
```

### Problem: Database connection error

**Check:**
```bash
mysql -h host.docker.internal -u root -p12345 remp-chat-bot -e "SELECT COUNT(*) FROM task_transaction;"
# If fails, use: mysql -h localhost -u root -p12345 remp-chat-bot -e "..."
```

### Problem: Backend not healthy

**Wait longer:**
```bash
docker-compose logs tag_backend
# Wait 60 seconds for startup (first-time is slow)
sleep 60 && docker-compose ps
```

---

## 📊 What's Been Done (Don't Redo!)

✅ **Metadata Created** (all in git)
- 22 enums mapped
- 80+ field definitions
- 40+ business rules
- 10 reports configured
- 5 automation workflows

✅ **Docker Configured** (ready to use)
- docker-compose.yml reads from .env
- Frontend + Backend + Redis set up
- Health checks in place

✅ **Documentation Done**
- RUN.md (detailed guide)
- COMPLETION_SUMMARY.md (metadata overview)
- METADATA_REPORT.md (schema reference)

---

## 🚨 REMEMBER

1. **Change .env ONLY** - don't edit docker-compose.yml or Dockerfile
2. **Always REBUILD** - `docker-compose up --build` not `docker-compose restart`
3. **Wait 30+ seconds** - first startup takes time
4. **Check logs** - `docker-compose logs tag_backend` if unsure
5. **Use RUN.md** - for detailed troubleshooting

---

## 📞 Quick Reference

| Need | Command |
|------|---------|
| Check status | `docker-compose ps` |
| View logs | `docker-compose logs tag_backend` |
| Stop all | `docker-compose down` |
| Restart all | `docker-compose restart` |
| Rebuild & start | `docker-compose up --build -d` |
| Test backend | `curl http://localhost:8012/health` |
| Test FITS | Open http://localhost:5174 |

---

## 🎬 Demo Flow (5 min)

1. **Show .env** - "This controls which domain loads"
2. **Rebuild** - `docker-compose up --build -d`
3. **Check logs** - `docker-compose logs tag_backend | grep -i fits`
4. **Open UI** - http://localhost:5174
5. **Run queries** - Use the demo queries above
6. **Show reports** - Backend has 10 pre-configured reports
7. **Explain workflows** - 5 YAML workflows for automation

---

## 🎯 Talking Points

- **Single .env file controls everything** - no hardcoding needed
- **Domain isolation** - VTS, FITS, IMS can all coexist
- **Production-ready metadata** - 2000+ lines from real schema
- **Automation workflows** - Create tasks, update status, assign, schedule, checklist
- **Natural language** - 50+ business terms understood
- **Business rules enforced** - Field immutability, status transitions, SLA tracking

---

**Good luck! You've got this! 🚀**

*If stuck, read RUN.md for detailed instructions*
