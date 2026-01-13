"""
Configuration validation for Cyberwave Edge.

Validates edge configurations against the JSON schema defined in the asset's
edge_runtimes metadata. Provides helpful error messages and suggestions.
"""

from typing import Any

from rich.console import Console

console = Console()


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    
    def __init__(self, errors: list[str], suggestions: list[str] | None = None):
        self.errors = errors
        self.suggestions = suggestions or []
        super().__init__(self._format_message())
    
    def _format_message(self) -> str:
        msg = "Configuration validation failed:\n"
        for error in self.errors:
            msg += f"  • {error}\n"
        if self.suggestions:
            msg += "\nSuggested fixes:\n"
            for suggestion in self.suggestions:
                msg += f"  • {suggestion}\n"
        return msg


def validate_edge_config(
    config: dict,
    asset: dict,
    runtime_name: str = "cyberwave-edge-python"
) -> list[str]:
    """
    Validate edge configuration against asset's runtime schema.
    
    Args:
        config: Edge configuration dictionary to validate.
        asset: Asset dictionary containing edge_runtimes metadata.
        runtime_name: Name of the runtime to validate against.
    
    Returns:
        List of validation error messages. Empty list if valid.
    
    Raises:
        ConfigValidationError: If validation fails (optional, for convenience).
    
    Example:
        >>> errors = validate_edge_config(
        ...     config={"cameras": [{"camera_id": "default", "fps": 999}]},
        ...     asset=asset_data,
        ...     runtime_name="cyberwave-edge-python"
        ... )
        >>> if errors:
        ...     print("Validation failed:", errors)
    """
    errors = []
    
    # Get runtime configuration
    metadata = asset.get('metadata', {}) or {}
    runtimes = metadata.get('edge_runtimes', [])
    
    runtime = None
    for r in runtimes:
        if r.get('name') == runtime_name:
            runtime = r
            break
    
    if not runtime:
        # No runtime found - check if any runtimes exist
        if runtimes:
            available = [r.get('name', 'unknown') for r in runtimes]
            errors.append(
                f"Runtime '{runtime_name}' not supported by this asset. "
                f"Available: {', '.join(available)}"
            )
        # If no runtimes defined, skip validation
        return errors
    
    # Get schema for this runtime
    schema = runtime.get('config_schema')
    if not schema:
        # No schema defined - skip validation
        return errors
    
    # Try to use jsonschema for validation
    try:
        from jsonschema import Draft7Validator, ValidationError
        
        validator = Draft7Validator(schema)
        for error in validator.iter_errors(config):
            path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"
            errors.append(f"{path}: {error.message}")
        
    except ImportError:
        # jsonschema not available - do basic validation
        errors.extend(_basic_validation(config, schema))
    
    return errors


def _basic_validation(config: dict, schema: dict) -> list[str]:
    """
    Basic validation without jsonschema library.
    
    Checks:
    - Required fields are present
    - Basic type matching
    - Enum values
    """
    errors = []
    properties = schema.get('properties', {})
    required = schema.get('required', [])
    
    # Check required fields
    for field in required:
        if field not in config:
            errors.append(f"Missing required field: {field}")
    
    # Check types and constraints for provided fields
    for field, value in config.items():
        if field not in properties:
            continue
        
        prop_schema = properties[field]
        errors.extend(_validate_value(field, value, prop_schema))
    
    return errors


def _validate_value(path: str, value: Any, schema: dict) -> list[str]:
    """Validate a single value against its schema."""
    errors = []
    expected_type = schema.get('type')
    
    # Type checking
    type_map = {
        'string': str,
        'integer': int,
        'number': (int, float),
        'boolean': bool,
        'array': list,
        'object': dict,
    }
    
    if expected_type and expected_type in type_map:
        expected = type_map[expected_type]
        if not isinstance(value, expected):
            errors.append(f"{path}: Expected {expected_type}, got {type(value).__name__}")
            return errors  # Skip further validation if type is wrong
    
    # Enum validation
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f"{path}: '{value}' not in allowed values: {schema['enum']}")
    
    # Numeric constraints
    if isinstance(value, (int, float)):
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(f"{path}: {value} is below minimum ({schema['minimum']})")
        if 'maximum' in schema and value > schema['maximum']:
            errors.append(f"{path}: {value} exceeds maximum ({schema['maximum']})")
    
    # String constraints
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            errors.append(f"{path}: String too short (min {schema['minLength']})")
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            errors.append(f"{path}: String too long (max {schema['maxLength']})")
    
    # Array validation
    if isinstance(value, list) and 'items' in schema:
        item_schema = schema['items']
        for i, item in enumerate(value):
            errors.extend(_validate_value(f"{path}[{i}]", item, item_schema))
    
    # Object validation
    if isinstance(value, dict) and 'properties' in schema:
        for key, val in value.items():
            if key in schema['properties']:
                errors.extend(_validate_value(f"{path}.{key}", val, schema['properties'][key]))
    
    return errors


def format_validation_errors(errors: list[str], suggestions: list[str] | None = None) -> str:
    """
    Format validation errors for display.
    
    Args:
        errors: List of error messages.
        suggestions: Optional list of suggested fixes.
    
    Returns:
        Formatted string for terminal display.
    """
    if not errors:
        return "[green]✓[/green] Configuration valid"
    
    lines = ["[red]✗[/red] Validation failed:\n"]
    for error in errors:
        lines.append(f"  [red]•[/red] {error}")
    
    if suggestions:
        lines.append("\n[yellow]Suggested fixes:[/yellow]")
        for suggestion in suggestions:
            lines.append(f"  [yellow]•[/yellow] {suggestion}")
    
    return "\n".join(lines)


def suggest_fixes(errors: list[str], schema: dict | None = None) -> list[str]:
    """
    Generate fix suggestions based on validation errors.
    
    Args:
        errors: List of validation error messages.
        schema: Optional schema for context.
    
    Returns:
        List of suggested fixes.
    """
    suggestions = []
    
    for error in errors:
        # Extract field and issue from error message
        if "exceeds maximum" in error:
            # Extract the maximum value and suggest it
            if "maximum" in error:
                import re
                match = re.search(r'maximum \((\d+)\)', error)
                if match:
                    max_val = match.group(1)
                    field = error.split(':')[0] if ':' in error else 'value'
                    suggestions.append(f"Set {field} to {max_val} (maximum supported)")
        
        elif "below minimum" in error:
            import re
            match = re.search(r'minimum \((\d+)\)', error)
            if match:
                min_val = match.group(1)
                field = error.split(':')[0] if ':' in error else 'value'
                suggestions.append(f"Set {field} to at least {min_val}")
        
        elif "not in allowed values" in error:
            import re
            match = re.search(r"allowed values: \[([^\]]+)\]", error)
            if match:
                allowed = match.group(1)
                suggestions.append(f"Use one of: {allowed}")
        
        elif "Missing required field" in error:
            field = error.replace("Missing required field: ", "")
            suggestions.append(f"Add required field: {field}")
    
    return suggestions
