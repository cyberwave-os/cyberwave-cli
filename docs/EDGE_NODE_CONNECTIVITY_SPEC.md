# Edge Node Connectivity & Identity System - Implementation Specification

## 🎯 Overview

This document specifies the implementation requirements for the backend (BE) and frontend (FE) to support the new Edge Node connectivity and identity system. The system provides seamless online/offline operation with automatic node identification and registration workflow.

## 🏗️ Architecture Overview

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Edge Node (CLI)   │    │    Backend (BE)     │    │   Frontend (FE)     │
│                     │    │                     │    │                     │
│ • Node Identity     │◄──►│ • Node Registry     │◄──►│ • Registration UI   │
│ • Connectivity Mgr  │    │ • Authentication    │    │ • Node Management   │
│ • Offline Cache     │    │ • Sync Service      │    │ • Status Dashboard  │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

## 🤖 Node Identity System

### Automatic Node ID Generation

Each edge node automatically generates a unique identifier on first startup:

```json
{
  "node_id": "edge_123456abcdef1234",
  "node_name": "hostname-edge-abcdef12",
  "created_at": "2024-01-15T10:30:00Z",
  "platform": "Darwin",
  "architecture": "arm64",
  "hostname": "MacBook-Pro.local",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "installation_id": "uuid-4-string",
  "version": "0.11.5",
  "last_seen": "2024-01-15T11:45:00Z",
  "registered_backend": false,
  "registration_token": null
}
```

### Node ID Format

- **Format**: `edge_{timestamp}{hash}`
- **Length**: 22 characters
- **Example**: `edge_123456abcdef1234`
- **Uniqueness**: Globally unique across all installations
- **Persistence**: Cached locally in `~/.cyberwave/node_identity.json`

## 📡 Connectivity Modes

### 1. Online Mode
- Backend is available and responsive
- Real-time registration and telemetry
- Full feature set available

### 2. Hybrid Mode  
- Backend temporarily unavailable
- Uses cached credentials
- Automatic sync when backend returns

### 3. Offline Mode
- Backend unavailable, no cached credentials
- Local operation only
- Manual registration required

## 🔄 Registration Workflow

### CLI Side Flow

1. **Node Identity Creation** (automatic on first run)
   ```bash
   # Automatically creates node identity
   cyberwave edge node-info
   ```

2. **Registration Information Display**
   ```bash
   # Shows node info for manual registration
   cyberwave edge register-node
   ```

3. **Connectivity Check** (automatic during operations)
   ```bash
   # Checks backend availability
   cyberwave edge ip-camera register --camera 192.168.1.100
   ```

4. **Offline Setup** (when backend unavailable)
   - Displays registration URL with node ID
   - Prompts for authentication token
   - Caches credentials locally

### Expected User Flow

1. User installs CLI → Node ID auto-generated
2. User runs device command → Connectivity check fails
3. CLI shows registration URL with Node ID
4. User goes to frontend, registers node, gets token
5. User enters token in CLI → Offline mode configured
6. Node operates locally, syncs when backend available

## 🖥️ Backend (BE) Implementation Requirements

### 1. Edge Nodes API Endpoints

#### `POST /api/v1/edge/nodes`
Register a new edge node.

**Request Body:**
```json
{
  "node_id": "edge_123456abcdef1234",
  "node_name": "hostname-edge-abcdef12",
  "project_id": "project-uuid",
  "platform": "Darwin",
  "architecture": "arm64",
  "hostname": "MacBook-Pro.local",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "installation_id": "uuid-4-string",
  "version": "0.11.5",
  "capabilities": [
    "camera_processing",
    "computer_vision",
    "telemetry",
    "offline_operation"
  ]
}
```

**Response:**
```json
{
  "success": true,
  "node": {
    "id": "db-node-uuid",
    "node_id": "edge_123456abcdef1234",
    "status": "registered",
    "auth_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
    "created_at": "2024-01-15T10:30:00Z"
  }
}
```

#### `GET /api/v1/edge/nodes/{node_id}`
Get node information and status.

**Response:**
```json
{
  "node_id": "edge_123456abcdef1234",
  "node_name": "hostname-edge-abcdef12",
  "status": "online|offline|registered",
  "last_seen": "2024-01-15T11:45:00Z",
  "project_id": "project-uuid",
  "capabilities": ["camera_processing", "computer_vision"],
  "registered_devices": 3,
  "sync_status": "synced|pending|error"
}
```

#### `PUT /api/v1/edge/nodes/{node_id}/heartbeat`
Update node last seen timestamp.

**Request Body:**
```json
{
  "status": "online",
  "telemetry": {
    "cpu_usage": 45.2,
    "memory_usage": 62.1,
    "devices_active": 3
  }
}
```

#### `POST /api/v1/edge/nodes/{node_id}/sync`
Sync offline data with backend.

**Request Body:**
```json
{
  "offline_sensors": [
    {
      "name": "Camera_192_168_1_100",
      "type": "ip_camera",
      "ip_address": "192.168.1.100",
      "registered_at": "2024-01-15T10:30:00Z"
    }
  ],
  "offline_telemetry": [
    {
      "timestamp": "2024-01-15T10:31:00Z",
      "sensor_id": "local_camera_1",
      "data": {"motion_detected": true}
    }
  ]
}
```

### 2. Authentication & Authorization

#### JWT Token Format
```json
{
  "sub": "edge_123456abcdef1234",
  "iss": "cyberwave-backend",
  "aud": "edge-node",
  "exp": 1705392000,
  "iat": 1705305600,
  "node_id": "edge_123456abcdef1234",
  "project_id": "project-uuid",
  "capabilities": ["camera_processing", "telemetry"]
}
```

#### Token Validation
- Validate `node_id` matches registered node
- Check `project_id` permissions
- Verify token hasn't expired
- Validate capabilities for requested operations

### 3. Database Schema

#### `edge_nodes` table
```sql
CREATE TABLE edge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id VARCHAR(50) UNIQUE NOT NULL,
    node_name VARCHAR(255) NOT NULL,
    project_id UUID REFERENCES projects(id),
    platform VARCHAR(50),
    architecture VARCHAR(50),
    hostname VARCHAR(255),
    mac_address VARCHAR(17),
    installation_id UUID,
    version VARCHAR(20),
    status VARCHAR(20) DEFAULT 'registered',
    auth_token_hash VARCHAR(255),
    capabilities JSONB DEFAULT '[]',
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_edge_nodes_node_id ON edge_nodes(node_id);
CREATE INDEX idx_edge_nodes_project_id ON edge_nodes(project_id);
CREATE INDEX idx_edge_nodes_status ON edge_nodes(status);
```

#### `edge_node_sessions` table
```sql
CREATE TABLE edge_node_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id VARCHAR(50) REFERENCES edge_nodes(node_id),
    session_start TIMESTAMP DEFAULT NOW(),
    session_end TIMESTAMP,
    sync_status VARCHAR(20) DEFAULT 'pending',
    offline_duration INTERVAL,
    devices_count INTEGER DEFAULT 0,
    telemetry_points INTEGER DEFAULT 0
);
```

## 🎨 Frontend (FE) Implementation Requirements

### 1. Edge Node Registration Page

#### Route: `/edge/register`

**Query Parameters:**
- `node_id` (optional): Pre-fill node ID if provided
- `device` (optional): Device type context

**UI Components:**

1. **Node Registration Form**
   ```jsx
   <NodeRegistrationForm>
     <Input 
       label="Node ID" 
       value={nodeId}
       placeholder="edge_123456abcdef1234"
       required
     />
     <Select 
       label="Project"
       options={userProjects}
       required
     />
     <Input 
       label="Node Name"
       placeholder="hostname-edge-abcdef12"
       required
     />
     <Button type="submit">Register Node</Button>
   </NodeRegistrationForm>
   ```

2. **Registration Success**
   ```jsx
   <RegistrationSuccess>
     <Alert type="success">
       Node registered successfully!
     </Alert>
     <CopyableField 
       label="Authentication Token"
       value={authToken}
       sensitive={true}
     />
     <Instructions>
       Copy this token and enter it in your CLI
     </Instructions>
   </RegistrationSuccess>
   ```

### 2. Edge Nodes Management Dashboard

#### Route: `/project/{id}/edge/nodes`

**Features:**
- List all edge nodes in project
- Show online/offline status
- Display last seen timestamps
- Show registered devices count
- Sync status indicators

**UI Components:**

```jsx
<EdgeNodesTable>
  <TableHeader>
    <Column>Node ID</Column>
    <Column>Name</Column>
    <Column>Status</Column>
    <Column>Last Seen</Column>
    <Column>Devices</Column>
    <Column>Actions</Column>
  </TableHeader>
  <TableBody>
    {nodes.map(node => (
      <TableRow key={node.node_id}>
        <Cell>{node.node_id}</Cell>
        <Cell>{node.node_name}</Cell>
        <Cell>
          <StatusBadge status={node.status} />
        </Cell>
        <Cell>
          <RelativeTime timestamp={node.last_seen} />
        </Cell>
        <Cell>{node.registered_devices}</Cell>
        <Cell>
          <NodeActions node={node} />
        </Cell>
      </TableRow>
    ))}
  </TableBody>
</EdgeNodesTable>
```

### 3. Node Detail View

#### Route: `/project/{id}/edge/nodes/{node_id}`

**Information Displayed:**
- Node identity and system info
- Registration history
- Connected devices/sensors
- Telemetry data
- Sync status and offline periods

### 4. Registration Wizard

For guided setup when users first install CLI:

```jsx
<RegistrationWizard>
  <Step title="CLI Installation">
    <InstallationInstructions />
  </Step>
  <Step title="Node Information">
    <NodeInfoForm />
  </Step>
  <Step title="Project Assignment">
    <ProjectSelector />
  </Step>
  <Step title="Complete Setup">
    <TokenGeneration />
    <CLIInstructions />
  </Step>
</RegistrationWizard>
```

## 🌐 Environment Configuration

The system uses the Cyberwave SDK's environment profiles for consistent configuration across all components.

### Supported Environments

| Environment | Backend URL | Frontend URL |
|-------------|-------------|--------------|
| **local** | `http://localhost:8000/api/v1` | `http://localhost:3000` |
| **dev** | `https://api-dev.cyberwave.com/api/v1` | `https://app-dev.cyberwave.com` |
| **qa** | `https://api-qa.cyberwave.com/api/v1` | `https://app-qa.cyberwave.com` |
| **staging** | `https://api-staging.cyberwave.com/api/v1` | `https://app-staging.cyberwave.com` |
| **prod** | `https://api.cyberwave.com/api/v1` | `https://app.cyberwave.com` |

### Environment Selection Priority

1. **Direct CLI argument**: `--backend-url` or `--environment`
2. **Environment variable**: `CYBERWAVE_ENVIRONMENT` or `CYBERWAVE_BACKEND_URL`
3. **CLI configuration**: `~/.cyberwave/config.toml`
4. **Default**: `local` (for development)

### CLI Environment Management

```bash
# Show current environment
cyberwave edge environment

# Switch environment
cyberwave edge environment local
cyberwave edge environment prod

# Set via environment variable
export CYBERWAVE_ENVIRONMENT=dev
cyberwave edge node-info
```

## 🔧 API Integration Points

### CLI → Backend Communication

1. **Health Check Endpoint**
   ```
   GET {backend_url}/health
   Response: {"status": "healthy", "version": "1.0.0"}
   ```

2. **Node Registration**
   ```
   POST {backend_url}/api/v1/edge/nodes
   Headers: Authorization: Bearer <admin-token>
   ```

3. **Node Authentication**
   ```
   POST {backend_url}/api/v1/auth/edge-token
   Body: {"node_id": "edge_123...", "secret": "registration-secret"}
   ```

4. **Device Registration**
   ```
   POST {backend_url}/api/v1/edge/nodes/{node_id}/devices
   Headers: Authorization: Bearer <node-token>
   ```

5. **Telemetry Upload**
   ```
   POST {backend_url}/api/v1/edge/nodes/{node_id}/telemetry
   Headers: Authorization: Bearer <node-token>
   ```

### Frontend → Backend Communication

1. **Node Management**
   ```
   GET /api/v1/projects/{id}/edge/nodes
   POST /api/v1/edge/nodes
   PUT /api/v1/edge/nodes/{node_id}
   DELETE /api/v1/edge/nodes/{node_id}
   ```

2. **Token Generation**
   ```
   POST /api/v1/edge/nodes/{node_id}/generate-token
   ```

## 📋 Testing Requirements

### Backend Tests

1. **Node Registration Flow**
   - Valid node registration
   - Duplicate node ID handling
   - Invalid data validation
   - Project permissions

2. **Authentication**
   - Token generation and validation
   - Expired token handling
   - Invalid node ID rejection
   - Capability-based authorization

3. **Sync Operations**
   - Offline data ingestion
   - Conflict resolution
   - Large data handling
   - Network failure resilience

### Frontend Tests

1. **Registration UI**
   - Form validation
   - Token display and copy
   - Error handling
   - Mobile responsiveness

2. **Node Management**
   - Table sorting and filtering
   - Real-time status updates
   - Bulk operations
   - Export functionality

### Integration Tests

1. **Complete Registration Flow**
   - CLI generates node ID
   - Frontend registers node
   - CLI receives token
   - Backend validates token

2. **Offline/Online Transitions**
   - Offline operation
   - Sync on reconnection
   - Data consistency
   - Error recovery

## 🚀 Deployment Considerations

### Backend Deployment

1. **Database Migrations**
   - Add edge node tables
   - Index creation
   - Data migration (if needed)

2. **Environment Variables**
   ```env
   EDGE_NODE_TOKEN_SECRET=your-secret-key
   EDGE_NODE_TOKEN_EXPIRY=30d
   EDGE_SYNC_BATCH_SIZE=1000
   ```

3. **API Versioning**
   - Use `/api/v1/edge/*` for all edge endpoints
   - Maintain backward compatibility
   - Version headers for future updates

### Frontend Deployment

1. **Route Configuration**
   - Add edge management routes
   - Protect with authentication
   - Mobile-responsive design

2. **Feature Flags**
   ```javascript
   const EDGE_FEATURES = {
     nodeRegistration: true,
     bulkOperations: false,
     advancedTelemetry: true
   };
   ```

## 📊 Monitoring & Analytics

### Metrics to Track

1. **Registration Metrics**
   - New node registrations per day
   - Registration success/failure rates
   - Time to complete registration

2. **Connectivity Metrics**
   - Online/offline node distribution
   - Average offline duration
   - Sync success rates

3. **Usage Metrics**
   - Active nodes per project
   - Device registrations per node
   - Telemetry volume

### Alerts

1. **Node Health**
   - Node offline > 24 hours
   - Failed sync attempts
   - Authentication failures

2. **System Health**
   - High registration failure rates
   - Sync service degradation
   - Token generation issues

## 🔒 Security Considerations

1. **Token Security**
   - Use strong JWT secrets
   - Implement token rotation
   - Secure token transmission

2. **Node Validation**
   - Validate node identity claims
   - Prevent node ID spoofing
   - Rate limit registration attempts

3. **Data Protection**
   - Encrypt offline data
   - Secure sync transmission
   - Audit trail for changes

## 📝 Documentation Requirements

1. **API Documentation**
   - OpenAPI/Swagger specs
   - Authentication examples
   - Error code reference

2. **Integration Guide**
   - Step-by-step setup
   - Troubleshooting guide
   - Best practices

3. **User Documentation**
   - Registration walkthrough
   - Dashboard user guide
   - CLI command reference

This specification provides the complete implementation requirements for supporting the Edge Node connectivity and identity system across the backend and frontend components.
