"""
Asset resolution for Cyberwave CLI.

Resolves asset identifiers from multiple sources:
- Registry ID: "unitree/go2", "cyberwave/standard-cam"
- Alias: "go2", "camera" (short names defined in asset metadata)
- Local file: "./my-robot.json"
- URL: "https://example.com/asset.json"
"""

import json
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


class AssetResolutionError(Exception):
    """Raised when asset cannot be resolved."""
    pass


def resolve_asset(identifier: str, client: Any) -> dict:
    """
    Resolve an asset from various identifier formats.
    
    Args:
        identifier: Can be:
            - Registry ID: "unitree/go2"
            - Alias: "go2", "camera"
            - Local file path: "./my-robot.json", "/path/to/asset.json"
            - URL: "https://example.com/asset.json"
        client: Cyberwave SDK client instance
    
    Returns:
        Asset data as dictionary. For SDK assets, returns a dict with:
        - uuid, name, registry_id, metadata, capabilities, etc.
        For local/URL assets, returns the parsed JSON.
    
    Raises:
        AssetResolutionError: If asset cannot be found or loaded.
    
    Examples:
        >>> asset = resolve_asset("camera", client)
        >>> asset = resolve_asset("unitree/go2", client)
        >>> asset = resolve_asset("./my-camera.json", client)
    """
    # 1. Check if it's a local file
    if _is_local_file(identifier):
        return _load_local_asset(identifier)
    
    # 2. Check if it's a URL
    if _is_url(identifier):
        return _load_url_asset(identifier)
    
    # 3. Try as registry ID first (contains '/')
    if '/' in identifier:
        asset = _get_by_registry_id(identifier, client)
        if asset:
            return _asset_to_dict(asset)
    
    # 4. Try as alias
    asset = _get_by_alias(identifier, client)
    if asset:
        return _asset_to_dict(asset)
    
    # 5. Try as registry ID without slash (maybe partial match)
    asset = _get_by_registry_id(identifier, client)
    if asset:
        return _asset_to_dict(asset)
    
    raise AssetResolutionError(
        f"Asset not found: '{identifier}'\n"
        f"Try:\n"
        f"  - Registry ID: cyberwave/standard-cam\n"
        f"  - Alias: camera\n"
        f"  - Local file: ./my-asset.json\n"
        f"  - URL: https://example.com/asset.json"
    )


def _is_local_file(identifier: str) -> bool:
    """Check if identifier looks like a local file path."""
    # Explicit JSON extension
    if identifier.endswith('.json'):
        return True
    # Path-like (starts with ./ or /)
    if identifier.startswith('./') or identifier.startswith('/'):
        return Path(identifier).suffix == '.json' or Path(identifier).exists()
    return False


def _is_url(identifier: str) -> bool:
    """Check if identifier is a URL."""
    return identifier.startswith('http://') or identifier.startswith('https://')


def _load_local_asset(path: str) -> dict:
    """Load asset configuration from local JSON file."""
    file_path = Path(path).expanduser().resolve()
    
    if not file_path.exists():
        raise AssetResolutionError(f"File not found: {file_path}")
    
    if not file_path.suffix == '.json':
        raise AssetResolutionError(f"Expected .json file, got: {file_path}")
    
    try:
        content = file_path.read_text()
        data = json.loads(content)
        
        # Mark as local asset
        data['_source'] = 'local_file'
        data['_source_path'] = str(file_path)
        
        return data
    except json.JSONDecodeError as e:
        raise AssetResolutionError(f"Invalid JSON in {file_path}: {e}")
    except Exception as e:
        raise AssetResolutionError(f"Error reading {file_path}: {e}")


def _load_url_asset(url: str) -> dict:
    """Load asset configuration from URL."""
    try:
        import httpx
    except ImportError:
        raise AssetResolutionError(
            "httpx package required for URL assets. Install with: pip install httpx"
        )
    
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
        
        # Mark as URL asset
        data['_source'] = 'url'
        data['_source_url'] = url
        
        return data
    except httpx.HTTPError as e:
        raise AssetResolutionError(f"Failed to fetch {url}: {e}")
    except json.JSONDecodeError as e:
        raise AssetResolutionError(f"Invalid JSON from {url}: {e}")


def _get_by_registry_id(registry_id: str, client: Any) -> Any | None:
    """Get asset by registry ID."""
    try:
        # Try direct lookup if SDK supports it
        if hasattr(client.assets, 'get_by_registry_id'):
            return client.assets.get_by_registry_id(registry_id)
        
        # Fall back to list and filter
        assets = client.assets.list()
        for asset in assets:
            if getattr(asset, 'registry_id', None) == registry_id:
                return asset
        
        return None
    except Exception:
        return None


def _get_by_alias(alias: str, client: Any) -> Any | None:
    """Get asset by alias defined in metadata."""
    try:
        assets = client.assets.list()
        alias_lower = alias.lower()
        
        for asset in assets:
            metadata = getattr(asset, 'metadata', {}) or {}
            aliases = metadata.get('aliases', [])
            
            # Case-insensitive alias matching
            if any(a.lower() == alias_lower for a in aliases):
                return asset
            
            # Also check if name matches (as implicit alias)
            name = getattr(asset, 'name', '').lower()
            if name == alias_lower:
                return asset
        
        return None
    except Exception:
        return None


def _asset_to_dict(asset: Any) -> dict:
    """Convert SDK asset object to dictionary."""
    # If it's already a dict, return it
    if isinstance(asset, dict):
        return asset
    
    # Convert SDK object to dict
    data = {
        'uuid': str(getattr(asset, 'uuid', '')),
        'name': getattr(asset, 'name', ''),
        'description': getattr(asset, 'description', ''),
        'registry_id': getattr(asset, 'registry_id', None),
        'metadata': getattr(asset, 'metadata', {}) or {},
        'capabilities': getattr(asset, 'capabilities', {}) or {},
        '_source': 'api',
    }
    
    return data


def get_asset_display_name(asset: dict) -> str:
    """Get a display name for an asset."""
    if asset.get('registry_id'):
        return f"{asset.get('name', 'Unknown')} ({asset['registry_id']})"
    return asset.get('name', 'Unknown Asset')


def get_asset_aliases(asset: dict) -> list[str]:
    """Get list of aliases for an asset."""
    metadata = asset.get('metadata', {}) or {}
    return metadata.get('aliases', [])


def get_asset_runtimes(asset: dict) -> list[dict]:
    """Get supported edge runtimes for an asset."""
    metadata = asset.get('metadata', {}) or {}
    return metadata.get('edge_runtimes', [])


def get_runtime_by_name(asset: dict, runtime_name: str) -> dict | None:
    """Get a specific runtime configuration by name."""
    runtimes = get_asset_runtimes(asset)
    for runtime in runtimes:
        if runtime.get('name') == runtime_name:
            return runtime
    return None
