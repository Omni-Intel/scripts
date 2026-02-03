#!/bin/bash

# Check if script is run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root."
    exit 1
fi

# Check if correct number of arguments are provided
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <username> <groupname> <ssh_public_key>"
    exit 1
fi

# Arguments
USERNAME=$1
GROUPNAME=$2
SSH_PUB_KEY=$3

# Check if the group exists, if not, create the group
if ! getent group "$GROUPNAME" > /dev/null; then
    echo "Creating group $GROUPNAME..."
    groupadd "$GROUPNAME"
else
    echo "Group $GROUPNAME already exists."
fi

# Retrieve the GID of the group by its name
GID=$(getent group "$GROUPNAME" | cut -d: -f3)

# Create the user with the retrieved GID and add to the group
echo "Creating user $USERNAME with GID $GID and group $GROUPNAME..."
useradd -m -g "$GROUPNAME" -s /bin/bash "$USERNAME"

usermod -p "*" "$USERNAME"

# Set password for the user (optional, can be left blank)
# echo "Please set a password for the new user $USERNAME:"
# passwd "$USERNAME"

# Create .ssh directory and set proper permissions
echo "Setting up SSH keys for $USERNAME..."
mkdir -p /home/"$USERNAME"/.ssh
chmod 700 /home/"$USERNAME"/.ssh

# Add the SSH public key to the authorized_keys file
echo "$SSH_PUB_KEY" > /home/"$USERNAME"/.ssh/authorized_keys
chmod 600 /home/"$USERNAME"/.ssh/authorized_keys

# Change the group ownership of the .ssh directory and authorized_keys to the given group
chown -R "$USERNAME":"$GROUPNAME" /home/"$USERNAME"/.ssh

# Confirm user creation and SSH key addition
echo "User $USERNAME created with group $GROUPNAME (GID $GID) and SSH key added successfully."
