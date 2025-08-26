#!/bin/bash

# Example 4: Create Camera Environment
# Shows how to create an environment for camera monitoring

echo "🏗️ Create Environment Example"
echo "============================="

# Create a simple camera environment
echo "Creating camera lab environment..."
cyberwave environments setup-camera-lab \
  --project demo-project \
  --name "My Camera Lab" \
  --dimensions "5x3x3"

echo ""
echo "✅ Environment created!"
echo ""
echo "💡 Next steps:"
echo "   1. Register cameras: cyberwave edge camera register --camera 192.168.1.100"
echo "   2. Start edge node: cyberwave edge run"
