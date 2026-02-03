## transfer subject into schaefer atlas

export SUBJECTS_DIR=""

annot_path="/mnt/dataset0/lapluis/workspace/HCP-MMP/fsaverage"
data_dir="/mnt/dataset4/DATASETS/ECT_fs/ECT"
sub_spec="sub-*"

subjs=$(find "$data_dir" -maxdepth 1 -type d -name "$sub_spec" -exec basename {} \;)
fsaverage="/mnt/dataset0/DATASETS/OSF/WSGZP/ccepcoreg/derivatives/freesurfer_7.3.2/fsaverage"

process_subject() {
    subject=$1
    subjects_dir=$2
    FSAVERAGE=$3
    annot_path=$4

    echo "processing $subject"

    for hemi in lh rh
    do
        mri_surf2surf \
            --srcsubject "$FSAVERAGE" \
            --trgsubject "$subjects_dir/${subject}" \
            --hemi "$hemi" \
            --sval-annot "$annot_path/$hemi.HCP-MMP1.annot" \
            --tval "$subjects_dir/$subject/label/$hemi.HCP-MMP1.annot"
    done

    echo -e "done $subject\n\n"
}

export -f process_subject

parallel -j 16 process_subject ::: "${subjs[@]}" ::: "$data_dir" ::: "$fsaverage" ::: "$annot_path"
