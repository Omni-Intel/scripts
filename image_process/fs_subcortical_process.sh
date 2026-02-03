#!/bin/bash

# check $FREESURFER_HOME is set
if [ -z "$FREESURFER_HOME" ]; then
    echo "Error: FREESURFER_HOME is not set."
    exit 1
fi

# subcortical list
subcortical_list=(Left-Thalamus \
Left-Caudate \
Left-Putamen \
Left-Pallidum \
Left-Hippocampus \
Left-Amygdala \
Left-Accumbens-area \
Right-Thalamus \
Right-Caudate \
Right-Putamen \
Right-Pallidum \
Right-Hippocampus \
Right-Amygdala \
Right-Accumbens-area)

SUBJECTS_DIR='/mnt/dataset0/DATASETS/MNE/MNE-sample-data/subjects'
SUBJECT='fsaverage'
mkdir -p tmp


for subcortical in ${subcortical_list[@]}; do
    echo "processing $subcortical"
    # find cortical ID in FreeSurferColorLUT.txt
    cortical_id=$(cat $FREESURFER_HOME/FreeSurferColorLUT.txt | grep -E "(^|\s)$subcortical(^|\s)" | awk '{print $1}')
    # cortical_id=$(cat $FREESURFER_HOME/FreeSurferColorLUT.txt | grep -E "(^|&|\s)$subcortical([*]|^|&|\s)" | awk '{print $1}')
    if [ -z "$cortical_id" ]; then
        echo "Error: $subcortical not found in FreeSurferColorLUT.txt"
    else
        echo "cortical_id: $cortical_id"
    fi

    # if Left, name is lh.$subcortical in lower case and replace - with _
    subcortical_name=$(echo $subcortical | tr '[:upper:]' '[:lower:]' | sed 's/-/_/g')
    if [[ $subcortical == Left* ]]; then
        subcortical_name=$(echo $subcortical_name | sed 's/left_//g')
        hemi='lh'
    else
        subcortical_name=$(echo $subcortical_name | sed 's/right_//g')
        hemi='rh'
    fi

    echo "subcortical_name: $hemi.$subcortical_name"

    mri_binarize --i $SUBJECTS_DIR/$SUBJECT/mri/aseg.mgz --match $cortical_id --o tmp/$hemi.$subcortical_name.mgz
    mri_tessellate tmp/$hemi.$subcortical_name.mgz 1 tmp/$hemi.$subcortical_name
    mris_convert tmp/$hemi.$subcortical_name $SUBJECTS_DIR/$SUBJECT/surf/$hemi.$subcortical_name
    rm tmp/$hemi.$subcortical_name.mgz tmp/$hemi.$subcortical_name
done

rm -rf tmp
