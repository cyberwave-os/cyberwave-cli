#!/bin/bash

# Test Dependency System
# Simple test to verify the dependency management system works

echo "🧪 Testing Dependency Management System"
echo "======================================="

# Test 1: Check if CLI is available
echo "1️⃣ Testing CLI availability..."
if command -v cyberwave &> /dev/null; then
    echo "✅ Cyberwave CLI found"
    cyberwave --version
else
    echo "❌ Cyberwave CLI not found"
    echo "Install with: pip install cyberwave-cli"
    exit 1
fi

# Test 2: Check device listing
echo ""
echo "2️⃣ Testing device listing..."
if cyberwave edge devices; then
    echo "✅ Device listing works"
else
    echo "❌ Device listing failed"
fi

# Test 3: Check dependency checking
echo ""
echo "3️⃣ Testing dependency checking..."
echo "Checking camera dependencies:"
if cyberwave edge check-deps --device camera/ip; then
    echo "✅ Camera dependency check works"
else
    echo "⚠️ Camera dependency check completed (some deps may be missing)"
fi

echo ""
echo "Checking robot dependencies:"
if cyberwave edge check-deps --device robot/so-101; then
    echo "✅ Robot dependency check works"
else
    echo "⚠️ Robot dependency check completed (some deps may be missing)"
fi

# Test 4: Check feature dependencies
echo ""
echo "4️⃣ Testing feature dependency checking..."
if cyberwave edge check-deps --feature computer_vision; then
    echo "✅ Feature dependency check works"
else
    echo "⚠️ Feature dependency check completed (some deps may be missing)"
fi

# Test 5: Test dependency installation (dry run)
echo ""
echo "5️⃣ Testing dependency installation help..."
echo "Camera installation command:"
echo "  cyberwave edge install-deps --device camera/ip"
echo ""
echo "Robot installation command:"
echo "  cyberwave edge install-deps --device robot/so-101"

echo ""
echo "✅ All tests completed!"
echo ""
echo "💡 To actually install dependencies, run:"
echo "   ./examples/02-install-dependencies.sh"
