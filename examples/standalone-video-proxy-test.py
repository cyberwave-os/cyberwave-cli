#!/usr/bin/env python3
"""
Standalone Video Proxy Test
Tests the video proxy service with real Uniview NVR cameras
"""

import asyncio
import sys
import os
import logging
from pathlib import Path

# Add the CLI source directory to Python path
cli_src = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(cli_src))

async def test_video_proxy():
    """Test video proxy with real cameras"""
    
    print("🎥 Standalone Video Proxy Test")
    print("=" * 50)
    
    try:
        # Import the video proxy service
        from cyberwave_cli.plugins.edge.services.video_proxy import VideoProxyService
        print("✅ Video proxy module imported successfully")
        
        # Camera configurations for real Uniview NVR
        cameras = [
            {
                'id': 1,
                'name': 'D1 (Camerette)',
                'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c1/s1/live'
            },
            {
                'id': 2,
                'name': 'D2 (Salone)',
                'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c2/s1/live'
            },
            {
                'id': 3,
                'name': 'D3 (Ingresso)',
                'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c3/s1/live'
            },
            {
                'id': 4,
                'name': 'D4 (Salone > Ovest)',
                'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c4/s1/live'
            }
        ]
        
        print(f"📹 Configured {len(cameras)} cameras")
        
        # Initialize video proxy service
        service = VideoProxyService(
            backend_url="http://localhost:8000",
            node_id="test_node_12345",
            proxy_port=8001
        )
        
        print("✅ Video proxy service initialized")
        
        # Initialize streams
        await service.initialize_streams(cameras)
        print("✅ Streams initialized")
        
        # Start video captures (motion detection enabled)
        service.start_all_streams()
        print("✅ Video captures started")
        
        # Create and start web server
        from aiohttp import web
        app = service.create_web_app()
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', 8001)
        await site.start()
        
        print("🚀 Video Proxy Service started on http://localhost:8001")
        print("\n📡 Available endpoints:")
        print("   GET  http://localhost:8001/streams              - List all streams")
        print("   GET  http://localhost:8001/streams/1/status     - Stream 1 status")
        print("   GET  http://localhost:8001/streams/1/snapshot   - Stream 1 snapshot")
        print("   GET  http://localhost:8001/streams/1/mjpeg      - Stream 1 MJPEG")
        print("   GET  http://localhost:8001/health               - Health check")
        print("   WS   ws://localhost:8001/ws                     - Events WebSocket")
        
        print("\n🎬 Live camera streams:")
        for camera in cameras:
            print(f"   Camera {camera['id']}: http://localhost:8001/streams/{camera['id']}/mjpeg")
        
        print("\n💡 Test commands:")
        print("   # Check health:")
        print("   curl http://localhost:8001/health")
        print("")
        print("   # List streams:")
        print("   curl http://localhost:8001/streams")
        print("")
        print("   # View stream in browser:")
        print("   open http://localhost:8001/streams/1/mjpeg")
        print("")
        
        print("⚠️  Press Ctrl+C to stop the service")
        
        try:
            # Keep running
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Shutting down...")
            service.stop_all_streams()
            await runner.cleanup()
            print("✅ Service stopped")
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure all dependencies are installed:")
        print("pip install opencv-python aiohttp aiohttp-cors websockets numpy")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    # Run the test
    try:
        success = asyncio.run(test_video_proxy())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️  Test cancelled")
        sys.exit(130)
    except Exception as e:
        print(f"🚫 Test failed: {e}")
        sys.exit(1)
