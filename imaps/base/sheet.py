"""Sheet with sample descriptors."""
import collections
import csv
import gzip
import re

import xlrd

import resdk

from .constants import SPECIES_SHORT_NAMES
from .constants.sheet import (
    ADAPTER_COLUMN_NAME, BARCODE3_COLUMN_NAME, BARCODE5_COLUMN_NAME, COLUMNS, MAX_SAMPLE_NAME_SIZE, METHODS, PROTEINS,
    REQUIRED_COLUMNS, TISSUES,
)
from .exceptions import ValidationError
from .utils import get_part_before_colon, get_part_before_colon_hypen, get_temp_file_name, is_date, is_integer


class DescriptorSheet:
    """iMaps Sheet for sample descriptors."""

    def __init__(self, filename):
        """Initiaize."""
        # Tab separated file, representing sample descriptors
        self.tsv = None
        # Excel file, representing sample descriptors
        self.excel = None
        # Parsed content
        self._content = None
        # Flag indicating if sheet is valid
        self.is_valid = None
        # Errors reported during validation
        self.errors = []

        if filename.endswith('.xls') or filename.endswith('.xlsx'):
            self.excel = filename
            self.tsv = self.excel_to_tsv(excel_file=filename)
        elif filename.endswith('.tsv') or filename.endswith('.tsv.gz'):
            self.tsv = filename
        else:
            raise ValueError("Filename should have one of the following extensions: .xls, .xlsx, .tsv, .tsv.gz")

    def excel_to_tsv(self, excel_file):
        """Parse excel into tsv file."""
        tsv_file = get_temp_file_name(extension='tsv.gz')

        with gzip.open(tsv_file, mode='wt') as outfile:
            csvwriter = csv.writer(outfile, delimiter=str('\t'), lineterminator='\n')
            try:
                workbook = xlrd.open_workbook(excel_file)
                worksheet = workbook.sheets()[0]
                column_names = worksheet.row_values(0)
                for rownum in range(worksheet.nrows):
                    ascii_row_content = []
                    for cell_content, column_name in zip(worksheet.row_values(rownum), column_names):
                        # Handle non-ascii charachters:
                        try:
                            ascii_value = bytes(str(cell_content).encode('utf-8')).decode('ascii', 'strict')
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            for position, char in enumerate(cell_content):
                                if ord(char) > 127:
                                    break
                            self.fail('Problem decoding row {}, column "{}" at position {}.'.format(
                                rownum + 1, column_name, position))  # pylint: disable=undefined-loop-variable
                            ascii_value = bytes(str(cell_content).encode('utf-8')).decode('ascii', 'ignore')
                        finally:
                            ascii_row_content.append(ascii_value)

                    csvwriter.writerow(ascii_row_content)
            except Exception:
                self.fail('Corrupted or unrecognized file.', raise_exception=True)
                raise

        return tsv_file

    def fail(self, message, raise_exception=False, **kwargs):
        """Fail."""
        message = message.format(kwargs)
        self.errors.append(message)
        if raise_exception:
            raise ValidationError(message)

    @property
    def content(self):
        """Get content of DEscriptor sheet."""
        if self._content is None:
            open_function = gzip.open if self.tsv.endswith('.gz') else open
            with open_function(self.tsv, 'rt') as tfile:
                self._content = list(csv.DictReader(tfile, dialect='unix', delimiter='\t'))

        return self._content

    def get_barcodes5_mismatches(self):
        """Get list of 5' barcodes and their mismatches."""
        sequences = []
        mismatches = []
        for row in self.content:
            sequence = re.sub(r"[^ACGTN]+", '', row[BARCODE5_COLUMN_NAME])
            sequences.append(sequence)
            mismatch = re.sub(r"[^\d]+", '', row[BARCODE5_COLUMN_NAME])
            mismatches.append(0 if not mismatch.isdigit() else int(mismatch))
        return sequences, mismatches

    def get_barcodes3_mismatches(self):
        """Get list of 3' barcodes and their mismatches."""
        sequences = []
        mismatches = []
        for row in self.content:
            sequence = re.sub(r"[^ACGTN]+", '', row[BARCODE3_COLUMN_NAME])
            sequences.append('.' if sequence == '' else sequence)
            mismatch = re.sub(r"[^\d]+", '', row[BARCODE3_COLUMN_NAME])
            mismatches.append(0 if not mismatch.isdigit() else int(mismatch))
        return sequences, mismatches

    def get_adapters_mismatches(self):
        """Get list of 3' barcodes and their mismatches."""
        sequences = []
        mismatches = []
        for row in self.content:
            sequence = re.sub(r"[^ACGTN]+", '', row[ADAPTER_COLUMN_NAME])
            sequences.append(sequence)
            mismatch = re.sub(r"[^\d]+", '', row[ADAPTER_COLUMN_NAME])
            mismatches.append(0 if not mismatch.isdigit() else int(mismatch))
        return sequences, mismatches

    # --------------------------------------------
    # From here on, validation methods take place.
    # --------------------------------------------

    def validate(self, raise_exception=True):
        """Validate sheet."""
        if self.is_valid:
            return

        # Reset errors to empty list.
        self.errors = []

        # 1. Validate sheet in general.
        self.validate_header()
        self.validate_sample_names()
        self.validate_barcodes()
        self.validate_adapters()

        # 2. Validate each row.
        for row in self.content:
            self.validate_row(row)

        if self.errors:
            self.is_valid = False
            if raise_exception:
                raise ValidationError("Descriptor sheet is not valid. Inspect `.errors` attribute for details.")
        else:
            self.is_valid = True

    def validate_header(self):
        """Validate that spreadsheet contains all expected columns."""
        missing = set(COLUMNS) - set(self.content[0].keys())
        if missing:
            self.fail('Spreadsheet does not contain all the required columns. Missing columns: {}'.format(missing))

    def validate_sample_names(self):
        """Validate sample name uniqness."""
        values = [row['Sample name'] for row in self.content]
        if len(set(values)) != len(values):
            counter = collections.Counter(values)
            repeated = [value for value, count in counter.items() if count > 1]
            repeated = ', '.join(repeated)
            self.fail("Column 'Sample name' must contain unique items. These are not unique: {}".format(repeated))

    def validate_barcodes(self):
        """Validate barcodes."""
        barcodes5, mismatches5 = self.get_barcodes5_mismatches()
        barcodes3, mismatches3 = self.get_barcodes3_mismatches()

        if len(set(mismatches5)) != 1:
            self.fail("All 5' barcodes must have same number of mismatches.")
        if len(set(mismatches3)) != 1:
            self.fail("All 3' barcodes must have same number of mismatches.")

        if all([bcode == '.' for bcode in barcodes3]):
            # No 3' barcodes are given, check only for uniqness of 5' ones.
            if len(barcodes5) != len(set(barcodes5)):
                self.fail("Barcodes on 5' end are not unique.")
        else:
            combined = list(zip(barcodes5, barcodes3))
            if len(combined) != len(set(combined)):
                self.fail("Combination of barcodes on 3' and 5' end is not unique.")

    def validate_adapters(self):
        """Validate adapters."""
        values = [row[ADAPTER_COLUMN_NAME] for row in self.content]
        if len(set(values)) != 1:
            self.fail("All adapters must be the same.")

    def validate_row(self, row):  # pylint: disable=too-many-branches
        """Validate one row in DescriptorSheet."""
        name = row['Sample name']

        # All required columns have values.
        for column_name in REQUIRED_COLUMNS:
            if not row[column_name]:
                self.fail("There are missing values in column {}.".format(column_name))

        # Sample name is not too long.
        if len(name) >= MAX_SAMPLE_NAME_SIZE:
            self.fail('Sample name "{}" should be shorter than 100 characters.'.format(name))

        # Validate method.
        if row['Method'] not in METHODS:
            self.fail('SAMPLE: {} - Value {} is not a valid entry for cells/tissue.'.format(name, row['Method']))

        # Validate protein.
        # Only validate protein names if species is human or mouse
        if row['mapto'] in ['Hs', 'Mm']:
            gene_symbol = get_part_before_colon_hypen(row['Protein'])
            if gene_symbol not in PROTEINS:
                res = resdk.Resolwe()
                kb_gene = res.feature.filter(source="UCSC", feature_id=gene_symbol)  # pylint: disable=no-member
                if not kb_gene:
                    self.fail('SAMPLE: {} - Value {} is not a valid protein.'.format(name, row['Protein']))

        # Validate tissue.
        tissue = get_part_before_colon(row['cells/tissue'])
        if tissue not in TISSUES:
            self.fail('SAMPLE: {} - Value {} is not a valid cells/tissue entry.'.format(name, tissue))

        # Validate species choice.
        if row['mapto'] not in SPECIES_SHORT_NAMES:
            self.fail('SAMPLE: {} - Value {} is not a valid "mapto" entry.'.format(name, row['mapto']))

        # Field has "yes" or "no" value.
        column_name = 'consensus mapping (optional)'
        value = row[column_name]
        if value and value not in ['yes', 'no']:
            self.fail('SAMPLE: {} - Value {} in column {} should be "yes" or "no".'.format(name, value, column_name))

        # Field is an integer.
        integer_columns = [
            'resequencing ID (optional)',
            'replicate (optional)',
            'page of gel images in lab notebook (optional)',
            'year of data release (optional)',
        ]
        for column_name in integer_columns:
            value = row[column_name]
            if not is_integer(value, allow_empty=True):
                self.fail('SAMPLE: {} - Value {} in column {} should be an integer.'.format(name, value, column_name))

        # Date field contains a valid date.
        column_name = 'date of gel images in lab notebook (optional)'
        value = row[column_name]
        if not is_date(value, allow_empty=True):
            self.fail(
                'SAMPLE: {sample} - Value {value} in column {column}, should be in YYYY-MM-DD format.',
                sample=name,
                value=value,
                column=column_name,
            )
