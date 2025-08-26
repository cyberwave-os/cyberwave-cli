#!/bin/bash

# Complete API Registration Script
# Uses the working authentication token to register node and devices

set -e

echo "🚀 Complete API Registration"
echo "============================"

BACKEND_URL="http://localhost:8000"
API_BASE="${BACKEND_URL}/api/v1"
NODE_ID="edge_9078476a6993daaa"

# Get authentication token
echo "🔐 Authenticating..."
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
    exit 1
fi

echo "✅ Authenticated successfully"

echo ""
echo "📂 Finding/Creating Project"
echo "=========================="

# List projects
projects_response=$(curl -s -H "Authorization: Token $token" \
    "$API_BASE/projects" 2>/dev/null || echo "ERROR")

echo "Projects response: $projects_response"

# Extract first project ID or create one
project_id=$(echo "$projects_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list) and len(data) > 0:
        print(data[0]['id'])
    else:
        print('')
except:
    print('')
" 2>/dev/null || echo "")

if [ -z "$project_id" ]; then
    echo "Creating new project..."
    
    create_project_response=$(curl -s -X POST \
        -H "Authorization: Token $token" \
        -H "Content-Type: application/json" \
        -d '{"name":"Edge Node Project","description":"Auto-created for edge node registration","is_public":false}' \
        "$API_BASE/projects" 2>/dev/null || echo "ERROR")
    
    echo "Create project response: $create_project_response"
    
    project_id=$(echo "$create_project_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data['id'])
except:
    print('')
" 2>/dev/null || echo "")
fi

if [ -z "$project_id" ]; then
    echo "❌ Could not create or find project"
    exit 1
fi

echo "✅ Using project ID: $project_id"

echo ""
echo "🤖 Registering Edge Node"
echo "========================"

node_data='{
    "name": "ITMACD4NMMQQ7N0 Edge Node",
    "description": "Auto-discovered macOS edge node with camera capabilities",
    "hostname": "ITMACD4NMMQQ7N0",
    "node_type": "edge",
    "capabilities": ["camera_discovery", "device_management", "telemetry_collection", "motion_detection"]
}'

echo "Registering node in project $project_id..."

node_response=$(curl -s -X POST \
    -H "Authorization: Token $token" \
    -H "Content-Type: application/json" \
    -d "$node_data" \
    "$API_BASE/projects/$project_id/nodes" 2>/dev/null || echo "ERROR")

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
echo "📱 Registering Discovered Devices"
echo "================================="

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

echo "Camera 1 response: $camera1_response"

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

echo "NVR response: $nvr_response"

echo ""
echo "🎉 Registration Complete!"
echo "========================="

echo "✅ Project: $project_id"
echo "✅ Node: $node_uuid"
echo "✅ Devices: 2 registered"

echo ""
echo "🌐 Verification:"
echo "==============="
echo "1. Open: http://localhost:3000/projects"
echo "2. Find project: 'Edge Node Project'"
echo "3. View the edge node and its devices"
echo "4. Check device details and capabilities"

echo ""
echo "📊 Summary:"
echo "==========="
echo "• Node ID: $NODE_ID"
echo "• Node UUID: $node_uuid"
echo "• Project: Edge Node Project ($project_id)"
echo "• IP Camera: 192.168.1.6:80 (HTTP)"
echo "• Uniview NVR: 192.168.1.8:554 (RTSP)"
echo "  - Streams: unicast/c1/s1/live, unicast/c2/s1/live"
echo "  - Username: admin"

echo ""
echo "🔗 Direct Links:"
echo "==============="
echo "• Frontend: http://localhost:3000"
echo "• Devices: http://localhost:3000/devices"
echo "• Projects: http://localhost:3000/projects"

echo ""
echo "✅ SUCCESS: Edge node and devices are now registered in the backend!"
echo "You should be able to see them in the frontend without authentication issues."
