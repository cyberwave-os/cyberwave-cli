#!/bin/bash

# Example 3: Discover Cameras
# Simple camera discovery on your network

echo "📷 Camera Discovery Example"
echo "=========================="

# Discover cameras on the network
echo "Scanning for cameras on your network..."
cyberwave edge ip-camera discover --auto-install-deps

echo ""
echo "💡 You can also specify a network range:"
echo "   cyberwave edge ip-camera discover --network 192.168.1.0/24"
echo ""
echo "📄 Results are saved to ~/.cyberwave/discovered_cameras.json"
