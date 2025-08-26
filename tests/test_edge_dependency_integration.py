"""
Integration tests for edge CLI and dependency management.

Tests the integration between edge device commands and the dependency system.
"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import Mock, patch

# Add the source path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_dependency_manager_import():
    """Test that dependency manager can be imported."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        assert dependency_manager is not None
        assert hasattr(dependency_manager, 'check_dependency')
        assert hasattr(dependency_manager, 'get_device_dependencies')
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

def test_camera_device_dependencies():
    """Test camera device dependency mapping."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        
        # Check camera device dependencies
        camera_deps = dependency_manager.get_device_dependencies("camera/ip")
        assert len(camera_deps) > 0
        
        # Should include expected dependencies
        dep_packages = [dep.package for dep in camera_deps]
        expected_packages = ["opencv-python", "aiohttp", "pillow"]
        
        for pkg in expected_packages:
            assert pkg in dep_packages, f"Expected {pkg} in camera dependencies"
            
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

def test_robot_device_dependencies():
    """Test robot device dependency mapping."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        
        # Check SO-101 robot dependencies  
        robot_deps = dependency_manager.get_device_dependencies("robot/so-101")
        assert len(robot_deps) > 0
        
        # Should include expected dependencies
        dep_packages = [dep.package for dep in robot_deps]
        expected_packages = ["lerobot", "pyserial", "pygame"]
        
        for pkg in expected_packages:
            assert pkg in dep_packages, f"Expected {pkg} in robot dependencies"
            
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

def test_feature_dependencies():
    """Test feature-based dependency mapping."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        
        # Check computer vision feature
        cv_deps = dependency_manager.get_feature_dependencies("computer_vision")
        assert len(cv_deps) > 0
        
        # Should include OpenCV
        dep_packages = [dep.package for dep in cv_deps]
        assert "opencv-python" in dep_packages
        
        # Check hand pose feature
        hand_deps = dependency_manager.get_feature_dependencies("hand_pose_estimation")
        assert len(hand_deps) > 0
        
        dep_packages = [dep.package for dep in hand_deps]
        assert "mediapipe" in dep_packages
        
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

def test_dependency_checking():
    """Test dependency availability checking."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        
        # Test with a package that definitely doesn't exist
        available, module = dependency_manager.check_dependency("definitely_not_a_real_package_xyz123")
        assert available is False
        assert module is None
        
        # Test checking known packages (might or might not be installed)
        packages_to_test = ["opencv-python", "psutil", "lerobot"]
        
        for pkg in packages_to_test:
            available, module = dependency_manager.check_dependency(pkg)
            assert isinstance(available, bool)
            if available:
                assert module is not None
            else:
                assert module is None
                
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

@patch('subprocess.run')
def test_auto_install_capability(mock_run):
    """Test auto-installation capability detection."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import dependency_manager
        
        # Mock successful pip check
        mock_run.return_value = Mock(returncode=0)
        can_install = dependency_manager._can_auto_install()
        assert can_install is True
        
        # Mock failed pip check
        mock_run.side_effect = FileNotFoundError()
        can_install = dependency_manager._can_auto_install()
        assert can_install is False
        
    except ImportError as e:
        pytest.skip(f"Dependency manager not available: {e}")

def test_device_readiness_check():
    """Test device readiness checking without auto-install."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import check_device_readiness
        
        # Test known device types (should not crash)
        device_types = ["camera/ip", "robot/so-101", "drone/tello"]
        
        for device_type in device_types:
            # This should return bool without crashing
            ready = check_device_readiness(device_type, auto_install=False)
            assert isinstance(ready, bool)
            
    except ImportError as e:
        pytest.skip(f"Check device readiness not available: {e}")

def test_graceful_import_handling():
    """Test that optional imports work gracefully."""
    try:
        from cyberwave_cli.plugins.edge.utils.dependencies import optional_import
        
        # Test with a package that doesn't exist
        result = optional_import("nonexistent_package_12345")
        assert result is None
        
        # Test with fallback message (should not crash)
        result = optional_import("another_nonexistent_package", "This is a fallback message")
        assert result is None
        
    except ImportError as e:
        pytest.skip(f"Optional import not available: {e}")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
