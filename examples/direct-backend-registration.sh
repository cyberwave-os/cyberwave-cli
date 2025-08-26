#!/bin/bash

# Direct Backend Registration Script
# Bypasses CLI auth issues and registers directly with backend API

set -e

echo "🔧 Direct Backend Registration"
echo "============================="

# Configuration
BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:3000"
NODE_ID="edge_9078476a6993daaa"

echo ""
echo "📡 Testing Backend Connectivity"
echo "==============================="

# Test basic backend health
echo "Testing backend health..."
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo "✅ Backend health endpoint responding"
else
    echo "❌ Backend health endpoint not responding"
    echo "   Make sure backend is running: cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
    exit 1
fi

# Test API endpoints
echo ""
echo "Testing API endpoints..."

# Test auth endpoint variations
AUTH_ENDPOINTS=(
    "/api/v1/users/auth/cli/status"
    "/api/v1/auth/status" 
    "/users/auth/status"
    "/auth/status"
    "/api/auth/status"
)

for endpoint in "${AUTH_ENDPOINTS[@]}"; do
    echo "Testing: $BACKEND_URL$endpoint"
    response=$(curl -s -w "%{http_code}" "$BACKEND_URL$endpoint" || echo "000")
    echo "  Response: $response"
done

echo ""
echo "📱 Testing Device Registration"
echo "============================="

# Try to register a device directly
DEVICE_DATA='{
  "device_id": "test_camera_001",
  "name": "Test Camera Direct",
  "type": "camera/ip",
  "node_id": "'$NODE_ID'",
  "status": "online",
  "configuration": {
    "ip_address": "192.168.1.6",
    "port": 80,
    "protocol": "http",
    "capabilities": ["video_streaming", "motion_detection"]
  },
  "metadata": {
    "registration_method": "direct_api",
    "source": "cli_script"
  }
}'

# Try different device registration endpoints
DEVICE_ENDPOINTS=(
    "/api/v1/devices/register"
    "/api/v1/devices"
    "/devices/register"
    "/devices"
    "/api/devices"
)

echo "Device data to register:"
echo "$DEVICE_DATA" | jq . 2>/dev/null || echo "$DEVICE_DATA"

for endpoint in "${DEVICE_ENDPOINTS[@]}"; do
    echo ""
    echo "Trying device registration at: $BACKEND_URL$endpoint"
    
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$DEVICE_DATA" \
        "$BACKEND_URL$endpoint" 2>/dev/null || echo "ERROR")
    
    echo "Response: $response"
done

echo ""
echo "🔍 Testing Node Registration"
echo "============================"

# Try to register the node directly
NODE_DATA='{
  "node_id": "'$NODE_ID'",
  "hostname": "ITMACD4NMMQQ7N0",
  "platform": "Darwin",
  "status": "active",
  "capabilities": ["camera_discovery", "device_management", "telemetry_collection"],
  "metadata": {
    "registration_method": "direct_api",
    "cli_version": "0.11.5"
  }
}'

# Try different node registration endpoints
NODE_ENDPOINTS=(
    "/api/v1/edge/nodes/register"
    "/api/v1/edge/nodes"
    "/api/v1/nodes/register"
    "/api/v1/nodes"
    "/edge/nodes/register"
    "/edge/nodes"
    "/nodes/register"
    "/nodes"
)

echo "Node data to register:"
echo "$NODE_DATA" | jq . 2>/dev/null || echo "$NODE_DATA"

for endpoint in "${NODE_ENDPOINTS[@]}"; do
    echo ""
    echo "Trying node registration at: $BACKEND_URL$endpoint"
    
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$NODE_DATA" \
        "$BACKEND_URL$endpoint" 2>/dev/null || echo "ERROR")
    
    echo "Response: $response"
done

echo ""
echo "📋 API Discovery"
echo "==============="

# Try to discover available API endpoints
echo "Discovering API structure..."

API_DISCOVERY_ENDPOINTS=(
    "/api/v1/"
    "/api/"
    "/docs"
    "/swagger"
    "/openapi.json"
    "/schema"
)

for endpoint in "${API_DISCOVERY_ENDPOINTS[@]}"; do
    echo ""
    echo "Checking: $BACKEND_URL$endpoint"
    response=$(curl -s "$BACKEND_URL$endpoint" 2>/dev/null | head -c 200)
    if [ ! -z "$response" ]; then
        echo "  Found content (first 200 chars): $response"
    else
        echo "  No content"
    fi
done

echo ""
echo "🌐 Frontend Check"
echo "================"

echo "Checking if devices appear in frontend..."
echo "Open in browser: $FRONTEND_URL/devices"

# Test if frontend is accessible
if curl -s "$FRONTEND_URL" > /dev/null 2>&1; then
    echo "✅ Frontend is accessible"
else
    echo "❌ Frontend not accessible"
    echo "   Make sure frontend is running"
fi

echo ""
echo "📊 Summary"
echo "=========="

echo "Node ID: $NODE_ID"
echo "Backend: $BACKEND_URL"
echo "Frontend: $FRONTEND_URL"
echo ""
echo "Next steps:"
echo "1. Check backend logs for any errors"
echo "2. Verify API endpoints in backend code"
echo "3. Check if devices appear in: $FRONTEND_URL/devices"
echo "4. Look for node in: $FRONTEND_URL (navigation menu)"

echo ""
echo "🔧 Manual Registration Alternative"
echo "================================="

echo "If API registration fails, you can manually register in the frontend:"
echo "1. Open: $FRONTEND_URL"
echo "2. Look for 'Add Device' or 'Register Node' buttons"
echo "3. Enter Node ID: $NODE_ID"
echo "4. Add device details manually"
