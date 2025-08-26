#!/bin/bash

# Simple Device Registration Test
# Tests the actual device registration with backend

echo "📱 Testing Device Registration"
echo "============================="

# Make scripts executable
chmod +x examples/*.sh

# Show current environment
echo ""
echo "1️⃣ Current Configuration:"
cyberwave edge environment

echo ""
echo "2️⃣ Node Identity:"
cyberwave edge node-info

echo ""
echo "3️⃣ Authentication Status:"
cyberwave auth status

echo ""
echo "4️⃣ Available Projects:"
cyberwave projects list 2>/dev/null || echo "No projects available or not authenticated"

echo ""
echo "5️⃣ Camera Discovery:"
echo "Discovering cameras with dependencies..."
cyberwave edge ip-camera discover --timeout 5 --auto-install-deps

echo ""
echo "6️⃣ Test Device Registration:"
echo "Creating a test camera device..."

# Try to register a test camera
cyberwave edge ip-camera register \
  --camera "192.168.1.50" \
  --environment "test-environment" \
  --name "CLI_Test_Camera" \
  --x 1.0 --y 1.0 --z 2.0 \
  --no-test \
  2>&1 || echo "Registration failed - this is expected if backend is not running"

echo ""
echo "7️⃣ Backend Connectivity Check:"
echo "Testing backend health..."

# Test direct connectivity
curl -s http://localhost:8000/health 2>/dev/null && echo "✅ Backend is responding" || echo "❌ Backend not available at http://localhost:8000"

echo ""
echo "8️⃣ Next Steps:"
echo "To complete the test:"
echo "1. Start the backend: cd cyberwave/cyberwave-backend && docker-compose -f local.yml up"
echo "2. Authenticate: cyberwave auth login"
echo "3. Create project: cyberwave projects create 'Test Project'"
echo "4. Re-run this script"
echo "5. Check frontend: http://localhost:3000/devices"
