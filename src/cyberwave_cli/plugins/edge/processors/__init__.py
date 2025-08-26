"""
Enhanced Processors for Edge v2

Combines the new v2 processor architecture with all existing v1 processors:
- Computer Vision (motion detection, object detection)
- Robotics Data Processing (anomaly detection, performance analysis)
- Hand Pose Estimation (MediaPipe integration)
- Sensor Fusion (multi-sensor data integration)
- Custom Processors (extensible framework)

All v1 processors are preserved and enhanced with the new v2 architecture.
"""

from .base import BaseProcessor, ProcessorConfig
from .manager import ProcessorManager

# Graceful imports for processors
try:
    from .computer_vision import ComputerVisionProcessor, MotionDetector, ObjectDetector
except ImportError:
    ComputerVisionProcessor = MotionDetector = ObjectDetector = None

try:
    from .hand_pose import HandPoseProcessor
except ImportError:
    HandPoseProcessor = None

__all__ = [
    # Base classes
    "BaseProcessor",
    "ProcessorConfig",
    "ProcessorManager",
    
    # Computer Vision (optional)
    "ComputerVisionProcessor",
    "MotionDetector", 
    "ObjectDetector",
    
    # Specialized (optional)
    "HandPoseProcessor"
]
