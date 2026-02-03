# -*- coding: utf-8 -*-

import argparse
import os

import mne.bem

# Set FreeSurfer environment variables for NeuroDesk
os.environ['FREESURFER_HOME'] = '/opt/freesurfer-7.4.1'


def main():
    parser = argparse.ArgumentParser(description='Create watershed BEM surfaces')
    parser.add_argument('subject', type=str, help='Subject name')
    parser.add_argument('subjects_dir', type=str, help='Subjects directory')
    parser.add_argument('-f', '--overwrite', action='store_true', help='Overwrite existing files')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    args = parser.parse_args()

    # Create watershed BEM surfaces
    mne.bem.make_watershed_bem(subject=args.subject, subjects_dir=args.subjects_dir,
                               overwrite=args.overwrite, verbose=args.verbose)


if __name__ == '__main__':
    main()
