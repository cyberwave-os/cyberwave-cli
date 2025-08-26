#!/bin/bash

# Example 1: Check Dependencies
# Simple example showing how to check and install dependencies for different devices

echo "🔍 Checking Dependencies Example"
echo "================================"

# Check what devices are available
echo "📋 Available devices:"
cyberwave edge devices

echo ""
echo "🔍 Checking camera dependencies:"
cyberwave edge check-deps --device camera/ip

echo ""
echo "🔍 Checking robot dependencies:" 
cyberwave edge check-deps --device robot/so-101

echo ""
echo "💡 To install missing dependencies automatically:"
echo "   cyberwave edge install-deps --device camera/ip"
echo "   cyberwave edge install-deps --device robot/so-101"
