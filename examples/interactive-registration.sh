#!/bin/bash

# Interactive Edge Node Registration Script
# Asks user for project preference and handles registration accordingly

set -e

echo "🚀 Interactive Edge Node Registration"
echo "===================================="

BACKEND_URL="http://localhost:8000"
API_BASE="${BACKEND_URL}/api/v1"
NODE_ID="edge_9078476a6993daaa"
MEMORY_FILE="$HOME/.cyberwave/last_project.json"

# Ensure .cyberwave directory exists
mkdir -p "$HOME/.cyberwave"

echo ""
echo "🔐 Step 1: Authentication"
echo "========================="

# Get authentication token
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
echo "📂 Step 2: Project Selection"
echo "============================"

# Check if we have a remembered project
REMEMBERED_PROJECT=""
REMEMBERED_PROJECT_NAME=""

if [ -f "$MEMORY_FILE" ]; then
    REMEMBERED_PROJECT=$(cat "$MEMORY_FILE" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data.get('project_uuid', ''))
except:
    print('')
" 2>/dev/null || echo "")
    
    REMEMBERED_PROJECT_NAME=$(cat "$MEMORY_FILE" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data.get('project_name', ''))
except:
    print('')
" 2>/dev/null || echo "")
fi

if [ ! -z "$REMEMBERED_PROJECT" ] && [ ! -z "$REMEMBERED_PROJECT_NAME" ]; then
    echo "🔍 Found previous project: $REMEMBERED_PROJECT_NAME"
    echo ""
    read -p "Use previous project '$REMEMBERED_PROJECT_NAME'? (y/n): " use_previous
    
    if [[ "$use_previous" =~ ^[Yy]$ ]]; then
        project_uuid="$REMEMBERED_PROJECT"
        project_name="$REMEMBERED_PROJECT_NAME"
        echo "✅ Using previous project: $project_name"
        echo ""
        echo "🤖 Step 3: Register Edge Node"
        echo "============================="
        # Skip to registration
    else
        REMEMBERED_PROJECT=""
    fi
fi

if [ -z "$REMEMBERED_PROJECT" ]; then
    echo ""
    read -p "Do you want to create a new project? (y/n): " create_new
    
    if [[ "$create_new" =~ ^[Yy]$ ]]; then
        # Create new project
        echo ""
        read -p "Enter project name: " project_name
        read -p "Enter project description (optional): " project_description
        
        if [ -z "$project_description" ]; then
            project_description="Project for edge node $NODE_ID"
        fi
        
        echo ""
        echo "Creating project '$project_name'..."
        
        create_project_response=$(curl -s -X POST \
            -H "Authorization: Token $token" \
            -H "Content-Type: application/json" \
            -d "{\"name\":\"$project_name\",\"description\":\"$project_description\",\"visibility\":\"private\"}" \
            "$API_BASE/projects" 2>/dev/null || echo "ERROR")
        
        echo "Create project response: $create_project_response"
        
        project_uuid=$(echo "$create_project_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    print(data['uuid'])
except:
    print('')
" 2>/dev/null || echo "")
        
        if [ -z "$project_uuid" ]; then
            echo "❌ Failed to create project"
            echo "Response: $create_project_response"
            exit 1
        fi
        
        echo "✅ Created project: $project_name ($project_uuid)"
        
        # Remember this project
        echo "{\"project_uuid\":\"$project_uuid\",\"project_name\":\"$project_name\"}" > "$MEMORY_FILE"
        
    else
        # Use existing project
        echo ""
        echo "Fetching available projects..."
        
        projects_response=$(curl -s -H "Authorization: Token $token" \
            "$API_BASE/projects" 2>/dev/null || echo "ERROR")
        
        echo ""
        echo "📋 Available Projects:"
        echo "====================="
        
        # Display projects with numbers
        echo "$projects_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    for i, project in enumerate(data[:10], 1):  # Show first 10 projects
        print(f\"{i:2}. {project['name']} ({project['uuid'][:8]}...)\")
        print(f\"    Description: {project.get('description', 'No description')}\")
        print()
except Exception as e:
    print('Error parsing projects:', e)
"
        
        echo ""
        echo "Options:"
        echo "• Enter project number (1-10)"
        echo "• Enter project name to search"
        echo "• Enter project UUID directly"
        echo ""
        read -p "Your choice: " user_choice
        
        # Try to parse as number first
        if [[ "$user_choice" =~ ^[0-9]+$ ]] && [ "$user_choice" -ge 1 ] && [ "$user_choice" -le 10 ]; then
            # User selected by number
            project_info=$(echo "$projects_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    if $user_choice <= len(data):
        project = data[$user_choice-1]
        print(project['uuid'] + '|' + project['name'])
    else:
        print('')
except:
    print('')
")
            
            if [ ! -z "$project_info" ]; then
                project_uuid=$(echo "$project_info" | cut -d'|' -f1)
                project_name=$(echo "$project_info" | cut -d'|' -f2)
            fi
            
        elif [[ "$user_choice" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$ ]]; then
            # User entered a UUID
            project_uuid="$user_choice"
            project_name=$(echo "$projects_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    for project in data:
        if project['uuid'] == '$user_choice':
            print(project['name'])
            break
    else:
        print('Unknown Project')
except:
    print('Unknown Project')
")
            
        else
            # User entered a name - search for it
            project_info=$(echo "$projects_response" | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    search_term = '$user_choice'.lower()
    for project in data:
        if search_term in project['name'].lower():
            print(project['uuid'] + '|' + project['name'])
            break
    else:
        print('')
except:
    print('')
")
            
            if [ ! -z "$project_info" ]; then
                project_uuid=$(echo "$project_info" | cut -d'|' -f1)
                project_name=$(echo "$project_info" | cut -d'|' -f2)
            fi
        fi
        
        if [ -z "$project_uuid" ]; then
            echo "❌ Could not find or select project"
            exit 1
        fi
        
        echo "✅ Selected project: $project_name ($project_uuid)"
        
        # Remember this project
        echo "{\"project_uuid\":\"$project_uuid\",\"project_name\":\"$project_name\"}" > "$MEMORY_FILE"
    fi
fi

echo ""
echo "🤖 Step 3: Register Edge Node"
echo "============================="

node_data="{
    \"name\": \"ITMACD4NMMQQ7N0 Edge Node\",
    \"description\": \"Auto-discovered macOS edge node with camera capabilities\",
    \"hostname\": \"ITMACD4NMMQQ7N0\",
    \"node_type\": \"edge\",
    \"capabilities\": [\"camera_discovery\", \"device_management\", \"telemetry_collection\", \"motion_detection\"]
}"

echo "Registering node in project '$project_name'..."

node_response=$(curl -s -X POST \
    -H "Authorization: Token $token" \
    -H "Content-Type: application/json" \
    -d "$node_data" \
    "$API_BASE/projects/$project_uuid/nodes" 2>/dev/null || echo "ERROR")

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
echo "📱 Step 4: Register Discovered Devices"
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
echo "🎉 Registration Complete!"
echo "========================="

echo "✅ Project: $project_name"
echo "✅ Node: $node_uuid"
echo "✅ Devices: 2 registered"

echo ""
echo "💾 Project Remembered"
echo "===================="
echo "Your project choice has been saved to: $MEMORY_FILE"
echo "Next time you run this script, it will remember '$project_name'"

echo ""
echo "🌐 Verification:"
echo "==============="
echo "1. Open: http://localhost:3000/projects"
echo "2. Find project: '$project_name'"
echo "3. View the edge node and its devices"
echo "4. Check device details and capabilities"

echo ""
echo "📊 Summary:"
echo "==========="
echo "• Node ID: $NODE_ID"
echo "• Node UUID: $node_uuid"
echo "• Project: $project_name ($project_uuid)"
echo "• IP Camera: 192.168.1.6:80 (HTTP)"
echo "• Uniview NVR: 192.168.1.8:554 (RTSP)"
echo "  - Streams: unicast/c1/s1/live, unicast/c2/s1/live"
echo "  - Username: admin"

echo ""
echo "🔗 Direct Links:"
echo "==============="
echo "• Frontend: http://localhost:3000"
echo "• Projects: http://localhost:3000/projects"
echo "• Your Project: http://localhost:3000/projects (find '$project_name')"

echo ""
echo "✅ SUCCESS: Edge node and devices are now registered!"
echo "You should be able to see them in the frontend devices page."
