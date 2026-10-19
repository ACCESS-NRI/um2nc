"""
ESM1.6 conversion driver

Defines a ModelDriver class for running the conversion on ESM1.6 history
directories
"""

import datetime
import errno
import os
import re
import warnings


import f90nml

from functools import cached_property
from pathlib import Path

from um2nc.common import DelayedCubePath
from um2nc.drivers.common import ModelDriver, find_matching_files, get_ff_date
from um2nc.um2netcdf import process


class Am3Driver(ModelDriver):

    def __init__(self, args):
            super().__init__(Path(args.model_directory), args.one_nc_per_stash_variable)
            self._output_dir = Path(args.output_directory)
            self._start_time = args.start_time
            self._end_time = args.end_time

    @property
    def output_dir(self):
        self._output_dir.mkdir(exist_ok=True)
        return self._output_dir

    @cached_property
    def start_time(self):
        return datetime.datetime.strptime(self._start_time, "%Y.%m.%d.%H.%S")

    @cached_property
    def end_time(self):
        return datetime.datetime.strptime(self._end_time, "%Y.%m.%d.%H.%S")

    @cached_property
    def input_name_pattern(self):
        # regex pattern for matching input files
        return re.compile(r"^am3a.p(?P<unit>[a-z]).(?P<year>[0-9]+)-(?P<month>[0-9]+)-(?P<day>[0-9]+)$")

    @property
    def atmoscntl(self):
        pass

    @property
    def stashc(self):
        pass

    def convert(self, input_group, output_path, process_args):
        """
        Convert an individual input fields file to netCDF.

        Parameters:
        -----------
        input_path: Path to input fields file.
        output_path: Path or DelayedCubePath for writing output netCDF.
        process_args: conversion arguments.
        """
        process(input_group, output_path, process_args)

    def get_input_paths(self):
        """
        Find atmosphere fields files for conversion.

        Returns:
        --------
        input_paths: List of paths to UM fields files to be converted.
        """
        # Suggested to find files based on the namelist. Ok so what if we do:
        # 1. Read ATMOSCNTL
        # 2. Get nlstcall_pp namelists
        # 3. Generate a regex based on the filename_base options (hopefully not too fiddly)


        # In theory there should only be one stream per file ID
        #  https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/rose-meta/um-atmos/HEAD/lib/python/macros/output_streams.py#L97-L103
        # For each stream, generate a dict of its STASH entry. 
        # At startup, load in the whole STASHC namelist. So then from stash entry we can get the relevant dict for the tim_name, and domain name should it be needed?
        # I guess that way, perhaps we won't need to 1. read and 2. scour through the namelists over and over again?


        filename_base = '/home/565/sw6175/cylc-run/access-am3-configs/20261009-am3-splitdir-3/work/19820101T0000Z/atmos_main/am3a.pe%C'
        filename_base = 'am3a.pe%C'

        # I guess we replace each "%char" (char can come from a defined subset) with ".*" or whatever?
        filename_regex = regex_from_filename(filename_base)

        # Try to ensure the same file is not picked up by multiple regexs

        um_files = find_matching_files(self.model_directory, self.input_name_pattern)
        print("Found files:")
        print(um_files)
        current_cycle_files = filter_by_datetime(um_files, self.start_time, self.end_time, self.input_name_pattern)
        print("Files after date filtering:")
        print(current_cycle_files)
        input_groups = group_by_stream(current_cycle_files, self.input_name_pattern)

        if len(input_groups) == 0:
            #TODO: More appropriate message
            warnings.warn(
                f"No files matching pattern '{self.input_name_pattern}' "
                f"found in {self.model_directory.resolve()}. No files will be "
                "converted to netCDF."
            )

        return input_groups

    def get_output_path(self, input_group):
        """
        Return the output path for a given input group.

        Parameters:
        -----------
        input_group: tuple containing paths to input files of the same stream.

        Returns:
        --------
        output_path: Path or DelayedCubePath for writing output netCDF.
        """
        if self.one_nc_per_stash_variable:
            return Am3DelayedCubePath(self.output_dir)
        else:
            input_name = input_group[0].name  #TODO: something better here, also this isn't working
            return self.output_dir / f"{input_name}.nc"


def filter_by_datetime(files, start_time, end_time, pattern):
    filtered_files = []
    for file in files:

        year = int(pattern.match(file.name).group("year"))
        month = int(pattern.match(file.name).group("month"))
        day = int(pattern.match(file.name).group("day"))
        file_date = datetime.datetime(year, month, day)

        if start_time <= file_date <= end_time:
            filtered_files.append(file)

    return filtered_files


def group_by_stream(input_paths, pattern):
    """
    Group a list of UM output files by their output streams

    Parameters:
    -----------
    input_paths: List of paths to input files.
    pattern: Regex pattern for matching input paths.

    Returns:
    --------
    groups: List of tuples containing grouped input paths.
    """
    streams = {
        pattern.match(path.name).group("unit") for path in input_paths
    }

    print(streams)

    groups = [
        tuple(path for path in input_paths if pattern.match(path.name).group("unit") == stream)
        for stream in streams
    ]
    print(groups)
    return groups



# def get_freq(cube, file, atmoscntl, stashc):
#     item_code = cube.item_code # may need to recalculate...
#     file_id = get_file_id(file, atmoscntl)
#     use_name = get_use_name(stashc, file_id)
#     time_name = get_time_name(stashc, use_name, item_code)
#     freq = get_time_freq(stashc, time_name)
    

# def get_file_id(file, atmoscntl):
#     pass


class Am3DelayedCubePath(DelayedCubePath):
    # template = "access-am3.um{um_version}.{dimensions}.{field_name}.{freq}{time_cell_method}{datestamp}.nc"

    def __init__(self, output_dir_path):
        self.output_dir_path = output_dir_path

    # TODO: How to best determine the frequency – I'm not sure that we can rely on the streams as we did for ESM1.6
    # Probably need to get from namelist. Or can we get it from the cube? But you could have multiple frequencies in a file
    # Bounds could work for means etc, but not for instantaneous data...
    # Possibly need to get it from the namelist then...
    def _get_freq(self):
        # Determine the freq from the cube
        # TODO
        return "unknown_freq"

    def _get_datestamp(self, cube):
        # Since ESM1.6 output files are yearly, just need YYYY for the datestamp
        # Ensure years with less than 4 digits are formatted with leading zeros
        # TODO: Need some way to set this appropriately for AM3
        fmt = '%4Y'

        # Get the appropriately truncated datetime for the average time
        d_str = cube.coord('time').units.num2date(cube.coord('time').points.mean()).strftime(fmt)
        return f".{d_str}"

    def _build_filename(self, cube):
        components = ["access-am3", "um"+self._get_um_version(cube), self._get_dimensions(cube), self._get_time_cell_methods(cube), self._get_freq(), self._get_datestamp(cube), "nc"]

        non_empty_components = [component for component in components if component is not None]

        return ".".join(non_empty_components)

    def _get_output_dir(self):
        return self.output_dir_path
