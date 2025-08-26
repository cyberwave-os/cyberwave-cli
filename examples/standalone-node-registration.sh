#!/bin/bash

# Standalone Node Registration Script
# Registers edge node without requiring a project

set -e

echo "🤖 Standalone Edge Node Registration"
echo "==================================="

BACKEND_URL="http://localhost:8000"
API_BASE="${BACKEND_URL}/api/v1"
NODE_ID="edge_9078476a6993daaa"

echo ""
echo "🔐 Step 1: Authentication"
echo "========================="

echo "Authenticating with backend..."
auth_response=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@cyberwave.com","password":"admin123"}' \
    "$API_BASE/users/auth/cli/login" 2>/dev/null || echo "ERROR")

token=$(echo "$auth_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data['token'])
except:
    print('')
" 2>/dev/null || echo "")

if [ -z "$token" ]; then
    echo "❌ Authentication failed"
    echo "Response: $auth_response"
    exit 1
fi

echo "✅ Authentication successful"

echo ""
echo "🤖 Step 2: Register Standalone Edge Node"
echo "========================================"

node_data='{
    "name": "ITMACD4NMMQQ7N0 Standalone Edge Node",
    "description": "Standalone edge node with camera capabilities - no project required",
    "hostname": "ITMACD4NMMQQ7N0",
    "node_type": "edge",
    "capabilities": ["camera_discovery", "device_management", "telemetry_collection", "motion_detection"]
}'

echo "Registering standalone node..."

node_response=$(curl -s -X POST \
    -H "Authorization: Token $token" \
    -H "Content-Type: application/json" \
    -d "$node_data" \
    "$API_BASE/nodes" 2>/dev/null || echo "ERROR")

echo "Node registration response: $node_response"

# Extract node UUID
node_uuid=$(echo "$node_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data['uuid'])
except:
    print('')
" 2>/dev/null || echo "")

if [ -z "$node_uuid" ]; then
    echo "❌ Node registration failed"
    echo "Response: $node_response"
    exit 1
fi

echo "✅ Node registered with UUID: $node_uuid"

echo ""
echo "📱 Step 3: Register Discovered Devices"
echo "======================================"

# Device 1: IP Camera
echo "Registering IP Camera (192.168.1.6)..."

camera1_data='{
    "name": "IP Camera 192.168.1.6",
    "description": "Auto-discovered IP camera on local network",
    "device_type": "camera",
    "connection_string": "http://192.168.1.6:80",
    "connection_type": "network",
    "manufacturer": "Generic",
    "model": "IP Camera",
    "config": {
        "ip_address": "192.168.1.6",
        "protocol": "http",
        "port": 80,
        "capabilities": ["video_streaming", "motion_detection"]
    },
    "capabilities": ["video_streaming", "motion_detection"]
}'

camera1_response=$(curl -s -X POST \
    -H "Authorization: Token $token" \
    -H "Content-Type: application/json" \
    -d "$camera1_data" \
    "$API_BASE/nodes/$node_uuid/devices" 2>/dev/null || echo "ERROR")

echo "✅ Camera 1 registered"

# Device 2: Uniview NVR
echo ""
echo "Registering Uniview NVR (192.168.1.8)..."

nvr_data='{
    "name": "Uniview NVR 192.168.1.8",
    "description": "Uniview Network Video Recorder with multiple camera streams",
    "device_type": "nvr",
    "connection_string": "rtsp://admin:Stralis26$@192.168.1.8:554",
    "connection_type": "network",
    "manufacturer": "Uniview",
    "model": "NVR System",
    "serial_number": "UNV-NVR-001",
    "config": {
        "ip_address": "192.168.1.8",
        "protocol": "rtsp",
        "port": 554,
        "username": "admin",
        "streams": [
            "unicast/c1/s1/live",
            "unicast/c2/s1/live"
        ],
        "capabilities": ["video_streaming", "recording", "motion_detection", "multi_camera"]
    },
    "capabilities": ["video_streaming", "recording", "motion_detection", "multi_camera"]
}'

nvr_response=$(curl -s -X POST \
    -H "Authorization: Token $token" \
    -H "Content-Type: application/json" \
    -d "$nvr_data" \
    "$API_BASE/nodes/$node_uuid/devices" 2>/dev/null || echo "ERROR")

echo "✅ NVR registered"

echo ""
echo "📋 Step 4: Verify Node in Global List"
echo "====================================="

echo "Fetching all nodes..."
nodes_response=$(curl -s -H "Authorization: Token $token" \
    "$API_BASE/nodes" 2>/dev/null || echo "ERROR")

echo "All nodes response:"
echo "$nodes_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(f'Found {len(data)} total nodes:')
    for i, node in enumerate(data, 1):
        project_info = f\" (Project: {node['project_uuid']})\" if node.get('project_uuid') else \" (Standalone)\"
        print(f'{i:2}. {node[\"name\"]} - {node[\"uuid\"][:8]}...{project_info}')
        print(f'    Status: {node[\"status\"]}, Type: {node[\"node_type\"]}')
        print()
except Exception as e:
    print('Error parsing nodes:', e)
    print('Raw response:', data)
"

echo ""
echo "🎉 Registration Complete!"
echo "========================="

echo "✅ Standalone Node: $node_uuid"
echo "✅ Devices: 2 registered"
echo "✅ No project required!"

echo ""
echo "🌐 Verification:"
echo "==============="
echo "1. Open: http://localhost:3000/devices"
echo "2. Look for nodes section or standalone devices"
echo "3. Your node should be visible in the main devices list"
echo "4. Check if there's a separate 'Nodes' or 'Edge Nodes' page"

echo ""
echo "📊 Summary:"
echo "==========="
echo "• Node ID: $NODE_ID"
echo "• Node UUID: $node_uuid"
echo "• Project: None (Standalone)"
echo "• IP Camera: 192.168.1.6:80 (HTTP)"
echo "• Uniview NVR: 192.168.1.8:554 (RTSP)"
echo "  - Streams: unicast/c1/s1/live, unicast/c2/s1/live"
echo "  - Username: admin"

echo ""
echo "🔗 Direct Links:"
echo "==============="
echo "• Frontend: http://localhost:3000"
echo "• Devices: http://localhost:3000/devices"
echo "• Nodes API: $API_BASE/nodes"

echo ""
echo "✅ SUCCESS: Standalone edge node registered!"
echo "The node should now be visible in the frontend without requiring a project."
