"""
Backend Configuration Client

Handles communication with backend services for dynamic configuration management.
Supports configuration retrieval, updates, and real-time notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

class BackendConfigClient:
    """
    Client for retrieving and updating configuration from backend services.
    
    Provides methods to:
    - Fetch node-specific configuration
    - Update configuration on backend
    - Subscribe to configuration changes
    - Handle authentication and retries
    """
    
    def __init__(self, 
                 backend_url: str,
                 access_token: str,
                 node_id: Optional[str] = None,
                 timeout: float = 30.0,
                 retry_attempts: int = 3,
                 retry_delay: float = 1.0):
        self.backend_url = backend_url.rstrip("/")
        self.access_token = access_token
        self.node_id = node_id
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        
        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
        
        # Configuration cache
        self._cached_config: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)
        
        # Change notification callback
        self._change_callback: Optional[Callable] = None

    async def initialize(self) -> None:
        """Initialize the backend client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "User-Agent": "cyberwave-edge/2.0.0"
            }
        )
        
        # Test connectivity
        try:
            await self._test_connectivity()
            logger.info("Backend configuration client initialized successfully")
        except Exception as e:
            logger.warning(f"Backend connectivity test failed: {e}")

    async def _test_connectivity(self) -> bool:
        """Test connectivity to backend service."""
        if not self._client:
            return False
        
        try:
            response = await self._client.get(f"{self.backend_url}/health")
            return response.status_code == 200
        except Exception:
            return False

    async def get_node_config(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get configuration for this node from backend.
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh config
            
        Returns:
            Configuration dictionary or None if failed
        """
        # Check cache first
        if not force_refresh and self._is_cache_valid():
            logger.debug("Returning cached configuration")
            return self._cached_config
        
        if not self._client:
            logger.error("Backend client not initialized")
            return None
        
        endpoint = f"{self.backend_url}/api/v1/edge/nodes"
        if self.node_id:
            endpoint += f"/{self.node_id}/config"
        else:
            endpoint += "/config"
        
        try:
            config = await self._make_request("GET", endpoint)
            
            if config:
                self._cached_config = config
                self._cache_timestamp = datetime.now()
                logger.info("Retrieved node configuration from backend")
            
            return config
            
        except Exception as e:
            logger.error(f"Failed to get node config: {e}")
            return None

    async def update_node_config(self, config: Dict[str, Any]) -> bool:
        """
        Update node configuration on backend.
        
        Args:
            config: Configuration dictionary to update
            
        Returns:
            True if successful, False otherwise
        """
        if not self._client:
            logger.error("Backend client not initialized")
            return False
        
        endpoint = f"{self.backend_url}/api/v1/edge/nodes"
        if self.node_id:
            endpoint += f"/{self.node_id}/config"
        else:
            endpoint += "/config"
        
        try:
            result = await self._make_request("PUT", endpoint, data=config)
            
            if result:
                # Invalidate cache
                self._cached_config = None
                self._cache_timestamp = None
                logger.info("Updated node configuration on backend")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to update node config: {e}")
            return False

    async def get_device_config(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get device-specific configuration from backend.
        
        Args:
            device_id: Device identifier
            
        Returns:
            Device configuration or None if failed
        """
        if not self._client:
            return None
        
        endpoint = f"{self.backend_url}/api/v1/devices/{device_id}/config"
        
        try:
            return await self._make_request("GET", endpoint)
        except Exception as e:
            logger.error(f"Failed to get device config for {device_id}: {e}")
            return None

    async def get_processor_configs(self) -> Optional[Dict[str, Any]]:
        """
        Get processor configurations from backend.
        
        Returns:
            Dictionary of processor configurations
        """
        if not self._client:
            return None
        
        endpoint = f"{self.backend_url}/api/v1/edge/processors/config"
        
        try:
            return await self._make_request("GET", endpoint)
        except Exception as e:
            logger.error(f"Failed to get processor configs: {e}")
            return None

    async def register_node(self, node_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Register this node with backend service.
        
        Args:
            node_info: Node information for registration
            
        Returns:
            Registration response or None if failed
        """
        if not self._client:
            return None
        
        endpoint = f"{self.backend_url}/api/v1/edge/nodes/register"
        
        try:
            return await self._make_request("POST", endpoint, data=node_info)
        except Exception as e:
            logger.error(f"Failed to register node: {e}")
            return None

    async def heartbeat(self, status: Dict[str, Any]) -> bool:
        """
        Send heartbeat to backend with node status.
        
        Args:
            status: Node status information
            
        Returns:
            True if successful
        """
        if not self._client:
            return False
        
        endpoint = f"{self.backend_url}/api/v1/edge/nodes"
        if self.node_id:
            endpoint += f"/{self.node_id}/heartbeat"
        else:
            endpoint += "/heartbeat"
        
        try:
            await self._make_request("POST", endpoint, data=status)
            return True
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")
            return False

    async def _make_request(self, 
                          method: str, 
                          endpoint: str, 
                          data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Make HTTP request with retry logic.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            
        Returns:
            Response data or None if failed
        """
        if not self._client:
            return None
        
        for attempt in range(self.retry_attempts):
            try:
                if method.upper() == "GET":
                    response = await self._client.get(endpoint)
                elif method.upper() == "POST":
                    response = await self._client.post(endpoint, json=data)
                elif method.upper() == "PUT":
                    response = await self._client.put(endpoint, json=data)
                elif method.upper() == "DELETE":
                    response = await self._client.delete(endpoint)
                else:
                    logger.error(f"Unsupported HTTP method: {method}")
                    return None
                
                response.raise_for_status()
                
                # Return JSON response if available
                if response.headers.get("content-type", "").startswith("application/json"):
                    return response.json()
                else:
                    return {"success": True}
                
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code} for {endpoint}: {e}")
                if e.response.status_code < 500:
                    # Client error, don't retry
                    break
            except httpx.RequestError as e:
                logger.warning(f"Request error for {endpoint}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error for {endpoint}: {e}")
            
            # Wait before retry
            if attempt < self.retry_attempts - 1:
                await asyncio.sleep(self.retry_delay * (2 ** attempt))
        
        return None

    def _is_cache_valid(self) -> bool:
        """Check if cached configuration is still valid."""
        if not self._cached_config or not self._cache_timestamp:
            return False
        
        return datetime.now() - self._cache_timestamp < self._cache_ttl

    def set_change_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Set callback for configuration changes."""
        self._change_callback = callback

    async def start_change_monitoring(self) -> None:
        """Start monitoring for configuration changes (WebSocket or polling)."""
        # This would implement WebSocket connection or polling for config changes
        # For now, we'll implement polling as a fallback
        async def poll_changes():
            last_config = self._cached_config
            
            while True:
                try:
                    current_config = await self.get_node_config(force_refresh=True)
                    
                    if current_config and current_config != last_config:
                        logger.info("Configuration change detected")
                        if self._change_callback:
                            await self._change_callback(current_config)
                        last_config = current_config
                    
                    await asyncio.sleep(30)  # Poll every 30 seconds
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in change monitoring: {e}")
                    await asyncio.sleep(60)  # Wait longer on error
        
        # Start polling task
        asyncio.create_task(poll_changes())

    async def shutdown(self) -> None:
        """Shutdown the backend client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        
        logger.info("Backend configuration client shutdown")

    def get_status(self) -> Dict[str, Any]:
        """Get client status."""
        return {
            "backend_url": self.backend_url,
            "node_id": self.node_id,
            "connected": self._client is not None,
            "cached_config": self._cached_config is not None,
            "cache_timestamp": self._cache_timestamp.isoformat() if self._cache_timestamp else None,
            "cache_valid": self._is_cache_valid()
        }
