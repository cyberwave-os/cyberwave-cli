"""
Unit tests for the dependency management system.

Tests the scalable dependency management framework including:
- Dependency registration and discovery
- Device-specific dependency checking
- Auto-installation capabilities
- Graceful handling of missing dependencies
- Feature-based dependency mapping
"""

import pytest
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Import the dependency management system
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cyberwave_cli.plugins.edge.utils.dependencies import (
    DependencyManager,
    DependencySpec,
    DependencyType,
    dependency_manager,
    require_for_device,
    require_for_feature,
    optional_import,
    check_device_readiness,
    requires_dependency
)


class TestDependencySpec:
    """Test DependencySpec data class."""
    
    def test_basic_spec_creation(self):
        """Test creating a basic dependency specification."""
        spec = DependencySpec(
            name="Test Package",
            package="test-package",
            import_name="test_package",
            description="A test package"
        )
        
        assert spec.name == "Test Package"
        assert spec.package == "test-package"
        assert spec.import_name == "test_package"
        assert spec.description == "A test package"
        assert spec.dep_type == DependencyType.PIP
        assert spec.required_for == []
        assert spec.alternatives == []
    
    def test_spec_with_all_fields(self):
        """Test creating a spec with all fields."""
        spec = DependencySpec(
            name="Advanced Package",
            package="advanced-pkg",
            import_name="advanced_pkg",
            version="1.2.3",
            dep_type=DependencyType.CONDA,
            description="Advanced package for testing",
            docs_url="https://example.com/docs",
            install_guide="conda install advanced-pkg",
            fallback_message="Features will be limited",
            required_for=["feature1", "feature2"],
            alternatives=["alt-pkg"]
        )
        
        assert spec.version == "1.2.3"
        assert spec.dep_type == DependencyType.CONDA
        assert spec.docs_url == "https://example.com/docs"
        assert spec.install_guide == "conda install advanced-pkg"
        assert spec.fallback_message == "Features will be limited"
        assert spec.required_for == ["feature1", "feature2"]
        assert spec.alternatives == ["alt-pkg"]


class TestDependencyManager:
    """Test DependencyManager functionality."""
    
    def setup_method(self):
        """Set up test dependency manager."""
        self.manager = DependencyManager()
        # Clear the built-in dependencies for clean testing
        self.manager._dependencies.clear()
        self.manager._device_requirements.clear()
        self.manager._feature_requirements.clear()
        self.manager._import_cache.clear()
    
    def test_register_dependency(self):
        """Test registering a dependency."""
        spec = DependencySpec(
            name="Test Lib",
            package="testlib",
            import_name="testlib",
            description="Test library"
        )
        
        self.manager.register_dependency(spec)
        
        assert "testlib" in self.manager._dependencies
        assert self.manager._dependencies["testlib"] == spec
    
    def test_get_device_dependencies(self):
        """Test getting dependencies for a device type."""
        # Register test dependencies
        spec1 = DependencySpec("Lib1", "lib1", "lib1", description="Library 1")
        spec2 = DependencySpec("Lib2", "lib2", "lib2", description="Library 2")
        
        self.manager.register_dependency(spec1)
        self.manager.register_dependency(spec2)
        
        # Register device requirements
        self.manager._device_requirements["test_device"] = ["lib1", "lib2"]
        
        deps = self.manager.get_device_dependencies("test_device")
        
        assert len(deps) == 2
        assert deps[0].name == "Lib1"
        assert deps[1].name == "Lib2"
    
    def test_get_feature_dependencies(self):
        """Test getting dependencies for a feature."""
        spec = DependencySpec("Feature Lib", "feature_lib", "feature_lib", description="Feature library")
        self.manager.register_dependency(spec)
        
        self.manager._feature_requirements["test_feature"] = ["feature_lib"]
        
        deps = self.manager.get_feature_dependencies("test_feature")
        
        assert len(deps) == 1
        assert deps[0].name == "Feature Lib"
    
    @patch('importlib.import_module')
    def test_check_dependency_available(self, mock_import):
        """Test checking for an available dependency."""
        mock_module = Mock()
        mock_import.return_value = mock_module
        
        spec = DependencySpec("Available Lib", "available_lib", "available_lib", description="Available library")
        self.manager.register_dependency(spec)
        
        available, module = self.manager.check_dependency("available_lib")
        
        assert available is True
        assert module == mock_module
        mock_import.assert_called_once_with("available_lib")
    
    @patch('importlib.import_module')
    def test_check_dependency_missing(self, mock_import):
        """Test checking for a missing dependency."""
        mock_import.side_effect = ImportError("No module named 'missing_lib'")
        
        spec = DependencySpec("Missing Lib", "missing_lib", "missing_lib", description="Missing library")
        self.manager.register_dependency(spec)
        
        available, module = self.manager.check_dependency("missing_lib")
        
        assert available is False
        assert module is None
    
    def test_check_dependency_caching(self):
        """Test that dependency checks are cached."""
        with patch('importlib.import_module') as mock_import:
            mock_module = Mock()
            mock_import.return_value = mock_module
            
            spec = DependencySpec("Cached Lib", "cached_lib", "cached_lib", description="Cached library")
            self.manager.register_dependency(spec)
            
            # First call
            available1, module1 = self.manager.check_dependency("cached_lib")
            # Second call should use cache
            available2, module2 = self.manager.check_dependency("cached_lib")
            
            assert available1 is True
            assert available2 is True
            assert module1 == module2 == mock_module
            # import_module should only be called once due to caching
            mock_import.assert_called_once()
    
    @patch('subprocess.run')
    def test_can_auto_install_with_pip(self, mock_run):
        """Test checking if auto-installation is possible with pip."""
        mock_run.return_value = Mock(returncode=0)
        
        can_install = self.manager._can_auto_install()
        
        assert can_install is True
        mock_run.assert_called_with(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            check=True
        )
    
    @patch('subprocess.run')
    def test_can_auto_install_no_pip(self, mock_run):
        """Test when pip is not available."""
        mock_run.side_effect = [
            FileNotFoundError(),  # No pip module
            FileNotFoundError()   # No pip3 command
        ]
        
        can_install = self.manager._can_auto_install()
        
        assert can_install is False
    
    @patch('subprocess.run')
    def test_install_dependency_success(self, mock_run):
        """Test successful dependency installation."""
        mock_run.return_value = Mock(returncode=0)
        
        spec = DependencySpec("Install Test", "install_test", "install_test", description="Installation test")
        
        success = self.manager._install_dependency(spec)
        
        assert success is True
        mock_run.assert_called_with(
            [sys.executable, "-m", "pip", "install", "install_test"],
            capture_output=True,
            text=True,
            timeout=300
        )
    
    @patch('subprocess.run')
    def test_install_dependency_failure(self, mock_run):
        """Test failed dependency installation."""
        mock_run.return_value = Mock(returncode=1, stderr="Installation failed")
        
        spec = DependencySpec("Install Fail", "install_fail", "install_fail", description="Installation failure test")
        
        success = self.manager._install_dependency(spec)
        
        assert success is False
    
    def test_check_device_dependencies(self):
        """Test checking all dependencies for a device."""
        with patch.object(self.manager, 'check_dependency') as mock_check:
            mock_check.side_effect = [
                (True, Mock()),   # First dep available
                (False, None),    # Second dep missing
                (True, Mock())    # Third dep available
            ]
            
            # Set up test device
            self.manager._device_requirements["test_device"] = ["dep1", "dep2", "dep3"]
            
            # Register specs
            for dep in ["dep1", "dep2", "dep3"]:
                spec = DependencySpec(f"Dep {dep}", dep, dep, description=f"Dependency {dep}")
                self.manager.register_dependency(spec)
            
            available, missing = self.manager.check_device_dependencies("test_device")
            
            assert available == ["dep1", "dep3"]
            assert missing == ["dep2"]


class TestBuiltinDependencies:
    """Test the built-in dependency registry."""
    
    def test_opencv_dependency(self):
        """Test OpenCV dependency is properly registered."""
        spec = dependency_manager._dependencies.get("opencv-python")
        
        assert spec is not None
        assert spec.name == "OpenCV"
        assert spec.import_name == "cv2"
        assert "computer_vision" in spec.required_for
        assert "camera_analysis" in spec.required_for
    
    def test_lerobot_dependency(self):
        """Test LeRobot dependency is properly registered."""
        spec = dependency_manager._dependencies.get("lerobot")
        
        assert spec is not None
        assert spec.name == "LeRobot"
        assert spec.import_name == "lerobot"
        assert "so101" in spec.required_for
        assert "robot_control" in spec.required_for
    
    def test_psutil_dependency(self):
        """Test psutil dependency is properly registered."""
        spec = dependency_manager._dependencies.get("psutil")
        
        assert spec is not None
        assert spec.name == "psutil"
        assert spec.import_name == "psutil"
        assert "health_monitoring" in spec.required_for
    
    def test_camera_device_requirements(self):
        """Test camera device requirements."""
        reqs = dependency_manager._device_requirements.get("camera/ip")
        
        assert reqs is not None
        assert "opencv-python" in reqs
        assert "aiohttp" in reqs
        assert "pillow" in reqs
    
    def test_so101_device_requirements(self):
        """Test SO-101 device requirements."""
        reqs = dependency_manager._device_requirements.get("robot/so-101")
        
        assert reqs is not None
        assert "lerobot" in reqs
        assert "pyserial" in reqs
        assert "pygame" in reqs
    
    def test_computer_vision_feature_requirements(self):
        """Test computer vision feature requirements."""
        reqs = dependency_manager._feature_requirements.get("computer_vision")
        
        assert reqs is not None
        assert "opencv-python" in reqs
        assert "pillow" in reqs


class TestConvenienceFunctions:
    """Test convenience functions for dependency management."""
    
    @patch.object(dependency_manager, 'require_dependency')
    def test_require_for_device(self, mock_require):
        """Test require_for_device convenience function."""
        mock_require.return_value = (True, Mock())
        
        success, module = require_for_device("test_device", "test_package")
        
        assert success is True
        mock_require.assert_called_once_with(
            "test_package",
            context="test_device device",
            auto_install=False
        )
    
    @patch.object(dependency_manager, 'require_dependency')
    def test_require_for_feature(self, mock_require):
        """Test require_for_feature convenience function."""
        mock_require.return_value = (True, Mock())
        
        success, module = require_for_feature("test_feature", "test_package")
        
        assert success is True
        mock_require.assert_called_once_with(
            "test_package",
            context="test_feature feature",
            auto_install=False
        )
    
    @patch.object(dependency_manager, 'check_dependency')
    def test_optional_import_available(self, mock_check):
        """Test optional_import when module is available."""
        mock_module = Mock()
        mock_check.return_value = (True, mock_module)
        
        result = optional_import("available_package")
        
        assert result == mock_module
    
    @patch.object(dependency_manager, 'check_dependency')
    def test_optional_import_missing(self, mock_check):
        """Test optional_import when module is missing."""
        mock_check.return_value = (False, None)
        
        with patch('rich.console.Console.print') as mock_print:
            result = optional_import("missing_package", "Package not available")
            
            assert result is None
            mock_print.assert_called_once()
    
    @patch.object(dependency_manager, 'check_device_dependencies')
    def test_check_device_readiness_ready(self, mock_check_deps):
        """Test check_device_readiness when device is ready."""
        mock_check_deps.return_value = (["dep1", "dep2"], [])
        
        with patch('rich.console.Console.print') as mock_print:
            ready = check_device_readiness("test_device")
            
            assert ready is True
            # Should print success message
            mock_print.assert_called()
    
    @patch.object(dependency_manager, 'check_device_dependencies')
    @patch.object(dependency_manager, 'show_device_requirements')
    def test_check_device_readiness_not_ready(self, mock_show_reqs, mock_check_deps):
        """Test check_device_readiness when device is not ready."""
        mock_check_deps.return_value = (["dep1"], ["missing_dep"])
        
        with patch('rich.console.Console.print') as mock_print:
            ready = check_device_readiness("test_device")
            
            assert ready is False
            mock_show_reqs.assert_called_once_with("test_device")


class TestDecorator:
    """Test the requires_dependency decorator."""
    
    def test_decorator_success(self):
        """Test decorator when dependency is available."""
        with patch.object(dependency_manager, 'require_dependency') as mock_require:
            mock_require.return_value = (True, Mock())
            
            @requires_dependency("test_package", "test_feature")
            def test_function():
                return "success"
            
            result = test_function()
            
            assert result == "success"
            mock_require.assert_called_once_with(
                "test_package",
                context="test_feature",
                auto_install=False
            )
    
    def test_decorator_failure(self):
        """Test decorator when dependency is missing."""
        with patch.object(dependency_manager, 'require_dependency') as mock_require:
            mock_require.return_value = (False, None)
            
            @requires_dependency("missing_package", "test_feature")
            def test_function():
                return "success"
            
            with pytest.raises(ImportError) as exc_info:
                test_function()
            
            assert "Required dependency missing_package not available" in str(exc_info.value)
    
    def test_decorator_auto_install(self):
        """Test decorator with auto-install option."""
        with patch.object(dependency_manager, 'require_dependency') as mock_require:
            mock_require.return_value = (True, Mock())
            
            @requires_dependency("test_package")
            def test_function():
                return "success"
            
            result = test_function(auto_install_deps=True)
            
            assert result == "success"
            mock_require.assert_called_once_with(
                "test_package",
                context="test_function",
                auto_install=True
            )


class TestIntegration:
    """Integration tests for the dependency system."""
    
    def test_full_workflow(self):
        """Test a complete dependency management workflow."""
        # This test uses the actual global dependency_manager
        
        # Check a device that should exist
        camera_deps = dependency_manager.get_device_dependencies("camera/ip")
        assert len(camera_deps) > 0
        
        # Check availability
        available, missing = dependency_manager.check_device_dependencies("camera/ip")
        
        # opencv-python should be available in most test environments
        # psutil and aiohttp might be missing
        assert isinstance(available, list)
        assert isinstance(missing, list)
        
        # Test feature dependencies
        cv_deps = dependency_manager.get_feature_dependencies("computer_vision")
        assert len(cv_deps) > 0
        
        # Test unknown device/feature
        unknown_deps = dependency_manager.get_device_dependencies("unknown/device")
        assert len(unknown_deps) == 0
        
        unknown_feature_deps = dependency_manager.get_feature_dependencies("unknown_feature")
        assert len(unknown_feature_deps) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
