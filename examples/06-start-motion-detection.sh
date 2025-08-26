#!/bin/bash

# Example 6: Start Motion Detection
# Shows how to start an edge node with motion detection

echo "🤖 Motion Detection Example"
echo "==========================="

# Start edge node with computer vision
echo "Starting edge node with motion detection..."
cyberwave edge run --background

# Wait a moment for startup
sleep 3

# Check if it's running
echo "Checking status..."
cyberwave edge status

echo ""
echo "✅ Motion detection is now running!"
echo ""
echo "💡 Monitor activity:"
echo "   cyberwave edge monitor"
echo ""
echo "💡 Stop when done:"
echo "   cyberwave edge stop"
