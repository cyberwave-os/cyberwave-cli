"""
Stream Detection Utility for NVR and IP Cameras

This module provides functionality to programmatically detect and validate
RTSP streams from NVR systems and IP cameras.
"""

import asyncio
import socket
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from urllib.parse import urlparse
import json


@dataclass
class StreamInfo:
    """Information about a detected stream"""
    url: str
    status: str  # 'accessible', 'inaccessible', 'timeout', 'error'
    resolution: Optional[str] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    error_message: Optional[str] = None
    response_time_ms: Optional[float] = None


class StreamDetector:
    """Utility class for detecting and validating RTSP streams"""
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
    
    async def test_rtsp_connectivity(self, url: str) -> StreamInfo:
        """
        Test basic RTSP connectivity to a stream URL
        """
        start_time = time.time()
        
        try:
            # Parse the RTSP URL
            parsed = urlparse(url)
            if not parsed.hostname or not parsed.port:
                return StreamInfo(
                    url=url,
                    status='error',
                    error_message='Invalid URL format'
                )
            
            # Test TCP connectivity to RTSP port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            
            try:
                result = sock.connect_ex((parsed.hostname, parsed.port))
                response_time = (time.time() - start_time) * 1000
                
                if result == 0:
                    # Port is open, try RTSP handshake
                    stream_info = await self._test_rtsp_handshake(url)
                    stream_info.response_time_ms = response_time
                    return stream_info
                else:
                    return StreamInfo(
                        url=url,
                        status='inaccessible',
                        error_message=f'Cannot connect to {parsed.hostname}:{parsed.port}',
                        response_time_ms=response_time
                    )
            finally:
                sock.close()
                
        except socket.timeout:
            return StreamInfo(
                url=url,
                status='timeout',
                error_message=f'Connection timeout after {self.timeout}s'
            )
        except Exception as e:
            return StreamInfo(
                url=url,
                status='error',
                error_message=str(e)
            )
    
    async def _test_rtsp_handshake(self, url: str) -> StreamInfo:
        """
        Perform basic RTSP handshake to verify stream accessibility
        """
        try:
            # Try to get stream info using ffprobe (if available)
            stream_info = await self._probe_stream_with_ffprobe(url)
            if stream_info:
                return stream_info
            
            # Fallback to basic RTSP DESCRIBE method
            return await self._basic_rtsp_test(url)
            
        except Exception as e:
            return StreamInfo(
                url=url,
                status='error',
                error_message=f'RTSP handshake failed: {str(e)}'
            )
    
    async def _probe_stream_with_ffprobe(self, url: str) -> Optional[StreamInfo]:
        """
        Use ffprobe to get detailed stream information
        """
        try:
            # Check if ffprobe is available
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', url],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get('streams', [])
                
                # Find video stream
                video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
                
                if video_stream:
                    return StreamInfo(
                        url=url,
                        status='accessible',
                        resolution=f"{video_stream.get('width', 'unknown')}x{video_stream.get('height', 'unknown')}",
                        fps=float(video_stream.get('r_frame_rate', '0/1').split('/')[0]) / max(1, float(video_stream.get('r_frame_rate', '0/1').split('/')[1])),
                        codec=video_stream.get('codec_name', 'unknown')
                    )
                else:
                    return StreamInfo(
                        url=url,
                        status='accessible',
                        error_message='No video stream found'
                    )
            else:
                # ffprobe failed, but stream might still be accessible
                return None
                
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            # ffprobe not available or failed
            return None
    
    async def _basic_rtsp_test(self, url: str) -> StreamInfo:
        """
        Basic RTSP DESCRIBE test
        """
        try:
            parsed = urlparse(url)
            
            # Create RTSP DESCRIBE request
            request = f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: StreamDetector/1.0\r\n\r\n"
            
            # Send request
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((parsed.hostname, parsed.port))
            sock.send(request.encode())
            
            # Read response
            response = sock.recv(4096).decode()
            sock.close()
            
            if "200 OK" in response:
                return StreamInfo(
                    url=url,
                    status='accessible',
                    error_message='Basic RTSP handshake successful'
                )
            else:
                return StreamInfo(
                    url=url,
                    status='inaccessible',
                    error_message=f'RTSP error: {response.split()[2] if len(response.split()) > 2 else "Unknown"}'
                )
                
        except Exception as e:
            return StreamInfo(
                url=url,
                status='error',
                error_message=f'RTSP test failed: {str(e)}'
            )
    
    async def detect_nvr_streams(self, base_url: str, stream_paths: List[str]) -> List[StreamInfo]:
        """
        Detect multiple streams from an NVR
        """
        results = []
        
        for i, path in enumerate(stream_paths):
            # Construct full URL
            clean_base = base_url.rstrip('/')
            clean_path = path.lstrip('/')
            full_url = f"{clean_base}/{clean_path}"
            
            print(f"🔍 Testing Camera {i+1}: {full_url}")
            
            # Test the stream
            stream_info = await self.test_rtsp_connectivity(full_url)
            results.append(stream_info)
            
            # Add a small delay between tests
            await asyncio.sleep(0.5)
        
        return results
    
    def print_detection_results(self, results: List[StreamInfo]):
        """
        Print formatted detection results
        """
        print("\n" + "="*80)
        print("🎥 RTSP Stream Detection Results")
        print("="*80)
        
        for i, result in enumerate(results):
            status_emoji = {
                'accessible': '✅',
                'inaccessible': '❌', 
                'timeout': '⏰',
                'error': '🚫'
            }.get(result.status, '❓')
            
            print(f"\n📹 Camera {i+1}: {status_emoji} {result.status.upper()}")
            print(f"   URL: {result.url}")
            
            if result.response_time_ms:
                print(f"   Response Time: {result.response_time_ms:.1f}ms")
            
            if result.resolution:
                print(f"   Resolution: {result.resolution}")
            
            if result.fps:
                print(f"   Frame Rate: {result.fps:.1f} FPS")
            
            if result.codec:
                print(f"   Codec: {result.codec}")
            
            if result.error_message:
                print(f"   Error: {result.error_message}")
        
        # Summary
        accessible_count = sum(1 for r in results if r.status == 'accessible')
        print(f"\n📊 Summary: {accessible_count}/{len(results)} streams accessible")
        print("="*80)


async def test_uniview_nvr():
    """
    Test the specific Uniview NVR configuration
    """
    # Configuration from environment variables
    base_url = "rtsp://admin:Stralis26$@192.168.1.8:554"
    stream_paths = [
        "unicast/c1/s1/live",
        "unicast/c2/s1/live"
    ]
    
    print("🎥 Testing Uniview NVR Camera Streams")
    print(f"📡 Base URL: {base_url}")
    print(f"📹 Stream Paths: {stream_paths}")
    
    detector = StreamDetector(timeout=15)
    results = await detector.detect_nvr_streams(base_url, stream_paths)
    detector.print_detection_results(results)
    
    return results


async def main():
    """
    Main function for command-line usage
    """
    print("🔍 RTSP Stream Detector")
    print("Programmatically testing NVR camera accessibility...")
    
    try:
        results = await test_uniview_nvr()
        
        # Exit with appropriate code
        accessible_count = sum(1 for r in results if r.status == 'accessible')
        if accessible_count == len(results):
            print("\n✅ All streams are accessible!")
            sys.exit(0)
        elif accessible_count > 0:
            print(f"\n⚠️  {accessible_count}/{len(results)} streams accessible")
            sys.exit(1)
        else:
            print("\n❌ No streams accessible")
            sys.exit(2)
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Stream detection cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n🚫 Stream detection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
