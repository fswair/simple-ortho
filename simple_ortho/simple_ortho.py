"""
    Copyright (c) 2021 Dugal Harris - dugalh@gmail.com
    This project is licensed under the terms of the MIT license.
"""

from simple_ortho import get_logger
import numpy as np
import os
import cv2
import rasterio as rio
from rasterio.warp import reproject, Resampling,  transform
import pathlib
import datetime
import multiprocessing
import pandas as pd

logger = get_logger(__name__)

class Camera():
    def __init__(self, focal_len, sensor_size, im_size, geo_transform, position, orientation, dtype='float32'):
        """
        Camera class to project from camera 2D pixel co-ordinates to world 3D (x,y,z) co-ordinates, and vice-versa

        Parameters
        ----------
        focal_len :     float
                        focal length in mm
        sensor_size :   numpy.array_like
                        sensor (ccd) [width, height] in mm
        im_size :       numpy.array_like
                        image [width, height]] in pixels
        geo_transform :     numpy.array_like
                            gdal or rasterio 6 element image transform
        position :      numpy.array_like
                        column vector of [x=easting, y=northing, z=altitude] camera location co-ordinates, in image CRS
        orientation :   numpy.array_like
                        camera orientation [omega, phi, kappa] angles in degrees
        """

        self._dtype = dtype
        self.update_extrinsic(position, orientation)

        if np.size(sensor_size) != 2 or np.size(im_size) != 2:
            raise Exception('len(sensor_size) != 2 or len(image_size) != 2')

        self._sensor_size = sensor_size
        self._im_size = im_size
        self._focal_len = focal_len

        self.update_intrinsic(geo_transform)


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
        self._Rtv = cv2.Rodrigues(self._R.T)[0]
        return

    def update_intrinsic(self, geo_transform, kappa=None):
        """
        Update camera instrinsic parameters

        Parameters
        ----------
        geo_transform : numpy.array_like
                        gdal or rasterio 6 element image transform
        kappa :         float
                        (optional) kappa angle in degrees - if not specified kappa from last call of update_extrinsic()
                        is used
        """

        # Adapted from https://support.pix4d.com/hc/en-us/articles/202559089-How-are-the-Internal-and-External-Camera-Parameters-defined
        # and https://en.wikipedia.org/wiki/Camera_resectioning
        if np.size(geo_transform) < 6:
            raise Exception('len(geo_transform) < 6')

        if kappa is None:
            kappa = self._kappa

        # image signed dimensions for orientation (origin and kappa)
        image_size_s = -np.sign(np.cos(kappa)) * np.float64(
            [np.sign(geo_transform[0]) * self._im_size[0], np.sign(geo_transform[4]) * self._im_size[1]])
        sigma_xy = self._focal_len * image_size_s / self._sensor_size   # x,y signed focal lengths in pixels

        self._K = np.array([[sigma_xy[0], 0, self._im_size[0] / 2],
                            [0, sigma_xy[1], self._im_size[1] / 2],
                            [0, 0, 1]], dtype=self._dtype)
        return

    def unproject(self, X, use_cv=False):
        """
        Unproject from 3D world co-ordinates to 2D image co-ordinates

        Parameters
        ----------
        X : numpy.array_like
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
        if not(X.shape[0] == 3 and X.shape[1] > 0):
            raise Exception('X must have 3 rows and more than one column')

        # TODO: will this be faster as a single matrix mult in homog co-ords?
        if use_cv:  # use opencv
            ij, _ = cv2.projectPoints(X - self._T, self._Rtv, np.array([0., 0., 0.], dtype=self._dtype), self._K, distCoeffs=None)
            ij = np.squeeze(ij).T
        else:
            # reshape/transpose to xyz along 1st dimension, and broadcast rotation and translation for each xyz vector
            X_ = np.dot(self._R.T, (X - self._T))
            # homogenise xyz/z and apply intrinsic matrix, discarding 3rd dimension
            ij = np.dot(self._K, X_/X_[2, :])[:2, :]

        return ij

    def project_to_z(self, ij, Z):
        """
        Project from 2D image co-ordinates to 3D world co-ordinates at a specified Z

        Parameters
        ----------
        ij : numpy.array_like
             2-by-N array of image (i=row, j=column) co-ordinates. (i, j) along the first dimension.
        Z :  numpy.array_like
             1-by-N array of Z (altitude) values to project to

        Returns
        -------
        X : numpy.array_like
            3-by-N array of 3D world (x=easting, y=northing, z=altitude) co-ordinates.
            (x,y,z) along the first dimension.
        """
        if not(ij.shape[0] == 2 and ij.shape[1] > 0):
            raise Exception('not(ij.shape[0] == 2 and ij.shape[1] > 0)')

        ij_ = np.row_stack([ij, np.ones((1, ij.shape[1]))])
        X_ = np.dot(np.linalg.inv(self._K), ij_)
        X_R = np.dot(self._R, X_) # rotate first (camera to world)
        X = (X_R * (Z - self._T[2])/X_R[2, :]) + self._T  # scale to desired Z and offset to world

        return X

class OrthoIm():
    def __init__(self, raw_im_filename, dem_filename, camera, config=None, ortho_im_filename=None, ):
        """
        Class to orthorectify image with known DEM and camera model

        Parameters
        ----------
        raw_im_filename :   str
                            Filename of raw image to orthorectified
        dem_filename :      str
                            Filename of DEM covering raw image
        camera :            simple_orth.Camera
                            camera object relevant to raw image
        ortho_im_filename : str
                            (optional) specify the filename of the orthorectified image to create.  If not specified,
                            appends '_ORTHO' to the raw_im_filename
        config :            dict
                            (optional) dictionary of configuration parameters.  With key, value pairs as follows:
                                'dem_interp':   Interpolation type for resampling DEM (average, bilinear, cubic,
                                                cubic_spline, gauss, lanczos) default = 'cubic_spline'
                                'dem_band':     1-based index of band in DEM raster to use, default=1
                                'ortho_interp': Interpolation type for ortho-image (average, bilinear, cubic, lanczos,
                                                nearest), default = 'bilinear'
                                'resolution':   Output pixel size [x, y] in m, default = [0.5, 0.5]
                                'compression':  GeoTIFF compression type (deflate, jpeg, jpeg2000, lzw, zstd, none),
                                                default = 'deflate'
                                'tile_size':    Tile/block [x, y] size in pixels, default = [512, 512]
        """
        if not os.path.exists(raw_im_filename):
            raise Exception(f"Raw image file {raw_im_filename} does not exist")

        if not os.path.exists(dem_filename):
            raise Exception(f"DEM file {dem_filename} does not exist")

        self._raw_im_filename = pathlib.Path(raw_im_filename)
        self._dem_filename = pathlib.Path(dem_filename)

        self._camera = camera

        if ortho_im_filename is None:
            self._ortho_im_filename = self._raw_im_filename.parent.joinpath(self._raw_im_filename.stem + '_ORTHO.TIF')
        else:
            self._ortho_im_filename = pathlib.Path(ortho_im_filename)

        if config is None: # set defaults:
            config = dict(dem_interp='cubic_spline', ortho_interp='bilinear', resolution=[0.5, 0.5],
                          compression='deflate', tile_size=[512, 512])

        self._parse_config(config)
        self.dem_min = 0.

        # init dict for profiling processor times
        self.time_rec = dict(dem_min=datetime.timedelta(0), raw_im_read=datetime.timedelta(0),
                        grid_creation=datetime.timedelta(0), dem_reproject=datetime.timedelta(0),
                        unproject=datetime.timedelta(0), raw_remap=datetime.timedelta(0), write=datetime.timedelta(0))
        # TODO: some error checking like does DEM cover raw image
        # TODO: deal with case where input is not geotiff

    def _parse_config(self, config):
        """
        Parse dict config items where necessary

        Parameters
        ----------
        config :  dict
                  e.g. dict(dem_interp='cubic_spline', ortho_interp='bilinear', resolution=[0.5, 0.5],
                          compression='deflate', tile_size=[512, 512])
        """

        for key, value in config.items():
            setattr(self, key, value)

        try:
            self.dem_interp = Resampling[config['dem_interp']]
        except :
            logger.error(f'Unknown dem_interp configuration type: {config["dem_interp"]}')
            raise

        cv_interp_dict = dict(average=cv2.INTER_AREA, bilinear=cv2.INTER_LINEAR, cubic=cv2.INTER_CUBIC,
                              lanczos=cv2.INTER_LANCZOS4, nearest=cv2.INTER_NEAREST)

        if self.ortho_interp not in cv_interp_dict:
            raise Exception(f'Unknown ortho_interp configuration type: {config["ortho_interp"]}')
        else:
            self.ortho_interp = cv_interp_dict[self.ortho_interp]

    def _get_dem_min(self):
        """
        Find minimum of the DEM over the bounds of the raw image
        """

        dem_min = 0.
        with rio.Env():
            with rio.open(self._raw_im_filename, 'r') as raw_im:
                with rio.open(self._dem_filename, 'r') as dem_im:
                    # find raw image bounds in DEM CRS
                    [dem_xbounds, dem_ybounds] = transform(raw_im.crs, dem_im.crs,
                                                           [raw_im.bounds.left, raw_im.bounds.right],
                                                           [raw_im.bounds.top, raw_im.bounds.bottom])
                    dem_win = rio.windows.from_bounds(dem_xbounds[0], dem_ybounds[1], dem_xbounds[1], dem_ybounds[0],
                                                      transform=dem_im.transform)

                    # read DEM in raw image ROI and find minimum
                    dem_im_array = dem_im.read(1, window=dem_win)
                    dem_min = np.max([dem_im_array.min(), 0])

        return dem_min

    def _get_ortho_bounds(self, dem_min=0):
        """
        Get the bounds of the output ortho image in its CRS

        Parameters
        ----------
        dem_min : (optional) minimum altitude over the image area in m, default=0

        Returns
        -------
        ortho_bl: numpy.array_like
                  [x, y] co-ordinates of the bottom left corner
        ortho_tr: numpy.array_like
                  [x, y] co-ordinates of the top right corner
        """

        with rio.Env():
            with rio.open(self._raw_im_filename, 'r') as raw_im:
                # find the bounds of the ortho by projecting 2D image pixel corners onto 3D Z plane = dem_min
                ortho_cnrs = self._camera.project_to_z(
                    np.array([[0, 0], [raw_im.width, 0], [raw_im.width, raw_im.height], [0, raw_im.height]]).T,
                    dem_min)[:2, :]

                raw_cnrs = np.array(
                    [[raw_im.bounds.left, raw_im.bounds.bottom], [raw_im.bounds.right, raw_im.bounds.bottom],
                     [raw_im.bounds.right, raw_im.bounds.top], [raw_im.bounds.left, raw_im.bounds.top]]).T

                ortho_cnrs = np.column_stack([ortho_cnrs, raw_cnrs])  # make double sure we encompass the raw image
                ortho_bl = ortho_cnrs.min(axis=1)   # bottom left
                ortho_tr = ortho_cnrs.max(axis=1)   # top right
                # ortho_wh = np.ceil(np.abs((ortho_bl - ortho_tr).squeeze()[:2] / self.resolution)) # ortho im width, height
                # ortho_transform = rio.transform.from_origin(ortho_bl[0], ortho_tr[1], *self.resolution)

        return ortho_bl, ortho_tr

    def _remap_all_bands(self, ortho_profile, dem_array):
        """
        Interpolate the ortho image from the raw image.  Read/write all bands at once - the fastest option for YCbCr
        encoded jpegs.  Needs sufficient memory to hold all raw bands though.

        Parameters
        ----------
        ortho_profile : dict
                        rasterio profile for ortho image
        dem_array     : numpy.array
                        array of altitude values on corresponding to ortho image i.e. on the same grid
        """

        # initialse tile grid here once off (save cpu) - to offset later (requires N-up geotransform)
        j_range = np.arange(0, self.tile_size[0], dtype='float32')
        i_range = np.arange(0, self.tile_size[1], dtype='float32')
        jgrid, igrid = np.meshgrid(j_range, i_range, indexing='xy')
        xgrid, ygrid = ortho_profile['transform'] * [jgrid, igrid]

        with rio.open(self._raw_im_filename, 'r') as raw_im:
            with rio.open(self._ortho_im_filename, 'w', **ortho_profile) as ortho_im:
                bands = list(range(1, raw_im.count + 1))
                start = datetime.datetime.now()
                raw_im_array = raw_im.read(bands)
                self.time_rec['raw_im_read'] += (datetime.datetime.now() - start)

                for ji, ortho_win in ortho_im.block_windows(1):
                    print((ji, ortho_win))

                    # offset tile grids to ortho_win
                    start = datetime.datetime.now()
                    ortho_win_transform = rio.windows.transform(ortho_win, ortho_im.transform)
                    ortho_xgrid = xgrid[:ortho_win.width, :ortho_win.height] + (
                            ortho_win_transform.xoff - ortho_im.transform.xoff)
                    ortho_ygrid = ygrid[:ortho_win.width, :ortho_win.height] + (
                            ortho_win_transform.yoff - ortho_im.transform.yoff)

                    # extract ortho_win from dem_array
                    ortho_zgrid = dem_array[ortho_win.row_off:(ortho_win.row_off + ortho_win.height),
                               ortho_win.col_off:(ortho_win.col_off + ortho_win.width)]
                    self.time_rec['grid_creation'] += (datetime.datetime.now() - start)

                    # find the 2D raw image pixel co-ords corresponding to ortho image 3D co-ords
                    start = datetime.datetime.now()
                    raw_ji = self._camera.unproject(np.array([ortho_xgrid.reshape(-1, ), ortho_ygrid.reshape(-1, ),
                                                             ortho_zgrid.reshape(-1, )]))
                    raw_jj = raw_ji[0, :].reshape(ortho_win.height, ortho_win.width)
                    raw_ii = raw_ji[1, :].reshape(ortho_win.height, ortho_win.width)
                    self.time_rec['unproject'] += (datetime.datetime.now() - start)

                    # Interpolate the ortho tile from the raw image based on warped/unprojected grids
                    start = datetime.datetime.now()
                    ortho_im_win_array = np.zeros((raw_im.count, ortho_win.height, ortho_win.width),
                                                  dtype=raw_im.dtypes[0])
                    for band_i in bands:    # remap by band - for some reason this is faster than doing at all at once
                        ortho_im_win_array[band_i - 1, :, :] = cv2.remap(raw_im_array[band_i - 1, :, :], raw_jj, raw_ii,
                                                                         self.ortho_interp,
                                                                         borderMode=cv2.BORDER_CONSTANT)
                    self.time_rec['raw_remap'] += (datetime.datetime.now() - start)

                    # write out the ortho tile to disk
                    start = datetime.datetime.now()
                    ortho_im.write(ortho_im_win_array, bands, window=ortho_win)
                    self.time_rec['write'] += (datetime.datetime.now() - start)
                    start = datetime.datetime.now()
        self.time_rec['write'] += (datetime.datetime.now() - start)

    def _remap_per_band(self, ortho_profile, dem_array):
        """
        Interpolate the ortho image from the raw image.  Read/write one band at a time.  More memory efficient, but
        generally less processor efficient than _remap_all_bands()

        Parameters
        ----------
        ortho_profile : dict
                        rasterio profile for ortho image
        dem_array     : numpy.array
                        array of altitude values on corresponding to ortho image i.e. on the same grid
        """

        # initialse tile grid here once off (save cpu) - to offset later (requires N-up geotransform)
        j_range = np.arange(0, self.tile_size[0], dtype='float32')
        i_range = np.arange(0, self.tile_size[1], dtype='float32')
        jgrid, igrid = np.meshgrid(j_range, i_range, indexing='xy')
        xgrid, ygrid = ortho_profile['transform'] * [jgrid, igrid]

        with rio.open(self._raw_im_filename, 'r') as raw_im:
            with rio.open(self._ortho_im_filename, 'w', **ortho_profile) as ortho_im:
                bands = list(range(1, raw_im.count + 1))
                for band_i in bands:
                    start = datetime.datetime.now()
                    raw_im_array = raw_im.read(band_i)
                    self.time_rec['raw_im_read'] += (datetime.datetime.now() - start)

                    for ji, ortho_win in ortho_im.block_windows(1):
                        print((ji, ortho_win))

                        # offset tile grids to ortho_win
                        start = datetime.datetime.now()
                        ortho_win_transform = rio.windows.transform(ortho_win, ortho_im.transform)
                        ortho_xgrid = xgrid[:ortho_win.width, :ortho_win.height] + (
                                ortho_win_transform.xoff - ortho_im.transform.xoff)
                        ortho_ygrid = ygrid[:ortho_win.width, :ortho_win.height] + (
                                ortho_win_transform.yoff - ortho_im.transform.yoff)

                        # extract ortho_win from dem_array
                        ortho_zgrid = dem_array[ortho_win.row_off:(ortho_win.row_off + ortho_win.height),
                                   ortho_win.col_off:(ortho_win.col_off + ortho_win.width)]
                        self.time_rec['grid_creation'] += (datetime.datetime.now() - start)

                        # find the 2D raw image pixel co-ords corresponding to ortho image 3D co-ords
                        start = datetime.datetime.now()
                        raw_ji = self._camera.unproject(np.array([ortho_xgrid.reshape(-1, ), ortho_ygrid.reshape(-1, ),
                                                                 ortho_zgrid.reshape(-1, )]))
                        raw_jj = raw_ji[0, :].reshape(ortho_win.height, ortho_win.width)
                        raw_ii = raw_ji[1, :].reshape(ortho_win.height, ortho_win.width)
                        self.time_rec['unproject'] += (datetime.datetime.now() - start)

                        # Interpolate the ortho tile from the raw image based on warped/unprojected grids
                        start = datetime.datetime.now()
                        ortho_im_win_array = cv2.remap(raw_im_array, raw_jj, raw_ii,
                                                                         self.ortho_interp,
                                                                         borderMode=cv2.BORDER_CONSTANT)
                        self.time_rec['raw_remap'] += (datetime.datetime.now() - start)

                        # write out the ortho tile to disk
                        start = datetime.datetime.now()
                        ortho_im.write(ortho_im_win_array, band_i, window=ortho_win)
                        self.time_rec['write'] += (datetime.datetime.now() - start)
                        start = datetime.datetime.now()
        self.time_rec['write'] += (datetime.datetime.now() - start)


    def orthorectify(self, per_band=False):
        """
        Orthorectify the raw image based on specified camera model and DEM.

        Parameters
        ----------
        per_band :  bool
                    True = remap the raw image per-band (memory efficient)
                    False = remap the raw image as a whole (processor efficient)
        """

        # init process time dict
        start_ttl = start = datetime.datetime.now()
        with rio.Env():
            start = datetime.datetime.now()
            dem_min = self._get_dem_min()   # get min of DEM over image area
            self.time_rec['dem_min'] += (datetime.datetime.now() - start)

            # set up ortho profile based on raw profile and predicted bounds
            start = datetime.datetime.now()
            with rio.open(self._raw_im_filename, 'r') as raw_im:
                ortho_profile = raw_im.profile

            ortho_bl, ortho_tr = self._get_ortho_bounds(dem_min=dem_min)    # find extreme case (z=dem_min) image bounds
            ortho_wh = np.int32(np.ceil(np.abs((ortho_bl - ortho_tr).squeeze()[:2] / self.resolution))) # image size

            ortho_transform = rio.transform.from_origin(ortho_bl[0], ortho_tr[1], self.resolution[0], self.resolution[1])
            # TODO: can we specify different output data type?
            ortho_profile.update(nodata=0, compress=self.compression, tiled=True, blockxsize=self.tile_size[0],
                                 blockysize=self.tile_size[1], transform=ortho_transform, width=ortho_wh[0],
                                 height=ortho_wh[1], num_threads='all_cpus', interleave=self.ortho_interleave)

            # initialse tile grid here once off (save cpu) - to offset later
            j_range = np.arange(0, self.tile_size[0], dtype='float32')
            i_range = np.arange(0, self.tile_size[1], dtype='float32')
            jgrid, igrid = np.meshgrid(j_range, i_range, indexing='xy')
            xgrid, ygrid = ortho_transform * [jgrid, igrid]
            self.time_rec['grid_creation'] += (datetime.datetime.now() - start)

            # reproject and resample DEM to ortho bounds, CRS and grid
            start = datetime.datetime.now()
            with rio.open(self._dem_filename, 'r') as dem_im:
                dem_array = np.zeros((ortho_wh[1], ortho_wh[0]), 'float32')
                reproject(rio.band(dem_im, self.dem_band), dem_array, dst_transform=ortho_transform,
                          dst_crs=ortho_profile['crs'], resampling=self.dem_interp, src_transform=dem_im.transform,
                          src_crs=dem_im.crs, num_threads=multiprocessing.cpu_count())
            self.time_rec['dem_reproject'] += (datetime.datetime.now() - start)

            if per_band == False:
                self._remap_all_bands(ortho_profile, dem_array)
            else:
                self._remap_per_band(ortho_profile, dem_array)

        self.time_rec['ttl'] = (datetime.datetime.now() - start_ttl)
        timed = pd.DataFrame.from_dict(self.time_rec, orient='index')
        print(timed.sort_values(by=0))