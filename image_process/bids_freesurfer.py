import glob
import multiprocessing as mp
import os
import shutil
import subprocess
import time


def get_datetime() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def write_log(content: str, log_path: str):
    with open(log_path, 'a') as f:
        f.writelines(content)
        f.writelines('\n')


def mkdir(f_path: str) -> None:
    if (pardir := os.path.dirname(f_path)) == f_path:
        return
    else:
        mkdir(pardir)
    if not os.path.isdir(f_path):
        os.mkdir(f_path)


# def load_mri(bids_path: str):
#     sub_list = glob.glob('sub-*', root_dir=bids_path)
#     ses_dir = dict(zip(sub_list,
#                        [sorted(glob.glob('ses-*', root_dir=os.path.join(bids_path, sub))) for sub in sub_list]))
#     pick_elements = []
#     for sub in sub_list:
#         for ses in ses_dir[sub]:
#             if os.path.isdir(anat_folder := os.path.join(bids_path, sub, ses, 'anat')):
#                 anat = glob.glob('*.nii*', root_dir=anat_folder)
#                 pick_elements.extend([(bids_path, sub, ses, 'anat', img) for img in anat])
#     return pick_elements

def load_mri(bids_path: str):
    sub_list = glob.glob('sub-*', root_dir=bids_path)
    pick_elements = []
    for sub in sub_list:
        if os.path.isdir(anat_folder := os.path.join(bids_path, sub, 'anat')):
            anat = glob.glob('*.nii*', root_dir=anat_folder)
            pick_elements.extend([(bids_path, sub, 'anat', img) for img in anat])
    return pick_elements


def prepare_files(fs_dir: str, pick_elements):
    mkdir(fs_dir)
    work_list = []
    for elements in pick_elements:
        src = os.path.join(*elements)
        tgt_name = elements[-1].split('.')[0]
        dst_path = os.path.join(fs_dir, elements[1], elements[2])
        mkdir(dst_path)
        shutil.copyfile(src, '{}.nii.gz'.format(os.path.join(dst_path, tgt_name)))
        work_list.append((dst_path, tgt_name))
    return work_list


def fs_worker(idx: int, total: int, work_cont):
    workdir = work_cont[0]
    src = '{}.nii.gz'.format(work_cont[1])
    tmp = os.path.join(workdir, '{}_convert.nii.gz'.format(work_cont[1]))
    log_file = os.path.join(workdir, '{}.log'.format(work_cont[1]))
    print(log := 'Start worker {:02d}/{:02d} at {}'.format(idx, total, get_datetime()))
    write_log(log, log_file)
    write_log('', log_file)
    worker_start_time = time.process_time_ns()
    # res_convert = subprocess.run(
    #     args=['/bin/bash', '-c', 'mri_convert -c -oc 0 0 0 {} {}'.format(os.path.join(workdir, src), tmp)],
    #     capture_output=True
    # )
    res_convert = subprocess.run(
        args=['/bin/bash', '-c', 'cp {} {}'.format(os.path.join(workdir, src), tmp)],
        capture_output=True
    )
    print(log := 'Finish mri_convert at {}, cost {:.4f} s'.format(get_datetime(),
                                                                  (time.process_time_ns() - worker_start_time) / 1e9))
    write_log('std_out:', log_file)
    write_log(res_convert.stdout.decode('utf-8'), log_file)
    write_log('std_err:', log_file)
    write_log(res_convert.stderr.decode('utf-8'), log_file)
    write_log(log, log_file)
    write_log('', log_file)

    res_fs = subprocess.run(
        args=['/bin/bash', '-c', 'recon-all -i {} -s {} -sd {} -all -threads 4'.format(tmp, work_cont[1], workdir)],
        capture_output=True
    )
    print(log := 'Finish recon-all at {}, cost {:.4f} s'.format(get_datetime(),
                                                                (time.process_time_ns() - worker_start_time) / 1e9))
    write_log('std_out:', log_file)
    write_log(res_fs.stdout.decode('utf-8'), log_file)
    write_log('std_err:', log_file)
    write_log(res_fs.stderr.decode('utf-8'), log_file)
    write_log(log, log_file)
    write_log('', log_file)

    res_segment_HA = subprocess.run(
        args=['/bin/bash', '-c', 'segmentHA_T1.sh {} {}'.format(work_cont[1], workdir)],
        capture_output=True
    )
    print(log := 'Finish segmentHA_T1 at {}, cost {:.4f} s'.format(get_datetime(),
                                                                   (time.process_time_ns() - worker_start_time) / 1e9))
    write_log('std_out:', log_file)
    write_log(res_segment_HA.stdout.decode('utf-8'), log_file)
    write_log('std_err:', log_file)
    write_log(res_segment_HA.stderr.decode('utf-8'), log_file)
    write_log(log, log_file)
    write_log('', log_file)
    return res_convert.returncode or res_fs.returncode or res_segment_HA.returncode


if __name__ == '__main__':
    picks = load_mri('/mnt/dataset0/DATASETS/OpenNEURO/ds002336')
    fs_work_list = prepare_files('/mnt/dataset0/lapluis/workspace/ds002336', picks)
    for i in range(len(fs_work_list)):
        print(f'{i}\t{fs_work_list[i]}')
    # exit(0)
    with mp.Pool(processes=32) as pool:
        results = pool.starmap_async(fs_worker,
                                     [(i, len(fs_work_list), fs_work_list[i]) for i in range(len(fs_work_list))])
        results.wait()
    print(results.get())
