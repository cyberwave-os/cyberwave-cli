#!/bin/bash

# End-to-End Device Registration Workflow
# This script demonstrates the complete flow from CLI to frontend verification

set -e  # Exit on error

echo "🚀 End-to-End Device Registration Workflow"
echo "==========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:3000"
PROJECT_NAME="Edge Camera Test"
ENVIRONMENT_NAME="Camera Lab"

echo ""
echo -e "${BLUE}Step 1: Environment Setup${NC}"
echo "========================="

# Set environment to local
echo "Setting environment to local..."
cyberwave edge environment local

# Show node info
echo ""
echo "Node identity:"
cyberwave edge node-info

echo ""
echo -e "${BLUE}Step 2: Authentication${NC}"
echo "====================="

# Check if already authenticated
echo "Checking authentication status..."
if cyberwave auth status | grep -q "✓ Authenticated"; then
    echo -e "${GREEN}✅ Already authenticated${NC}"
else
    echo -e "${YELLOW}⚠️ Not authenticated. Please authenticate first:${NC}"
    echo "Run: cyberwave auth login --backend-url $BACKEND_URL"
    echo "Or start the backend server at $BACKEND_URL"
    exit 1
fi

echo ""
echo -e "${BLUE}Step 3: Project and Environment Setup${NC}"
echo "====================================="

# Create project (this will fail gracefully if it already exists)
echo "Creating project: $PROJECT_NAME"
PROJECT_RESULT=$(cyberwave projects create "$PROJECT_NAME" --description "Automated edge camera testing" 2>&1 || true)

# Extract project ID (try to get from creation or list existing)
echo "Getting project ID..."
PROJECT_ID=$(cyberwave projects list --format json | grep -o '"id":"[^"]*"' | grep -o '[^"]*"$' | sed 's/"$//' | head -1 2>/dev/null || echo "")

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}❌ Could not get project ID. Please check authentication and backend connectivity.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Project ID: $PROJECT_ID${NC}"

# Create environment
echo ""
echo "Creating environment: $ENVIRONMENT_NAME"
ENVIRONMENT_RESULT=$(cyberwave environments create "$ENVIRONMENT_NAME" --project-id "$PROJECT_ID" --setup-cameras --dimensions "10x8x3" 2>&1 || true)

# Get environment ID
echo "Getting environment ID..."
ENVIRONMENT_ID=$(cyberwave environments list --project-id "$PROJECT_ID" --format json | grep -o '"id":"[^"]*"' | grep -o '[^"]*"$' | sed 's/"$//' | head -1 2>/dev/null || echo "")

if [ -z "$ENVIRONMENT_ID" ]; then
    echo -e "${YELLOW}⚠️ Could not get environment ID, but continuing...${NC}"
    ENVIRONMENT_ID="default-env"
fi

echo -e "${GREEN}✅ Environment ID: $ENVIRONMENT_ID${NC}"

echo ""
echo -e "${BLUE}Step 4: Camera Discovery${NC}"
echo "======================="

# Discover cameras on the network
echo "Discovering IP cameras on the network..."
cyberwave edge ip-camera discover --timeout 10 --auto-install-deps --save

# Check if any cameras were found
CAMERA_FILE="$HOME/.cyberwave/camera_discovery.json"
if [ -f "$CAMERA_FILE" ]; then
    CAMERA_COUNT=$(cat "$CAMERA_FILE" | grep -o '"ip_address"' | wc -l 2>/dev/null || echo "0")
    echo -e "${GREEN}✅ Found $CAMERA_COUNT camera(s)${NC}"
    
    if [ "$CAMERA_COUNT" -gt 0 ]; then
        echo "Camera details:"
        cat "$CAMERA_FILE" | head -20
    fi
else
    echo -e "${YELLOW}⚠️ No camera discovery file found${NC}"
    CAMERA_COUNT=0
fi

echo ""
echo -e "${BLUE}Step 5: Device Registration${NC}"
echo "=========================="

# Register discovered cameras or create a test camera
if [ "$CAMERA_COUNT" -gt 0 ]; then
    echo "Registering discovered cameras..."
    
    # Get first camera IP (parse JSON)
    FIRST_CAMERA=$(cat "$CAMERA_FILE" | grep -o '"ip_address":"[^"]*"' | head -1 | cut -d'"' -f4)
    
    if [ ! -z "$FIRST_CAMERA" ]; then
        echo "Registering camera: $FIRST_CAMERA"
        
        # Register camera as device
        cyberwave edge ip-camera register \
            --camera "$FIRST_CAMERA" \
            --environment "$ENVIRONMENT_ID" \
            --name "Discovered_Camera_${FIRST_CAMERA//./_}" \
            --x 2.0 --y 1.5 --z 2.0
        
        echo -e "${GREEN}✅ Camera $FIRST_CAMERA registered${NC}"
    fi
else
    echo "No cameras discovered. Creating a test camera entry..."
    
    # Create a test camera entry
    TEST_CAMERA_IP="192.168.1.100"
    
    cyberwave edge ip-camera register \
        --camera "$TEST_CAMERA_IP" \
        --environment "$ENVIRONMENT_ID" \
        --name "Test_Camera_Demo" \
        --x 1.0 --y 2.0 --z 2.5 \
        --no-test
    
    echo -e "${GREEN}✅ Test camera $TEST_CAMERA_IP registered${NC}"
fi

echo ""
echo -e "${BLUE}Step 6: Verification${NC}"
echo "=================="

# Show registered devices
echo "Checking registered devices..."
cyberwave devices list --project-id "$PROJECT_ID" 2>/dev/null || echo "Device listing not available"

echo ""
echo -e "${BLUE}Step 7: Frontend Verification${NC}"
echo "============================="

echo -e "${CYAN}To verify the devices are visible in the frontend:${NC}"
echo ""
echo "1. Open your browser and go to:"
echo -e "   ${FRONTEND_URL}/devices"
echo ""
echo "2. You should see the registered cameras in the devices list"
echo ""
echo "3. Check the specific project:"
echo -e "   ${FRONTEND_URL}/project/${PROJECT_ID}/devices"
echo ""
echo "4. Environment details:"
echo -e "   ${FRONTEND_URL}/project/${PROJECT_ID}/environments/${ENVIRONMENT_ID}"
echo ""

echo -e "${BLUE}Step 8: Edge Node Verification${NC}"
echo "=============================="

echo "Node registration information for backend verification:"
cyberwave edge register-node

echo ""
echo -e "${GREEN}🎉 End-to-End Workflow Complete!${NC}"
echo ""
echo -e "${CYAN}Summary:${NC}"
echo "• Node ID: $(cyberwave edge node-info --export | grep '"node_id"' | cut -d'"' -f4 2>/dev/null || echo 'N/A')"
echo "• Environment: local ($BACKEND_URL ↔ $FRONTEND_URL)"
echo "• Project: $PROJECT_NAME ($PROJECT_ID)"
echo "• Environment: $ENVIRONMENT_NAME ($ENVIRONMENT_ID)"
echo "• Cameras: $CAMERA_COUNT discovered, at least 1 registered"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Open $FRONTEND_URL/devices to see registered devices"
echo "2. Start edge processing: cyberwave edge ip-camera analyze"
echo "3. Monitor telemetry and device status"
echo ""
echo -e "${BLUE}Troubleshooting:${NC}"
echo "• If devices don't appear: Check authentication and project permissions"
echo "• If camera discovery fails: Check network connectivity and permissions"
echo "• If registration fails: Verify backend is running and accessible"
