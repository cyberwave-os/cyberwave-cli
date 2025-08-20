# Device Flow Authentication API

This document describes the backend API endpoints required to support device flow authentication for the CyberWave CLI.

## Overview

Device flow authentication allows CLI users to authenticate through a web browser without entering credentials directly in the terminal. This is similar to how GitHub CLI, AWS CLI, and other modern CLIs handle authentication.

## Flow Diagram

```
CLI                 Backend                 Frontend
 |                     |                        |
 |-- POST /auth/device/initiate -------------->|
 |<- device_code, user_code, verification_url -|
 |                     |                        |
 |-- Open browser to verification_url -------->|
 |                     |                        |
 |-- Poll POST /auth/device/token ------------>|
 |<- 202 Accepted (pending) -------------------|
 |                     |                        |
 |                     |<-- User visits URL ---|
 |                     |<-- User enters code --|
 |                     |-- User authenticates ->|
 |                     |                        |
 |-- Poll POST /auth/device/token ------------>|
 |<- 200 OK with tokens -----------------------|
```

## API Endpoints

### 1. Initiate Device Flow

**Endpoint:** `POST /api/v1/auth/device/initiate`

**Description:** Initiates the device flow authentication process.

**Request Body:**
```json
{
  "client_type": "cli"
}
```

**Response:** `200 OK`
```json
{
  "device_code": "550e8400-e29b-41d4-a716-446655440000",
  "user_code": "WDJB-MJHT",
  "verification_url": "https://app.cyberwave.com/auth/device",
  "expires_in": 300,
  "interval": 5
}
```

**Fields:**
- `device_code`: Internal identifier for this authentication session (UUID)
- `user_code`: Human-readable code for user to enter (8 chars, format: XXXX-XXXX)
- `verification_url`: URL where user should go to authenticate
- `expires_in`: Seconds until this authentication request expires (default: 300)
- `interval`: Minimum seconds between polling requests (default: 5)

### 2. Poll for Token

**Endpoint:** `POST /api/v1/auth/device/token`

**Description:** Poll for completion of device flow authentication.

**Request Body:**
```json
{
  "device_code": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response (Pending):** `202 Accepted`
```json
{
  "status": "pending",
  "message": "User has not completed authentication yet"
}
```

**Response (Success):** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "def502004a8b7e0...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "default_workspace": 1,
  "default_project": 42,
  "user": {
    "id": 123,
    "email": "user@example.com",
    "full_name": "John Doe"
  }
}
```

**Response (Expired):** `410 Gone`
```json
{
  "error": "expired_token",
  "message": "The device code has expired"
}
```

**Response (Invalid):** `400 Bad Request`
```json
{
  "error": "invalid_device_code",
  "message": "Invalid or unknown device code"
}
```

## Frontend Integration

### Device Authentication Page

The frontend needs a page at `/auth/device` that:

1. **Accepts user_code parameter:** `https://app.cyberwave.com/auth/device?user_code=WDJB-MJHT`
2. **Displays the user code prominently**
3. **Prompts user to login** (if not already authenticated)
4. **Shows confirmation page** after successful linking
5. **Handles errors** (expired codes, invalid codes, etc.)

### Page Flow

1. User visits verification URL with user_code
2. If not logged in: redirect to login page with return URL
3. After login: show device authorization page with:
   - The user code
   - Description of what's being authorized ("CyberWave CLI")
   - Confirm/Deny buttons
4. On confirmation: link the device_code to the user's session
5. Show success page: "Device successfully authorized! You can return to your terminal."

## Backend Implementation Notes

### Database Schema

```sql
-- Device authentication sessions
CREATE TABLE device_auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_code UUID UNIQUE NOT NULL,
    user_code VARCHAR(9) UNIQUE NOT NULL, -- Format: XXXX-XXXX
    user_id INTEGER REFERENCES users(id) NULL, -- Set when user completes auth
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL
);

-- Index for efficient lookups
CREATE INDEX idx_device_auth_device_code ON device_auth_sessions(device_code);
CREATE INDEX idx_device_auth_user_code ON device_auth_sessions(user_code);
CREATE INDEX idx_device_auth_expires_at ON device_auth_sessions(expires_at);
```

### User Code Generation

- Format: `XXXX-XXXX` (8 characters with hyphen)
- Use uppercase letters and numbers, excluding confusing characters: `0`, `O`, `1`, `I`, `L`
- Character set: `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (29 characters)
- Example: `A4B7-C9D2`

### Security Considerations

1. **Short expiration time:** Default 5 minutes
2. **Rate limiting:** Limit initiation and polling requests
3. **Single use:** Device codes should be invalidated after use
4. **Secure random generation:** Use cryptographically secure random for codes
5. **Cleanup:** Regularly clean up expired sessions

### Error Handling

- **Rate limiting:** Return `429 Too Many Requests`
- **Malformed requests:** Return `400 Bad Request`
- **Server errors:** Return `500 Internal Server Error`
- **Device code not found:** Return `400 Bad Request`
- **Expired device code:** Return `410 Gone`

## Example Implementation (Python/Django)

```python
# models.py
class DeviceAuthSession(models.Model):
    device_code = models.UUIDField(unique=True, default=uuid.uuid4)
    user_code = models.CharField(max_length=9, unique=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

# views.py
@api_view(['POST'])
def initiate_device_flow(request):
    # Generate user code
    user_code = generate_user_code()
    
    # Create session
    session = DeviceAuthSession.objects.create(
        user_code=user_code,
        expires_at=timezone.now() + timedelta(minutes=5)
    )
    
    return Response({
        'device_code': session.device_code,
        'user_code': session.user_code,
        'verification_url': settings.FRONTEND_URL + '/auth/device',
        'expires_in': 300,
        'interval': 5
    })

@api_view(['POST'])
def poll_device_token(request):
    device_code = request.data.get('device_code')
    
    try:
        session = DeviceAuthSession.objects.get(device_code=device_code)
    except DeviceAuthSession.DoesNotExist:
        return Response({'error': 'invalid_device_code'}, status=400)
    
    if session.expires_at < timezone.now():
        session.delete()  # Cleanup
        return Response({'error': 'expired_token'}, status=410)
    
    if session.user is None:
        return Response({'status': 'pending'}, status=202)
    
    # Generate tokens for the user
    access_token = generate_access_token(session.user)
    refresh_token = generate_refresh_token(session.user)
    
    session.delete()  # Cleanup after successful auth
    
    return Response({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': 3600,
        'user': UserSerializer(session.user).data
    })
```

## Testing

### Manual Testing

1. Run CLI command: `cyberwave auth login`
2. Note the user code and verification URL
3. Visit the URL in a browser
4. Enter the user code
5. Complete authentication
6. Verify CLI receives tokens

### Automated Testing

- Test device flow initiation
- Test polling with pending state
- Test successful token exchange
- Test expired device codes
- Test invalid device codes
- Test rate limiting 