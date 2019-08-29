"""imaps utility functions."""
import datetime
import os
import re
import tempfile


def get_temp_file_name(tmp_dir=None, extension=''):
    """Return an availiable name for temporary file."""
    tmp_name = next(tempfile._get_candidate_names())  # pylint: disable=protected-access
    if not tmp_dir:
        tmp_dir = tempfile._get_default_tempdir()  # pylint: disable=protected-access
    if extension:
        tmp_name += '.' + extension
    return os.path.join(tmp_dir, tmp_name)


def get_temp_dir():
    """Return a temporary directory."""
    return tempfile.mkdtemp()


def is_integer(value, allow_empty=False):
    """Check if value is representing integer."""
    if not value:
        if allow_empty:
            return True
        return False
    try:
        float_value = float(value)
        int_value = int(float(value))
        assert int_value - float_value == 0
        return True
    except (ValueError, AssertionError):
        return False


def is_date(value, format='%Y-%m-%d', allow_empty=False):  # pylint: disable=redefined-builtin
    """Check if value is representing date in expected format."""
    if not value:
        if allow_empty:
            return True
        return False
    try:
        datetime.datetime.strptime(value, format)
        return True
    except ValueError:
        return False


def get_part_before_colon(string):
    """Return part of string before the first colon."""
    try:
        return re.match(r".*?([^:]+)[:]?", string).group(1)
    except AttributeError:
        return string


def get_part_before_colon_hypen(string):
    """Return part of string before the first colon / hypen."""
    try:
        return re.match(r".*?([^:\-]+)[:\-]?", string).group(1)
    except AttributeError:
        return string
