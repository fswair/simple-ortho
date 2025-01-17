# Simple orthorectification

![banner](./data/outputs/test_example/readme_banner.webp)

Fast and simple orthorectification of images with known DEM and camera model.  Designed and tested on [NGI](http://www.ngi.gov.za/index.php/what-we-do/aerial-photography-and-imagery) aerial imagery.  

## Installation
Using `conda` is the simplest way to resolve `simple-ortho` binary dependencies.  The [Minconda](https://docs.conda.io/en/latest/miniconda.html) installation includes a minimal `conda`.
1) Create a conda environment and install dependencies:
```shell
conda create -n <environment name> python=3.10 -c conda-forge 
conda activate <environment name> 
conda install -c conda-forge rasterio opencv pandas pyyaml shapely
````
2) Clone the git repository and link into the conda environment:
``` shell
git clone https://github.com/leftfield-geospatial/simple-ortho.git
pip install -e simple-ortho
```

### Requirements  
These dependencies are installed in the process above.
  
  - python >= 3.8
  - rasterio >= 1.2
  - opencv >= 4.5
  - pandas >= 1.2
  - pyyaml >= 5.4
  - shapely >= 1.7

## Usage
`simple-ortho` functionality can be accessed from the `conda` command line.

### [simple-ortho](simple_ortho/command_line.py)
Orthorectify image(s). 

`simple-ortho [-h] [-od <ortho_dir>] [-rc <config_path>] [-wc <config_path>] [-v {1,2,3,4}] src_im_file [src_im_file ...] dem_file pos_ori_file`

#### Required arguments
Argument  | Description
----------|--------------
`src_im_file` | One or more path(s) and or wildcard(s) specifying the source unrectified image file(s).
`dem_file` | Path to a DEM, that covers all image(s) specified by `src_im_file`.  
`pos_ori_file` | Path to a text file specifying the camera position and orientation for  all image(s) specified by `src_im_file`.  See [camera position and orientation section](#camera-position-and-orientation) for more detail. 

#### Optional arguments
Argument | Long form | Description
---------|-----------|------------
`-h` | `--help` | Print help and exit.
`-od` `<ortho_dir>` | `--ortho-dir` `<ortho_dir>` | Write orthorectified images to `<ortho_dir>` (default: write to source directory).
`-rc` `<config_path>` | `--read_conf` `<config_path>` | Read a custom configuration from the specified `<config_path>`.  If not specified, defaults are read from [config.yaml](config.yaml).  See [configuration](#configuration-file) for more details.  
`-wc` `<config_path>` | `--write_conf` `<config_path>` | Write current configuration to  `<config_path>` and exit.
`-v` `{1,2,3,4}` | `--verbosity {1,2,3,4}` | Set the logging level (lower means more logging).  1=debug, 2=info, 3=warning, 4=error (default: 2).

#### Examples
Orthorectify a single image with a user provided configuration, writing to a specified folder.
```shell
simple-ortho -v 2 -rc ./data/inputs/test_example/config.yaml -od ./data/outputs/test_example/ ./data/inputs/test_example/3324c_2015_1004_06_0253_RGB.tif ./data/inputs/test_example/dem.tif ./data/inputs/test_example/camera_pos_ori.txt
```
Orthorectify images matching a wildcard, with a user provided configuration, writing to a specified folder.
```shell
simple-ortho -v 2 -rc ./data/inputs/test_example/config.yaml -od ./data/outputs/test_example ./data/inputs/test_example/*_RGB.tif ./data/inputs/test_example/dem.tif ./data/inputs/test_example/camera_pos_ori.txt
```

### Camera position and orientation

Camera position and orientation for an image is specified in a space-separated text file.  The file format is the same as that used by PCI Geomatica's OrthoEngine i.e. each row specifies the camera position and orientation for an image as follows:    
```
<Image file stem> <Easting (m)> <Northing (m)> <Altitude (m)> <Omega (deg)> <Phi (deg)> <Kappa (deg)> 
```
Where `<Image file stem>` is the source file name excluding extension.  

For [`simple-ortho`](#simple-ortho), there should be a row with an `<Image file stem>` corresponding to each image specified by `src_im_file` argument(s).

**Note** that the coordinate reference system (CRS) of camera positions should be a projected, and not geographic CRS.  If the source image(s) aren't projected in this CRS, it should be specified in [``config.yaml``](#configuration-file).  

Example file:
```
...
3323d_2015_1001_01_0001_RGBN 43333.970620 -3709166.407240 5672.686250 0.448258 -0.200394 -0.184258
3323d_2015_1001_01_0002_RGBN 44710.649080 -3709211.341900 5672.299410 -0.168341 0.013147 -0.380978
3323d_2015_1001_01_0003_RGBN 46091.888940 -3709233.718060 5676.132710 -1.493311 -0.004520 -0.158283
...
```

### Camera type

`simple-ortho` implements common lens distortion models, selectable via the camera type.  The *camera* section of the [configuration file](#configuration-file) contains the camera type and distortion parameter specification.  `simple-ortho` distortion models are compatible with [OpenDroneMap (ODM)](https://opendronemap.org/) / [OpenSFM](https://github.com/mapillary/OpenSfM) and [OpenCV](https://opencv.org/) distortion parameter estimates.  ODM writes parameter values to the *&lt;ODM dataset path&gt;/cameras.json* file, and OpenSFM to the *&lt;OpenSFM dataset path&gt;/camera_models.json* file.  Any parameters not specified will default to zero.  The following camera types are supported.

| Type      | Parameters                                                                          | Description
|-----------|-------------------------------------------------------------------------------------|------------
| `pinhole` | None                                                                                | Pinhole camera model with no distortion.
| `brown`   | `k1`, `k2`, `p1`, `p2`, `k3`, `cx`, `cy`                                            | Brown-Conrady lens distortion compatible with ODM / OpenSFM *brown* parameters, and the 4- and 5- element version of the [generic OpenCV distortion model](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html). The OpenCV 4- and 5- element models are special cases of the ODM / OpenSFM brown model with `k3, cx, cy = 0` and `cx, cy = 0` respectively.
| `fisheye` | `k1`, `k2`, `k3`, `k4`                                                              | Fisheye lens distortion compatible ODM / OpenSFM, and [OpenCV](https://docs.opencv.org/4.x/db/d58/group__calib3d__fisheye.html) *fisheye* parameters.  The ODM / OpenSFM model is a special case of the OpenCV version with `k3, k4 = 0`.  
| `opencv`  | `k1`, `k2`, `p1`, `p2`, `k3`, `k4`, `k5`, `k6`, `s1`, `s2`, `s3`, `s4`, `tx`, `τy` | The full [generic OpenCV distortion model](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).  Partial or special cases of the model can be specified by omitting some or all of the parameters; e.g. if no distortion coefficients are specified, this model corresponds to `pinhole`, or if the first 5 distortion coefficients are specified, this model corresponds to `brown` with `cx, cy = 0`.

### Configuration file

Default configuration settings, not passed explicitly on the command line, are read from [config.yaml](config.yaml).  Users can make their own configuration files and pass them to [`simple-ortho`](#simple-ortho) with the `-rc <config_path>` argument.   The configuration file is separated into *camera* and *ortho* sections, with settings for the camera model and orthorectification respectively.  Parameters in each section are described below and commented in [config.yaml](config.yaml).  

| Section  | Parameter       | Description
|----------|-----------------|------------
| `camera` | `name`          | Descriptive name
|          | `type`          | [Camera type](#camera-type) (`pinhole`, `brown`, `fisheye`, `opencv`).
|          | `focal_len`     | Focal length in same units/scale as `sensor_size`.  Can be a single value or `[x, y]` pair.
|          | `sensor_size`   | Optional sensor `[width, height]` in same units/scale as `focal_len`.  If omitted, pixels are assumed square, and`focal_len` should be normalised and unitless:  i.e. `focal_len` = (focal length) / (sensor width).
|          | `im_size`       | Image `[width, height]` dimensions in pixels.
|          | `k1`, `k2`, ... | Optional distortion coefficients for the `brown`, `fisheye` and `opencv`  [camera types](#camera-type).  Values default to zero if not specified.
| `ortho`  | `crs`           | CRS of the camera positions and ortho image as an EPSG, proj4 or WKT string.  It should be a projected, and not geographic CRS.  Can be omitted if the source image has this CRS.
|          | `dem_interp`    | Interpolation method for resampling the DEM (`average`, `bilinear`, `cubic`, `cubic_spline`, `gauss`, `lanczos`).  `cubic_spline` is recommended where the DEM resolution is coarser than the ortho-image resolution.
|          | `dem_band`      | Index of band in DEM raster to use (1-based).
|          | `interp`        | Interpolation method to use for warping source to orthorectified image (`nearest`, `average`, `bilinear`, `cubic`, `lanczos`).  `nearest` is recommended where the ortho-image resolution is close to the source image resolution.
|          | `per_band`      | Remap the source to the ortho-image band-by-band (`True`), or all at once (`False`).  `per_band=False` is generally faster, but requires more memory.   (`True`, `False`).
|          | `build_ovw`     | Build internal overviews (`True`, `False`).
|          | `overwrite`     | Overwrite ortho image(s) if they exist (`True`, `False`).
|          | `write_mask`    | Write an internal mask band - can help remove jpeg noise in nodata area  (`True`, `False`).  (`False` recommended.)
|          | `full_remap`    | Remap source to ortho with full camera model (`True`), or remap undistorted source to ortho with pinhole model (`False`). 
|          | `dtype`         | Data type of ortho image (`uint8`, `uint16`, `float32` or `float64`).  If no `dtype` is specified the same type as the source image will be used (recommended).
|          | `resolution`    | Output pixel size `[x, y]` in m.
|          | `compress`      | Ortho image compression type (`deflate`, `jpeg`, or `auto`). 

## Example Application
Four [NGI](http://www.ngi.gov.za/index.php/what-we-do/aerial-photography-and-imagery) images before and after orthorectification with simple-ortho.  No radiometric (colour) adjustments have been applied, this can be addressed with [`homonim`](https://github.com/leftfield-geospatial/homonim). 

![example](./data/outputs/test_example/readme_eg.webp)

Coarse resolution versions of these images, together with supporting data, are included in the [data/inputs/test_example](data/inputs/test_example) directory.  You can orthorectify this data with the following command line (from the simple-ortho directory):
```shell
simple-ortho -v 2 -rc ./data/inputs/test_example/config.yaml -od ./data/outputs/test_example ./data/inputs/test_example/*RGB.tif ./data/inputs/test_example/dem.tif ./data/inputs/test_example/camera_pos_ori.txt
```

## Known limitations

The `conda` `gdal` package does not support 12bit jpeg compression (the format sometimes used by NGI).  Any tiff compressed in this way would need to be converted using a tool capable of reading these images.  

## License
This project is licensed under the terms of the [Apache-2.0 License](LICENSE).

## Acknowledgements
Special thanks to [National Geo-spatial Information (NGI)](http://www.ngi.gov.za/index.php/what-we-do/aerial-photography-and-imagery) and the [Centre for Geographical Analysis (CGA)](http://www0.sun.ac.za/cga/) for providing imagery, DEM and aero-triangulation data.
