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
import cv2

class CameraType(Enum):
    """
    Enumeration for the camera model type.
    """
    pinhole = 'pinhole'
    """ Pinhole camera model. """

    brown = 'brown'
    """ 
    Brown-Conrady camera model.  Compatible with `ODM <https://docs.opendronemap.org/arguments/camera-lens/`_ / 
    `OpenSFM 
    <https://github.com/mapillary/OpenSfM>`_ 'brown' parameter estimates; and the 4 & 5-coefficient version of the 
    `general OpenCV distortion model <https://docs.opencv.org/4.7.0/d9/d0c/group__calib3d.html>`_.  
    """

    fisheye = 'fisheye'
    """ 
    Fisheye camera model.  Compatible with `ODM <https://docs.opendronemap.org/arguments/camera-lens/`_ / `OpenSFM 
    <https://github.com/mapillary/OpenSfM>`_, and 
    `OpenCV <https://docs.opencv.org/4.7.0/db/d58/group__calib3d__fisheye.html>`_ fisheye parameter estimates.  
    """

    opencv = 'opencv'
    """ 
    OpenCV `general camera model <https://docs.opencv.org/4.7.0/d9/d0c/group__calib3d.html>`_ supporting the full set 
    of distortion coefficient estimates.
    """

class CvInterp(Enum):
    """
    Enumeration for `OpenCV interpolation
    <https://docs.opencv.org/4.7.0/da/d54/group__imgproc__transform.html#ga5bb5a1fea74ea38e1a5445ca803ff121.>`_.
    """
    average = cv2.INTER_AREA
    """ Average of the input pixels over the output pixel area - recommended for downsampling. """
    bilinear = cv2.INTER_LINEAR
    """ Bilnear interpolation. """
    cubic = cv2.INTER_CUBIC
    """ Bicubic interpolation. """
    lanczos = cv2.INTER_LANCZOS4
    """ Lanczos interpolation with an 8x8 neighborhood. """
    nearest = cv2.INTER_NEAREST
    """ Nearest neighbor interpolation. """

