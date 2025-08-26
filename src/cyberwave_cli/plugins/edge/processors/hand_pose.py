"""
Enhanced Hand Pose Processor

Preserves and enhances the v1 hand pose estimation functionality:
- MediaPipe hand tracking integration
- HandSkeleton data generation
- Teleoperation integration
- Real-time hand gesture recognition

Adds v2 enhancements:
- Resource-aware processing
- Multiple hand detection
- Gesture recognition pipeline
- Improved teleoperation integration
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .base import BaseProcessor, EdgeEvent

logger = logging.getLogger(__name__)

class HandPoseProcessor(BaseProcessor):
    """
    Enhanced hand pose estimation processor with v2 architecture.
    
    Integrates MediaPipe hand tracking with the Cyberwave edge layer,
    providing HandSkeleton data for teleoperation controllers and
    real-time hand gesture recognition.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("hand_pose", config)
        
        # Hand pose specific config
        self.min_detection_confidence = config.get("min_detection_confidence", 0.7)
        self.min_tracking_confidence = config.get("min_tracking_confidence", 0.5)
        self.max_num_hands = config.get("max_num_hands", 2)  # Enhanced to support 2 hands
        self.enable_hand_landmarks = config.get("enable_hand_landmarks", True)
        self.enable_teleoperation = config.get("enable_teleoperation", False)
        self.enable_gestures = config.get("enable_gestures", False)
        
        # Teleoperation integration
        self.twin_uuid = config.get("twin_uuid")
        self.binding_id = config.get("binding_id", "default_hand_binding")
        self.cinematic_layer = None
        
        # MediaPipe components
        self.mp_hands = None
        self.hands = None
        self.mp_drawing = None
        
        # Enhanced state tracking
        self.frame_count = 0
        self.last_detection_time = 0.0
        self.detection_rate_limit = config.get("detection_rate_limit", 30)  # Hz
        self.hand_history: Dict[str, List[Dict[str, Any]]] = {"left": [], "right": []}
        self.gesture_recognizer = None
        
        # Performance optimization
        self.processing_resolution = config.get("processing_resolution", (640, 480))
        self.smoothing_window = config.get("smoothing_window", 3)
    
    async def initialize(self) -> None:
        """Initialize MediaPipe hand tracking components."""
        await super().initialize()
        
        try:
            import mediapipe as mp
            
            self.mp_hands = mp.solutions.hands
            self.mp_drawing = mp.solutions.drawing_utils
            
            self.hands = self.mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=self.max_num_hands,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence
            )
            
            logger.info(f"Hand pose processor initialized (max_hands: {self.max_num_hands}, "
                       f"detection_conf: {self.min_detection_confidence}, "
                       f"tracking_conf: {self.min_tracking_confidence})")
            
            # Initialize gesture recognition if enabled
            if self.enable_gestures:
                await self._initialize_gesture_recognition()
            
            # Initialize teleoperation if enabled
            if self.enable_teleoperation:
                await self._initialize_teleoperation()
                
        except ImportError as e:
            logger.error(f"MediaPipe not available: {e}. Install with: pip install mediapipe")
            self.hands = None
            raise
        except Exception as e:
            logger.error(f"Failed to initialize hand pose processor: {e}")
            self.hands = None
            raise
    
    async def _initialize_gesture_recognition(self) -> None:
        """Initialize gesture recognition components."""
        try:
            # Import or create gesture recognizer
            from .gestures import GestureRecognizer  # Would be implemented separately
            
            gesture_config = self.config.get("gesture_recognition", {})
            self.gesture_recognizer = GestureRecognizer(gesture_config)
            await self.gesture_recognizer.initialize()
            
            logger.info("Gesture recognition initialized")
            
        except ImportError:
            logger.warning("Gesture recognition not available")
        except Exception as e:
            logger.error(f"Failed to initialize gesture recognition: {e}")
    
    async def _initialize_teleoperation(self) -> None:
        """Initialize teleoperation components if enabled."""
        try:
            # Import teleoperation components
            from cyberwave_robotics_integrations.cinematic_skeleton import CinematicSkeletonLayer
            from cyberwave import Cyberwave
            
            # Create cinematic layer with TwinsAPI
            base_url = self.config.get("cyberwave_base_url", "http://localhost:8000")
            token = self.config.get("cyberwave_token", "")
            
            if token:
                cyberwave_client = Cyberwave(base_url=base_url, token=token)
                self.cinematic_layer = CinematicSkeletonLayer(twins_api=cyberwave_client.twins)
                
                # Create binding if twin_uuid is provided
                if self.twin_uuid:
                    self.cinematic_layer.bind_skeleton_to_twin(
                        binding_id=self.binding_id,
                        skeleton_type="hand",
                        twin_uuid=self.twin_uuid,
                        twin_type="arm"
                    )
                    
                logger.info(f"Teleoperation initialized for twin {self.twin_uuid}")
            else:
                logger.warning("No Cyberwave token provided, teleoperation disabled")
                
        except ImportError as e:
            logger.warning(f"Teleoperation components not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize teleoperation: {e}")
    
    async def process(self, frame: Any) -> List[EdgeEvent]:
        """Process video frame for hand pose estimation."""
        if not self.enabled or frame is None or self.hands is None:
            return []
        
        # Rate limiting
        current_time = time.time()
        if current_time - self.last_detection_time < (1.0 / self.detection_rate_limit):
            return []
        
        events = []
        self.frame_count += 1
        
        try:
            import cv2
            
            # Resize frame for processing if needed
            processed_frame = self._resize_frame_for_processing(frame)
            
            # Convert BGR to RGB for MediaPipe
            rgb_frame = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
            results = self.hands.process(rgb_frame)
            
            if results.multi_hand_landmarks:
                # Process each detected hand
                for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                    # Get hand type (left/right) if available
                    hand_type = "right"  # Default
                    if results.multi_handedness and idx < len(results.multi_handedness):
                        hand_label = results.multi_handedness[idx].classification[0].label
                        hand_type = hand_label.lower()
                    
                    # Create HandSkeleton from landmarks
                    hand_skeleton = self._create_hand_skeleton(hand_landmarks, hand_type, current_time)
                    
                    if hand_skeleton:
                        # Apply smoothing
                        smoothed_skeleton = self._apply_smoothing(hand_skeleton, hand_type)
                        
                        # Create edge event for hand detection
                        event = EdgeEvent(
                            timestamp=current_time,
                            event_type="hand_detected",
                            source=self.name,
                            confidence=smoothed_skeleton.confidence if smoothed_skeleton else 0.8,
                            data={
                                "hand_type": hand_type,
                                "hand_index": idx,
                                "frame_id": str(self.frame_count),
                                "landmarks": self._landmarks_to_dict(hand_landmarks),
                                "skeleton_data": self._skeleton_to_dict(smoothed_skeleton) if smoothed_skeleton else None,
                                "processing_resolution": self.processing_resolution
                            }
                        )
                        events.append(event)
                        
                        # Gesture recognition
                        if self.enable_gestures and self.gesture_recognizer:
                            gesture_events = await self._recognize_gestures(smoothed_skeleton, hand_type, current_time)
                            events.extend(gesture_events)
                        
                        # Send to teleoperation if enabled
                        if self.enable_teleoperation and self.cinematic_layer and smoothed_skeleton:
                            await self.cinematic_layer.process_skeleton_data(
                                self.binding_id, 
                                smoothed_skeleton
                            )
            
            # Update hand history for temporal consistency
            self._update_hand_history(results, current_time)
            
            self.last_detection_time = current_time
            
        except Exception as e:
            logger.error(f"Hand pose processing failed: {e}")
        
        return events
    
    def _resize_frame_for_processing(self, frame: Any) -> Any:
        """Resize frame to processing resolution for optimization."""
        import cv2
        
        height, width = frame.shape[:2]
        target_width, target_height = self.processing_resolution
        
        if width != target_width or height != target_height:
            frame = cv2.resize(frame, (target_width, target_height))
        
        return frame
    
    def _create_hand_skeleton(self, hand_landmarks, hand_type: str, timestamp: float):
        """Create HandSkeleton object from MediaPipe landmarks."""
        try:
            from cyberwave_robotics_integrations.skeleton_data import HandSkeleton
            
            return HandSkeleton.from_mediapipe_landmarks(
                hand_landmarks.landmark,
                hand_type=hand_type,
                timestamp=timestamp
            )
            
        except ImportError:
            logger.debug("HandSkeleton class not available, creating simplified skeleton")
            return self._create_simplified_skeleton(hand_landmarks, hand_type, timestamp)
    
    def _create_simplified_skeleton(self, hand_landmarks, hand_type: str, timestamp: float):
        """Create simplified skeleton when HandSkeleton class not available."""
        landmarks = hand_landmarks.landmark
        
        # Extract key landmarks
        wrist = landmarks[0]
        thumb_tip = landmarks[4]
        index_tip = landmarks[8]
        middle_tip = landmarks[12]
        ring_tip = landmarks[16]
        pinky_tip = landmarks[20]
        
        # Calculate grip strength (simplified)
        thumb_to_index_dist = ((thumb_tip.x - index_tip.x) ** 2 + 
                              (thumb_tip.y - index_tip.y) ** 2 + 
                              (thumb_tip.z - index_tip.z) ** 2) ** 0.5
        grip_strength = max(0, 1 - (thumb_to_index_dist * 5))  # Simplified calculation
        
        return {
            "hand_type": hand_type,
            "timestamp": timestamp,
            "confidence": 0.8,
            "wrist": {"position": [wrist.x, wrist.y, wrist.z]},
            "thumb_tip": {"position": [thumb_tip.x, thumb_tip.y, thumb_tip.z]},
            "index_tip": {"position": [index_tip.x, index_tip.y, index_tip.z]},
            "middle_tip": {"position": [middle_tip.x, middle_tip.y, middle_tip.z]},
            "ring_tip": {"position": [ring_tip.x, ring_tip.y, ring_tip.z]},
            "pinky_tip": {"position": [pinky_tip.x, pinky_tip.y, pinky_tip.z]},
            "grip_strength": grip_strength
        }
    
    def _apply_smoothing(self, hand_skeleton, hand_type: str):
        """Apply temporal smoothing to hand skeleton data."""
        if not hand_skeleton:
            return hand_skeleton
        
        # Add to history
        self.hand_history[hand_type].append(hand_skeleton)
        
        # Keep only recent history
        if len(self.hand_history[hand_type]) > self.smoothing_window:
            self.hand_history[hand_type].pop(0)
        
        # Apply smoothing if we have enough history
        if len(self.hand_history[hand_type]) >= 2:
            return self._smooth_skeleton_data(self.hand_history[hand_type])
        
        return hand_skeleton
    
    def _smooth_skeleton_data(self, skeleton_history: List[Any]) -> Any:
        """Apply smoothing to skeleton data using moving average."""
        if not skeleton_history:
            return None
        
        # For simplified implementation, return the most recent
        # In practice, this would apply weighted averaging
        return skeleton_history[-1]
    
    def _update_hand_history(self, results, timestamp: float) -> None:
        """Update hand detection history for temporal consistency."""
        # Clear history for hands not detected in this frame
        detected_hands = set()
        
        if results.multi_hand_landmarks and results.multi_handedness:
            for idx, handedness in enumerate(results.multi_handedness):
                hand_type = handedness.classification[0].label.lower()
                detected_hands.add(hand_type)
        
        # Clear history for undetected hands
        for hand_type in ["left", "right"]:
            if hand_type not in detected_hands:
                # Gradually fade out undetected hands
                if self.hand_history[hand_type]:
                    self.hand_history[hand_type] = []
    
    async def _recognize_gestures(self, hand_skeleton, hand_type: str, timestamp: float) -> List[EdgeEvent]:
        """Recognize gestures from hand skeleton data."""
        if not self.gesture_recognizer:
            return []
        
        try:
            gestures = await self.gesture_recognizer.recognize(hand_skeleton)
            
            events = []
            for gesture in gestures:
                event = EdgeEvent(
                    timestamp=timestamp,
                    event_type="gesture_recognized",
                    source=f"{self.name}_gesture",
                    confidence=gesture.get("confidence", 0.5),
                    data={
                        "hand_type": hand_type,
                        "gesture_name": gesture.get("name", "unknown"),
                        "gesture_params": gesture.get("params", {}),
                        "frame_id": str(self.frame_count)
                    }
                )
                events.append(event)
            
            return events
            
        except Exception as e:
            logger.error(f"Gesture recognition failed: {e}")
            return []
    
    def _landmarks_to_dict(self, hand_landmarks) -> List[Dict[str, float]]:
        """Convert MediaPipe landmarks to serializable format."""
        return [
            {
                "x": landmark.x,
                "y": landmark.y,
                "z": landmark.z,
                "visibility": getattr(landmark, 'visibility', 1.0)
            }
            for landmark in hand_landmarks.landmark
        ]
    
    def _skeleton_to_dict(self, hand_skeleton) -> Dict[str, Any]:
        """Convert hand skeleton to dictionary format."""
        if isinstance(hand_skeleton, dict):
            return hand_skeleton
        
        # If it's a HandSkeleton object
        try:
            return {
                "hand_type": hand_skeleton.hand_type,
                "confidence": hand_skeleton.confidence,
                "wrist": {"position": hand_skeleton.wrist.position},
                "thumb_tip": {"position": hand_skeleton.thumb_tip.position},
                "index_tip": {"position": hand_skeleton.index_tip.position},
                "grip_strength": hand_skeleton.grip_strength
            }
        except AttributeError:
            # Fallback for simplified skeleton
            return hand_skeleton
    
    async def cleanup(self) -> None:
        """Cleanup MediaPipe resources."""
        await super().cleanup()
        
        if self.hands:
            self.hands.close()
            self.hands = None
        
        if self.gesture_recognizer:
            await self.gesture_recognizer.cleanup()
            self.gesture_recognizer = None
        
        self.hand_history = {"left": [], "right": []}
        
        logger.info("Hand pose processor cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """Get enhanced processor status information."""
        base_status = super().get_status()
        
        hand_status = {
            "hands_initialized": self.hands is not None,
            "frame_count": self.frame_count,
            "last_detection_time": self.last_detection_time,
            "components": {
                "teleoperation": {
                    "enabled": self.enable_teleoperation,
                    "twin_uuid": self.twin_uuid,
                    "binding_id": self.binding_id,
                    "cinematic_layer": self.cinematic_layer is not None
                },
                "gesture_recognition": {
                    "enabled": self.enable_gestures,
                    "recognizer": self.gesture_recognizer is not None
                }
            },
            "hand_history": {
                "left_detections": len(self.hand_history.get("left", [])),
                "right_detections": len(self.hand_history.get("right", []))
            },
            "config": {
                "min_detection_confidence": self.min_detection_confidence,
                "min_tracking_confidence": self.min_tracking_confidence,
                "max_num_hands": self.max_num_hands,
                "detection_rate_limit": self.detection_rate_limit,
                "processing_resolution": self.processing_resolution,
                "smoothing_window": self.smoothing_window
            }
        }
        
        base_status.update(hand_status)
        return base_status

# Convenience functions for configuration

def create_hand_pose_config(
    max_hands: int = 2,
    detection_confidence: float = 0.7,
    tracking_confidence: float = 0.5,
    enable_teleoperation: bool = False,
    enable_gestures: bool = False,
    twin_uuid: Optional[str] = None
) -> Dict[str, Any]:
    """Create hand pose processor configuration."""
    return {
        "enabled": True,
        "max_num_hands": max_hands,
        "min_detection_confidence": detection_confidence,
        "min_tracking_confidence": tracking_confidence,
        "enable_teleoperation": enable_teleoperation,
        "enable_gestures": enable_gestures,
        "twin_uuid": twin_uuid,
        "detection_rate_limit": 30,
        "processing_resolution": (640, 480),
        "smoothing_window": 3
    }

# Integration helper for adding hand pose to existing edge nodes
def add_hand_pose_to_edge_node(edge_node, config: Optional[Dict[str, Any]] = None):
    """
    Helper function to add hand pose processing to an existing edge node.
    
    Args:
        edge_node: Existing EdgeNode instance
        config: Optional configuration for hand pose processor
    """
    if config is None:
        config = create_hand_pose_config()
    
    hand_processor = HandPoseProcessor(config)
    edge_node.processor_manager.add_processor(hand_processor)
    
    logger.info("Added enhanced hand pose processor to edge node")
    return hand_processor
