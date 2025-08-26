#!/bin/bash

# NVR Connection Diagnostic Tool
# Comprehensive testing for Uniview NVR connectivity issues

echo "🔍 NVR Connection Diagnostic Tool"
echo "=================================="

# Configuration
NVR_IP="192.168.1.8"
USERNAME="admin"
PASSWORD="Stralis26$"

echo "📡 Testing NVR: $NVR_IP"
echo "👤 Username: $USERNAME"
echo ""

# Step 1: Basic connectivity
echo "🔍 Step 1: Basic Network Connectivity"
echo "-------------------------------------"
if ping -c 3 $NVR_IP > /dev/null 2>&1; then
    echo "✅ Host $NVR_IP is reachable via ping"
else
    echo "❌ Host $NVR_IP is not reachable"
    echo "   - Check if NVR is powered on"
    echo "   - Verify network cable connections"
    echo "   - Check if you're on the same network"
    exit 1
fi

# Step 2: Port scanning
echo ""
echo "🔍 Step 2: Port Scanning"
echo "------------------------"

# Common NVR ports
PORTS=(554 8000 80 443 37777 37778)

for port in "${PORTS[@]}"; do
    echo -n "Testing port $port... "
    if nc -z -w5 $NVR_IP $port 2>/dev/null; then
        echo "✅ OPEN"
    else
        echo "❌ CLOSED/FILTERED"
    fi
done

# Step 3: Web interface test
echo ""
echo "🔍 Step 3: Web Interface Testing"
echo "--------------------------------"

# Test HTTP
echo -n "Testing HTTP (port 80)... "
HTTP_RESPONSE=$(curl -s -w "%{http_code}" -m 5 http://$NVR_IP/ -o /dev/null 2>/dev/null)
if [ "$HTTP_RESPONSE" != "000" ]; then
    echo "✅ HTTP responds with code: $HTTP_RESPONSE"
    echo "   💡 Web interface: http://$NVR_IP/"
else
    echo "❌ No HTTP response"
fi

# Test HTTPS
echo -n "Testing HTTPS (port 443)... "
HTTPS_RESPONSE=$(curl -s -w "%{http_code}" -m 5 -k https://$NVR_IP/ -o /dev/null 2>/dev/null)
if [ "$HTTPS_RESPONSE" != "000" ]; then
    echo "✅ HTTPS responds with code: $HTTPS_RESPONSE"
    echo "   💡 Secure web interface: https://$NVR_IP/"
else
    echo "❌ No HTTPS response"
fi

# Test common NVR web ports
for port in 8000 8080 8081; do
    echo -n "Testing HTTP on port $port... "
    WEB_RESPONSE=$(curl -s -w "%{http_code}" -m 5 http://$NVR_IP:$port/ -o /dev/null 2>/dev/null)
    if [ "$WEB_RESPONSE" != "000" ]; then
        echo "✅ Responds with code: $WEB_RESPONSE"
        echo "   💡 Web interface: http://$NVR_IP:$port/"
    else
        echo "❌ No response"
    fi
done

# Step 4: RTSP testing with different approaches
echo ""
echo "🔍 Step 4: RTSP Protocol Testing"
echo "--------------------------------"

# Test standard RTSP port
echo "Testing RTSP on port 554..."
RTSP_URLS=(
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/unicast/c1/s1/live"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/unicast/c2/s1/live"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/cam1"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/cam2"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/ch1"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/ch2"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/stream1"
    "rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/stream2"
)

# Use Python to test RTSP connectivity
echo "Using Python to test RTSP URLs..."
python3 << 'EOF'
import socket
import sys

def test_rtsp_port(host, port, timeout=5):
    """Test if RTSP port is accessible"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        return False

# Test RTSP port
nvr_ip = "192.168.1.8"
rtsp_port = 554

if test_rtsp_port(nvr_ip, rtsp_port):
    print(f"✅ RTSP port {rtsp_port} is accessible")
    
    # Try RTSP DESCRIBE request
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((nvr_ip, rtsp_port))
        
        # Send RTSP DESCRIBE request
        request = f"DESCRIBE rtsp://{nvr_ip}:{rtsp_port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: DiagnosticTool/1.0\r\n\r\n"
        sock.send(request.encode())
        
        response = sock.recv(4096).decode()
        sock.close()
        
        print("📡 RTSP Server Response:")
        print("   " + "\n   ".join(response.split('\n')[:5]))
        
        if "401" in response:
            print("🔐 Authentication required - trying with credentials...")
        elif "200" in response:
            print("✅ RTSP server responding correctly")
        else:
            print("⚠️ Unexpected RTSP response")
            
    except Exception as e:
        print(f"❌ RTSP handshake failed: {e}")
else:
    print(f"❌ RTSP port {rtsp_port} is not accessible")
    print("   Possible causes:")
    print("   - RTSP service is disabled")
    print("   - Different port is used")
    print("   - Firewall blocking connection")
    print("   - Authentication required at network level")

# Test alternative RTSP ports
alternative_ports = [555, 8554, 1554, 7001]
print(f"\n🔍 Testing alternative RTSP ports...")
for port in alternative_ports:
    if test_rtsp_port(nvr_ip, port, 3):
        print(f"✅ Found RTSP service on port {port}")
    else:
        print(f"❌ Port {port} not accessible")
EOF

# Step 5: Protocol alternatives
echo ""
echo "🔍 Step 5: Alternative Protocols"
echo "--------------------------------"

echo "Testing if cameras support other protocols..."

# Test HTTP streaming (MJPEG)
echo -n "Testing HTTP MJPEG stream... "
HTTP_STREAM_RESPONSE=$(curl -s -w "%{http_code}" -m 5 http://$USERNAME:$PASSWORD@$NVR_IP/video.cgi -o /dev/null 2>/dev/null)
if [ "$HTTP_STREAM_RESPONSE" != "000" ]; then
    echo "✅ HTTP stream responds: $HTTP_STREAM_RESPONSE"
else
    echo "❌ No HTTP stream response"
fi

# Step 6: Network diagnostics
echo ""
echo "🔍 Step 6: Network Diagnostics"
echo "------------------------------"

echo "Network route to NVR:"
if command -v traceroute > /dev/null 2>&1; then
    traceroute -m 5 $NVR_IP 2>/dev/null | head -3
elif command -v tracert > /dev/null 2>&1; then
    tracert -h 5 $NVR_IP 2>/dev/null | head -5
else
    echo "   traceroute not available"
fi

echo ""
echo "Local network interface:"
if command -v ip > /dev/null 2>&1; then
    ip route | grep default
elif command -v route > /dev/null 2>&1; then
    route -n get default 2>/dev/null | grep interface
else
    echo "   Network info not available"
fi

echo ""
echo "🔍 Diagnostic Summary"
echo "===================="
echo ""
echo "💡 Troubleshooting Steps:"
echo "1. Check NVR web interface accessibility"
echo "2. Verify RTSP service is enabled in NVR settings"
echo "3. Check if default RTSP port (554) is changed"
echo "4. Verify username/password credentials"
echo "5. Check firewall settings on NVR"
echo "6. Try connecting from NVR's local network"
echo ""
echo "🔗 Common NVR URLs to try:"
echo "   Web: http://$NVR_IP/ or http://$NVR_IP:8000/"
echo "   RTSP: rtsp://$USERNAME:$PASSWORD@$NVR_IP:554/"
echo ""
echo "📖 For Uniview NVR documentation:"
echo "   Check manual for correct RTSP URLs and port settings"
