#!/usr/bin/env bash
set -euo pipefail

# Get all regular users (UID >= 1000)
# Added 7th column: login shell
awk -F: '$3 >= 1000 {print $1, $6, $7}' /etc/passwd | while read -r u home shell; do

# 1. Skip users without a home directory
if [ ! -d "$home" ]; then
  echo "Skipping $u: Home directory $home does not exist."
  continue
fi

# 2. Skip users with disabled/unavailable login (nologin or false)
if [[ "$shell" =~ (nologin|false)$ ]]; then
  echo "Skipping $u: User has no login shell ($shell)."
  continue
fi

# Switch to the user and execute cleanup commands
echo ">>> Switching to $u (Shell: $shell)"

# Use sudo -i to ensure environment variables are fully loaded
sudo -iu "$u" bash -l <<EOF
echo "[\$USER] Attempting to source conda..."
for s in "\$HOME/miniconda3/etc/profile.d/conda.sh" "\$HOME/anaconda3/etc/profile.d/conda.sh"; do
  if [ -f "\$s" ]; then
    . "\$s"
    echo "[\$USER] Sourced \$s"
    conda clean --all --yes
    break
  fi
done

echo "[\$USER] Cleaning pip cache..."
rm -rf "\$HOME/.cache/pip"
EOF

done
