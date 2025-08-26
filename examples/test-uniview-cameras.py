#!/usr/bin/env python3
"""
Test Uniview NVR Camera Streams
Comprehensive testing for the 8 Uniview cameras connected to the NVR
"""

import asyncio
import socket
import time
import subprocess
import json
import sys
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Camera:
    """Camera information from NVR"""
    id: int
    name: str
    internal_ip: str
    port: int
    brand: str
    model: str
    remote_id: int = 1


@dataclass
class StreamResult:
    """Stream test result"""
    camera: Camera
    rtsp_url: str
    status: str  # 'accessible', 'inaccessible', 'timeout', 'error'
    response_time_ms: Optional[float] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    error_message: Optional[str] = None


class UnivielwStreamTester:
    """Test Uniview camera streams through NVR"""
    
    def __init__(self, nvr_ip: str = "192.168.1.8", nvr_port: int = 554, timeout: int = 10):
        self.nvr_ip = nvr_ip
        self.nvr_port = nvr_port
        self.timeout = timeout
        self.username = "admin"
        self.password = "Stralis26$"
        
        # Camera configuration from NVR interface
        self.cameras = [
            Camera(1, "D1 (Camerette)", "172.16.0.12", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
            Camera(2, "D2 (Salone)", "172.16.0.10", 80, "UNIVIEW", "IPC3614LB-SF28K-G"),
            Camera(3, "D3 (Ingresso)", "172.16.0.11", 80, "UNIVIEW", "IPC3614LB-SF28K-G"),
            Camera(4, "D4 (Salone > Ovest)", "172.16.0.102", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
            Camera(5, "D5 (Salone > Sud)", "172.16.0.100", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
            Camera(6, "D6 (Cameretta > Est)", "172.16.0.105", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
            Camera(7, "D7 (Settimo piano)", "172.16.0.104", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
            Camera(8, "D8 (Camera Letto)", "172.16.0.101", 80, "UNIVIEW", "IPC2124LB-SF28KM-G"),
        ]
    
    def generate_rtsp_urls(self, camera: Camera) -> List[str]:
        """Generate possible RTSP URLs for a camera"""
        urls = []
        
        # Common Uniview RTSP URL patterns
        patterns = [
            f"unicast/c{camera.id}/s1/live",  # Standard pattern
            f"unicast/c{camera.id}/s0/live",  # Alternative stream
            f"cam{camera.id}",                # Simple pattern
            f"ch{camera.id}",                 # Channel pattern
            f"stream{camera.id}",             # Stream pattern
            f"cam/realmonitor?channel={camera.id}&subtype=0",  # Dahua-style
            f"cam{camera.id:02d}",            # Zero-padded
            f"live/{camera.id}/main",         # Live stream
            f"media/video{camera.id}",        # Media pattern
        ]
        
        for pattern in patterns:
            url = f"rtsp://{self.username}:{self.password}@{self.nvr_ip}:{self.nvr_port}/{pattern}"
            urls.append(url)
        
        return urls
    
    async def test_rtsp_connectivity(self, url: str) -> tuple[bool, float, str]:
        """Test basic RTSP connectivity"""
        start_time = time.time()
        
        try:
            parsed = urlparse(url)
            
            # Test TCP connectivity
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            
            try:
                result = sock.connect_ex((parsed.hostname, parsed.port))
                response_time = (time.time() - start_time) * 1000
                
                if result == 0:
                    return True, response_time, "Port accessible"
                else:
                    return False, response_time, f"Cannot connect to {parsed.hostname}:{parsed.port}"
            finally:
                sock.close()
                
        except socket.timeout:
            return False, self.timeout * 1000, "Connection timeout"
        except Exception as e:
            return False, 0, str(e)
    
    async def test_stream_with_ffprobe(self, url: str) -> Optional[dict]:
        """Test stream with ffprobe if available"""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-timeout', str(self.timeout * 1000000), url],
                capture_output=True,
                text=True,
                timeout=self.timeout + 5
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get('streams', [])
                video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
                
                if video_stream:
                    fps_str = video_stream.get('r_frame_rate', '0/1')
                    try:
                        num, den = fps_str.split('/')
                        fps = float(num) / max(1, float(den))
                    except:
                        fps = 0
                    
                    return {
                        'resolution': f"{video_stream.get('width', 'unknown')}x{video_stream.get('height', 'unknown')}",
                        'fps': fps,
                        'codec': video_stream.get('codec_name', 'unknown')
                    }
            
            return None
            
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return None
    
    async def test_camera_streams(self, camera: Camera) -> StreamResult:
        """Test all possible RTSP URLs for a camera"""
        urls = self.generate_rtsp_urls(camera)
        
        print(f"🔍 Testing {camera.name} (Camera {camera.id})...")
        
        for i, url in enumerate(urls):
            print(f"   Trying pattern {i+1}/{len(urls)}: {url.split('/')[-1]}")
            
            # Test basic connectivity
            accessible, response_time, error_msg = await self.test_rtsp_connectivity(url)
            
            if accessible:
                print(f"   ✅ Port accessible ({response_time:.1f}ms)")
                
                # Try to get stream info
                stream_info = await self.test_stream_with_ffprobe(url)
                
                if stream_info:
                    print(f"   ✅ Stream accessible! {stream_info['resolution']} @ {stream_info['fps']:.1f}fps")
                    return StreamResult(
                        camera=camera,
                        rtsp_url=url,
                        status='accessible',
                        response_time_ms=response_time,
                        **stream_info
                    )
                else:
                    print(f"   ⚠️  Port open but stream may not be available")
            else:
                print(f"   ❌ {error_msg}")
        
        # No working URL found
        return StreamResult(
            camera=camera,
            rtsp_url=urls[0],  # Return the first URL as example
            status='inaccessible',
            error_message="No accessible RTSP stream found"
        )
    
    async def test_all_cameras(self) -> List[StreamResult]:
        """Test all cameras"""
        print(f"🎥 Testing Uniview NVR Camera Streams")
        print(f"📡 NVR: {self.nvr_ip}:{self.nvr_port}")
        print(f"👤 Authentication: {self.username}:{'*' * len(self.password)}")
        print(f"📹 Cameras: {len(self.cameras)}")
        print("=" * 80)
        
        results = []
        
        for camera in self.cameras:
            result = await self.test_camera_streams(camera)
            results.append(result)
            print()  # Add spacing between cameras
        
        return results
    
    def print_summary(self, results: List[StreamResult]):
        """Print summary of results"""
        print("=" * 80)
        print("📊 STREAM TEST SUMMARY")
        print("=" * 80)
        
        accessible_cameras = [r for r in results if r.status == 'accessible']
        
        print(f"✅ Accessible streams: {len(accessible_cameras)}/{len(results)}")
        print(f"❌ Inaccessible streams: {len(results) - len(accessible_cameras)}/{len(results)}")
        
        if accessible_cameras:
            print("\n🎯 WORKING RTSP STREAMS:")
            for result in accessible_cameras:
                print(f"   {result.camera.name}: {result.rtsp_url}")
                if result.resolution:
                    print(f"      📐 {result.resolution} @ {result.fps:.1f}fps ({result.codec})")
        
        print("\n💡 INTEGRATION NOTES:")
        print("   1. Update device config with working RTSP URLs")
        print("   2. Use these URLs in the video preview component")
        print("   3. Consider adding WebRTC gateway for browser playback")
        
        if accessible_cameras:
            print(f"\n🔗 Example usage:")
            example = accessible_cameras[0]
            print(f"   ffplay \"{example.rtsp_url}\"")
            print(f"   VLC: {example.rtsp_url}")


async def main():
    """Main test function"""
    
    # Test both possible NVR IPs
    nvr_ips = ["192.168.1.8", "192.168.1.6"]
    
    for nvr_ip in nvr_ips:
        print(f"\n🌐 Testing NVR at {nvr_ip}")
        print("=" * 50)
        
        # First test if the NVR is reachable
        try:
            result = subprocess.run(['ping', '-c', '1', nvr_ip], 
                                  capture_output=True, timeout=5)
            if result.returncode == 0:
                print(f"✅ {nvr_ip} is reachable")
            else:
                print(f"❌ {nvr_ip} is not reachable")
                continue
        except:
            print(f"❌ Cannot test {nvr_ip} reachability")
            continue
        
        # Test RTSP port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        rtsp_accessible = sock.connect_ex((nvr_ip, 554)) == 0
        sock.close()
        
        if rtsp_accessible:
            print(f"✅ RTSP port 554 is accessible on {nvr_ip}")
            
            # Run full camera test
            tester = UnivielwStreamTester(nvr_ip=nvr_ip)
            results = await tester.test_all_cameras()
            tester.print_summary(results)
            
            # If we found working streams, we're done
            accessible_count = sum(1 for r in results if r.status == 'accessible')
            if accessible_count > 0:
                print(f"\n🎉 Found {accessible_count} working streams on {nvr_ip}!")
                return results
        else:
            print(f"❌ RTSP port 554 is not accessible on {nvr_ip}")
    
    print("\n❌ No accessible RTSP streams found on any NVR IP")
    return []


if __name__ == "__main__":
    try:
        results = asyncio.run(main())
        
        # Exit with appropriate code
        accessible_count = sum(1 for r in results if r.status == 'accessible')
        if accessible_count > 0:
            print(f"\n✅ SUCCESS: {accessible_count} camera streams are accessible!")
            sys.exit(0)
        else:
            print(f"\n❌ FAILURE: No camera streams are accessible")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Test cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n🚫 Test failed: {e}")
        sys.exit(1)
