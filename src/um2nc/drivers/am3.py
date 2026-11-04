"""
AM3 conversion driver

Defines a ModelDriver class for running the conversion on AM3 output
directories
"""

import collections
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


@dataclass(frozen=True)
class Stream:
    file_id: str
    um_basename: str
    use_name: str
    re_pattern: re.Pattern
    files: tuple

class Am3Driver(ModelDriver):

    def __init__(self, input_directories, output_directory, one_nc_per_stash_variable=True):
            super().__init__(one_nc_per_stash_variable)
            self._output_directory = output_directory
            self._input_directories = [Path(dir) for dir in input_directories]

    @property
    def output_directory(self):
        self._output_directory.mkdir(exist_ok=True)
        return self._output_directory

    @cached_property
    def input_directories(self):
        for dir in self._input_directories:
            if not dir.exists():
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), dir
                )
        return self._input_directories

    @cached_property
    def atmoscntl(self):
        atmoscntl_nml = f90nml.read(self.input_directories[0] / "ATMOSCNTL")
        return atmoscntl_nml

    @cached_property
    def stashc(self):
        stashc_nml = f90nml.read(self.input_directories[0] / "STASHC")
        return stashc_nml

    @cached_property
    def input_name_patterns(self):
        # regex pattern for matching input files
        um_patterns = [stream_namelist["filename_base"] for stream_namelist in self.atmoscntl["nlstcall_pp"]]
        return [base_to_regex(um_pattern) for um_pattern in um_patterns]

    @cached_property
    def streams(self):
        _streams = []
        for pp_namelist in self.atmoscntl["nlstcall_pp"]: # TODO: What if only one namelist stream? What if none?
            um_basename = pp_namelist["filename_base"]
            file_regex = base_to_regex(um_basename)
            file_id = pp_namelist["file_id"]

            stash_use_namelist = get_matching_namelist_group(self.stashc, "umstash_use", {"file_id": file_id})
            use_name = stash_use_namelist["use_name"]

            files = tuple(file for dir in self.input_directories for file in find_matching_files(dir, file_regex))
            _streams.append(
                Stream(
                    file_id=file_id,
                    um_basename=um_basename,
                    use_name=use_name,
                    re_pattern=file_regex,
                    files=files
                )
            )

        self.validate_streams(_streams)
        return _streams

    @staticmethod
    def validate_streams(streams):
        """
        Check for problems in the input streams identified for conversion.
        """
        # Check that input files aren't associated with multiple streams
        counts = [collections.Counter(stream.files) for stream in streams]
        total_count = sum(counts, collections.Counter())
        duplicate_inputs = [item for item, count in total_count.items() if count > 1]
        if duplicate_inputs:
            raise RuntimeError(
                "The following input files were grouped into more than one file stream: ",
                f"{duplicate_inputs}. Concatenation of UM files cannot proceed."
            )

        # Check that input files have been found for conversion
        if total_count.total() == 0:
            warnings.warn("No input files were found to convert.")


    def convert(self, input_stream, output_path, process_args):
        """
        Convert an input stream to netCDF.

        Parameters:
        -----------
        input_path: Path to input fields file.
        output_path: Path or DelayedCubePath for writing output netCDF.
        process_args: conversion arguments.
        """
        process(input_stream.files, output_path, process_args)
        

    def get_input_paths(self):
        """
        Find atmosphere fields files for conversion.

        Returns:
        --------
        input_paths: List of paths to UM fields files to be converted.
        """

        return self.streams

    def get_output_path(self, input_stream):
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
            return Am3DelayedCubePath(self.output_directory, input_stream, self.stashc)
        else:
            input_name = input_stream.files[0].name  #TODO: something better here, also this isn't working
            return self.output_directory / f"{input_name}.nc"


def base_to_regex(filename_base):
    month_names_3char = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    season_names_3char = r"(jfm|fma|mam|amj|mjj|jja|jas|aso|son|ond|ndj|djf)"
    special_char = r"[yvxluzpwtr]"

    replacements = {
        r"%Y" : r"\\d{{4,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L364
        r"%m": r"\\d{{2,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L395
        r"%d": r"\\d{{2,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L427
        r"%H": r"\\d{{2,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L475
        r"%M": r"\\d{{2,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L506
        r"%S": r"\\d{{2,}}", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L521
        r"%AY": r"\\d{{4,}}",
        r"%Am": r"\\d{{2,}}",
        r"%Ad": r"\\d{{2,}}",
        r"%AH": r"\\d{{2,}}",
        r"%AM": r"\\d{{2,}}",
        r"%AS": r"\\d{{2,}}",
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
        r"%C": rf"(\\d{{8}}_\\d{{2}}|\\d{{4}}{month_names_3char}|\\d{{8}}|m\\d{{4}}{month_names_3char}|s\\d{{4}}{season_names_3char}|{special_char}\\d{{8}}|\\d{{9}}_\\d{{2}})", # https://github.com/ACCESS-NRI/UM/blob/4954ca82035202842f7891366bdba7c70d8ca52e/src/control/top_level/filename_generation_mod.F90#L682
    }
    filename = Path(filename_base).name
    filename_regex = filename
    for um_pattern, python_pattern in replacements.items():
        filename_regex = re.sub(um_pattern, python_pattern, filename_regex)

    filename_regex = f"{filename_regex}$"

    return filename_regex



def get_matching_namelist_group(namelist, group_name, match_values):
    matching_groups = []
    for group in namelist[group_name]:
        try:
            if all(group[key] == val for key, val in match_values.items()):
                matching_groups.append(group)
        except KeyError:
            continue

    if len(matching_groups) == 0:
        raise RuntimeError(
            f"No {group_name} groups found in namelist matching {match_values}"
        )
    elif len(matching_groups) > 1:
        raise RuntimeError(
            f"Multiple {group_name} groups found in namelist matching {match_values}.\n"
            f"Found groups: {matching_groups}"
        )

    return matching_groups[0]
            



class Am3DelayedCubePath(DelayedCubePath):
    # template = "access-am3.um{um_version}.{dimensions}.{field_name}.{freq}{time_cell_method}{datestamp}.nc"

    def __init__(self, output_dir_path, stream, stashc):
        self.output_dir_path = output_dir_path
        self.stream = stream
        self.stashc = stashc

    def _get_freq(self, cube):
        # Determine the freq from the cube
        section = cube.attributes["STASH"].section
        item = cube.attributes["STASH"].item
        use_name = self.stream.use_name

        # TODO: Do we raise an error here if no or multiple namelist groups are found? Or just a warning and revert to "unknown_freq"
        try:
            stash_request = get_matching_namelist_group(self.stashc, "umstash_streq", {"isec": section, "item": item, "use_name": use_name})
        except RuntimeError as r:
            warnings.warn(r.args[0])
            return "unknown_freq"
        tim_name = stash_request["tim_name"]
        time_profile = get_matching_namelist_group(self.stashc, "umstash_time", {"tim_name": tim_name})

        ifre = time_profile["ifre"]
        unt3 = time_profile["unt3"]

        FREQ_CODES = {
             1: "timestep",
             2: "hour",
             3: "day",
             4: "dmp",
             5: "min",
             6: "sec",
             7: "mon",
        }

        return f"{ifre}{FREQ_CODES[unt3]}"

    def _get_datestamp(self, cube):
        # Since ESM1.6 output files are yearly, just need YYYY for the datestamp
        # Ensure years with less than 4 digits are formatted with leading zeros
        # TODO: Need some way to set this appropriately for AM3, ideally flexible enough for rAM3?
        fmt = '%4Y'

        # Get the appropriately truncated datetime for the average time
        d_str = cube.coord('time').units.num2date(cube.coord('time').points.mean()).strftime(fmt)
        return f"{d_str}"

    def _build_filename(self, cube):
        components = ["access-am3", "um"+self._get_um_version(cube), self._get_dimensions(cube), self._get_var_name(cube), self._get_freq(cube), self._get_time_cell_method(cube), self._get_datestamp(cube), "nc"]

        non_empty_components = [component for component in components if component is not None]

        return ".".join(non_empty_components)

    def _get_output_dir(self):
        return self.output_dir_path
