"""Test utils."""
# pylint: disable=missing-docstring
from imaps.base.utils import get_part_before_colon, get_part_before_colon_hypen, is_date, is_integer

from .base import ImapsTestCase


class TestUtils(ImapsTestCase):

    def test_is_integer(self):
        self.assertTrue(is_integer(1))
        self.assertTrue(is_integer('1'))
        self.assertTrue(is_integer(1.0))
        self.assertTrue(is_integer('1.0'))
        self.assertTrue(is_integer(1.00000))
        self.assertTrue(is_integer('1.00000'))
        self.assertTrue(is_integer('', allow_empty=True))

        self.assertFalse(is_integer('foo'))
        self.assertFalse(is_integer('123 go'))
        self.assertFalse(is_integer(1.1))
        self.assertFalse(is_integer('1.1'))
        self.assertFalse(is_integer(''))

    def test_is_date(self):
        self.assertTrue(is_date('2019-01-04'))
        self.assertTrue(is_date('2019-1-4'))
        self.assertTrue(is_date('', allow_empty=True))

        self.assertFalse(is_date('2019 1 4'))
        self.assertTrue(is_date('2019 1 4', format='%Y %m %d'))
        self.assertFalse(is_date('', allow_empty=False))

    def test_part_before_colon(self):
        self.assertEqual(get_part_before_colon('brain:test'), 'brain')
        self.assertEqual(get_part_before_colon('foo:bar:foo'), 'foo')

    def test_part_before_colon_hypen(self):
        self.assertEqual(get_part_before_colon_hypen('TARDBP-GFP'), 'TARDBP')
        self.assertEqual(get_part_before_colon_hypen('TARBP2:nFLAG'), 'TARBP2')
        self.assertEqual(get_part_before_colon_hypen('TARDBP:274del319-eGFP'), 'TARDBP')
        self.assertEqual(get_part_before_colon_hypen('TARDBP-eGFP'), 'TARDBP')
        self.assertEqual(get_part_before_colon_hypen('SMB1-FLAG-His'), 'SMB1')
        self.assertEqual(get_part_before_colon_hypen('noUV:PRP22:K512A'), 'noUV')
