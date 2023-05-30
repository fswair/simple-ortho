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

import cProfile
import logging
import multiprocessing
import os
import pathlib
import pstats
import sys
import tracemalloc

import cv2
import numpy as np
import rasterio as rio
from rasterio.warp import reproject, Resampling, transform
from rasterio.windows import Window

from simple_ortho import get_logger

# from scipy.ndimage import map_coordinates

logger = get_logger(__name__)


class Camera:
    def __init__(self, focal_len, sensor_size, im_size, geo_transform, position, orientation, dtype='float32'):
        """
        Camera class to project from 2D camera (i,j) pixel co-ordinates to 3D world (x,y,z) co-ordinates,
        and vice-versa

        Parameters
        ----------
        focal_len :     float
                        focal length in mm
        sensor_size :   numpy.array_like
                        sensor (ccd) [width, height] in mm
        im_size :       numpy.array_like
                        image [width, height]] in pixels
        geo_transform :     numpy.array_like
                            gdal or rasterio 6 element image transform (only the pixel scale is used)
        position :      numpy.array_like
                        column vector of [x=easting, y=northing, z=altitude] camera location co-ordinates, in image CRS
        orientation :   numpy.array_like
                        camera orientation [omega, phi, kappa] angles in radians
        dtype :         numpy.dtype, Type
                        Data type to use for camera parameters (to avoid e.g. unproject forcing float32 to 64)
        """

        self._dtype = dtype
        self.update_extrinsic(position, orientation)

        if np.size(sensor_size) != 2 or np.size(im_size) != 2:
            raise Exception('len(sensor_size) != 2 or len(image_size) != 2')

        self._sensor_size = np.array(sensor_size)
        self._im_size = np.array(im_size)
        self._focal_len = focal_len

        self.update_intrinsic(geo_transform)
        logger.debug(f'Camera configuration: {dict(focal_len=focal_len, sensor_size=sensor_size, im_size=im_size)}')
        logger.debug(f'Position: {position}')
        logger.debug(f'Orientation: {orientation}')

    def update_extrinsic(self, position, orientation):
        """
        Update camera extrinsic parameters

        Parameters
        ----------
        position :      numpy.array_like
                        list of [x=easting, y=northing, z=altitude] camera location co-ordinates, in image CRS
        orientation :   numpy.array_like
                        camera orientation [omega, phi, kappa] angles in degrees
        """
        if np.size(position) != 3 or np.size(orientation) != 3:
            raise Exception('len(position) != 3 or len(orientation) != 3')
        self._T = np.array(position, dtype=self._dtype).reshape(3, 1)

        self._omega, self._phi, self._kappa = orientation

        # PATB convention
        # See https://support.pix4d.com/hc/en-us/articles/202559089-How-are-the-Internal-and-External-Camera-Parameters-defined
        omega_r = np.array([[1, 0, 0],
                            [0, np.cos(self._omega), -np.sin(self._omega)],
                            [0, np.sin(self._omega), np.cos(self._omega)]])

        phi_r = np.array([[np.cos(self._phi), 0, np.sin(self._phi)],
                          [0, 1, 0],
                          [-np.sin(self._phi), 0, np.cos(self._phi)]])

        kappa_r = np.array([[np.cos(self._kappa), -np.sin(self._kappa), 0],
                            [np.sin(self._kappa), np.cos(self._kappa), 0],
                            [0, 0, 1]])

        self._R = np.dot(np.dot(omega_r, phi_r), kappa_r).astype(self._dtype)
        # self._R = np.dot(np.dot(kappa_r, phi_r), omega_r).astype(self._dtype)
        # self._R = cv2.Rodrigues(np.array(orientation))[0].astype(self._dtype).T
        self._Rtv = cv2.Rodrigues(self._R.T)[0]
        return

    def update_intrinsic(self, geo_transform=None, kappa=None):
        """
        Update camera intrinsic parameters

        Parameters
        ----------
        geo_transform : (optional) numpy.array_like
                        gdal or rasterio 6 element image transform
        kappa :         float
                        (optional) kappa angle in degrees - if not specified kappa from last call of update_extrinsic()
                        is used
        """

        # Adapted from https://support.pix4d.com/hc/en-us/articles/202559089-How-are-the-Internal-and-External-Camera-Parameters-defined
        # and https://en.wikipedia.org/wiki/Camera_resectioning
        if geo_transform and np.size(geo_transform) < 6:
            raise Exception('len(geo_transform) < 6')

        if kappa is None:
            kappa = self._kappa

        # if False:
        #     # TODO: can this be removed in favour of the general option below?
        #     # image signed dimensions for orientation (origin and kappa)
        #     image_size_s = -np.sign(np.cos(kappa)) * np.float64(
        #         [np.sign(geo_transform[0]) * self._im_size[0], np.sign(geo_transform[4]) * self._im_size[1]])
        # else:
        #     # TODO: understand this sign convention and check its generality
        #     # TODO: does the source having a geotransform affect the signing below e.g. affect the orientation of read array
        #     image_size_s = np.float64([self._im_size[0], self._im_size[1]])  # NGI
        #     # image_size_s = np.float64([self._im_size[0], self._im_size[1]])     # ODM

        sigma_xy = self._focal_len * self._im_size / self._sensor_size  # xy focal lengths in pixels

        # Intrinsic matrix to convert from camera co-ords in PATB convention (x->right, y->up, z->backwards,
        # looking through the camera at the scene) to pixel co-ords (x->right, y->down convention).
        self._K = np.array([[-sigma_xy[0], 0, self._im_size[0] / 2],
                            [0, sigma_xy[1], self._im_size[1] / 2],
                            [0, 0, 1]], dtype=self._dtype)

        # Kc = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=self._dtype) # ODM
        # Kc = np.array([[1, 0, 0], [0, -1, 0], [0, 0, 1]], dtype=self._dtype) # NGI

        # self._K = np.dot(Kc, self._K)
        # self._K = np.dot(self._K, Kc)
        # NGI
        # self._K = np.array([[sigma_xy[0], 0, self._im_size[0] / 2],
        #                     [0, sigma_xy[1], self._im_size[1] / 2],
        #                     [0, 0, 1]], dtype=self._dtype)
        return

    def unproject(self, x, use_cv=False):
        """
        Unproject from 3D world co-ordinates to 2D image co-ordinates

        Parameters
        ----------
        x : numpy.array_like
            3-by-N array of 3D world (x=easting, y=northing, z=altitude) co-ordinates to unproject.
            (x,y,z) along the first dimension.
        use_cv : bool (optional)
                 False = use the numpy implementation (faster - recommended)
                 True = use the opencv implementation (faster - recommended)

        Returns
        -------
        ij : numpy.array_like
             2-by-N array of image (i=row, j=column) co-ordinates. (i, j) along the first dimension.
        """
        # x,y,z down 1st dimension
        if not (x.shape[0] == 3 and x.shape[1] > 0):
            raise Exception('x must have 3 rows and more than one column')

        if use_cv:  # use opencv
            ij, _ = cv2.projectPoints(x - self._T, self._Rtv, np.array([0., 0., 0.], dtype=self._dtype), self._K,
                                      distCoeffs=None)
            ij = np.squeeze(ij).T
        else:
            # reshape/transpose to xyz along 1st dimension, and broadcast rotation and translation for each xyz vector
            x_ = np.dot(self._R.T, (x - self._T))
            # homogenise xyz/z and apply intrinsic matrix, then discard the 3rd dimension
            ij = (np.dot(self._K, x_ / x_[2, :]))[:2, :]

        return ij

    def project_to_z(self, ij, z):
        """
        Project from 2D image co-ordinates to 3D world co-ordinates at a specified Z

        Parameters
        ----------
        ij : numpy.array_like
             2-by-N array of image (i=row, j=column) co-ordinates. (i, j) along the first dimension.
        z :  numpy.array_like
             1-by-N array of Z (altitude) values to project to

        Returns
        -------
        x : numpy.array_like
            3-by-N array of 3D world (x=easting, y=northing, z=altitude) co-ordinates.
            (x,y,z) along the first dimension.
        """
        if not (ij.shape[0] == 2 and ij.shape[1] > 0):
            raise Exception('not(ij.shape[0] == 2 and ij.shape[1] > 0)')

        ij_ = np.row_stack([ij, np.ones((1, ij.shape[1]))])
        # TODO: store inverse rather than recalculate each time
        x_ = np.dot(np.linalg.inv(self._K), ij_)
        # rotate first (camera to world) so that we have world aligned axes with origin on the camera
        x_r = np.dot(self._R, x_)
        # scale to desired z (offset for camera z) with origin on camera, then offset to world
        x = (x_r * (z - self._T[2]) / x_r[2, :]) + self._T
        return x


class OrthoIm:
    # maximum number of ortho bounds iterations
    ortho_bound_max_iters = 10
    # stop iterating ortho bounds when the difference in min(DEM) estimates goes below this value (m)
    ortho_bound_stop_crit = 1
    default_config = dict(
        crs=None, dem_interp='cubic_spline', dem_band=1, interp='bilinear', resolution=[0.5, 0.5], compress='deflate',
        tile_size=[512, 512], interleave='band', photometric=None, nodata=0, per_band=False, driver='GTiff', dtype=None,
        build_ovw=True, overwrite=True, write_mask=False
    )

    def __init__(self, src_im_filename, dem_filename, camera, config=None, ortho_im_filename=None):
        """
        Class to orthorectify image with known DEM and camera model

        Parameters
        ----------
        src_im_filename :   str, pathlib.Path
                            Filename of source image to orthorectified
        dem_filename :      str, pathlib.Path
                            Filename of DEM covering source image
        camera :            simple_ortho.Camera
                            camera object relevant to source image
        ortho_im_filename : str, pathlib.Path
                            (optional) specify the filename of the orthorectified image to create.  If not specified,
                            appends '_ORTHO' to the src_im_filename
        config :            dict
                            (optional) dictionary of configuration parameters.  If None, sensible defaults are used.
                            Key, value pairs as follows:
                                dem_interp: Interpolation type for resampling DEM (average, bilinear, cubic,
                                    cubic_spline, gauss, lanczos)
                                dem_band: 1-based index of band in DEM raster to use
                                interp: Interpolation type for generating ortho-image (nearest, average, bilinear,
                                    cubic, lanczos)
                                resolution: Output pixel size [x, y] in m
                                compress: GeoTIFF compress type (deflate, jpeg, jpeg2000, lzw, zstd, none)
                                interleave: Interleave by 'pixel' or 'band' (pixel, band)
                                photometric: Photometric interpretation, see https://gdal.org/drivers/raster/gtiff.html
                                    for options (None = same format as source image, recommended)
                                tile_size: Tile/block [x, y] size in pixels  ([512, 512] recommended)
                                nodata: NODATA value
                                per_band: Remap the source raster to the ortho per-band (True), or all bands at once
                                    (False - recommended)
                                driver: Format of ortho raster - see www.gdal.org/formats_list.html (None = same format
                                    as source image)
                                dtype: Data type of ortho raster (e.g. uint8, uint16, float32 etc)  (None = same type as
                                    source image)
                                build_ovw: Build internal overviews
                                overwrite: Overwrite ortho raster if it exists
                                write_mask: True = write an internal mask band, can help remove jpeg noise in nodata
                                    area (False - recommended)
        """
        if not os.path.exists(src_im_filename):
            raise Exception(f"Source image file {src_im_filename} does not exist")

        if not os.path.exists(dem_filename):
            raise Exception(f"DEM file {dem_filename} does not exist")

        self._src_im_filename = pathlib.Path(src_im_filename)
        self._dem_filename = pathlib.Path(dem_filename)

        self._camera = camera

        if ortho_im_filename is None:
            self._ortho_im_filename = self._src_im_filename.parent.joinpath(self._src_im_filename.stem + '_ORTHO' +
                                                                            self._src_im_filename.suffix)
        else:
            self._ortho_im_filename = pathlib.Path(ortho_im_filename)

        # allow for partial or no config
        _config = self.default_config.copy()
        if config is not None:
            _config.update(**config)
        self._parse_config(_config)

        self._check_rasters()

        logger.debug(f'Ortho configuration: {config}')
        logger.debug(f'DEM: {self._dem_filename.parts[-1]}')

    def _check_rasters(self):
        """
        Check that the source image is not 12 bit and that DEM and source image overlap
        """
        with rio.Env(GDAL_NUM_THREADS='ALL_CPUs'):
            with rio.open(self._src_im_filename, 'r') as src_im:
                try:
                    # check that we can read the source image
                    tmp_array = src_im.read(1, window=Window(0, 0, 1, 1))
                except Exception as ex:
                    if src_im.profile['compress'] == 'jpeg':  # assume it is a 12bit JPEG
                        raise Exception(f'Could not read {self._src_im_filename.stem}\n'
                                        f'    JPEG compression with NBITS==12 is not supported by conda GDAL (and others), \n'
                                        f'    you probably need to recompress this file.\n'
                                        f'    See the README for details.')
                    else:
                        raise ex

    def _parse_config(self, config):
        """
        Parse dict config items where necessary

        Parameters
        ----------
        config :  dict
                  e.g. dict(dem_interp='cubic_spline', ortho_interp='bilinear', resolution=[0.5, 0.5],
                          compress='deflate', tile_size=[512, 512])
        """

        for key, value in config.items():
            setattr(self, key, value)

        with rio.Env(GDAL_NUM_THREADS='ALL_CPUs'), rio.open(self._src_im_filename, 'r') as src_im:
            src_crs = src_im.crs

        if not src_crs and not self.crs:
            raise ValueError(f'"crs" configuration value must be specified when the source image has no projection.')
        try:
            # derive crs from configuration string if specified, otherwise use source image crs
            self.crs = rio.CRS.from_string(self.crs) if self.crs else src_crs
        except:
            raise ValueError(f'Unsupported "crs" configuration value: {self.crs}.')
        if (src_crs and self.crs) and (src_crs != self.crs):
            raise ValueError(f'"crs" configuration value ({self.crs}), and source image CRS ({src_crs}) are different.')

        try:
            self.dem_interp = Resampling[self.dem_interp]
        except:
            raise ValueError(f'Unsupported "dem_interp" configuration type: {self.dem_interp}.')

        cv_interp_dict = dict(average=cv2.INTER_AREA, bilinear=cv2.INTER_LINEAR, cubic=cv2.INTER_CUBIC,
                              lanczos=cv2.INTER_LANCZOS4, nearest=cv2.INTER_NEAREST)

        if self.interp not in cv_interp_dict:
            raise Exception(f'Unsupported "interp" configuration type: {self.interp}.')
        else:
            self.interp = cv_interp_dict[self.interp]

        if self._ortho_im_filename.exists():
            if self.overwrite:
                logger.warning(f'Deleting existing ortho file: {self._ortho_im_filename.stem}.')
                os.remove(self._ortho_im_filename)
            else:
                raise FileExistsError(f'Ortho file {self._ortho_im_filename.stem} exists, skipping.')

    def _get_ortho_bounds(self):
        """
        Get the bounds of the output ortho image in its CRS

        Returns
        -------
        ortho_bounds: numpy.array_like
                  [left, bottom, right, top]
        """
        with rio.Env(GDAL_NUM_THREADS='ALL_CPUs'):
            with rio.open(self._src_im_filename, 'r') as src_im, rio.open(self._dem_filename, 'r') as dem_im:
                # iteratively reduce ortho bounds until the encompassed DEM minimum stabilises
                dem_min = 0
                dem_array = dem_array_win = None
                dem_win = Window(0, 0, dem_im.width, dem_im.height)
                for i in range(1, self.ortho_bound_max_iters):
                    # find the ortho bounds in DEM crs by projecting 2D image pixel corners onto 3D z plane = dem_min
                    ortho_cnrs = self._camera.project_to_z(
                        np.array([[0, 0], [src_im.width, 0], [src_im.width, src_im.height], [0, src_im.height]]).T,
                        dem_min)[:2, :]
                    # TODO: rather warp/transform the bounds themselves in case of CRS distortions?
                    ortho_dem_cnrs = np.array(transform(self.crs, dem_im.crs, ortho_cnrs[0, :], ortho_cnrs[1, :]))
                    ortho_dem_bounds = [*ortho_dem_cnrs.min(axis=1), *ortho_dem_cnrs.max(axis=1)]
                    ortho_dem_win = dem_im.window(*ortho_dem_bounds)
                    bounded_sub_win = ortho_dem_win.intersection(dem_win)

                    if bounded_sub_win.width * bounded_sub_win.height <= 0:
                        raise ValueError(
                            f'Ortho {self._ortho_im_filename.name} lies outside DEM {self._dem_filename.name}'
                        )

                    if dem_array is None:
                        # read the maximum extent (dem_min=0) from file once, using masking to exclude nodata from the
                        # the call to .min() below
                        # TODO: test boundless with ortho bounds outside of dem bounds
                        # TODO: implement boundless read internally for speed
                        dem_array = dem_im.read(self.dem_band, window=bounded_sub_win, masked=True)
                        dem_array_win = bounded_sub_win

                    # find the minimum DEM value inside ortho_dem_bounds
                    dem_array_slices = Window(
                        bounded_sub_win.col_off - dem_array_win.col_off,
                        bounded_sub_win.row_off - dem_array_win.row_off,
                        bounded_sub_win.width, bounded_sub_win.height
                    ).toslices()
                    dem_min_prev = dem_min
                    dem_min = np.max([dem_array[dem_array_slices].min(), 0])

                    # exit on convergence
                    if np.abs(dem_min_prev - dem_min) <= self.ortho_bound_stop_crit:
                        break

                if (bounded_sub_win.width * bounded_sub_win.height < ortho_dem_win.width * ortho_dem_win.height):
                    logger.warning(
                        f'Ortho {self._ortho_im_filename.name} not fully covered by DEM {self._dem_filename.name}'
                    )

                if i >= self.ortho_bound_max_iters - 1:
                    logger.warning(f'Ortho {self._ortho_im_filename.name} bounds iteration did not converge')

        return [*ortho_cnrs.min(axis=1), *ortho_cnrs.max(axis=1)]


    def _remap_src_to_ortho(self, ortho_profile, dem_array):
        """
        Interpolate the ortho image from the source image.
            self.per_band = True: Read/write one band at a time - memory efficient
            self.per_band = False: Read/write all bands at once - processor efficient (recommended)

        Parameters
        ----------
        ortho_profile : dict
                        rasterio profile for ortho image
        dem_array     : numpy.array
                        array of altitude values on corresponding to ortho image i.e. on the same grid
        """

        # initialise tile grid here once off (save cpu) - to offset later (requires N-up geotransform)
        j_range = np.arange(0, self.tile_size[0], dtype='float32')
        i_range = np.arange(0, self.tile_size[1], dtype='float32')
        jgrid, igrid = np.meshgrid(j_range, i_range, indexing='xy')
        xgrid, ygrid = ortho_profile['transform'] * [jgrid, igrid]

        block_count = 0
        with rio.open(self._src_im_filename, 'r') as src_im:
            with rio.open(self._ortho_im_filename, 'w', **ortho_profile) as ortho_im:
                if self.per_band:
                    bands = np.array([range(1, src_im.count + 1)]).T  # RW one row of bands i.e. one band at a time
                else:
                    bands = np.array([range(1, src_im.count + 1)])  # RW one row of bands i.e. all bands at once

                ttl_blocks = np.ceil(ortho_profile['width'] / ortho_profile['blockxsize']) * np.ceil(
                    ortho_profile['height'] / ortho_profile['blockysize']) * bands.shape[0]

                # TODO: if we want to allow for src nodata, then read masks here, and set src_im_array[mask] = nan below

                for bi in bands.tolist():
                    # read source image band(s)
                    src_im_array = src_im.read(bi)

                    for ji, ortho_win in ortho_im.block_windows(1):

                        # offset tile grids to ortho_win
                        ortho_win_transform = rio.windows.transform(ortho_win, ortho_im.transform)
                        ortho_xgrid = xgrid[:ortho_win.height, :ortho_win.width] + (
                                ortho_win_transform.xoff - ortho_im.transform.xoff)
                        ortho_ygrid = ygrid[:ortho_win.height, :ortho_win.width] + (
                                ortho_win_transform.yoff - ortho_im.transform.yoff)

                        # extract ortho_win from dem_array, will be nan outside dem bounds or in dem nodata
                        ortho_zgrid = dem_array[ortho_win.row_off:(ortho_win.row_off + ortho_win.height),
                                                ortho_win.col_off:(ortho_win.col_off + ortho_win.width)]

                        # find the 2D source image pixel co-ords corresponding to ortho image 3D co-ords
                        src_ji = self._camera.unproject(np.array([ortho_xgrid.reshape(-1, ), ortho_ygrid.reshape(-1, ),
                                                                  ortho_zgrid.reshape(-1, )]))
                        src_jj = src_ji[0, :].reshape(ortho_win.height, ortho_win.width)
                        src_ii = src_ji[1, :].reshape(ortho_win.height, ortho_win.width)

                        # Interpolate the ortho tile from the source image based on warped/unprojected grids.
                        ortho_im_win_array = np.full(
                            (src_im_array.shape[0], ortho_win.height, ortho_win.width), fill_value=self.nodata,
                            dtype=ortho_im.dtypes[0]
                        )
                        # TODO: check how this works with source nodata.  generally the source image will be rectangular
                        #  with full coverage, but this may not always be the case.  maybe filling src nodata with nan
                        #  would work
                        for oi in range(0, src_im_array.shape[0]):  # for per_band=True, this will loop once only
                            # ortho pixels outside dem bounds or in dem nodata, will be set to borderValue=self.nodata
                            ortho_im_win_array[oi, :, :] = cv2.remap(src_im_array[oi, :, :], src_jj, src_ii,
                                                                     self.interp, borderMode=cv2.BORDER_CONSTANT,
                                                                     borderValue=self.nodata)
                            # below is the scipy equivalent to cv2.remap.  it is ~3x slower but doesn't blur with nodata
                            # ortho_im_win_array[oi, :, :] = map_coordinates(src_im_array[oi, :, :], (src_ii, src_jj),
                            #                                                order=2, mode='constant', cval=self.nodata,
                            #                                                prefilter=False)
                        # remove blurring with nodata at the boundary where necessary
                        nodata_mask = (ortho_im_win_array[0, :, :] == self.nodata)
                        if (self.interp != 'nearest') and (np.sum(nodata_mask) > np.min(self.tile_size)):
                            nodata_mask_d = cv2.dilate(nodata_mask.astype(np.uint8, copy=False),
                                                       np.ones((3, 3), np.uint8))
                            ortho_im_win_array[:, nodata_mask_d.astype(bool, copy=False)] = self.nodata
                        else:
                            nodata_mask_d = nodata_mask

                        # write out the ortho tile to disk
                        ortho_im.write(ortho_im_win_array, bi, window=ortho_win)

                        if self.write_mask and np.all(bi == bands[0]):  # write mask once for all bands
                            with np.testing.suppress_warnings() as sup:
                                sup.filter(DeprecationWarning, "")  # suppress the np.bool warning as it is buggy
                                ortho_im.write_mask(np.bitwise_not(255 * nodata_mask_d).astype(np.uint8, copy=False),
                                                    window=ortho_win)

                        # print progress
                        block_count += 1
                        progress = (block_count / ttl_blocks)
                        sys.stdout.write('\r')
                        sys.stdout.write("[%-50s] %d%%" % ('=' * int(50 * progress), 100 * progress))
                        sys.stdout.flush()

            sys.stdout.write('\n')

    def build_ortho_overviews(self):
        """
        Builds internal overviews for an existing ortho-image
        """

        if self.build_ovw and self._ortho_im_filename.exists():  # build internal overviews
            with rio.Env(GDAL_NUM_THREADS='ALL_CPUs'):
                with rio.open(self._ortho_im_filename, 'r+', num_threads='all_cpus') as ortho_im:
                    ortho_im.build_overviews([2, 4, 8, 16, 32], Resampling.average)

    def orthorectify(self):
        """
        Orthorectify the source image based on specified camera model and DEM.
        """

        # init profiling
        if logger.level == logging.DEBUG:
            tracemalloc.start()
            proc_profile = cProfile.Profile()
            proc_profile.enable()

        with rio.Env(GDAL_NUM_THREADS='ALL_CPUs', GDAL_TIFF_INTERNAL_MASK=True):
            # set up ortho profile based on source profile and predicted bounds
            with rio.open(self._src_im_filename, 'r') as src_im:
                ortho_profile = src_im.profile

            ortho_bounds = self._get_ortho_bounds()  # find extreme case (z=min(DEM)) image bounds
            ortho_dims = np.array(ortho_bounds[2:]) - np.array(ortho_bounds[:2]) # width, height in meters
            ortho_wh = np.int32(np.ceil(ortho_dims / self.resolution))  # width, height in pixels

            ortho_transform = rio.transform.from_origin(
                ortho_bounds[0], ortho_bounds[3], self.resolution[0], self.resolution[1]
            )
            ortho_profile.update(
                crs=self.crs, nodata=self.nodata, tiled=True, blockxsize=self.tile_size[0],
                blockysize=self.tile_size[1], transform=ortho_transform, width=ortho_wh[0], height=ortho_wh[1],
                num_threads='all_cpus'
            )

            # overwrite source attributes in ortho_profile where config is not None
            attrs_to_check = ['driver', 'dtype', 'compress', 'interleave', 'photometric']
            for attr in attrs_to_check:
                val = getattr(self, attr)
                if val is not None:
                    ortho_profile[attr] = val

            # work around an apparent gdal issue with writing masks, building overviews and non-jpeg compression
            if self.write_mask and ortho_profile['compress'] != 'jpeg':
                self.write_mask = False
                logger.warning('Setting write_mask=False, write_mask=True should only be used with compress=jpeg')

            # reproject and resample DEM to ortho bounds, CRS and grid
            with rio.open(self._dem_filename, 'r') as dem_im:
                # TODO: avoid reading dem_im twice (here and in _get_ortho_bounds)
                # TODO: also, for orthorectifying multiple images with the same dem, it would make sense to read it once
                #  for the batch, so perhaps a separate dem class
                # TODO: read only the relevant sub-window from the dem, dems may be high res / large memory - does
                #  passing rio.band(dem_im) do this automatically?
                # TODO: generalise the dtype, to allow float64 internal computation - see https://github.com/leftfield-geospatial/simple-ortho/issues/3
                # Reproject dem to ortho crs and grid. Use nan for dem nodata internally, so that portions of the
                # ortho in dem nodata, or outside the dem, will be set to self.nodata in self._remap_src_to_ortho.
                dem_array = np.full((ortho_wh[1], ortho_wh[0]), fill_value=np.float32('nan'), dtype='float32')
                reproject(rio.band(dem_im, self.dem_band), dem_array, dst_transform=ortho_transform,
                          dst_crs=ortho_profile['crs'], resampling=self.dem_interp, src_transform=dem_im.transform,
                          src_crs=dem_im.crs, num_threads=multiprocessing.cpu_count(), dst_nodata=np.float32('nan'),
                          init_dest_nodata=True)

            self._remap_src_to_ortho(ortho_profile, dem_array)

        if logger.level == logging.DEBUG:  # print profiling info
            proc_profile.disable()
            # tottime is the total time spent in the function alone. cumtime is the total time spent in the function
            # plus all functions that this function called
            proc_stats = pstats.Stats(proc_profile).sort_stats('cumtime')
            logger.debug(f'Processing time:')
            proc_stats.print_stats(20)

            current, peak = tracemalloc.get_traced_memory()
            logger.debug(f"Memory usage: current: {current / 10 ** 6:.1f} MB, peak: {peak / 10 ** 6:.1f} MB")

##
