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

from enum import Enum
from rasterio.enums import Resampling
import cv2


class CameraType(str, Enum):
    """
    Enumeration for the camera model type.
    """
    pinhole = 'pinhole'
    """ Pinhole camera model. """

    brown = 'brown'
    """ 
    Brown-Conrady camera model.  Compatible with `ODM <https://docs.opendronemap.org/arguments/camera-lens/>`_ / 
    `OpenSFM <https://github.com/mapillary/OpenSfM>`_ *brown* parameter estimates; and the 4 & 5-coefficient version of the 
    `general OpenCV distortion model <https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html>`_.  
    """

    fisheye = 'fisheye'
    """ 
    Fisheye camera model.  Compatible with `ODM <https://docs.opendronemap.org/arguments/camera-lens/>`_ / `OpenSFM 
    <https://github.com/mapillary/OpenSfM>`_, and 
    `OpenCV <https://docs.opencv.org/4.7.0/db/d58/group__calib3d__fisheye.html>`_ *fisheye* parameter estimates.  
    """

    opencv = 'opencv'
    """ 
    OpenCV `general camera model <https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html>`_ supporting the full set 
    of distortion coefficient estimates.
    """


class Interp(str, Enum):
    """
    Enumeration for common `OpenCV <https://docs.opencv.org/4.8.0/da/d54/group__imgproc__transform.html
    #ga5bb5a1fea74ea38e1a5445ca803ff121>`_ and `rasterio
    https://rasterio.readthedocs.io/en/stable/api/rasterio.enums.html#rasterio.enums.Resampling`_ interpolation types.
    """
    average = 'average'
    """ Average input pixels over the corresponding output pixel area (suited to downsampling). """
    bilinear = 'bilinear'
    """ Bilinear interpolation. """
    cubic = 'cubic'
    """ Bicubic interpolation. """
    cubic_spline = 'cubic_spline'
    """ Cubic spline interpolation (not supported by OpenCV). """
    lanczos = 'lanczos'
    """ Lanczos windowed sinc interpolation. """
    nearest = 'nearest'
    """ Nearest neighbor interpolation. """

    def to_cv(self) -> int:
        """ Convert to OpenCV interpolation type. """
        name_to_cv = dict(
            average=cv2.INTER_AREA, bilinear=cv2.INTER_LINEAR, cubic=cv2.INTER_CUBIC, lanczos=cv2.INTER_LANCZOS4,
            nearest=cv2.INTER_NEAREST,
        )
        if self._name_ not in name_to_cv:
            raise ValueError(f'OpenCV does not support `{self._name_}` interpolation')
        return name_to_cv[self._name_]

    def to_rio(self) -> Resampling:
        """ Convert to rasterio resampling type. """
        return Resampling[self._name_]


class Compress(str, Enum):
    """ Enumeration for ortho compression. """
    jpeg = 'jpeg'
    """ Jpeg (lossy) compression.  """
    deflate = 'deflate'
    """ Deflate (lossless) compression. """
    auto = 'auto'
    """ Use jpeg compression if possible, otherwise deflate. """
