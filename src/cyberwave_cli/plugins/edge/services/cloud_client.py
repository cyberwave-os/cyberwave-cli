"""
Enhanced Cloud Client for Edge Nodes

Provides robust cloud connectivity with features like:
- Automatic reconnection
- Command queue management  
- Telemetry batching and compression
- Authentication management
- Error handling and retries
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import gzip
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Deque

import httpx

from ..config.schema import AuthConfig, DeviceConfig, TelemetryConfig
from ..utils.retry import with_retry, ExponentialBackoff

logger = logging.getLogger(__name__)

class CloudClient:
    """
    Enhanced cloud client for edge nodes.
    
    Features:
    - Automatic authentication management
    - Telemetry batching and compression
    - Command queue management
    - Robust error handling and reconnection
    - Device registration and management
    """
    
    def __init__(self,
                 backend_url: str,
                 auth_config: AuthConfig,
                 device_config: DeviceConfig,
                 node_id: Optional[str] = None,
                 telemetry_config: Optional[TelemetryConfig] = None):
        self.backend_url = backend_url.rstrip("/")
        self.auth_config = auth_config
        self.device_config = device_config
        self.node_id = node_id
        self.telemetry_config = telemetry_config or TelemetryConfig()
        
        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
        
        # Authentication state
        self._authenticated = False
        self._token_expires_at: Optional[datetime] = None
        
        # Telemetry management
        self._telemetry_buffer: Deque[Dict[str, Any]] = deque(maxlen=self.telemetry_config.buffer_size)
        self._last_telemetry_send = 0.0
        
        # Command management
        self._pending_commands: List[Dict[str, Any]] = []
        self._command_callbacks: Dict[str, callable] = {}
        
        # Connection state
        self._connected = False
        self._last_heartbeat = 0.0
        
        # Background tasks
        self._telemetry_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._command_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """Initialize the cloud client."""
        logger.info("Initializing cloud client")
        
        # Create HTTP client
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"cyberwave-edge/{self.node_id or 'unknown'}"
            }
        )
        
        # Authenticate
        await self._authenticate()
        
        # Register device if needed
        if self.device_config.auto_register and not self.device_config.device_id:
            await self._register_device()
        
        # Start background tasks
        self._start_background_tasks()
        
        self._connected = True
        logger.info("Cloud client initialized successfully")

    async def _authenticate(self) -> None:
        """Authenticate with the backend."""
        if self.auth_config.access_token:
            # Use provided token
            self._update_auth_header(self.auth_config.access_token)
            self._authenticated = True
            logger.info("Using provided access token")
            
        elif self.auth_config.username and self.auth_config.password:
            # Login with credentials
            await self._login()
            
        elif self.auth_config.device_token:
            # Use device token
            self._update_auth_header(self.auth_config.device_token)
            self._authenticated = True
            logger.info("Using device token")
            
        else:
            raise ValueError("No valid authentication method provided")

    @with_retry(ExponentialBackoff(max_attempts=3))
    async def _login(self) -> None:
        """Login with username/password."""
        if not self._client:
            raise RuntimeError("Client not initialized")
        
        login_data = {
            "username": self.auth_config.username,
            "password": self.auth_config.password
        }
        
        response = await self._client.post(
            f"{self.backend_url}/auth/login",
            json=login_data
        )
        response.raise_for_status()
        
        auth_response = response.json()
        access_token = auth_response.get("access_token")
        
        if not access_token:
            raise ValueError("No access token in login response")
        
        self._update_auth_header(access_token)
        
        # Parse token expiration if provided
        expires_in = auth_response.get("expires_in")
        if expires_in:
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        self._authenticated = True
        logger.info("Successfully authenticated with username/password")

    def _update_auth_header(self, token: str) -> None:
        """Update authentication header."""
        if self._client:
            self._client.headers["Authorization"] = f"Bearer {token}"

    async def _register_device(self) -> None:
        """Register device with backend."""
        if not self._authenticated:
            raise RuntimeError("Must be authenticated to register device")
        
        # First, ensure we have a node registered
        node_uuid = await self._ensure_node_registered()
        
        # Prepare device data according to backend schema
        device_data = {
            "name": self.device_config.device_name,
            "description": f"Auto-registered {self.device_config.device_type} device",
            "device_type": self.device_config.device_type,
            "connection_string": self.device_config.port or "",
            "connection_type": "local",
            "serial_number": "",
            "manufacturer": "",
            "model": "",
            "config": self.device_config.metadata,
            "capabilities": self.device_config.capabilities,
        }
        
        # Register device under the node
        response = await self._client.post(
            f"{self.backend_url}/api/v1/nodes/{node_uuid}/devices",
            json=device_data
        )
        response.raise_for_status()
        
        device_response = response.json()
        self.device_config.device_id = str(device_response.get("id"))
        
        logger.info(f"Device registered with ID: {self.device_config.device_id}")
        
        # Issue device token if requested
        if self.auth_config.use_device_token:
            await self._issue_device_token()

    async def _ensure_node_registered(self) -> str:
        """Ensure the node is registered and return its UUID."""
        # Try to register/get the node
        node_data = {
            "name": f"Edge Node {self.node_id}",
            "description": f"Auto-registered edge node",
            "hostname": self.node_id,
            "node_type": "edge",
            "capabilities": ["telemetry", "device_management"],
        }
        
        try:
            # Try to create the node
            response = await self._client.post(
                f"{self.backend_url}/api/v1/nodes/",
                json=node_data
            )
            response.raise_for_status()
            node_response = response.json()
            node_uuid = node_response.get("uuid")
            logger.info(f"Node registered with UUID: {node_uuid}")
            return node_uuid
        except Exception as e:
            # If node creation fails (maybe already exists), try to find it
            logger.debug(f"Node creation failed, may already exist: {e}")
            # For now, we'll use the node_id as UUID since we don't have a lookup endpoint
            # This should be improved with proper node lookup
            return self.node_id

    async def _issue_device_token(self) -> None:
        """Issue device token for offline operation."""
        if not self.device_config.device_id:
            raise RuntimeError("Device must be registered to issue token")
        
        response = await self._client.post(
            f"{self.backend_url}/api/v1/devices/{self.device_config.device_id}/token"
        )
        response.raise_for_status()
        
        token_response = response.json()
        device_token = token_response.get("token")
        
        if device_token:
            self.auth_config.device_token = device_token
            self._update_auth_header(device_token)
            logger.info("Device token issued and activated")

    async def fetch_node_devices(self) -> List[Dict[str, Any]]:
        """Fetch all devices registered to this node from the backend."""
        if not self._authenticated:
            raise RuntimeError("Must be authenticated to fetch devices")
        
        try:
            response = await self._client.get(
                f"{self.backend_url}/api/v1/nodes/{self.node_id}/devices"
            )
            response.raise_for_status()
            
            devices_data = response.json()
            logger.info(f"Found {len(devices_data)} devices registered to node {self.node_id}")
            return devices_data
            
        except Exception as e:
            logger.error(f"Failed to fetch node devices: {e}")
            return []

    def _start_background_tasks(self) -> None:
        """Start background tasks."""
        self._telemetry_task = asyncio.create_task(self._telemetry_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._command_task = asyncio.create_task(self._command_loop())

    async def _telemetry_loop(self) -> None:
        """Background task for sending telemetry."""
        while self._connected:
            try:
                await self._flush_telemetry()
                await asyncio.sleep(self.telemetry_config.flush_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in telemetry loop: {e}")
                await asyncio.sleep(5.0)

    async def _heartbeat_loop(self) -> None:
        """Background task for sending heartbeats."""
        while self._connected:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(60.0)  # Heartbeat every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(30.0)

    async def _command_loop(self) -> None:
        """Background task for polling commands."""
        while self._connected:
            try:
                await self._poll_commands()
                await asyncio.sleep(1.0)  # Poll commands every second
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in command loop: {e}")
                await asyncio.sleep(5.0)

    async def send_telemetry(self, data: Dict[str, Any]) -> None:
        """
        Send telemetry data (buffered).
        
        Args:
            data: Telemetry data to send
        """
        if not self.telemetry_config.enabled:
            return
        
        # Add timestamp
        data["timestamp"] = time.time()
        data["node_id"] = self.node_id
        
        # Filter fields if configured
        if self.telemetry_config.include_fields:
            data = {k: v for k, v in data.items() 
                   if k in self.telemetry_config.include_fields}
        
        if self.telemetry_config.exclude_fields:
            data = {k: v for k, v in data.items() 
                   if k not in self.telemetry_config.exclude_fields}
        
        # Add to buffer
        self._telemetry_buffer.append(data)
        
        # Flush if buffer is full or interval elapsed
        current_time = time.time()
        if (len(self._telemetry_buffer) >= self.telemetry_config.batch_size or
            current_time - self._last_telemetry_send >= self.telemetry_config.flush_interval):
            await self._flush_telemetry()

    @with_retry(ExponentialBackoff(max_attempts=3))
    async def _flush_telemetry(self) -> None:
        """Flush telemetry buffer to backend."""
        if not self._telemetry_buffer or not self._authenticated:
            return
        
        # Collect batch
        batch = []
        while self._telemetry_buffer and len(batch) < self.telemetry_config.batch_size:
            batch.append(self._telemetry_buffer.popleft())
        
        if not batch:
            return
        
        # Prepare payload
        payload = {
            "device_id": self.device_config.device_id,
            "telemetry": batch,
            "batch_size": len(batch),
            "timestamp": time.time()
        }
        
        # Compress if enabled
        if self.telemetry_config.compression:
            payload_json = json.dumps(payload)
            compressed_data = gzip.compress(payload_json.encode())
            headers = {"Content-Encoding": "gzip"}
        else:
            compressed_data = json.dumps(payload).encode()
            headers = {}
        
        # Send to backend
        response = await self._client.post(
            f"{self.backend_url}/api/v1/telemetry",
            content=compressed_data,
            headers=headers
        )
        response.raise_for_status()
        
        self._last_telemetry_send = time.time()
        logger.debug(f"Sent telemetry batch of {len(batch)} items")

    @with_retry(ExponentialBackoff(max_attempts=2))
    async def _send_heartbeat(self) -> None:
        """Send heartbeat to backend."""
        if not self._authenticated:
            return
        
        heartbeat_data = {
            "node_id": self.node_id,
            "device_id": self.device_config.device_id,
            "timestamp": time.time(),
            "status": "healthy"
        }
        
        response = await self._client.post(
            f"{self.backend_url}/api/v1/heartbeat",
            json=heartbeat_data
        )
        response.raise_for_status()
        
        self._last_heartbeat = time.time()
        logger.debug("Heartbeat sent successfully")

    async def _poll_commands(self) -> None:
        """Poll for pending commands."""
        if not self._authenticated or not self.device_config.device_id:
            return
        
        try:
            response = await self._client.get(
                f"{self.backend_url}/api/v1/devices/{self.device_config.device_id}/commands"
            )
            response.raise_for_status()
            
            commands = response.json().get("commands", [])
            
            for command in commands:
                self._pending_commands.append(command)
                
                # Execute command callback if registered
                command_type = command.get("type")
                if command_type in self._command_callbacks:
                    try:
                        await self._command_callbacks[command_type](command)
                    except Exception as e:
                        logger.error(f"Error executing command {command_type}: {e}")
                
                # Acknowledge command
                await self._acknowledge_command(command.get("id"))
            
        except Exception as e:
            logger.debug(f"Error polling commands: {e}")

    async def _acknowledge_command(self, command_id: str) -> None:
        """Acknowledge command execution."""
        if not command_id:
            return
        
        try:
            await self._client.post(
                f"{self.backend_url}/api/v1/commands/{command_id}/ack"
            )
        except Exception as e:
            logger.error(f"Failed to acknowledge command {command_id}: {e}")

    async def get_pending_commands(self) -> List[Dict[str, Any]]:
        """Get and clear pending commands."""
        commands = self._pending_commands.copy()
        self._pending_commands.clear()
        return commands

    def register_command_callback(self, command_type: str, callback: callable) -> None:
        """Register callback for specific command type."""
        self._command_callbacks[command_type] = callback

    async def send_command_response(self, command_id: str, response: Dict[str, Any]) -> None:
        """Send response to a command."""
        if not self._authenticated:
            return
        
        try:
            await self._client.post(
                f"{self.backend_url}/api/v1/commands/{command_id}/response",
                json=response
            )
        except Exception as e:
            logger.error(f"Failed to send command response: {e}")

    async def shutdown(self) -> None:
        """Shutdown cloud client."""
        logger.info("Shutting down cloud client")
        
        self._connected = False
        
        # Cancel background tasks
        tasks = [self._telemetry_task, self._heartbeat_task, self._command_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flush remaining telemetry
        await self._flush_telemetry()
        
        # Close HTTP client
        if self._client:
            await self._client.aclose()
            self._client = None
        
        logger.info("Cloud client shutdown complete")

    def get_status(self) -> Dict[str, Any]:
        """Get cloud client status."""
        return {
            "connected": self._connected,
            "authenticated": self._authenticated,
            "backend_url": self.backend_url,
            "device_id": self.device_config.device_id,
            "node_id": self.node_id,
            "telemetry_buffer_size": len(self._telemetry_buffer),
            "pending_commands": len(self._pending_commands),
            "last_heartbeat": self._last_heartbeat,
            "last_telemetry_send": self._last_telemetry_send
        }
