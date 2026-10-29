"""
ESM1.6 conversion driver

Defines a ModelDriver class for running the conversion on ESM1.6 history
directories
"""

import collections
import datetime
import errno
import os
import re
import warnings


import f90nml

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from um2nc.common import DelayedCubePath
from um2nc.drivers.common import ModelDriver, find_matching_files
from um2nc.um2netcdf import process


@dataclass
class Stream:
    pattern: re.Pattern
    ppid: str
    files: list

class Am3Driver(ModelDriver):

    def __init__(self, input_directories, output_directory, one_nc_per_stash_variable=True):
            super().__init__(one_nc_per_stash_variable)
            self._output_directory = output_directory
            self._input_directory = [Path(dir) for dir in input_directories]

    @property
    def output_directory(self):
        self._output_directory.mkdir(exist_ok=True)
        return self._output_directory

    @cached_property
    def input_directories(self):
        for dir in self._input_dirs:
            if not dir.exists():
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), dir
                )
        return self._input_directories

    @cached_property
    def atmoscntl(self):
        atmoscntl_nml = f90nml.read(self.input_dirs[0] / "ATMOSCNTL")
        return atmoscntl_nml

    @cached_property
    def stashc(self):
        stashc_nml = f90nml.read(self.input_dirs[0] / "STASHC")
        return stashc_nml

    @cached_property
    def input_name_patterns(self):
        # regex pattern for matching input files
        um_patterns = [stream_namelist["filename_base"] for stream_namelist in self.atmoscntl["nlstcall_pp"]]
        return [base_to_regex(um_pattern) for um_pattern in um_patterns]


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
        input_groups = []
        for stream_namelist in self.atmoscntl["nlstcall_pp"]: # TODO: What if only one namelist stream? What if none?
            um_pattern = stream_namelist["filename_base"]
            filename_regex = base_to_regex(um_pattern)
            stream_um_files = [find_matching_files(dir, filename_regex) for dir in self.input_dirs]
            input_groups.append(stream_um_files)

        # Check that files haven't been matched multiple times
        all_files = sum(input_groups, [])
        duplicate_inputs = [item for item, count in collections.Counter(all_files).items() if count > 1]
        if duplicate_inputs:
            raise RuntimeError(
                "The following input files were grouped into more than one file stream: ",
                f"{duplicate_inputs}. Concatenation of UM files cannot proceed."
            )

        if len(input_groups) == 0:
            input_paths = [dir.resolve for dir in self.input_dirs]
            warnings.warn(
                f"No input files were found in directories found in {input_paths}. "
                f"No files will be converted to netCDF."
            )

        # TODO: What of files with reinit that don't match the output cycle frequency?

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


def base_to_regex(filename_base):
    month_names_3char = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    season_names_3char = r"(jfm|fma|mam|amj|mjj|jja|jas|aso|son|ond|ndj|djf)"
    special_char = r"[yvxluzpwtr]"

    replacements = {
        r"%Y" : r"\\d{4,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L364
        r"%m": r"\\d{2,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L395
        r"%d": r"\\d{2,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L427
        r"%H": r"\\d{2,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L475
        r"%M": r"\\d{2,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L506
        r"%S": r"\\d{2,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L521
        r"%AY": r"\\d{4,}",
        r"%Am": r"\\d{2,}",
        r"%Ad": r"\\d{2,}",
        r"%AH": r"\\d{2,}",
        r"%AM": r"\\d{2,}",
        r"%AS": r"\\d{2,}",
        r"%Q\d*Y": r"\\d+", # TODO: Could insert the correct padding
        r"%Q\d*m": r"\\d+",
        r"%Q\d*d": r"\\d+",
        r"%Q\d*H": r"\\d+",
        r"%Q\d*M": r"\\d+",
        r"%Q\d*S": r"\\d+",
        r"%R\d*Y": r"\\d+",
        r"%R\d*m": r"\\d+",
        r"%R\d*d": r"\\d+",
        r"%R\d*H": r"\\d+",
        r"%R\d*M": r"\\d+",
        r"%R\d*S": r"\\d+",
        r"%b": month_names_3char, # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L582
        r"%s": season_names_3char, # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L596
        r"%T": r"\\d{4,}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L556
        r"%\+": r"[\+\-]",
        r"%%": r"%",
        r"%N": r"[a-z0-9][0-9][0-9]", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L599
        r"%n": r"[a-z0-9][0-9][0-9][0-9]", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L623
        r"%z": r"[za]",
        r"%C": rf"(\d{8}_\d{2}|\d{4}{month_names_3char}|\d{8}|m\d{4}{month_names_3char}|s\d{4}{season_names_3char}|{special_char}\d{8}|\d{9}_\d{2})", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L682
    }
    filename = Path(filename_base).name
    filename_regex = filename
    for um_pattern, python_pattern in replacements.items():
        filename_regex = re.sub(um_pattern, python_pattern, filename_regex)

    return filename_regex


def get_freq_from_namelists(cube, )

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
