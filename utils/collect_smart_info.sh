#!/bin/sh

# Get the current date in YYMMDD format
DATE=$(date +%y%m%d)

# Define the base output directory for SMART info
BASE_OUTPUT_DIR="/mnt/zpool/dataset0/lapluis/smart"

# Create a subdirectory with the current date
OUTPUT_DIR="$BASE_OUTPUT_DIR/$DATE"

# Create the directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Get the list of disks from sysctl
disks=$(sysctl -n kern.disks)

# Loop through each disk and collect SMART information
for disk in $disks; do
    # Default device name for smartctl
    smartctl_device="/dev/$disk"
    
    # Check if it's an NVMe device (BSD typically uses 'nvd' for NVMe devices)
    if echo "$disk" | grep -q "^nvd"; then
        # Convert the 'nvd' device name to 'nvme' (e.g., nvd0 -> nvme0)
        smartctl_device="/dev/nvme${disk#nvd}"
        # Skip SMART support check for NVMe devices and directly collect SMART info
        echo "Collecting SMART information for $smartctl_device (NVMe)..."
        smartctl -x "$smartctl_device" > "$OUTPUT_DIR/${disk}-smart-info.txt"
        continue
    fi
    
    # For non-NVMe devices, check if the disk supports SMART
    if smartctl -i "$smartctl_device" | grep -q "SMART[[:space:]]*support[[:space:]]*is:[[:space:]]*Enabled"; then
        echo "Collecting SMART information for $smartctl_device..."
        smartctl -x "$smartctl_device" > "$OUTPUT_DIR/${disk}-smart-info.txt"
    else
        echo "Skipping $smartctl_device: SMART support not enabled or available"
    fi
done

# use xz to compress the folder
XZ_OPT=-9 tar -cSJf "$OUTPUT_DIR.tar.xz" -C "$BASE_OUTPUT_DIR" "$DATE"
# Remove the uncompressed directory after compression
rm -rf "$OUTPUT_DIR"

echo "SMART information collection complete. Files stored in '$OUTPUT_DIR.tar.xz'."
