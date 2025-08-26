#!/bin/bash

# Example 2: Install Dependencies
# Shows how to install dependencies for camera features

echo "📦 Installing Dependencies Example"
echo "=================================="

# Install camera dependencies
echo "Installing camera dependencies..."
cyberwave edge install-deps --device camera/ip --confirm

echo ""
echo "✅ Dependencies installed!"
echo ""
echo "You can now use camera features like:"
echo "  cyberwave edge camera discover"
echo "  cyberwave edge camera register --camera 192.168.1.100"
