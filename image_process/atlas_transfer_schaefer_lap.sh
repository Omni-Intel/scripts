## transfer subject into schaefer atlas

export SUBJECTS_DIR=''

schaefer_path='/mnt/dataset0/DATASETS/CBIG/stable_projects/brain_parcellation/Schaefer2018_LocalGlobal/Parcellations/FreeSurfer5.3/fsaverage/label'
data_dir='/mnt/dataset0/DATASETS/OSE_dataset/T1'
sub_spec='sub-*'
parcels=(400 200 100)

for subj_dir in $data_dir/$sub_spec/
do
    subj=$(basename $subj_dir)

    echo "processing $subj"
    if [ "$subj" == "freesurfer" ]; then
        continue
    fi

    for hemi in lh rh
    do
        for parcel in ${parcels[@]}
        do
            mri_surf2surf \
                --srcsubject /mnt/dataset0/DATASETS/OSF/WSGZP/ccepcoreg/derivatives/freesurfer_7.3.2/fsaverage \
                --trgsubject $data_dir/${subj} \
                --src_type white \
                --hemi $hemi \
                --sval-annot $schaefer_path/$hemi.Schaefer2018_${parcel}Parcels_7Networks_order.annot \
                --tval $data_dir/$subj/label/$hemi.Schaefer2018_${parcel}Parcels_7Networks_order.annot
        done
    done

    echo -e "done $subj\n\n"
done
