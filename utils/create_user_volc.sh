#!/bin/bash

# 1. Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root."
    exit 1
fi

# 2. Check number of arguments (now only username and SSH_KEY are required)
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <username> <ssh_public_key>"
    exit 1
fi

# Assign arguments
USERNAME=$1
SSH_PUB_KEY=$2

# Define fixed group information
GROUPNAME="rekcod"
TARGET_GID=2000

# 3. Ensure group with GID 2000 exists
if ! getent group "$TARGET_GID" > /dev/null; then
    echo "Creating group $GROUPNAME with GID $TARGET_GID..."
    groupadd -g "$TARGET_GID" "$GROUPNAME"
else
    # If GID exists but name is different, get the actual group name
    EXISTING_GROUP=$(getent group "$TARGET_GID" | cut -d: -f1)
    echo "Group with GID $TARGET_GID already exists (Name: $EXISTING_GROUP)."
    GROUPNAME=$EXISTING_GROUP
fi

# 4. Create user
# -m: create home directory, -g: specify initial group, -s: specify shell
if id "$USERNAME" >/dev/null 2>&1; then
    echo "Error: User $USERNAME already exists."
    exit 1
fi

echo "Creating user $USERNAME and assigning to group $GROUPNAME (GID $TARGET_GID)..."
useradd -m -g "$TARGET_GID" -s /bin/bash "$USERNAME"

# Lock/disable password login (SSH only)
usermod -p "*" "$USERNAME"

# 5. Setup SSH keys
echo "Setting up SSH keys for $USERNAME..."
USER_HOME="/home/$USERNAME"
mkdir -p "$USER_HOME/.ssh"
chmod 700 "$USER_HOME/.ssh"

echo "$SSH_PUB_KEY" > "$USER_HOME/.ssh/authorized_keys"
chmod 600 "$USER_HOME/.ssh/authorized_keys"

# 6. Recursively change ownership
chown -R "$USERNAME":"$TARGET_GID" "$USER_HOME/.ssh"

echo "------------------------------------------------------"
echo "Success: User '$USERNAME' created."
echo "Group: $GROUPNAME (GID: $TARGET_GID)"
echo "SSH Key has been configured."
echo "------------------------------------------------------"
