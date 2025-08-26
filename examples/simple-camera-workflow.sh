#!/bin/bash

# Simple Camera Workflow
# Complete example in one script - easy to understand and modify

echo "🎬 Simple Camera Setup"
echo "====================="

# Step 1: Install what we need
echo "1️⃣ Installing camera dependencies..."
cyberwave edge install-deps --device camera/ip --no-confirm

# Step 2: Find cameras
echo "2️⃣ Looking for cameras..."
cyberwave edge camera discover --auto-install-deps

# Step 3: Create environment
echo "3️⃣ Creating environment..."
cyberwave environments create \
  --project "my-project" \
  --name "Security Cameras" \
  --setup-cameras

# Step 4: Register a camera (change this IP to your camera)
echo "4️⃣ Registering camera..."
cyberwave edge camera register \
  --camera "192.168.1.100" \
  --environment "my-environment" \
  --name "Front Door Camera"

# Step 5: Start monitoring
echo "5️⃣ Starting motion detection..."
cyberwave edge run --background

echo ""
echo "✅ Done! Your camera is now monitored."
echo ""
echo "Check status: cyberwave edge status"
echo "Stop monitoring: cyberwave edge stop"
