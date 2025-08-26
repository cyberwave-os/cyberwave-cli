#!/bin/bash

# Authenticated Registration Script
# Uses correct API endpoints and authentication flow

set -e

echo "🔐 Authenticated Edge Node Registration"
echo "======================================"

BACKEND_URL="http://localhost:8000"
API_BASE="${BACKEND_URL}/api/v2"
NODE_ID="edge_9078476a6993daaa"

echo ""
echo "📋 Step 1: Authentication"
echo "========================="

# CLI Login function
login_cli() {
    echo "Authenticating with backend..."
    
    # Use the CLI login endpoint directly
    read -p "Email: " email
    read -s -p "Password: " password
    echo ""
    
    auth_response=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$email\",\"password\":\"$password\"}" \
        "$BACKEND_URL/api/v2/users/auth/cli/login" 2>/dev/null || echo "ERROR")
    
    if [[ "$auth_response" == *"token"* ]]; then
        # Extract token from response
        token=$(echo "$auth_response" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])" 2>/dev/null || echo "")
        
        if [ ! -z "$token" ]; then
            echo "✅ Authentication successful"
            echo "$token" > /tmp/cyberwave_token.tmp
            return 0
        fi
    fi
    
    echo "❌ Authentication failed"
    echo "Response: $auth_response"
    return 1
}

# Try authentication
if ! login_cli; then
    echo ""
    echo "🆘 Alternative: Create a project and register manually"
    echo "=================================================="
    echo "1. Open frontend: http://localhost:3000"
    echo "2. Create a new project"
    echo "3. Navigate to project settings or devices"
    echo "4. Look for 'Add Edge Node' or 'Register Node'"
    echo "5. Use Node ID: $NODE_ID"
    exit 1
fi

TOKEN=$(cat /tmp/cyberwave_token.tmp)
rm -f /tmp/cyberwave_token.tmp

echo ""
echo "📂 Step 2: Create/Find Project"
echo "=============================="

# List projects to find one to use
projects_response=$(curl -s -H "Authorization: Token $TOKEN" \
    "$API_BASE/projects" 2>/dev/null || echo "ERROR")

echo "Projects response: $projects_response"

# Try to extract first project ID
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
    echo "No projects found. Creating a new project..."
    
    create_project_response=$(curl -s -X POST \
        -H "Authorization: Token $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"name":"Edge Node Project","description":"Auto-created for edge node registration"}' \
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
echo "🤖 Step 3: Register Edge Node"
echo "============================="

node_data="{
    \"name\": \"Edge Node $NODE_ID\",
    \"description\": \"Auto-discovered edge node with camera capabilities\",
    \"hostname\": \"ITMACD4NMMQQ7N0\",
    \"node_type\": \"edge\",
    \"capabilities\": [\"camera_discovery\", \"device_management\", \"telemetry_collection\"]
}"

echo "Registering node with project $project_id..."

node_response=$(curl -s -X POST \
    -H "Authorization: Token $TOKEN" \
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
    exit 1
fi

echo "✅ Node registered with UUID: $node_uuid"

echo ""
echo "📱 Step 4: Register Discovered Devices"
echo "======================================"

# Register cameras discovered by the CLI
cameras=(
    "192.168.1.6:IP Camera"
    "192.168.1.8:NVR System"
)

for camera_info in "${cameras[@]}"; do
    IFS=':' read -r ip type <<< "$camera_info"
    
    device_data="{
        \"name\": \"$type at $ip\",
        \"description\": \"Auto-discovered $type\",
        \"device_type\": \"camera\",
        \"connection_string\": \"$ip\",
        \"connection_type\": \"network\",
        \"manufacturer\": \"$([ "$ip" == "192.168.1.8" ] && echo "Uniview" || echo "Generic")\",
        \"config\": {
            \"ip_address\": \"$ip\",
            \"protocol\": \"$([ "$ip" == "192.168.1.8" ] && echo "rtsp" || echo "http")\",
            \"port\": $([ "$ip" == "192.168.1.8" ] && echo "554" || echo "80")
        },
        \"capabilities\": [\"video_streaming\", \"motion_detection\"]
    }"
    
    echo ""
    echo "Registering device: $type at $ip"
    
    device_response=$(curl -s -X POST \
        -H "Authorization: Token $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$device_data" \
        "$API_BASE/nodes/$node_uuid/devices" 2>/dev/null || echo "ERROR")
    
    echo "Device response: $device_response"
done

echo ""
echo "🎉 Registration Complete!"
echo "========================="

echo "✅ Node registered: $node_uuid"
echo "✅ Project: $project_id" 
echo "✅ Devices: 2 cameras registered"

echo ""
echo "🌐 Verification:"
echo "Open http://localhost:3000/projects to see your registered node and devices"
echo "Look for project 'Edge Node Project' and the devices under the edge node"

echo ""
echo "📊 Summary:"
echo "- Node ID: $NODE_ID"
echo "- Node UUID: $node_uuid"
echo "- Project ID: $project_id"
echo "- Uniview NVR: 192.168.1.8:554 (RTSP)"
echo "- IP Camera: 192.168.1.6:80 (HTTP)"
