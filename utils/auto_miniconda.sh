#!/bin/bash

# install miniconda
echo "Installing miniconda..."
mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm ~/miniconda3/miniconda.sh
echo "Miniconda installed."

echo "Configuring conda and pip..."
echo "Write:"
echo "\
channels:
  - defaults
  - conda-forge
show_channel_urls: true
default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2
custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
  pytorch: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
" | tee ~/.condarc
echo "to ~/.condarc"

echo "Write:"
mkdir -p ~/.config/pip
echo "\
[global]
index-url = https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
" | tee ~/.config/pip/pip.conf
echo "to ~/.config/pip/pip.conf"

echo "Add conda initialization to shell"
source ~/miniconda3/bin/activate
conda init --all
conda config --set auto_activate false
