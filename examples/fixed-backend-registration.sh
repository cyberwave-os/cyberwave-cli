#!/bin/bash

# Fixed Backend Registration Script
# Uses correct Django URL patterns with trailing slashes

set -e

echo "🔧 Fixed Backend Registration (Django URLs)"
echo "==========================================="

BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:3000"
NODE_ID="edge_9078476a6993daaa"

echo ""
echo "🔄 Registering Node with Fixed URLs"
echo "==================================="

# Fixed Node Registration (with trailing slash)
NODE_DATA='{
  "node_id": "'$NODE_ID'",
  "hostname": "ITMACD4NMMQQ7N0",
  "platform": "Darwin",
  "status": "active",
  "capabilities": ["camera_discovery", "device_management", "telemetry_collection"],
  "metadata": {
    "registration_method": "cli_fixed",
    "cli_version": "0.11.5"
  }
}'

echo "🔗 Trying node registration with trailing slash..."
echo "URL: $BACKEND_URL/nodes/"

response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$NODE_DATA" \
    "$BACKEND_URL/nodes/" 2>/dev/null || echo "ERROR")

echo "Response: $response"

echo ""
echo "📱 Registering Devices with Fixed URLs"
echo "======================================"

# Register the discovered cameras with trailing slash
CAMERAS=(
    "192.168.1.6:camera_ip"
    "192.168.1.8:nvr_system"
)

for camera_info in "${CAMERAS[@]}"; do
    IFS=':' read -r ip type <<< "$camera_info"
    
    DEVICE_DATA='{
      "device_id": "camera_'${ip//./_}'",
      "name": "Camera '${ip}'",
      "type": "camera/ip",
      "node_id": "'$NODE_ID'",
      "status": "online",
      "configuration": {
        "ip_address": "'$ip'",
        "port": 80,
        "protocol": "http",
        "camera_type": "'$type'",
        "capabilities": ["video_streaming", "motion_detection"]
      },
      "metadata": {
        "registration_method": "cli_fixed",
        "discovered_by": "edge_node"
      }
    }'
    
    echo ""
    echo "Registering camera: $ip"
    echo "URL: $BACKEND_URL/devices/"
    
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$DEVICE_DATA" \
        "$BACKEND_URL/devices/" 2>/dev/null || echo "ERROR")
    
    echo "Response: $response"
done

echo ""
echo "✅ Registration Attempts Complete"
echo "================================="

echo ""
echo "🌐 Verification Steps:"
echo "1. Open frontend: $FRONTEND_URL/devices"
echo "2. Look for registered devices in the UI"
echo "3. Check if node appears in navigation/menu"
echo "4. Verify cameras are listed"

echo ""
echo "📝 Node Details:"
echo "Node ID: $NODE_ID"
echo "Platform: Darwin (macOS)"
echo "Cameras: 192.168.1.6 (IP Camera), 192.168.1.8 (NVR)"

echo ""
echo "🔧 If Registration Still Fails:"
echo "1. Check backend logs for specific errors"
echo "2. Verify Django models match our payload structure"
echo "3. Check authentication requirements"
echo "4. Use frontend 'Add Device' button manually"
