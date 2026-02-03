# -*- coding: utf-8 -*-

import glob
import os
import subprocess
import time
from multiprocessing import pool


def get_datetime() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def write_log(log, content: str) -> None:
    log.write(content)
    print(content, end='')


def bash(cmd: str, log, std_out) -> int:
    write_log(log, '{}; bash$ {}\n\n'.format(get_datetime(), cmd))
    process = subprocess.Popen(['/bin/bash', '-c', cmd], stdout=std_out)
    s_time = time.perf_counter()
    code = process.wait()
    write_log(log, '{}; END with code {}, cost {} s\n\n'.format(get_datetime(), code, time.perf_counter() - s_time))
    return code


def free_surfer(nii) -> None:
    print('sub: {}\tstart freesurfer process'.format(nii[0]))
    if not os.path.exists('temp/{}'.format(nii[2])):
        os.makedirs('temp/{}'.format(nii[2]))
    log = open(os.path.join(nii[0], 'events.log'), 'a')
    std_out = open(os.path.join(nii[0], 'freesurfer.log'), 'a')
    code = bash('cp {}/{} temp/{}/mri.nii.gz'.format(nii[0], nii[1], nii[2]), log, std_out)
    # code += bash('mri_convert -c -oc 0 0 0 temp/{0}/mri.nii.gz temp/{0}/mri_convert.nii.gz'.format(nii[2]), log, std_out)
    code += bash('cp temp/{0}/mri.nii.gz temp/{0}/mri_convert.nii.gz'.format(nii[2]), log, std_out)
    code += bash('recon-all -i temp/{0}/mri_convert.nii.gz -s freesurfer -sd temp/{0} -all'.format(nii[2]), log, std_out)
    if code == 0:
        bash('mv temp/{}/freesurfer {}/'.format(nii[2], nii[0]), log, std_out)
        bash('cp temp/{}/mri_convert.nii.gz {}/'.format(nii[2], nii[0]), log, std_out)
        print('sub: {}\tDONE'.format(nii[0]))
    else:
        print('sub: {}\tERROR'.format(nii[0]))
    log.close()
    std_out.close()


if __name__ == '__main__':
    nii_list = [[s, n, s.encode('unicode_escape').decode('utf-8').replace('\\u', '')]
                for s, n in [nii.split('/') for nii in glob.glob('**/MR*.nii*', recursive=True)]]
    if not os.path.exists('temp'):
        os.makedirs('temp')

    thread_pool = pool.ThreadPool()
    for n in nii_list:
        thread_pool.apply_async(free_surfer, (n,))
    thread_pool.close()
    thread_pool.join()
