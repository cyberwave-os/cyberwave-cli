"""
Video Proxy Service for Edge Node

Provides secure video streaming and analysis capabilities:
1. Proxies RTSP streams from NVR/cameras without exposing credentials
2. Converts RTSP to WebSocket/HTTP streams for browser compatibility
3. Performs on-device video analysis (motion detection, object detection)
4. Sends analysis events to backend
5. Provides secure API endpoints for frontend access
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import base64
import cv2
import numpy as np
from threading import Thread, Event
import websockets
from aiohttp import web, ClientSession
import aiohttp_cors
from urllib.parse import urlparse


@dataclass
class VideoStream:
    """Video stream configuration"""
    camera_id: int
    name: str
    rtsp_url: str
    status: str = "inactive"  # inactive, active, error
    last_frame_time: Optional[float] = None
    fps: float = 0.0
    resolution: tuple = (0, 0)
    error_message: Optional[str] = None


@dataclass
class AnalysisEvent:
    """Video analysis event"""
    camera_id: int
    camera_name: str
    event_type: str  # motion, object_detected, face_detected
    timestamp: datetime
    confidence: float
    metadata: Dict[str, Any]
    frame_data: Optional[str] = None  # Base64 encoded thumbnail


class VideoAnalyzer:
    """Real-time video analysis engine"""
    
    def __init__(self):
        self.motion_detectors = {}
        self.background_subtractors = {}
        
    def initialize_motion_detection(self, camera_id: int):
        """Initialize motion detection for a camera"""
        self.background_subtractors[camera_id] = cv2.createBackgroundSubtractorMOG2(
            detectShadows=True,
            varThreshold=50,
            history=500
        )
        
    def analyze_frame(self, camera_id: int, frame: np.ndarray) -> List[AnalysisEvent]:
        """Analyze a single frame for events"""
        events = []
        
        if camera_id not in self.background_subtractors:
            self.initialize_motion_detection(camera_id)
        
        # Motion detection
        motion_event = self._detect_motion(camera_id, frame)
        if motion_event:
            events.append(motion_event)
            
        # TODO: Add object detection, face detection, etc.
        
        return events
    
    def _detect_motion(self, camera_id: int, frame: np.ndarray) -> Optional[AnalysisEvent]:
        """Detect motion in frame"""
        try:
            bg_subtractor = self.background_subtractors[camera_id]
            
            # Apply background subtraction
            fg_mask = bg_subtractor.apply(frame)
            
            # Morphological operations to reduce noise
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
            
            # Find contours
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Filter significant motion
            motion_areas = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if area > 500:  # Minimum motion area threshold
                    x, y, w, h = cv2.boundingRect(contour)
                    motion_areas.append({
                        'area': area,
                        'bbox': [x, y, w, h],
                        'center': [x + w//2, y + h//2]
                    })
            
            if motion_areas:
                # Calculate motion confidence based on total area
                total_motion_area = sum(area['area'] for area in motion_areas)
                frame_area = frame.shape[0] * frame.shape[1]
                confidence = min(total_motion_area / frame_area * 10, 1.0)
                
                # Generate thumbnail with motion highlighted
                thumbnail = self._generate_motion_thumbnail(frame, motion_areas)
                
                return AnalysisEvent(
                    camera_id=camera_id,
                    camera_name=f"Camera {camera_id}",
                    event_type="motion",
                    timestamp=datetime.now(),
                    confidence=confidence,
                    metadata={
                        'motion_areas': motion_areas,
                        'total_area': total_motion_area,
                        'num_objects': len(motion_areas)
                    },
                    frame_data=thumbnail
                )
                
        except Exception as e:
            logging.error(f"Motion detection error for camera {camera_id}: {e}")
            
        return None
    
    def _generate_motion_thumbnail(self, frame: np.ndarray, motion_areas: List[Dict]) -> str:
        """Generate base64 encoded thumbnail with motion highlights"""
        try:
            # Resize frame for thumbnail
            thumbnail = cv2.resize(frame, (320, 240))
            
            # Draw motion bounding boxes
            for area in motion_areas:
                x, y, w, h = area['bbox']
                # Scale coordinates for thumbnail
                x = int(x * 320 / frame.shape[1])
                y = int(y * 240 / frame.shape[0])
                w = int(w * 320 / frame.shape[1])
                h = int(h * 240 / frame.shape[0])
                
                cv2.rectangle(thumbnail, (x, y), (x + w, y + h), (0, 255, 0), 2)
            
            # Encode as JPEG
            _, buffer = cv2.imencode('.jpg', thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 70])
            return base64.b64encode(buffer).decode('utf-8')
            
        except Exception as e:
            logging.error(f"Thumbnail generation error: {e}")
            return ""


class VideoStreamCapture:
    """Captures video from RTSP streams"""
    
    def __init__(self, stream: VideoStream, analyzer: VideoAnalyzer):
        self.stream = stream
        self.analyzer = analyzer
        self.capture = None
        self.running = False
        self.thread = None
        self.frame_queue = asyncio.Queue(maxsize=30)
        self.event_callbacks = []
        
    def add_event_callback(self, callback):
        """Add callback for analysis events"""
        self.event_callbacks.append(callback)
        
    def start(self):
        """Start video capture"""
        if self.running:
            return
            
        self.running = True
        self.thread = Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        """Stop video capture"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self.capture:
            self.capture.release()
            
    def _capture_loop(self):
        """Main capture loop"""
        try:
            self.capture = cv2.VideoCapture(self.stream.rtsp_url)
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize latency
            
            if not self.capture.isOpened():
                self.stream.status = "error"
                self.stream.error_message = "Failed to open RTSP stream"
                return
                
            self.stream.status = "active"
            frame_count = 0
            start_time = time.time()
            
            while self.running:
                ret, frame = self.capture.read()
                
                if not ret:
                    self.stream.status = "error" 
                    self.stream.error_message = "Failed to read frame"
                    break
                    
                frame_count += 1
                current_time = time.time()
                self.stream.last_frame_time = current_time
                
                # Update FPS calculation
                if frame_count % 30 == 0:
                    elapsed = current_time - start_time
                    self.stream.fps = frame_count / elapsed
                    
                # Update resolution
                self.stream.resolution = (frame.shape[1], frame.shape[0])
                
                # Add frame to queue (non-blocking)
                try:
                    self.frame_queue.put_nowait(frame.copy())
                except asyncio.QueueFull:
                    # Remove oldest frame if queue is full
                    try:
                        self.frame_queue.get_nowait()
                        self.frame_queue.put_nowait(frame.copy())
                    except asyncio.QueueEmpty:
                        pass
                
                # Analyze frame for events
                events = self.analyzer.analyze_frame(self.stream.camera_id, frame)
                for event in events:
                    for callback in self.event_callbacks:
                        try:
                            callback(event)
                        except Exception as e:
                            logging.error(f"Event callback error: {e}")
                            
        except Exception as e:
            self.stream.status = "error"
            self.stream.error_message = str(e)
            logging.error(f"Capture loop error for camera {self.stream.camera_id}: {e}")
        finally:
            if self.capture:
                self.capture.release()


class VideoProxyService:
    """Main video proxy service"""
    
    def __init__(self, backend_url: str = "http://localhost:8000", node_id: str = "", proxy_port: int = 8001, auth_token: str = None):
        self.backend_url = backend_url
        self.node_id = node_id
        self.proxy_port = proxy_port
        self.auth_token = auth_token
        self.streams: Dict[int, VideoStream] = {}
        self.captures: Dict[int, VideoStreamCapture] = {}
        self.analyzer = VideoAnalyzer()
        self.app = None
        self.websocket_clients = set()
        self.proxy_url = f"http://localhost:{proxy_port}"
        
    async def initialize_streams(self, camera_configs: List[Dict]):
        """Initialize video streams from camera configurations"""
        for config in camera_configs:
            camera_id = config['id']
            name = config['name']
            rtsp_url = config['rtsp_url']
            
            stream = VideoStream(
                camera_id=camera_id,
                name=name,
                rtsp_url=rtsp_url
            )
            
            self.streams[camera_id] = stream
            
            # Create capture instance
            capture = VideoStreamCapture(stream, self.analyzer)
            capture.add_event_callback(self._handle_analysis_event)
            self.captures[camera_id] = capture
            
        logging.info(f"Initialized {len(self.streams)} video streams")
        
    def start_all_streams(self):
        """Start all video captures"""
        for capture in self.captures.values():
            capture.start()
        logging.info("Started all video captures")
        
    def stop_all_streams(self):
        """Stop all video captures"""
        for capture in self.captures.values():
            capture.stop()
        logging.info("Stopped all video captures")
        
    async def _handle_analysis_event(self, event: AnalysisEvent):
        """Handle analysis events"""
        try:
            # Send to backend
            await self._send_event_to_backend(event)
            
            # Send to connected WebSocket clients
            await self._broadcast_event_to_clients(event)
            
        except Exception as e:
            logging.error(f"Error handling analysis event: {e}")
            
    async def _send_event_to_backend(self, event: AnalysisEvent):
        """Send event to backend API"""
        try:
            async with ClientSession() as session:
                event_data = asdict(event)
                event_data['timestamp'] = event.timestamp.isoformat()
                
                async with session.post(
                    f"{self.backend_url}/api/v1/nodes/{self.node_id}/events",
                    json=event_data,
                    timeout=10
                ) as response:
                    if response.status == 201:
                        logging.info(f"Event sent to backend: {event.event_type} from camera {event.camera_id}")
                    else:
                        logging.warning(f"Backend event submission failed: {response.status}")
                        
        except Exception as e:
            logging.error(f"Failed to send event to backend: {e}")
            
    async def _broadcast_event_to_clients(self, event: AnalysisEvent):
        """Broadcast event to WebSocket clients"""
        if not self.websocket_clients:
            return
            
        event_data = asdict(event)
        event_data['timestamp'] = event.timestamp.isoformat()
        message = json.dumps({
            'type': 'analysis_event',
            'data': event_data
        })
        
        # Send to all connected clients
        disconnected_clients = set()
        for client in self.websocket_clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
            except Exception as e:
                logging.error(f"WebSocket broadcast error: {e}")
                disconnected_clients.add(client)
                
        # Remove disconnected clients
        self.websocket_clients -= disconnected_clients
        
    def create_web_app(self) -> web.Application:
        """Create aiohttp web application"""
        app = web.Application()
        
        # Enable CORS
        cors = aiohttp_cors.setup(app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })
        
        # API routes
        app.router.add_get('/streams', self._handle_list_streams)
        app.router.add_get('/streams/{camera_id}/status', self._handle_stream_status)
        app.router.add_get('/streams/{camera_id}/snapshot', self._handle_snapshot)
        app.router.add_get('/streams/{camera_id}/mjpeg', self._handle_mjpeg_stream)
        app.router.add_get('/ws', self._handle_websocket)
        app.router.add_get('/health', self._handle_health)
        
        # Add CORS to all routes
        for route in list(app.router.routes()):
            cors.add(route)
            
        self.app = app
        return app
        
    async def register_with_backend(self):
        """Register video proxy service with backend"""
        try:
            # Prepare headers with authentication
            headers = {"Content-Type": "application/json"}
            if self.auth_token:
                headers["Authorization"] = self.auth_token
            
            async with ClientSession() as session:
                proxy_data = {
                    "proxy_url": self.proxy_url,
                    "status": "running"
                }
                
                async with session.patch(
                    f"{self.backend_url}/api/v1/nodes/{self.node_id}/video-proxy",
                    json=proxy_data,
                    headers=headers,
                    timeout=10
                ) as response:
                    if response.status == 200:
                        logging.info(f"Video proxy registered with backend: {self.proxy_url}")
                        return True
                    else:
                        logging.warning(f"Failed to register proxy with backend: {response.status}")
                        return False
                        
        except Exception as e:
            logging.error(f"Failed to register proxy with backend: {e}")
            return False
            
    async def unregister_from_backend(self):
        """Unregister video proxy service from backend"""
        try:
            async with ClientSession() as session:
                proxy_data = {
                    "status": "stopped"
                }
                
                async with session.patch(
                    f"{self.backend_url}/api/v1/nodes/{self.node_id}/video-proxy",
                    json=proxy_data,
                    timeout=10
                ) as response:
                    if response.status == 200:
                        logging.info("Video proxy unregistered from backend")
                        return True
                    else:
                        logging.warning(f"Failed to unregister proxy from backend: {response.status}")
                        return False
                        
        except Exception as e:
            logging.error(f"Failed to unregister proxy from backend: {e}")
            return False
        
    async def _handle_list_streams(self, request):
        """List available streams"""
        streams_data = []
        for stream in self.streams.values():
            streams_data.append({
                'camera_id': stream.camera_id,
                'name': stream.name,
                'status': stream.status,
                'fps': stream.fps,
                'resolution': stream.resolution,
                'last_frame_time': stream.last_frame_time,
                'error_message': stream.error_message
            })
            
        return web.json_response({
            'streams': streams_data,
            'total': len(streams_data)
        })
        
    async def _handle_stream_status(self, request):
        """Get status of specific stream"""
        camera_id = int(request.match_info['camera_id'])
        
        if camera_id not in self.streams:
            return web.json_response({'error': 'Stream not found'}, status=404)
            
        stream = self.streams[camera_id]
        return web.json_response({
            'camera_id': stream.camera_id,
            'name': stream.name,
            'status': stream.status,
            'fps': stream.fps,
            'resolution': stream.resolution,
            'last_frame_time': stream.last_frame_time,
            'error_message': stream.error_message
        })
        
    async def _handle_snapshot(self, request):
        """Get current frame snapshot"""
        camera_id = int(request.match_info['camera_id'])
        
        if camera_id not in self.captures:
            return web.json_response({'error': 'Stream not found'}, status=404)
            
        try:
            capture = self.captures[camera_id]
            frame = await asyncio.wait_for(capture.frame_queue.get(), timeout=5.0)
            
            # Encode frame as JPEG
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            
            return web.Response(
                body=buffer.tobytes(),
                content_type='image/jpeg',
                headers={'Cache-Control': 'no-cache'}
            )
            
        except asyncio.TimeoutError:
            return web.json_response({'error': 'No frame available'}, status=503)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)
            
    async def _handle_mjpeg_stream(self, request):
        """Serve MJPEG stream"""
        camera_id = int(request.match_info['camera_id'])
        
        if camera_id not in self.captures:
            return web.json_response({'error': 'Stream not found'}, status=404)
            
        capture = self.captures[camera_id]
        
        response = web.StreamResponse()
        response.content_type = 'multipart/x-mixed-replace; boundary=frame'
        await response.prepare(request)
        
        try:
            while True:
                frame = await capture.frame_queue.get()
                
                # Encode frame
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                
                # Write MJPEG frame
                await response.write(b'--frame\r\n')
                await response.write(b'Content-Type: image/jpeg\r\n\r\n')
                await response.write(buffer.tobytes())
                await response.write(b'\r\n')
                
        except Exception as e:
            logging.error(f"MJPEG stream error: {e}")
        finally:
            await response.write_eof()
            
        return response
        
    async def _handle_websocket(self, request):
        """Handle WebSocket connections"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        self.websocket_clients.add(ws)
        logging.info("New WebSocket client connected")
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # Handle client messages (e.g., stream control)
                    pass
                elif msg.type == web.WSMsgType.ERROR:
                    logging.error(f"WebSocket error: {ws.exception()}")
                    
        except Exception as e:
            logging.error(f"WebSocket handler error: {e}")
        finally:
            self.websocket_clients.discard(ws)
            logging.info("WebSocket client disconnected")
            
        return ws
        
    async def _handle_health(self, request):
        """Health check endpoint"""
        active_streams = sum(1 for s in self.streams.values() if s.status == "active")
        
        return web.json_response({
            'status': 'healthy',
            'total_streams': len(self.streams),
            'active_streams': active_streams,
            'websocket_clients': len(self.websocket_clients),
            'timestamp': datetime.now().isoformat()
        })


async def main():
    """Main function for testing"""
    # Example camera configurations
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
        }
    ]
    
    # Initialize service
    service = VideoProxyService(
        backend_url="http://localhost:8000",
        node_id="21b0743b-50bf-4e1a-804e-a50499c88198"
    )
    
    await service.initialize_streams(cameras)
    service.start_all_streams()
    
    # Create web app
    app = service.create_web_app()
    
    # Start server
    logging.basicConfig(level=logging.INFO)
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', 8001)
    await site.start()
    
    print("🎥 Video Proxy Service started on http://localhost:8001")
    print("📡 Available endpoints:")
    print("   GET  /streams                    - List all streams")
    print("   GET  /streams/{id}/status        - Stream status")
    print("   GET  /streams/{id}/snapshot      - Current frame")
    print("   GET  /streams/{id}/mjpeg         - MJPEG stream")
    print("   WS   /ws                         - WebSocket for events")
    print("   GET  /health                     - Health check")
    
    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        service.stop_all_streams()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
