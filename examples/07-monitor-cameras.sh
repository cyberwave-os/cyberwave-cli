#!/bin/bash

# Example 7: Monitor Camera Activity
# Shows how to monitor your cameras and view analytics

echo "📊 Monitor Cameras Example"
echo "=========================="

# Show current status
echo "Current edge node status:"
cyberwave edge status --detailed

echo ""
echo "📈 Viewing analytics..."
cyberwave edge analytics summary

echo ""
echo "💡 Other monitoring commands:"
echo "   cyberwave edge monitor --live     # Live monitoring"
echo "   cyberwave edge logs --follow      # View logs"
echo "   cyberwave edge export --json      # Export data"
