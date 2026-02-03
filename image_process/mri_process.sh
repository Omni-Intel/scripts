#!/bin/bash

mri_process_worker () {
    local sub_id=$1
    eval "$(conda shell.bash hook)"
    conda activate process
    echo "Processing subject $sub_id"
    source /root/FreeSurf_SetUp.sh
    recon-all -i $sub_id.nii.gz -s $sub_id -sd . -all | tee "$sub_id"_recon-all.log
    python3 -c "import mne.bem; import os; os.environ['FREESURFER_HOME'] = '/usr/local/freesurfer/7.4.1'; mne.bem.make_watershed_bem('$sub_id', subjects_dir='.', overwrite=True)" | tee "$sub_id"_make_watershed_bem.log
}

for sub in $(ls sub-*.nii.gz | sed 's/\.nii\.gz//'); do
    mri_process_worker $sub &
done
