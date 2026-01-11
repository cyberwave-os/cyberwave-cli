"""
Shared plugin definitions for the Cyberwave CLI.

These are fallback definitions used when the edge package is not installed.
The authoritative source is cyberwave_edge.plugins.registry.BUILTIN_PLUGINS.
"""

# Fallback plugin definitions when edge package is not available
BUILTIN_PLUGINS_FALLBACK: dict[str, dict] = {
    "yolo": {
        "id": "yolo",
        "name": "YOLO Object Detection",
        "version": "1.0.0",
        "type": "model",
        "runtime": "ultralytics",
        "description": "YOLOv8 models for object detection, pose estimation, and segmentation",
        "author": "Ultralytics",
        "dependencies": ["ultralytics>=8.0.0"],
        "capabilities": ["object_detection", "pose", "segmentation"],
        "models": [
            {
                "id": "yolov8n",
                "name": "YOLOv8 Nano",
                "path": "yolov8n.pt",
                "task": "detect",
                "description": "Fastest YOLOv8 model, 80 COCO classes",
                "default_confidence": 0.5,
            },
            {
                "id": "yolov8s",
                "name": "YOLOv8 Small",
                "path": "yolov8s.pt",
                "task": "detect",
                "description": "Balanced speed/accuracy, 80 COCO classes",
                "default_confidence": 0.5,
            },
            {
                "id": "yolov8m",
                "name": "YOLOv8 Medium",
                "path": "yolov8m.pt",
                "task": "detect",
                "description": "Higher accuracy, 80 COCO classes",
                "default_confidence": 0.5,
            },
            {
                "id": "yolov8n-pose",
                "name": "YOLOv8 Nano Pose",
                "path": "yolov8n-pose.pt",
                "task": "pose",
                "description": "Human pose estimation with 17 keypoints",
                "default_confidence": 0.5,
            },
            {
                "id": "yolov8n-seg",
                "name": "YOLOv8 Nano Segmentation",
                "path": "yolov8n-seg.pt",
                "task": "segment",
                "description": "Instance segmentation with pixel masks",
                "default_confidence": 0.5,
            },
        ],
    },
    "opencv-cascades": {
        "id": "opencv-cascades",
        "name": "OpenCV Cascade Classifiers",
        "version": "1.0.0",
        "type": "model",
        "runtime": "opencv",
        "description": "Fast Haar cascade classifiers for face and body detection",
        "author": "OpenCV",
        "dependencies": ["opencv-python>=4.8.0"],
        "capabilities": ["face_detection", "object_detection"],
        "models": [
            {
                "id": "face-frontal",
                "name": "Frontal Face Detector",
                "path": "haarcascade_frontalface_default.xml",
                "task": "detect",
                "description": "Fast frontal face detection",
                "default_confidence": 1.0,
                "supported_classes": ["face"],
            },
            {
                "id": "face-profile",
                "name": "Profile Face Detector",
                "path": "haarcascade_profileface.xml",
                "task": "detect",
                "description": "Side profile face detection",
                "default_confidence": 1.0,
                "supported_classes": ["face"],
            },
            {
                "id": "full-body",
                "name": "Full Body Detector",
                "path": "haarcascade_fullbody.xml",
                "task": "detect",
                "description": "Pedestrian/full body detection",
                "default_confidence": 1.0,
                "supported_classes": ["person"],
            },
        ],
    },
    "motion-detector": {
        "id": "motion-detector",
        "name": "Motion Detection",
        "version": "1.0.0",
        "type": "processor",
        "runtime": "opencv",
        "description": "Frame differencing motion detection",
        "author": "Cyberwave",
        "dependencies": ["opencv-python>=4.8.0"],
        "capabilities": ["motion_detection"],
        "models": [
            {
                "id": "background-subtraction",
                "name": "Background Subtraction",
                "path": "",
                "task": "motion",
                "description": "MOG2 background subtractor for motion detection",
            },
        ],
    },
}


def get_builtin_plugins() -> dict[str, dict]:
    """
    Get built-in plugin definitions.
    
    Attempts to import from edge package for single source of truth.
    Falls back to BUILTIN_PLUGINS_FALLBACK if edge package not available.
    
    Returns:
        Dictionary of plugin_id -> plugin definition
    """
    try:
        from cyberwave_edge.plugins.registry import BUILTIN_PLUGINS
        return BUILTIN_PLUGINS
    except ImportError:
        return BUILTIN_PLUGINS_FALLBACK


def get_fallback_models() -> dict[str, dict]:
    """
    Get fallback edge models in a flat format.
    
    Converts plugin format to a simpler model-focused format for
    model listing and binding commands.
    
    Returns:
        Dictionary of model_id -> model info
    """
    plugins = get_builtin_plugins()
    models: dict[str, dict] = {}
    
    for plugin in plugins.values():
        runtime = plugin.get("runtime", "")
        for m in plugin.get("models", []):
            models[m["id"]] = {
                "name": m.get("name", m["id"]),
                "description": m.get("description", ""),
                "runtime": runtime,
                "model_path": m.get("path", ""),
                "event_types": [f"{m.get('task', 'detect')}_detected"],
                "plugin_id": plugin.get("id", ""),
            }
    
    return models
