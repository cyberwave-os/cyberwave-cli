#!/bin/bash

# Start Video Proxy Service
# This script starts the edge video proxy service for secure camera streaming

echo "🎥 Starting Video Proxy Service"
echo "================================"

# Check if CLI is available
if ! command -v cyberwave &> /dev/null; then
    echo "❌ Cyberwave CLI not found"
    echo "   Please install the CLI first:"
    echo "   cd cyberwave-cli && pip3 install -e ."
    exit 1
fi

# Set environment
echo "🌐 Setting up environment..."
export CYBERWAVE_ENVIRONMENT="local"

# Check dependencies
echo "🔍 Checking video proxy dependencies..."
python3 -c "
try:
    import cv2
    print('✅ OpenCV available')
except ImportError:
    print('❌ OpenCV missing - install with: pip install opencv-python')
    exit(1)

try:
    import aiohttp
    print('✅ aiohttp available')
except ImportError:
    print('❌ aiohttp missing - install with: pip install aiohttp aiohttp-cors')
    exit(1)

try:
    import websockets
    print('✅ websockets available')
except ImportError:
    print('❌ websockets missing - install with: pip install websockets')
    exit(1)

try:
    import numpy as np
    print('✅ numpy available')
except ImportError:
    print('❌ numpy missing - install with: pip install numpy')
    exit(1)
"

if [ $? -ne 0 ]; then
    echo ""
    echo "💡 Install missing dependencies:"
    echo "   pip install opencv-python aiohttp aiohttp-cors websockets numpy"
    exit 1
fi

# Show camera configuration
echo ""
echo "📹 Camera Configuration:"
echo "   NVR Host: 192.168.1.6:554"
echo "   Cameras: 8 Uniview cameras"
echo "   Authentication: Secure (credentials handled by edge node)"
echo ""

# Show service information
echo "🚀 Starting proxy service..."
echo "   Port: 8001"
echo "   Analysis: Enabled (motion detection)"
echo "   Backend: http://localhost:8000"
echo ""

echo "📡 Service endpoints will be:"
echo "   Streams list:     http://localhost:8001/streams"
echo "   Camera 1 MJPEG:   http://localhost:8001/streams/1/mjpeg"
echo "   Camera 1 snapshot: http://localhost:8001/streams/1/snapshot"
echo "   WebSocket events: ws://localhost:8001/ws"
echo "   Health check:     http://localhost:8001/health"
echo ""

echo "🔗 Frontend integration:"
echo "   Devices page:     http://localhost:3000/devices"
echo "   Node page:        http://localhost:3000/nodes/21b0743b-50bf-4e1a-804e-a50499c88198"
echo ""

echo "⚠️  Important notes:"
echo "   • RTSP credentials are secure and not exposed to frontend"
echo "   • Motion detection events are sent to backend"
echo "   • Streams are converted from RTSP to MJPEG for browser compatibility"
echo "   • Press Ctrl+C to stop the service"
echo ""

read -p "🎬 Ready to start video proxy service? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Cancelled by user"
    exit 0
fi

echo ""
echo "🎥 Starting Video Proxy Service..."
echo "=================================="

# Change to CLI directory
cd "$(dirname "$0")/.."

# Start the video proxy service
cyberwave edge start-video-proxy --port 8001 --analysis

echo ""
echo "✅ Video proxy service stopped"
