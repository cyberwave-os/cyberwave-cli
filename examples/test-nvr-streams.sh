#!/bin/bash

# Test NVR RTSP Stream Connectivity
# This script tests the connectivity to the Uniview NVR camera streams

echo "🎥 Testing Uniview NVR Camera Streams"
echo "======================================"

# Set environment variables for the NVR
export CAMERA_USERNAME="admin"
export CAMERA_PASSWORD="Stralis26$"
export CAMERA_HOST="192.168.1.8"
export CAMERA_PORT="554"
export CAMERA_PATH_1="unicast/c1/s1/live"
export CAMERA_PATH_2="unicast/c2/s1/live"

echo "📡 NVR Configuration:"
echo "  Host: $CAMERA_HOST:$CAMERA_PORT"
echo "  Username: $CAMERA_USERNAME"
echo "  Stream 1: $CAMERA_PATH_1"
echo "  Stream 2: $CAMERA_PATH_2"
echo ""

# Test basic network connectivity
echo "🔍 Step 1: Testing network connectivity..."
if ping -c 1 $CAMERA_HOST > /dev/null 2>&1; then
    echo "✅ Host $CAMERA_HOST is reachable"
else
    echo "❌ Host $CAMERA_HOST is not reachable"
    echo "   Check network connection and NVR power status"
    exit 1
fi

# Test RTSP port connectivity
echo ""
echo "🔍 Step 2: Testing RTSP port connectivity..."
if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$CAMERA_HOST/$CAMERA_PORT"; then
    echo "✅ RTSP port $CAMERA_PORT is open"
else
    echo "❌ RTSP port $CAMERA_PORT is not accessible"
    echo "   Check NVR RTSP service and firewall settings"
    exit 1
fi

# Test using CLI tool
echo ""
echo "🔍 Step 3: Testing RTSP streams using CLI..."
cd "$(dirname "$0")/.."

if python3 -c "
import sys
sys.path.insert(0, 'src')
from cyberwave_cli.plugins.edge.utils.stream_detector import test_uniview_nvr
import asyncio
asyncio.run(test_uniview_nvr())
" 2>/dev/null; then
    echo "✅ Stream detection completed successfully"
else
    echo "❌ CLI stream detection failed, trying manual RTSP test..."
    
    # Manual RTSP test using curl or ffprobe
    STREAM1_URL="rtsp://$CAMERA_USERNAME:$CAMERA_PASSWORD@$CAMERA_HOST:$CAMERA_PORT/$CAMERA_PATH_1"
    STREAM2_URL="rtsp://$CAMERA_USERNAME:$CAMERA_PASSWORD@$CAMERA_HOST:$CAMERA_PORT/$CAMERA_PATH_2"
    
    echo ""
    echo "🔍 Step 4: Manual RTSP stream testing..."
    echo "Stream 1 URL: $STREAM1_URL"
    echo "Stream 2 URL: $STREAM2_URL"
    
    # Try with ffprobe if available
    if command -v ffprobe > /dev/null 2>&1; then
        echo ""
        echo "📹 Testing Camera 1 with ffprobe..."
        if timeout 10 ffprobe -v quiet -print_format json -show_streams "$STREAM1_URL" > /dev/null 2>&1; then
            echo "✅ Camera 1 stream is accessible"
            
            # Get stream info
            echo "📊 Camera 1 Stream Information:"
            timeout 10 ffprobe -v quiet -print_format json -show_streams "$STREAM1_URL" 2>/dev/null | \
                python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if video:
        print(f'   Resolution: {video.get(\"width\", \"unknown\")}x{video.get(\"height\", \"unknown\")}')
        print(f'   Codec: {video.get(\"codec_name\", \"unknown\")}')
        print(f'   FPS: {video.get(\"r_frame_rate\", \"unknown\")}')
    else:
        print('   No video stream found')
except:
    print('   Unable to parse stream info')
"
        else
            echo "❌ Camera 1 stream is not accessible"
        fi
        
        echo ""
        echo "📹 Testing Camera 2 with ffprobe..."
        if timeout 10 ffprobe -v quiet -print_format json -show_streams "$STREAM2_URL" > /dev/null 2>&1; then
            echo "✅ Camera 2 stream is accessible"
            
            # Get stream info
            echo "📊 Camera 2 Stream Information:"
            timeout 10 ffprobe -v quiet -print_format json -show_streams "$STREAM2_URL" 2>/dev/null | \
                python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if video:
        print(f'   Resolution: {video.get(\"width\", \"unknown\")}x{video.get(\"height\", \"unknown\")}')
        print(f'   Codec: {video.get(\"codec_name\", \"unknown\")}')
        print(f'   FPS: {video.get(\"r_frame_rate\", \"unknown\")}')
    else:
        print('   No video stream found')
except:
    print('   Unable to parse stream info')
"
        else
            echo "❌ Camera 2 stream is not accessible"
        fi
    else
        echo "⚠️  ffprobe not available. Install ffmpeg for detailed stream analysis:"
        echo "   brew install ffmpeg  # macOS"
        echo "   apt install ffmpeg   # Ubuntu/Debian"
        echo ""
        echo "💡 You can test streams manually with:"
        echo "   ffplay \"$STREAM1_URL\""
        echo "   ffplay \"$STREAM2_URL\""
    fi
fi

echo ""
echo "✅ Stream connectivity test completed!"
echo ""
echo "🔗 Next Steps:"
echo "  1. If streams are accessible, they should work in the frontend"
echo "  2. For browser playback, consider adding WebRTC gateway"
echo "  3. Check device status in: http://localhost:3000/devices"
echo "  4. View node details: http://localhost:3000/nodes/21b0743b-50bf-4e1a-804e-a50499c88198"
