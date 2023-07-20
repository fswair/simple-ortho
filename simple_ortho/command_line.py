"""
   Copyright 2021 Dugal Harris - dugalh@gmail.com

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

import argparse
import datetime
import os
import pathlib
import logging

import numpy as np
import pandas as pd
import rasterio as rio
import yaml

from simple_ortho import root_path
from simple_ortho.camera import CameraType, create_camera
from simple_ortho.ortho import Ortho
from simple_ortho.utils import suppress_no_georef

# print formatting
np.set_printoptions(precision=4)
np.set_printoptions(suppress=True)
logger = logging.getLogger(__name__)


# TODO: remove pandas dependency

def parse_args():
    """ Parse arguments """

    parser = argparse.ArgumentParser(description='Orthorectify an image with known DEM and camera model.')
    parser.add_argument(
        "src_im_file", help="path(s) and or wildcard(s) specifying the source image file(s)", type=str,
        metavar='src_im_file', nargs='+'
    )
    parser.add_argument("dem_file", help="path to the DEM file", type=str)
    parser.add_argument("pos_ori_file", help="path to the camera position and orientation file", type=str)
    parser.add_argument(
        "-od", "--ortho-dir",
        help="write ortho image(s) to this directory (default: write ortho image(s) to source directory)",
        type=str
    )
    parser.add_argument(
        "-rc", "--read-conf", help="read custom config from this path (default: use config.yaml in simple-ortho root)",
        type=str
    )
    parser.add_argument("-wc", "--write-conf", help="write default config to this path and exit", type=str)
    parser.add_argument(
        "-v", "--verbosity", choices=[1, 2, 3, 4],
        help="logging level: 1=DEBUG, 2=INFO, 3=WARNING, 4=ERROR (default: 2)", type=int
    )
    return parser.parse_args()


def _check_args(src_im_file, dem_file, pos_ori_file, ortho_dir=None):
    """ Argument error checking """

    # check files exist
    for src_im_file_spec in src_im_file:
            src_im_file_path = pathlib.Path(src_im_file_spec)
            for src_im_filename in src_im_file_path.parent.glob(src_im_file_path.name):
                srf = str(src_im_filename).split("/")[-1].upper()
                srf = srf.split(".")[0]
                pd_indexes = list(map(lambda a: a.upper(), list(cam_pos_orid.index)))
                if srf not in pd_indexes:
                    raise Exception(f'Could not find {src_im_filename.stem} in {pos_ori_file}')

    if not pathlib.Path(dem_file).exists():
        raise Exception(f'DEM file {dem_file} does not exist')

    if not pathlib.Path(pos_ori_file).exists():
        raise Exception(f'Camera position and orientation file {pos_ori_file} does not exist')

    # check and create ortho_dir if necessary
    if ortho_dir is not None:
        ortho_dir = pathlib.Path(ortho_dir)
        if not ortho_dir.is_dir():
            raise Exception(f'Ortho directory {ortho_dir} is not a valid directory')
        if not ortho_dir.exists():
            logger.warning(f'Creating ortho directory {ortho_dir}')
            os.mkdir(str(ortho_dir))


def main(src_im_file, dem_file, pos_ori_file, ortho_dir=None, read_conf=None, write_conf=None, verbosity=2):
    """
    Orthorectification

    Parameters
    ----------
    src_im_file : str, pathlib.Path
                  Source image file(s)
    dem_file : str, pathlib.Path
               DEM file covering source image file(s)
    pos_ori_file : str, pathlib.Path
                   Position and orientation file for source image file(s)
    ortho_dir : str, pathlib.Path, optional
                Output directory
    read_conf : str, pathlib.Path, optional
                Read configuration from this file
    write_conf : str, pathlib.Path, optional
                Write configuration to this file and exit
    verbosity : int
                Logging verbosity 1=DEBUG, 2=INFO, 3=WARNING, 4=ERROR (default: 2)
    """
    try:
        # set logging level
        if verbosity is not None:
            pkg_logger = logging.getLogger(__package__)
            pkg_logger.setLevel(10 * verbosity)
            logging.captureWarnings(True)

        # read configuration
        if read_conf is None:
            config_filename = root_path.joinpath('config.yaml')
        else:
            config_filename = pathlib.Path(read_conf)

        if not config_filename.exists():
            raise Exception(f'Config file {config_filename} does not exist')

        with open(config_filename, 'r') as f:
            config = yaml.safe_load(f)

        # write configuration if requested and exit
        if write_conf is not None:
            out_config_filename = pathlib.Path(write_conf)
            with open(out_config_filename, 'w') as f:
                yaml.dump(config, stream=f)
            logger.info(f'Wrote config to {out_config_filename}')
            exit(0)

        # prepare ortho config
        ortho_config = config.get('ortho', {})
        ortho_crs = ortho_config.pop('crs', None)
        dem_band = ortho_config.pop('dem_band', Ortho._default_config['dem_band'])
        for key in ['driver', 'tile_size', 'nodata', 'interleave', 'photometric']:
            if key in ortho_config:
                ortho_config.pop(key)
                logger.warning(f'The `{key}` option is deprecated.')

        # prepare camera config
        camera = None
        camera_config = config['camera']
        camera_type = CameraType(camera_config.get('type', 'pinhole'))
        camera_config = {k: v for k, v in camera_config.items() if k not in ['name', 'type']}

        # checks paths etc
        _check_args(src_im_file, dem_file, pos_ori_file, ortho_dir=ortho_dir)

        # read camera position and rotation and find row for src_im_file
        cam_pos_orid = pd.read_csv(
            pos_ori_file, header=None, sep=' ', index_col=0,
            names=['file', 'easting', 'northing', 'altitude', 'omega', 'phi', 'kappa']
        )

        # loop through image file(s) or wildcard(s), or combinations thereof
        for src_im_file_spec in src_im_file:
            src_im_file_path = pathlib.Path(src_im_file_spec)
            for src_filename in src_im_file_path.parent.glob(src_im_file_path.name):
                if src_filename.stem not in cam_pos_orid.index:
                    raise Exception(f'Could not find {src_filename.stem} in {pos_ori_file}')

                im_pos_ori = cam_pos_orid.loc[src_filename.stem]
                rotation = np.array(np.pi * im_pos_ori[['omega', 'phi', 'kappa']] / 180.)
                position = np.array([im_pos_ori['easting'], im_pos_ori['northing'], im_pos_ori['altitude']])

                # set ortho filename
                ortho_dir = src_filename.parent if not ortho_dir else ortho_dir
                ortho_filename = pathlib.Path(ortho_dir).joinpath(src_filename.stem + '_ORTHO.tif')

                # Get src size
                with suppress_no_georef(), rio.open(src_filename) as src_im:
                    im_size = np.float64([src_im.width, src_im.height])

                if not camera or np.any(im_size != camera._im_size):
                    # create a new camera
                    camera = create_camera(camera_type, position, rotation, im_size=im_size, **camera_config)
                else:
                    # update existing camera
                    camera.update_extrinsic(position, rotation)

                # create Ortho  and orthorectify
                logger.info(f'Orthorectifying {src_filename.name}')
                start_ttl = datetime.datetime.now()
                ortho_im = Ortho(src_filename, dem_file, camera, crs=ortho_crs, dem_band=dem_band)
                ortho_im.process(ortho_filename, **ortho_config)
                ttl_time = (datetime.datetime.now() - start_ttl)
                logger.info(f'Completed in {ttl_time.total_seconds():.2f} secs')

    except Exception as ex:
        logger.error('Exception: ' + str(ex))
        raise ex


def main_entry():
    """  Command line entry point """

    args = parse_args()
    args_dict = vars(args)
    src_im_file = args_dict.pop('src_im_file')
    dem_file = args_dict.pop('dem_file')
    pos_ori_file = args_dict.pop('pos_ori_file')

    main(src_im_file, dem_file, pos_ori_file, **args_dict)
