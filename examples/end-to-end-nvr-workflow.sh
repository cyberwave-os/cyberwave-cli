#!/bin/bash

# Complete End-to-End NVR Workflow
# From discovery to backend registration and frontend verification

set -e  # Exit on error

echo "🚀 Complete NVR Integration Workflow"
echo "====================================="

# Configuration
export CAMERA_USERNAME="admin"
export CAMERA_PASSWORD="Stralis26$"
export CAMERA_HOST="192.168.1.8"
export CAMERA_PORT="554"
export CAMERA_MANUFACTURER="uniview"

BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:3000"
PROJECT_NAME="Security Camera System"
ENVIRONMENT_NAME="Main Building"

echo ""
echo "🔧 Step 1: Environment Setup"
echo "============================"

# Ensure local environment
cyberwave edge environment local

# Show node info
echo "Node Information:"
cyberwave edge node-info

echo ""
echo "🔐 Step 2: NVR Credentials Setup"
echo "================================"

echo "NVR Configuration:"
echo "• Host: $CAMERA_HOST"
echo "• Username: $CAMERA_USERNAME"
echo "• Manufacturer: $CAMERA_MANUFACTURER"

# Check if we can reach the NVR
echo "Testing NVR connectivity..."
if ping -c 1 "$CAMERA_HOST" > /dev/null 2>&1; then
    echo "✅ NVR is reachable at $CAMERA_HOST"
else
    echo "⚠️ Cannot ping NVR at $CAMERA_HOST - continuing anyway"
fi

echo ""
echo "📦 Step 3: Dependency Management"
echo "================================"

# Install dependencies for NVR operations
cyberwave edge install-deps --device camera/nvr --no-confirm

echo ""
echo "🔍 Step 4: NVR Discovery"
echo "======================"

# Discover the NVR system
cyberwave edge nvr discover \
  --host "$CAMERA_HOST" \
  --username "$CAMERA_USERNAME" \
  --password "$CAMERA_PASSWORD" \
  --manufacturer "$CAMERA_MANUFACTURER" \
  --validate \
  --auto-install-deps

echo ""
echo "📊 Step 5: System Analysis"
echo "=========================="

# Show detailed NVR configuration
cyberwave edge nvr show --host "$CAMERA_HOST"

# Validate camera streams
echo ""
echo "Validating camera streams..."
cyberwave edge nvr validate \
  --host "$CAMERA_HOST" \
  --limit 5 \
  --auto-install-deps

echo ""
echo "💾 Step 6: Configuration Export"
echo "==============================="

# Export camera configuration
EXPORT_FILE="$HOME/.cyberwave/nvr_cameras_$(date +%Y%m%d_%H%M%S).json"
cyberwave edge nvr export \
  --host "$CAMERA_HOST" \
  --output "$EXPORT_FILE"

echo "✅ Configuration exported to: $EXPORT_FILE"

echo ""
echo "🔗 Step 7: Backend Integration"
echo "=============================="

# Check backend connectivity
echo "Checking backend at $BACKEND_URL..."
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo "✅ Backend is available"
    
    # Check authentication
    echo "Checking authentication..."
    if cyberwave auth status | grep -q "✓ Authenticated"; then
        echo "✅ Already authenticated"
        
        # Try to create project
        echo "Setting up project..."
        PROJECT_RESULT=$(cyberwave projects create "$PROJECT_NAME" --description "Automated NVR integration" 2>&1 || true)
        
        # Get project ID
        PROJECT_ID=$(cyberwave projects list --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
        
        if [ ! -z "$PROJECT_ID" ]; then
            echo "✅ Using project: $PROJECT_ID"
            
            # Create environment
            ENV_RESULT=$(cyberwave environments create "$ENVIRONMENT_NAME" --project-id "$PROJECT_ID" --setup-cameras 2>&1 || true)
            
            # Get environment ID
            ENV_ID=$(cyberwave environments list --project-id "$PROJECT_ID" --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "default-env")
            
            echo "✅ Using environment: $ENV_ID"
            
            # Register NVR with backend
            echo "Registering NVR with backend..."
            cyberwave edge nvr register \
              --host "$CAMERA_HOST" \
              --environment "$ENV_ID" \
              --project "$PROJECT_ID" \
              --location "Main Building Security Office" \
              --main-only
            
            echo "✅ NVR registered with backend"
            
        else
            echo "⚠️ Could not get project ID"
            echo "Falling back to offline registration..."
            cyberwave edge nvr register \
              --host "$CAMERA_HOST" \
              --environment "$ENVIRONMENT_NAME" \
              --location "Main Building" \
              --main-only \
              --offline
        fi
        
    else
        echo "⚠️ Not authenticated with backend"
        echo "To authenticate: cyberwave auth login --backend-url $BACKEND_URL"
        echo "Registering in offline mode..."
        
        cyberwave edge nvr register \
          --host "$CAMERA_HOST" \
          --environment "$ENVIRONMENT_NAME" \
          --location "Main Building" \
          --main-only \
          --offline
    fi
    
else
    echo "❌ Backend not available at $BACKEND_URL"
    echo "To start backend: cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
    echo "Registering in offline mode..."
    
    cyberwave edge nvr register \
      --host "$CAMERA_HOST" \
      --environment "$ENVIRONMENT_NAME" \
      --location "Main Building" \
      --main-only \
      --offline
fi

echo ""
echo "📋 Step 8: Verification & Status"
echo "================================"

# Show final status
echo "NVR Systems:"
cyberwave edge nvr list

echo ""
echo "Edge Node Status:"
cyberwave edge node-info

echo ""
echo "🎉 End-to-End Workflow Complete!"
echo "================================"

echo ""
echo "📊 Summary:"
echo "• NVR Host: $CAMERA_HOST (Uniview)"
echo "• Cameras discovered and configured"
echo "• Node ID: $(cyberwave edge node-info --export 2>/dev/null | grep '"node_id"' | cut -d'"' -f4 || echo 'N/A')"
echo "• Configuration exported to: $EXPORT_FILE"

echo ""
echo "🌐 Frontend Verification:"
echo "1. Open: $FRONTEND_URL/devices"
echo "2. Check: $FRONTEND_URL/project/$PROJECT_ID/devices (if project was created)"
echo "3. Look for cameras from NVR $CAMERA_HOST"

echo ""
echo "🔧 Next Steps:"
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo "✅ Backend is running - devices should appear in frontend"
    echo "• Monitor camera feeds"
    echo "• Set up motion detection"
    echo "• Configure recording schedules"
else
    echo "🚀 Start backend server:"
    echo "  cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
    echo ""
    echo "🔐 Authenticate:"
    echo "  cyberwave auth login"
    echo ""
    echo "🔄 Re-run registration:"
    echo "  cyberwave edge nvr register --host $CAMERA_HOST --environment <env-id>"
fi

echo ""
echo "📹 RTSP Stream Examples:"
echo "Main Stream: rtsp://admin:***@$CAMERA_HOST:554/unicast/c1/s1/live"
echo "Sub Stream:  rtsp://admin:***@$CAMERA_HOST:554/unicast/c1/s2/live"

echo ""
echo "🔍 Troubleshooting:"
echo "• NVR not responding: Check network connection and credentials"
echo "• Streams not validating: Verify RTSP port and paths"
echo "• Backend registration fails: Check authentication and project permissions"
