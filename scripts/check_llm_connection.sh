#!/bin/bash
# LLM Connection Diagnostic Script

echo "=== TAG Backend LLM Connection Diagnostics ==="
echo ""

# Read LLM config from .env
if [ -f .env ]; then
    echo "📋 Current LLM Configuration:"
    grep -E "LLM_BASE_URL|LLM_MODEL|LLM_TIMEOUT" .env | sed 's/^/   /'
    echo ""
    
    # Extract LLM URL
    LLM_URL=$(grep "LLM_BASE_URL" .env | cut -d'=' -f2 | sed 's|/v1||')
    
    if [ ! -z "$LLM_URL" ]; then
        echo "🔍 Testing LLM Connection..."
        echo "   URL: $LLM_URL"
        echo ""
        
        # Test connection
        if timeout 5 curl -s -o /dev/null -w "%{http_code}" "$LLM_URL/v1/models" 2>/dev/null | grep -q "200\|404\|401"; then
            echo "✅ LLM server is REACHABLE"
        else
            echo "❌ LLM server is NOT REACHABLE"
            echo ""
            echo "   Troubleshooting:"
            echo "   1. Check if LLM server is running"
            echo "   2. Verify the IP address and port"
            echo "   3. Check firewall settings"
        fi
    fi
else
    echo "❌ .env file not found"
fi

echo ""
echo "📊 Backend Container Status:"
docker ps --filter "name=tag_backend" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "📝 Recent Backend Logs (errors only):"
docker logs tag_backend 2>&1 | grep -E "(ERROR|FAILED|initialized with LLM)" | tail -10

echo ""
echo "💡 Useful Commands:"
echo "   Watch logs:        docker logs tag_backend -f"
echo "   Check errors:      docker logs tag_backend 2>&1 | grep ERROR"
echo "   Restart backend:   docker compose restart tag_backend"
