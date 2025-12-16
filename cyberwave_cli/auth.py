"""Authentication module for the Cyberwave CLI.

This module handles authentication with the Cyberwave API.
The login endpoint is not part of the SDK, so we implement it here directly.
"""

from dataclasses import dataclass
from typing import Optional

import httpx

from .config import (
    API_TOKENS_ENDPOINT,
    AUTH_LOGIN_ENDPOINT,
    AUTH_USER_ENDPOINT,
    WORKSPACES_ENDPOINT,
    get_api_url,
)


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details


@dataclass
class User:
    """User information returned from the API."""

    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """Create a User from API response data."""
        return cls(
            email=data.get("email", ""),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
        )


@dataclass
class Workspace:
    """Workspace information returned from the API."""

    uuid: str
    name: str
    slug: str

    @classmethod
    def from_dict(cls, data: dict) -> "Workspace":
        """Create a Workspace from API response data."""
        return cls(
            uuid=data.get("uuid", ""),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
        )


@dataclass
class APIToken:
    """Permanent API token returned from the API."""

    uuid: str
    token: str
    workspace_uuid: Optional[str] = None
    workspace_name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "APIToken":
        """Create an APIToken from API response data."""
        return cls(
            uuid=data.get("uuid", ""),
            token=data.get("token", ""),
            workspace_uuid=data.get("workspace_uuid"),
            workspace_name=data.get("workspace_name"),
        )


class AuthClient:
    """Client for authentication API calls."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or get_api_url()
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._client.close()

    def login(self, email: str, password: str) -> str:
        """
        Authenticate with email and password.

        Args:
            email: User's email address
            password: User's password

        Returns:
            Authentication token

        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            response = self._client.post(
                AUTH_LOGIN_ENDPOINT,
                json={
                    "username": email,
                    "email": email,
                    "password": password,
                },
            )

            if response.status_code == 200:
                data = response.json()
                token = data.get("token") or data.get("key")
                if token:
                    return token
                raise AuthenticationError("No token in response")

            # Handle error responses
            if response.status_code == 400:
                data = response.json()
                # Check for non_field_errors (invalid credentials)
                errors = data.get("non_field_errors", [])
                if errors:
                    raise AuthenticationError(errors[0], details=data)
                # Check for field-specific errors
                for field in ["email", "password", "username"]:
                    if field in data:
                        raise AuthenticationError(
                            f"{field}: {data[field][0]}",
                            details=data,
                        )
                raise AuthenticationError("Invalid credentials", details=data)

            if response.status_code == 401:
                raise AuthenticationError("Invalid email or password")

            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise AuthenticationError(f"Connection error: {e}") from e

        raise AuthenticationError("Authentication failed")

    def get_current_user(self, token: str) -> User:
        """
        Get the current user's information.

        Args:
            token: Authentication token

        Returns:
            User information

        Raises:
            AuthenticationError: If the token is invalid
        """
        try:
            response = self._client.get(
                AUTH_USER_ENDPOINT,
                headers={"Authorization": f"Token {token}"},
            )

            if response.status_code == 200:
                return User.from_dict(response.json())

            if response.status_code == 401:
                raise AuthenticationError("Invalid or expired token")

            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise AuthenticationError(f"Connection error: {e}") from e

        raise AuthenticationError("Failed to get user information")

    def get_workspaces(self, token: str) -> list[Workspace]:
        """
        Get the user's workspaces.

        Args:
            token: Authentication token

        Returns:
            List of workspaces

        Raises:
            AuthenticationError: If the token is invalid
        """
        try:
            response = self._client.get(
                WORKSPACES_ENDPOINT,
                headers={"Authorization": f"Token {token}"},
            )

            if response.status_code == 200:
                return [Workspace.from_dict(w) for w in response.json()]

            if response.status_code == 401:
                raise AuthenticationError("Invalid or expired token")

            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise AuthenticationError(f"Connection error: {e}") from e

        raise AuthenticationError("Failed to get workspaces")

    def create_api_token(self, token: str, workspace_uuid: str) -> APIToken:
        """
        Create a permanent API token for programmatic access.

        The OAuth/session token from login expires, but the API token is permanent
        until manually revoked.

        Args:
            token: Authentication token (session/OAuth token)
            workspace_uuid: UUID of the workspace the token will have access to

        Returns:
            APIToken with the permanent token

        Raises:
            AuthenticationError: If token creation fails
        """
        try:
            # Use a fresh client without cookies to avoid CSRF issues
            # (session cookies trigger Django's SessionAuthentication which enforces CSRF)
            response = httpx.post(
                f"{self.base_url}{API_TOKENS_ENDPOINT}",
                headers={
                    "Authorization": f"Token {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"workspace_uuid": workspace_uuid},
                timeout=30.0,
            )

            if response.status_code in (200, 201):
                return APIToken.from_dict(response.json())

            if response.status_code == 401:
                raise AuthenticationError("Invalid or expired token")

            if response.status_code == 400:
                data = response.json()
                raise AuthenticationError(
                    f"Failed to create API token: {data}",
                    details=data,
                )

            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise AuthenticationError(f"Connection error: {e}") from e

        raise AuthenticationError("Failed to create API token")
