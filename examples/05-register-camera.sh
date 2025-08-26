#!/bin/bash

# Example 5: Register a Camera
# Shows how to register a camera as a sensor

echo "📋 Register Camera Example"
echo "=========================="

# Register a camera (replace IP with your camera)
CAMERA_IP="192.168.1.100"
ENVIRONMENT_ID="your-environment-id"

echo "Registering camera $CAMERA_IP..."
cyberwave edge camera register \
  --camera "$CAMERA_IP" \
  --environment "$ENVIRONMENT_ID" \
  --name "Security Camera 1"

echo ""
echo "✅ Camera registered!"
echo ""
echo "💡 You can also register multiple cameras:"
echo "   cyberwave edge camera register --camera 192.168.1.101 --name \"Camera 2\""
