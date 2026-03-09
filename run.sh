#!/bin/bash
BLENDER_PATH="/home/savant/Downloads/blender-5.0.1-linux-x64/blender"

# Check if the Blender executable exists
if [ ! -f "$BLENDER_PATH" ]; then
    echo "Error: Blender executable not found at $BLENDER_PATH"
    exit 1
fi

$BLENDER_PATH --background main.blend --python main.py