#!/bin/bash

# Test Connectivity System
# Demonstrates online/offline modes and backend connectivity

echo "🌐 Testing Connectivity & Node Identity System"
echo "=============================================="

# Test 1: Show node identity (created automatically on first run)
echo ""
echo "1️⃣ Testing node identity..."
echo "This shows the unique node ID created for this edge node:"

cyberwave edge node-info

echo ""
echo "2️⃣ Testing node registration info..."
echo "This shows the information needed for backend registration:"

cyberwave edge register-node

echo ""
echo "3️⃣ Testing connectivity check..."
echo "This will show what happens when backend is unavailable:"

# Test device dependency check (should work offline)
cyberwave edge check-deps --device camera/ip

echo ""
echo "4️⃣ Testing camera registration with connectivity..."
echo "This will demonstrate offline mode setup:"

# Test camera registration (will trigger connectivity flow)
cyberwave edge ip-camera register \
  --camera "192.168.1.100" \
  --environment "test-env" \
  --name "Test Camera" \
  --offline

echo ""
echo "5️⃣ Testing device discovery..."
echo "Discovery should work even without backend:"

# Test discovery (works offline)
cyberwave edge ip-camera discover --timeout 2

echo ""
echo "✅ Connectivity tests completed!"
echo ""
echo "💡 To test online mode:"
echo "   1. Start backend server"
echo "   2. Run: cyberwave edge ip-camera register --camera 192.168.1.100 --environment test"
echo ""
echo "💡 To setup offline mode:"
echo "   1. Visit: https://app.cyberwave.dev/edge/register"
echo "   2. Create node and get auth token"
echo "   3. Run commands without --offline flag"
