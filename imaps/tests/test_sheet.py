"""Test spreadsheet."""
# pylint: disable=missing-docstring
import csv
import random

from imaps.base.constants.sheet import COLUMNS, METHODS, PROTEINS, TISSUES
from imaps.base.sheet import DescriptorSheet

from .base import ImapsTestCase


class TestSpreadsheet(ImapsTestCase):

    def create_sheet_file(self, filename=None, nrows=3):
        if filename is None:
            filename = self.get_filename(extension='.tsv')

        barcodes5 = [
            'NNNN,CCGGA_0,NNN',
            'NNN,CTGC_0,NN',
            'NNN,CTATT_0,NN',
        ]
        barcodes3 = [
            'L3-GTC',
            'L3-GGA',
            'L3',
        ]

        with open(filename, 'w') as handle:
            csvwriter = csv.writer(handle, delimiter='\t', lineterminator='\n')
            csvwriter.writerow(COLUMNS)
            for i in range(nrows):
                csvwriter.writerow([
                    'My sample {}'.format(i + 1),
                    'My collection',
                    'This experimental design is so cool...',
                    'Me!',
                    'My PI',
                    random.choice(list(METHODS)),
                    'Protocol document...',
                    random.choice(list(PROTEINS)),
                    random.choice(list(TISSUES)),
                    '.',
                    'Hs',
                    barcodes5[i],
                    'AGATCGGAAG_1,AGCGGTTCAG_2',
                    'HiSeq',
                    'antibody',
                    'RT primer {}',
                    barcodes3[i],
                ])

        return filename

    def test_basic(self):
        sheet_file = self.create_sheet_file()
        sheet = DescriptorSheet(sheet_file)
        sheet.validate()
        self.assertTrue(sheet.is_valid)
        self.assertEqual(len(sheet.errors), 0)
