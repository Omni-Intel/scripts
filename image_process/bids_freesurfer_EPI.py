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


def load_mri(bids_path: str):
    sub_list = glob.glob('*.hdr', root_dir=bids_path)
    return sub_list


def prepare_files(fs_dir: str, pick_elements):
    mkdir(fs_dir)
    work_list = []
    for element in pick_elements:
        tgt_name = element.split('.')[0]
        ext_name = element.split('.')[1]
        work_list.append((fs_dir, tgt_name, ext_name))
    return work_list


def fs_worker(idx: int, total: int, work_cont):
    log_file = os.path.join(work_cont[0], '{}.log'.format(work_cont[1]))
    print(log := 'Start worker {:02d}/{:02d} at {}'.format(idx, total, get_datetime()))
    write_log(log, log_file)
    write_log('', log_file)
    worker_start_time = time.process_time_ns()

    # res_fs = subprocess.run(
    #     args=['/bin/bash', '-c', 'recon-all -i {} -T2 {} -T2pial -s {} -sd {} -all'.format(os.path.join(work_cont[0], work_cont[1] + '.nii.gz'),
    #                                                                                        os.path.join(work_cont[0], work_cont[2] + '.nii.gz'),
    #                                                                                        'freesurfer', work_cont[0])],
    #     capture_output=True
    # )
    # print(['/bin/bash', '-c', 'recon-all -i {} -s {} -sd {} -all'.format(os.path.join(work_cont[0], work_cont[1] + '.nii'),
                                                                            # 'freesurfer', work_cont[0])])
    res_fs = subprocess.run(
        args=['/bin/bash', '-c', 'recon-all -i {} -s {} -sd {} -all'.format(work_cont[1] + '.' + work_cont[2],
                                                                            work_cont[1], work_cont[0])],
        capture_output=True
    )
    print(log := 'Finish recon-all at {}, cost {:.4f} s'.format(get_datetime(),
                                                                (time.process_time_ns() - worker_start_time) / 1e9))
    write_log(log, log_file)
    write_log('', log_file)
    return res_fs.returncode
    # return -1


if __name__ == '__main__':
    picks = load_mri('/mnt/dataset0/lapluis/workspace/freesurfer')
    fs_work_list = prepare_files('/mnt/dataset0/lapluis/workspace/freesurfer/EPI_odzysk', picks)
    for i in range(len(fs_work_list)):
        print(f'{i}\t{fs_work_list[i]}')
    # exit(0)
    with mp.Pool(processes=8) as pool:
        results = pool.starmap_async(fs_worker,
                                     [(i, len(fs_work_list), fs_work_list[i]) for i in range(len(fs_work_list))])
        results.wait()
    print(results.get())
