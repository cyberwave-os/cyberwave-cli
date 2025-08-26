#!/bin/bash

# Test Live Camera System End-to-End
# Uses the verified working Uniview NVR cameras at 192.168.1.6

echo "🎥 Testing Live Camera System - End-to-End"
echo "=========================================="

# Verified camera configuration from previous tests
echo "📹 Camera Configuration:"
echo "   NVR: 192.168.1.6:554 (VERIFIED WORKING)"
echo "   Cameras: 8 Uniview cameras"
echo "   Authentication: admin:Stralis26$ (VERIFIED)"
echo "   Streams: unicast/c1/s1/live through unicast/c8/s1/live"
echo ""

# Set environment
export CYBERWAVE_ENVIRONMENT="local"

# Step 1: Quick connectivity test
echo "🔍 Step 1: Testing camera connectivity..."
python3 << 'EOF'
import socket
import time

def test_camera_connectivity():
    host = '192.168.1.6'
    port = 554
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print("✅ NVR is accessible at 192.168.1.6:554")
            return True
        else:
            print("❌ Cannot connect to NVR")
            return False
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False

if test_camera_connectivity():
    print("🎯 Proceeding with video proxy test...")
else:
    print("❌ Cannot continue without camera connectivity")
    exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "❌ Camera connectivity test failed"
    exit 1
fi

echo ""

# Step 2: Check dependencies
echo "🔍 Step 2: Checking video proxy dependencies..."
python3 -c "
import sys
missing = []

try:
    import cv2
    print('✅ OpenCV available')
except ImportError:
    print('❌ OpenCV missing')
    missing.append('opencv-python')

try:
    import aiohttp
    print('✅ aiohttp available')
except ImportError:
    print('❌ aiohttp missing')
    missing.append('aiohttp aiohttp-cors')

try:
    import websockets
    print('✅ websockets available')
except ImportError:
    print('❌ websockets missing')
    missing.append('websockets')

try:
    import numpy as np
    print('✅ numpy available')
except ImportError:
    print('❌ numpy missing')
    missing.append('numpy')

if missing:
    print(f'\\n💡 Install missing dependencies:')
    print(f'   pip install {\" \".join(missing)}')
    sys.exit(1)
else:
    print('\\n✅ All dependencies available')
"

if [ $? -ne 0 ]; then
    echo "❌ Missing dependencies"
    exit 1
fi

echo ""

# Step 3: Test CLI video proxy command
echo "🔍 Step 3: Testing CLI video proxy command availability..."
cd "$(dirname "$0")/.."

if cyberwave edge start-video-proxy --help > /dev/null 2>&1; then
    echo "✅ Video proxy command available"
else
    echo "❌ Video proxy command not found"
    echo "   Make sure CLI is properly installed: pip3 install -e ."
    exit 1
fi

echo ""

# Step 4: Create test camera configuration
echo "🔍 Step 4: Creating test camera configuration..."
cat > /tmp/test_cameras.json << 'EOF'
{
  "cameras": [
    {
      "id": 1,
      "name": "D1 (Camerette)",
      "rtsp_url": "rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c1/s1/live"
    },
    {
      "id": 2,
      "name": "D2 (Salone)",
      "rtsp_url": "rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c2/s1/live"
    },
    {
      "id": 3,
      "name": "D3 (Ingresso)",
      "rtsp_url": "rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c3/s1/live"
    },
    {
      "id": 4,
      "name": "D4 (Salone > Ovest)",
      "rtsp_url": "rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c4/s1/live"
    }
  ]
}
EOF

echo "✅ Created test configuration with 4 cameras"
echo ""

# Step 5: Check if backend is running
echo "🔍 Step 5: Checking backend availability..."
if curl -s http://localhost:8000/api/v1/health > /dev/null 2>&1; then
    echo "✅ Backend is running at http://localhost:8000"
else
    echo "⚠️  Backend not accessible at http://localhost:8000"
    echo "   Starting backend is recommended but not required for basic testing"
fi

echo ""

# Step 6: Quick stream test
echo "🔍 Step 6: Testing individual camera streams..."
python3 << 'EOF'
import socket
import base64
import time

def test_single_stream(camera_id, camera_name):
    host = '192.168.1.6'
    port = 554
    username = 'admin'
    password = 'Stralis26$'
    path = f'unicast/c{camera_id}/s1/live'
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        
        credentials = base64.b64encode(f'{username}:{password}'.encode()).decode()
        
        request = f'''DESCRIBE rtsp://{username}:{password}@{host}:{port}/{path} RTSP/1.0\r
CSeq: {camera_id}\r
User-Agent: TestScript/1.0\r
Authorization: Basic {credentials}\r
Accept: application/sdp\r
\r
'''
        
        sock.send(request.encode())
        response = sock.recv(4096).decode()
        sock.close()
        
        if '200 OK' in response:
            print(f'✅ Camera {camera_id} ({camera_name}): Stream accessible')
            return True
        else:
            print(f'❌ Camera {camera_id} ({camera_name}): {response.split()[2] if len(response.split()) > 2 else "Error"}')
            return False
            
    except Exception as e:
        print(f'❌ Camera {camera_id} ({camera_name}): {e}')
        return False

# Test first 4 cameras
cameras = [
    (1, "D1 (Camerette)"),
    (2, "D2 (Salone)"),
    (3, "D3 (Ingresso)"),
    (4, "D4 (Salone > Ovest)")
]

working_cameras = 0
for camera_id, camera_name in cameras:
    if test_single_stream(camera_id, camera_name):
        working_cameras += 1
    time.sleep(0.5)

print(f'\n📊 Result: {working_cameras}/{len(cameras)} cameras accessible')

if working_cameras > 0:
    print('🎉 Ready for video proxy testing!')
else:
    print('❌ No cameras accessible - check NVR configuration')
    exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "❌ Camera stream test failed"
    exit 1
fi

echo ""
echo "🚀 Ready to start video proxy service!"
echo ""
echo "📋 Next steps:"
echo "   1. Start video proxy:    cyberwave edge start-video-proxy --port 8001"
echo "   2. Test endpoints:       curl http://localhost:8001/streams"
echo "   3. View MJPEG stream:    http://localhost:8001/streams/1/mjpeg"
echo "   4. Check health:         http://localhost:8001/health"
echo "   5. Frontend integration: http://localhost:3000/devices"
echo ""
echo "🎥 Manual test commands:"
echo "   # View in browser:"
echo "   open http://localhost:8001/streams/1/mjpeg"
echo ""
echo "   # Test with VLC:"
echo "   vlc http://localhost:8001/streams/1/mjpeg"
echo ""
echo "   # Test with curl:"
echo "   curl -s http://localhost:8001/streams | jq"
echo ""

# Ask if user wants to start the service
read -p "🎬 Start video proxy service now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "🎥 Starting video proxy service..."
    echo "   Press Ctrl+C to stop"
    echo ""
    
    # Start the service with custom config
    cyberwave edge start-video-proxy --port 8001 --cameras /tmp/test_cameras.json --analysis
else
    echo "✅ Test preparation completed!"
    echo "   Run: cyberwave edge start-video-proxy --port 8001"
fi
