"""
Enhanced Computer Vision Processors

Preserves and enhances all v1 computer vision functionality:
- Motion Detection with background subtraction
- Object Detection (extensible for ML models)
- Frame Processing and optimization
- Real-time video analytics

Adds v2 enhancements:
- Modular CV pipeline
- Resource-aware processing
- Multiple detection algorithms
- Performance optimization
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseProcessor, EdgeEvent

logger = logging.getLogger(__name__)

class MotionDetector:
    """Standalone motion detection using background subtraction."""
    
    def __init__(self, config: Dict[str, Any]):
        self.motion_threshold = config.get("motion_threshold", 0.02)
        self.min_contour_area = config.get("min_contour_area", 500)
        self.max_contour_area = config.get("max_contour_area", 50000)
        self.background_learning_rate = config.get("background_learning_rate", 0.01)
        
        # OpenCV components
        self.background_subtractor = None
        self.initialized = False
    
    def initialize(self) -> None:
        """Initialize background subtractor."""
        try:
            import cv2
            self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
                detectShadows=True,
                varThreshold=50,
                history=100
            )
            self.initialized = True
            logger.info("Motion detector initialized")
        except ImportError:
            logger.error("OpenCV not available for motion detection")
    
    def detect_motion(self, frame: Any) -> List[Dict[str, Any]]:
        """Detect motion in frame and return motion events."""
        if not self.initialized or frame is None:
            return []
        
        import cv2
        import numpy as np
        
        detections = []
        
        try:
            # Apply background subtraction
            fg_mask = self.background_subtractor.apply(frame, learningRate=self.background_learning_rate)
            
            # Morphological operations to clean up the mask
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
            
            # Find contours
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            height, width = frame.shape[:2]
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                if self.min_contour_area <= area <= self.max_contour_area:
                    # Get bounding box
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Calculate motion metrics
                    center_x = x + w / 2
                    center_y = y + h / 2
                    
                    detection = {
                        "bbox": [x/width, y/height, w/width, h/height],  # Normalized
                        "center": [center_x/width, center_y/height],
                        "area": area,
                        "area_ratio": area / (width * height),
                        "aspect_ratio": w / h,
                        "confidence": min(area / 10000.0, 1.0)  # Simple confidence metric
                    }
                    
                    detections.append(detection)
            
        except Exception as e:
            logger.error(f"Motion detection failed: {e}")
        
        return detections

class ObjectDetector:
    """Extensible object detection framework."""
    
    def __init__(self, config: Dict[str, Any]):
        self.model_type = config.get("model_type", "yolo")  # yolo, tensorflow, custom
        self.confidence_threshold = config.get("confidence_threshold", 0.5)
        self.nms_threshold = config.get("nms_threshold", 0.4)
        self.model_path = config.get("model_path")
        self.class_names = config.get("class_names", [])
        
        # Model components
        self.model = None
        self.initialized = False
    
    def initialize(self) -> None:
        """Initialize object detection model."""
        if self.model_type == "yolo" and self.model_path:
            self._init_yolo()
        elif self.model_type == "tensorflow" and self.model_path:
            self._init_tensorflow()
        else:
            logger.info("No object detection model configured")
    
    def _init_yolo(self) -> None:
        """Initialize YOLO model."""
        try:
            import cv2
            self.model = cv2.dnn.readNet(self.model_path)
            self.initialized = True
            logger.info(f"YOLO model loaded: {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
    
    def _init_tensorflow(self) -> None:
        """Initialize TensorFlow model."""
        try:
            # Placeholder for TensorFlow Lite or TensorFlow integration
            logger.info("TensorFlow object detection not yet implemented")
        except Exception as e:
            logger.error(f"Failed to load TensorFlow model: {e}")
    
    def detect_objects(self, frame: Any) -> List[Dict[str, Any]]:
        """Detect objects in frame."""
        if not self.initialized or frame is None:
            return []
        
        if self.model_type == "yolo":
            return self._detect_yolo(frame)
        
        return []
    
    def _detect_yolo(self, frame: Any) -> List[Dict[str, Any]]:
        """YOLO object detection."""
        import cv2
        import numpy as np
        
        detections = []
        
        try:
            height, width = frame.shape[:2]
            
            # Prepare input blob
            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (416, 416), swapRB=True, crop=False)
            self.model.setInput(blob)
            
            # Run inference
            outputs = self.model.forward()
            
            # Parse outputs
            boxes = []
            confidences = []
            class_ids = []
            
            for output in outputs:
                for detection in output:
                    scores = detection[5:]
                    class_id = np.argmax(scores)
                    confidence = scores[class_id]
                    
                    if confidence > self.confidence_threshold:
                        center_x = int(detection[0] * width)
                        center_y = int(detection[1] * height)
                        w = int(detection[2] * width)
                        h = int(detection[3] * height)
                        
                        x = int(center_x - w / 2)
                        y = int(center_y - h / 2)
                        
                        boxes.append([x, y, w, h])
                        confidences.append(float(confidence))
                        class_ids.append(class_id)
            
            # Apply NMS
            indices = cv2.dnn.NMSBoxes(boxes, confidences, self.confidence_threshold, self.nms_threshold)
            
            if len(indices) > 0:
                for i in indices.flatten():
                    x, y, w, h = boxes[i]
                    class_id = class_ids[i]
                    confidence = confidences[i]
                    
                    class_name = self.class_names[class_id] if class_id < len(self.class_names) else f"class_{class_id}"
                    
                    detection = {
                        "bbox": [x/width, y/height, w/width, h/height],  # Normalized
                        "class_id": class_id,
                        "class_name": class_name,
                        "confidence": confidence
                    }
                    
                    detections.append(detection)
        
        except Exception as e:
            logger.error(f"YOLO detection failed: {e}")
        
        return detections

class ComputerVisionProcessor(BaseProcessor):
    """
    Enhanced computer vision processor combining all v1 functionality with v2 architecture.
    
    Features:
    - Motion detection with background subtraction
    - Object detection (YOLO, TensorFlow, custom models)
    - Frame processing and optimization
    - Configurable CV pipeline
    - Resource-aware processing
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("computer_vision", config)
        
        # CV-specific config
        self.enable_motion = config.get("enable_motion_detection", True)
        self.enable_objects = config.get("enable_object_detection", False)
        self.enable_preprocessing = config.get("enable_preprocessing", True)
        self.enable_postprocessing = config.get("enable_postprocessing", True)
        
        # Frame processing
        self.target_fps = config.get("target_fps", 30)
        self.resize_factor = config.get("resize_factor", 1.0)
        self.skip_frames = config.get("skip_frames", 0)  # Skip N frames between processing
        
        # Detection components
        self.motion_detector: Optional[MotionDetector] = None
        self.object_detector: Optional[ObjectDetector] = None
        
        # State tracking
        self.frame_count = 0
        self.last_process_time = 0.0
        self.fps_counter = 0
        self.fps_start_time = time.time()
        self.current_fps = 0.0
    
    async def initialize(self) -> None:
        """Initialize CV components."""
        await super().initialize()
        
        try:
            # Initialize motion detection
            if self.enable_motion:
                motion_config = self.config.get("motion_detection", {})
                self.motion_detector = MotionDetector(motion_config)
                self.motion_detector.initialize()
            
            # Initialize object detection
            if self.enable_objects:
                object_config = self.config.get("object_detection", {})
                self.object_detector = ObjectDetector(object_config)
                self.object_detector.initialize()
            
            logger.info(f"CV Processor initialized (motion: {self.enable_motion}, objects: {self.enable_objects})")
            
        except Exception as e:
            logger.error(f"CV processor initialization failed: {e}")
            raise
    
    async def process(self, frame: Any) -> List[EdgeEvent]:
        """Process video frame for computer vision events."""
        if not self.enabled or frame is None:
            return []
        
        self.frame_count += 1
        current_time = time.time()
        
        # Frame skipping for performance
        if self.skip_frames > 0 and self.frame_count % (self.skip_frames + 1) != 0:
            return []
        
        # FPS tracking
        self._update_fps_counter()
        
        events = []
        
        try:
            # Preprocess frame
            processed_frame = await self._preprocess_frame(frame) if self.enable_preprocessing else frame
            
            # Motion detection
            if self.enable_motion and self.motion_detector:
                motion_events = await self._process_motion_detection(processed_frame, current_time)
                events.extend(motion_events)
            
            # Object detection
            if self.enable_objects and self.object_detector:
                object_events = await self._process_object_detection(processed_frame, current_time)
                events.extend(object_events)
            
            # Postprocessing
            if self.enable_postprocessing:
                events = await self._postprocess_events(events, processed_frame)
            
        except Exception as e:
            logger.error(f"CV processing failed: {e}")
        
        self.last_process_time = current_time
        return events
    
    async def _preprocess_frame(self, frame: Any) -> Any:
        """Preprocess frame for optimization."""
        import cv2
        
        try:
            # Resize if factor is set
            if self.resize_factor != 1.0:
                height, width = frame.shape[:2]
                new_width = int(width * self.resize_factor)
                new_height = int(height * self.resize_factor)
                frame = cv2.resize(frame, (new_width, new_height))
            
            # Additional preprocessing could be added here
            # - Noise reduction
            # - Color space conversion
            # - Histogram equalization
            
        except Exception as e:
            logger.error(f"Frame preprocessing failed: {e}")
        
        return frame
    
    async def _process_motion_detection(self, frame: Any, timestamp: float) -> List[EdgeEvent]:
        """Process motion detection."""
        events = []
        
        try:
            detections = self.motion_detector.detect_motion(frame)
            
            for detection in detections:
                event = EdgeEvent(
                    timestamp=timestamp,
                    event_type="motion_detected",
                    source=self.name,
                    confidence=detection["confidence"],
                    data={
                        "frame_id": str(self.frame_count),
                        "bbox": detection["bbox"],
                        "center": detection["center"],
                        "area": detection["area"],
                        "area_ratio": detection["area_ratio"],
                        "aspect_ratio": detection["aspect_ratio"],
                        "fps": self.current_fps
                    }
                )
                events.append(event)
        
        except Exception as e:
            logger.error(f"Motion detection processing failed: {e}")
        
        return events
    
    async def _process_object_detection(self, frame: Any, timestamp: float) -> List[EdgeEvent]:
        """Process object detection."""
        events = []
        
        try:
            detections = self.object_detector.detect_objects(frame)
            
            for detection in detections:
                event = EdgeEvent(
                    timestamp=timestamp,
                    event_type="object_detected",
                    source=self.name,
                    confidence=detection["confidence"],
                    data={
                        "frame_id": str(self.frame_count),
                        "bbox": detection["bbox"],
                        "class_id": detection["class_id"],
                        "class_name": detection["class_name"],
                        "fps": self.current_fps
                    }
                )
                events.append(event)
        
        except Exception as e:
            logger.error(f"Object detection processing failed: {e}")
        
        return events
    
    async def _postprocess_events(self, events: List[EdgeEvent], frame: Any) -> List[EdgeEvent]:
        """Postprocess events for filtering and enhancement."""
        # Could add:
        # - Event filtering based on confidence
        # - Temporal consistency checking
        # - Event fusion and deduplication
        # - ROI filtering
        
        return events
    
    def _update_fps_counter(self) -> None:
        """Update FPS counter."""
        self.fps_counter += 1
        
        current_time = time.time()
        elapsed = current_time - self.fps_start_time
        
        if elapsed >= 1.0:  # Update FPS every second
            self.current_fps = self.fps_counter / elapsed
            self.fps_counter = 0
            self.fps_start_time = current_time
    
    async def cleanup(self) -> None:
        """Cleanup CV resources."""
        await super().cleanup()
        
        if self.motion_detector:
            self.motion_detector = None
        
        if self.object_detector:
            self.object_detector = None
        
        logger.info("CV processor cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """Get CV processor status."""
        base_status = super().get_status()
        
        cv_status = {
            "frame_count": self.frame_count,
            "current_fps": self.current_fps,
            "target_fps": self.target_fps,
            "last_process_time": self.last_process_time,
            "components": {
                "motion_detection": {
                    "enabled": self.enable_motion,
                    "initialized": self.motion_detector is not None and self.motion_detector.initialized
                },
                "object_detection": {
                    "enabled": self.enable_objects,
                    "initialized": self.object_detector is not None and self.object_detector.initialized
                }
            },
            "config": {
                "resize_factor": self.resize_factor,
                "skip_frames": self.skip_frames,
                "preprocessing": self.enable_preprocessing,
                "postprocessing": self.enable_postprocessing
            }
        }
        
        base_status.update(cv_status)
        return base_status

# Convenience functions for backward compatibility

def create_motion_detector_config(
    motion_threshold: float = 0.02,
    min_contour_area: int = 500,
    max_contour_area: int = 50000
) -> Dict[str, Any]:
    """Create motion detection configuration."""
    return {
        "enable_motion_detection": True,
        "motion_detection": {
            "motion_threshold": motion_threshold,
            "min_contour_area": min_contour_area,
            "max_contour_area": max_contour_area
        }
    }

def create_object_detector_config(
    model_type: str = "yolo",
    model_path: Optional[str] = None,
    confidence_threshold: float = 0.5,
    class_names: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Create object detection configuration."""
    return {
        "enable_object_detection": True,
        "object_detection": {
            "model_type": model_type,
            "model_path": model_path,
            "confidence_threshold": confidence_threshold,
            "class_names": class_names or []
        }
    }

def create_cv_processor_config(
    enable_motion: bool = True,
    enable_objects: bool = False,
    target_fps: int = 30,
    **kwargs
) -> Dict[str, Any]:
    """Create complete CV processor configuration."""
    config = {
        "enabled": True,
        "enable_motion_detection": enable_motion,
        "enable_object_detection": enable_objects,
        "target_fps": target_fps,
        "enable_preprocessing": True,
        "enable_postprocessing": True
    }
    
    config.update(kwargs)
    return config
