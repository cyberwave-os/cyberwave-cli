#!/bin/bash

# Test Uniview NVR Integration
# Uses real NVR credentials and system information

echo "🎥 Testing Uniview NVR Integration"
echo "=================================="

# Set up Uniview NVR credentials and configuration
export CAMERA_USERNAME="admin"
export CAMERA_PASSWORD="Stralis26$"
export CAMERA_HOST="192.168.1.8"
export CAMERA_PORT="554"
export CAMERA_MANUFACTURER="uniview"

# Specific Uniview RTSP paths
export CAMERA_PATH_1="unicast/c1/s1/live"
export CAMERA_PATH_2="unicast/c2/s1/live"

echo ""
echo "🔧 Environment Configuration:"
echo "Host: $CAMERA_HOST"
echo "Username: $CAMERA_USERNAME"
echo "Manufacturer: $CAMERA_MANUFACTURER"
echo "RTSP Port: $CAMERA_PORT"

echo ""
echo "1️⃣ Setting up credentials..."
# Setup interactive credentials (will use environment variables)
cyberwave edge nvr setup-credentials

echo ""
echo "2️⃣ Checking dependencies..."
cyberwave edge check-deps --device camera/nvr --auto-install-deps

echo ""
echo "3️⃣ Discovering NVR system..."
# Discover the Uniview NVR system
cyberwave edge nvr discover \
  --host "$CAMERA_HOST" \
  --manufacturer "$CAMERA_MANUFACTURER" \
  --validate \
  --auto-install-deps

echo ""
echo "4️⃣ Showing NVR configuration..."
cyberwave edge nvr show --host "$CAMERA_HOST"

echo ""
echo "5️⃣ Validating camera streams..."
# Validate some camera streams
cyberwave edge nvr validate \
  --host "$CAMERA_HOST" \
  --limit 3 \
  --auto-install-deps

echo ""
echo "6️⃣ Listing discovered NVR systems..."
cyberwave edge nvr list

echo ""
echo "7️⃣ Exporting camera configuration..."
# Export configuration to file
cyberwave edge nvr export \
  --host "$CAMERA_HOST" \
  --output ~/.cyberwave/uniview_cameras.json

echo ""
echo "8️⃣ Testing offline registration..."
# Register cameras in offline mode
cyberwave edge nvr register \
  --host "$CAMERA_HOST" \
  --environment "Security-Lab" \
  --location "Main Building" \
  --main-only \
  --offline

echo ""
echo "✅ Uniview NVR Integration Test Complete!"
echo ""
echo "📋 Summary:"
echo "• NVR Host: $CAMERA_HOST"
echo "• Manufacturer: Uniview"
echo "• Configuration saved locally"
echo "• Ready for backend integration"
echo ""
echo "🔗 Next Steps:"
echo "1. Start backend server"
echo "2. Authenticate: cyberwave auth login"
echo "3. Create project: cyberwave projects create 'Security System'"
echo "4. Register online: cyberwave edge nvr register --host $CAMERA_HOST --environment <env-id>"
echo "5. Check frontend: http://localhost:3000/devices"
