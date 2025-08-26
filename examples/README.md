# Cyberwave CLI Examples

Simple shell script examples to learn how to use the Cyberwave CLI.

## Quick Start

Run the complete workflow in one command:
```bash
./examples/simple-camera-workflow.sh
```

## Step-by-Step Examples

Learn each feature individually:

### 1. Check Dependencies
```bash
./examples/01-check-dependencies.sh
```
Shows what dependencies you need for different devices.

### 2. Install Dependencies  
```bash
./examples/02-install-dependencies.sh
```
Install missing dependencies automatically.

### 3. Discover Cameras
```bash
./examples/03-discover-cameras.sh
```
Find IP cameras on your network.

### 4. Create Environment
```bash
./examples/04-create-environment.sh
```
Set up a camera monitoring environment.

### 5. Register Camera
```bash
./examples/05-register-camera.sh
```
Add a camera to your environment.

### 6. Start Motion Detection
```bash
./examples/06-start-motion-detection.sh
```
Begin monitoring for motion and activity.

### 7. Monitor Activity
```bash
./examples/07-monitor-cameras.sh
```
View analytics and system status.

## Before Running

1. Install the Cyberwave CLI:
   ```bash
   pip install cyberwave-cli
   ```

2. Make scripts executable:
   ```bash
   chmod +x examples/*.sh
   ```

3. Update camera IPs in the scripts to match your network.

## Common Commands

- `cyberwave edge devices` - List available devices
- `cyberwave edge check-deps --device camera/ip` - Check dependencies
- `cyberwave edge camera discover` - Find cameras
- `cyberwave edge status` - Check system status
- `cyberwave edge stop` - Stop monitoring

## Need Help?

- `cyberwave --help` - General help
- `cyberwave edge --help` - Edge commands
- `cyberwave edge camera --help` - Camera commands
