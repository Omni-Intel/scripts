# -*- coding: utf-8 -*-

import glob
import multiprocessing as mp
import os

import mne.bem

os.environ['FREESURFER_HOME'] = '/usr/local/freesurfer/7.4.1'

def make_watershed_bem(sub, work_directory, overwrite=False):
    try:
        mne.bem.make_watershed_bem(sub, work_directory, overwrite=overwrite, verbose=False)
        return f'{sub} done'
    except Exception as e:
        print(f'Error in {sub}: {e}')
        return f'Error in {sub}: {e}'

if __name__ == '__main__':
    work_directory = r'/mnt/dataset0/lapluis/workspace/ds002336'
    subs = glob.glob('sub-*', root_dir=work_directory)
    # print([(sub, work_directory) for sub in subs])
    with mp.Pool(processes=8) as pool:
        results = pool.starmap_async(make_watershed_bem, [(sub, work_directory, True) for sub in subs])
        results.wait()
    print(results.get())
