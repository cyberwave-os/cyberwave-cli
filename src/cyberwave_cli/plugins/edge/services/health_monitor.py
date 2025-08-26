"""
Health monitoring service for edge nodes.

Provides comprehensive health monitoring including:
- System resource monitoring (CPU, memory, disk)
- Device connectivity monitoring
- Processor health monitoring
- Cloud connectivity monitoring
"""

import asyncio
import time
from typing import Dict, Any, Optional
import logging

# Graceful psutil import
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

class HealthMonitor:
    """Health monitoring service for edge nodes."""
    
    def __init__(self, check_interval: float = 30.0, enable_detailed_metrics: bool = True):
        """
        Initialize health monitor.
        
        Args:
            check_interval: Interval between health checks in seconds
            enable_detailed_metrics: Whether to collect detailed system metrics
        """
        self.check_interval = check_interval
        self.enable_detailed_metrics = enable_detailed_metrics
        self.is_running = False
        self._monitor_task = None
        self._last_health_check = None
        
    async def start(self) -> None:
        """Start the health monitoring service."""
        if self.is_running:
            logger.warning("Health monitor is already running")
            return
        
        self.is_running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")
    
    async def stop(self) -> None:
        """Stop the health monitoring service."""
        if not self.is_running:
            return
        
        self.is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Health monitor stopped")
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        try:
            while self.is_running:
                await self._perform_health_check()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info("Health monitor loop cancelled")
        except Exception as e:
            logger.error(f"Health monitor loop error: {e}")
    
    async def _perform_health_check(self) -> None:
        """Perform a comprehensive health check."""
        try:
            health_data = await self.get_health_status()
            self._last_health_check = health_data
            
            # Log critical issues
            if health_data.get("status") == "critical":
                logger.error(f"Critical health issues detected: {health_data.get('issues', [])}")
            elif health_data.get("status") == "warning":
                logger.warning(f"Health warnings detected: {health_data.get('issues', [])}")
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
    
    async def get_health_status(self) -> Dict[str, Any]:
        """
        Get comprehensive health status.
        
        Returns:
            Dict containing health status and metrics
        """
        try:
            # System metrics
            system_health = await self._get_system_health()
            
            # Device connectivity (placeholder)
            device_health = await self._get_device_health()
            
            # Cloud connectivity (placeholder)
            cloud_health = await self._get_cloud_health()
            
            # Determine overall status
            all_statuses = [
                system_health.get("status", "unknown"),
                device_health.get("status", "unknown"),
                cloud_health.get("status", "unknown")
            ]
            
            if "critical" in all_statuses:
                overall_status = "critical"
            elif "warning" in all_statuses:
                overall_status = "warning"
            elif all(s == "healthy" for s in all_statuses):
                overall_status = "healthy"
            else:
                overall_status = "unknown"
            
            # Collect all issues
            all_issues = []
            all_issues.extend(system_health.get("issues", []))
            all_issues.extend(device_health.get("issues", []))
            all_issues.extend(cloud_health.get("issues", []))
            
            return {
                "status": overall_status,
                "timestamp": time.time(),
                "issues": all_issues,
                "system": system_health,
                "device": device_health,
                "cloud": cloud_health
            }
            
        except Exception as e:
            logger.error(f"Failed to get health status: {e}")
            return {
                "status": "critical",
                "timestamp": time.time(),
                "issues": [f"Health check failed: {e}"],
                "system": {"status": "unknown"},
                "device": {"status": "unknown"},
                "cloud": {"status": "unknown"}
            }
    
    async def _get_system_health(self) -> Dict[str, Any]:
        """Get system health metrics."""
        if not PSUTIL_AVAILABLE:
            return {
                "status": "unknown",
                "issues": ["psutil not available - system monitoring disabled"],
                "metrics": {},
                "note": "Install psutil for system health monitoring: pip install psutil"
            }
        
        try:
            issues = []
            
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent > 90:
                issues.append(f"High CPU usage: {cpu_percent:.1f}%")
            
            # Memory usage
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                issues.append(f"High memory usage: {memory.percent:.1f}%")
            
            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = (disk.used / disk.total) * 100
            if disk_percent > 90:
                issues.append(f"High disk usage: {disk_percent:.1f}%")
            
            # Temperature (if available)
            temps = {}
            try:
                temp_sensors = psutil.sensors_temperatures()
                for sensor_name, sensor_list in temp_sensors.items():
                    for sensor in sensor_list:
                        if sensor.current and sensor.current > 80:  # 80°C threshold
                            issues.append(f"High temperature {sensor_name}: {sensor.current:.1f}°C")
                        temps[f"{sensor_name}_{sensor.label}"] = sensor.current
            except (AttributeError, OSError):
                # Temperature monitoring not available on this system
                pass
            
            # Determine system status
            if any("High" in issue for issue in issues):
                status = "critical" if len(issues) > 2 else "warning"
            else:
                status = "healthy"
            
            result = {
                "status": status,
                "issues": issues,
                "metrics": {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk_percent,
                    "memory_available_gb": memory.available / (1024**3),
                    "disk_free_gb": disk.free / (1024**3)
                }
            }
            
            if temps:
                result["metrics"]["temperatures"] = temps
            
            if self.enable_detailed_metrics and PSUTIL_AVAILABLE:
                # Add more detailed metrics
                result["metrics"].update({
                    "load_average": psutil.getloadavg() if hasattr(psutil, 'getloadavg') else None,
                    "uptime": time.time() - psutil.boot_time(),
                    "process_count": len(psutil.pids())
                })
            
            return result
            
        except Exception as e:
            return {
                "status": "critical",
                "issues": [f"System health check failed: {e}"],
                "metrics": {}
            }
    
    async def _get_device_health(self) -> Dict[str, Any]:
        """Get device connectivity health (placeholder)."""
        # This would be implemented to check actual device connectivity
        # For now, return a placeholder
        return {
            "status": "healthy",
            "issues": [],
            "metrics": {
                "devices_connected": 0,
                "devices_total": 0
            }
        }
    
    async def _get_cloud_health(self) -> Dict[str, Any]:
        """Get cloud connectivity health (placeholder)."""
        # This would be implemented to check cloud connectivity
        # For now, return a placeholder
        return {
            "status": "healthy", 
            "issues": [],
            "metrics": {
                "last_successful_connection": time.time(),
                "connection_latency_ms": 0
            }
        }
    
    def get_last_health_check(self) -> Optional[Dict[str, Any]]:
        """Get the results of the last health check."""
        return self._last_health_check
    
    def is_healthy(self) -> bool:
        """Check if the system is currently healthy."""
        if not self._last_health_check:
            return False
        
        return self._last_health_check.get("status") == "healthy"
