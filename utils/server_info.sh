#!/bin/bash

echo "========== CPU Information =========="
lscpu | grep -E 'Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|CPU\(s\):'
echo ""

echo "========== GPU Information =========="
if command -v nvidia-smi &> /dev/null; then
    # Format: Index, Name, Total VRAM
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | awk -F', ' '{print "Card ["$1"]: " $2 " | VRAM: " $3}'
else
    echo "NVIDIA driver not found. Attempting PCI identification:"
    lspci | grep -i -E "vga|3d|display"
fi
echo ""

echo "========== Memory Details =========="
# 1. Logical total capacity recognized by OS
free -h | grep "Mem" | awk '{print "Total OS-recognized capacity: " $2}'

# 2. Physical slot details (requires sudo)
if [ "$EUID" -ne 0 ]; then
    echo "[Note] Please run with 'sudo' to view detailed slot and part information"
else
    echo "Physical Memory Slot Distribution:"
    # Use colon as separator and strip leading/trailing whitespace
    sudo dmidecode -t memory | awk -F': ' '
        /^\tLocator:/ {loc=$2} 
        /^\tSize: [0-9]/ {size=$2} 
        /^\tType: / {type=$2} 
        /^\tType Detail: / {typedetail=$2} 
        /^\tSpeed: [0-9]/ {speed=$2} 
        /^\tPart Number: / {part=$2; 
            if(size != "" && size !~ /No Module/) {
                # Formatted output for alignment
                printf "  - Location: %-10s | Size: %-8s | Type: %-4s (%-10s) | Speed: %-10s | Part: %s\n", loc, size, type, typedetail, speed, part
                size="" 
            }
        }'
fi
echo ""

echo "========== Storage Information =========="
lsblk -d -o NAME,MODEL,SIZE,ROTA,TYPE | grep disk
echo "Total Disk Count: $(lsblk -d | grep disk | wc -l)" 
