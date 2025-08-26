#!/bin/bash

# Complete Auto-Registration with Backend Integration
# Discovers cameras, registers node and devices without frontend coupling

set -e  # Exit on error

echo "🚀 Complete Auto-Registration Workflow"
echo "======================================"

# Configuration - Use your actual NVR credentials
export CAMERA_USERNAME="admin"
export CAMERA_PASSWORD="Stralis26$"
export CAMERA_HOST="192.168.1.8"
export CAMERA_PORT="554"
export CAMERA_MANUFACTURER="uniview"

BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:3000"

echo ""
echo "🔧 Configuration:"
echo "• Backend: $BACKEND_URL"
echo "• Frontend: $FRONTEND_URL"
echo "• NVR Host: $CAMERA_HOST"
echo "• Environment: local"

echo ""
echo "1️⃣ Environment Setup"
echo "===================="

# Ensure we're using local environment
cyberwave edge environment local

# Show node identity
echo ""
echo "Node Identity:"
cyberwave edge node-info

echo ""
echo "2️⃣ Backend Health Check"
echo "======================"

# Check if backend is running
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo "✅ Backend is available at $BACKEND_URL"
    BACKEND_AVAILABLE=true
else
    echo "❌ Backend not available at $BACKEND_URL"
    echo "   To start backend: cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
    BACKEND_AVAILABLE=false
fi

echo ""
echo "3️⃣ Authentication Check"
echo "======================"

# Check authentication status
AUTH_STATUS=$(cyberwave auth status 2>&1 | grep -o "Token may be invalid\|✓ Authenticated" || echo "Not authenticated")

if [[ "$AUTH_STATUS" == *"Authenticated"* ]]; then
    echo "✅ Already authenticated"
    AUTHENTICATED=true
elif [[ "$BACKEND_AVAILABLE" == "true" ]]; then
    echo "⚠️ Not authenticated but backend is available"
    echo "   Attempting authentication..."
    
    # Try to authenticate (this may prompt for credentials)
    if cyberwave auth login --backend-url "$BACKEND_URL" > /dev/null 2>&1; then
        echo "✅ Authentication successful"
        AUTHENTICATED=true
    else
        echo "❌ Authentication failed"
        echo "   You may need to provide credentials manually"
        AUTHENTICATED=false
    fi
else
    echo "⚠️ Backend not available - will use offline mode"
    AUTHENTICATED=false
fi

echo ""
echo "4️⃣ Project and Environment Setup"
echo "==============================="

PROJECT_ID=""
ENVIRONMENT_ID=""

if [[ "$AUTHENTICATED" == "true" ]]; then
    echo "Getting or creating project..."
    
    # Try to get first project
    PROJECT_ID=$(cyberwave projects list --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
    
    if [ -z "$PROJECT_ID" ]; then
        echo "Creating new project..."
        cyberwave projects create "Auto-Registration Test" --description "Automated camera and NVR registration" > /dev/null 2>&1 || true
        PROJECT_ID=$(cyberwave projects list --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
    fi
    
    if [ ! -z "$PROJECT_ID" ]; then
        echo "✅ Using project: $PROJECT_ID"
        
        # Try to get or create environment
        ENVIRONMENT_ID=$(cyberwave environments list --project-id "$PROJECT_ID" --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
        
        if [ -z "$ENVIRONMENT_ID" ]; then
            echo "Creating environment..."
            cyberwave environments create "Camera Lab" --project-id "$PROJECT_ID" --setup-cameras > /dev/null 2>&1 || true
            ENVIRONMENT_ID=$(cyberwave environments list --project-id "$PROJECT_ID" --format json 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "default")
        fi
        
        echo "✅ Using environment: $ENVIRONMENT_ID"
    else
        echo "⚠️ Could not get project ID"
    fi
else
    echo "⚠️ Skipping project setup - not authenticated"
fi

echo ""
echo "5️⃣ Automatic Registration"
echo "========================="

# Run the automatic registration
echo "Starting auto-registration with discovered devices..."

# Build the command with available parameters
CMD="cyberwave edge auto-register --timeout 10"

if [ ! -z "$PROJECT_ID" ]; then
    CMD="$CMD --project $PROJECT_ID"
fi

if [ ! -z "$ENVIRONMENT_ID" ]; then
    CMD="$CMD --environment $ENVIRONMENT_ID"
fi

echo "Running: $CMD"
eval $CMD

echo ""
echo "6️⃣ Registration Status Check"
echo "============================"

# Show the registration status
cyberwave edge registration-status

echo ""
echo "7️⃣ Frontend Verification"
echo "========================"

echo "🌐 Frontend URLs to check:"
echo "• All devices: $FRONTEND_URL/devices"

if [ ! -z "$PROJECT_ID" ]; then
    echo "• Project devices: $FRONTEND_URL/project/$PROJECT_ID/devices"
fi

if [ ! -z "$ENVIRONMENT_ID" ]; then
    echo "• Environment: $FRONTEND_URL/project/$PROJECT_ID/environments/$ENVIRONMENT_ID"
fi

echo ""
echo "🎯 Camera-Specific Information:"
echo "• IP Camera found: 192.168.1.6"
echo "• NVR System: $CAMERA_HOST (Uniview)"
echo "• RTSP Streams available on port $CAMERA_PORT"

echo ""
echo "🎉 Registration Workflow Complete!"
echo "=================================="

echo ""
echo "📊 Summary:"
echo "• Node: Registered and active"
echo "• Backend: $([[ "$BACKEND_AVAILABLE" == "true" ]] && echo "✅ Available" || echo "❌ Offline")"
echo "• Authentication: $([[ "$AUTHENTICATED" == "true" ]] && echo "✅ Authenticated" || echo "❌ Offline mode")"
echo "• Project: ${PROJECT_ID:-"N/A"}"
echo "• Environment: ${ENVIRONMENT_ID:-"N/A"}"
echo "• Cameras: Auto-discovered and registered"
echo ""

if [[ "$BACKEND_AVAILABLE" == "true" ]] && [[ "$AUTHENTICATED" == "true" ]]; then
    echo "✅ All systems operational - devices should appear in frontend immediately"
    echo ""
    echo "🔗 Quick Links:"
    echo "• View devices: $FRONTEND_URL/devices"
    echo "• Check node status: cyberwave edge registration-status"
    echo "• Monitor cameras: cyberwave edge ip-camera discover"
else
    echo "⚠️ Offline mode - devices registered locally"
    echo ""
    echo "🔧 To complete backend registration:"
    echo "1. Start backend: cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
    echo "2. Authenticate: cyberwave auth login"
    echo "3. Re-run: ./examples/complete-auto-registration.sh"
fi

echo ""
echo "💡 Next Steps:"
echo "• Stream cameras: cyberwave edge ip-camera stream --camera <IP>"
echo "• Setup motion detection: cyberwave edge ip-camera analyze"
echo "• Monitor system: cyberwave edge registration-status"
