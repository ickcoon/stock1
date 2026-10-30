"""Offline characterization; synthetic values are not captured server responses."""
import json
from pathlib import Path
import unittest

from kiwoom_collector.stock_catalog import CatalogError, parse_row


class MarketContractTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).parent / 'fixtures' / 'ka10099_synthetic.json'
        self.row = json.loads(fixture.read_text(encoding='utf-8'))['pages'][0]['response']['body']['list'][0]

    def test_exact_request_codes_pass_and_raw_values_survive(self):
        for market in ('0', '10'):
            with self.subTest(market=market):
                row = dict(self.row, marketCode=market)
                parsed = parse_row(row, market)
                for field, value in row.items():
                    self.assertEqual(parsed['raw'][field], value)

    def test_other_market_missing_empty_padding_and_whitespace_fail_closed(self):
        for market, values in (('0', ('10', '', '00', ' 0', '0 ')),
                               ('10', ('0', '', '010', ' 10', '10 '))):
            for value in values:
                with self.subTest(market=market, value=value):
                    with self.assertRaisesRegex(CatalogError, '^response_market_mismatch$'):
                        parse_row(dict(self.row, marketCode=value), market)
        row = dict(self.row)
        del row['marketCode']
        with self.assertRaisesRegex(CatalogError, '^response_market_mismatch$'):
            parse_row(row, '0')

    def test_non_string_rejected_before_market_comparison(self):
        for value in (0, 10, None, False, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(CatalogError, '^invalid_field_type_or_length$'):
                    parse_row(dict(self.row, marketCode=value), '0')

    def test_market_name_is_preserved_but_currently_not_cross_checked(self):
        row = dict(self.row, marketCode='0', marketName='SYNTHETIC_OTHER_MARKET')
        self.assertEqual(parse_row(row, '0')['market_name'], row['marketName'])
