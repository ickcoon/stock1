"""Allowlisted response shape only: never headers, row values, messages or tokens."""
from collections import Counter

MARKET_FIELDS = ("stk_nm", "bigd_rank", "rank_chg", "rank_chg_sign", "past_curr_prc",
                 "base_comp_sign", "base_comp_chgr", "prev_base_sign", "prev_base_chgr", "dt", "tm", "stk_cd")


def type_name(value):
    for kind, label in ((str, 'str'), (bool, 'bool'), (int, 'int'), (float, 'float'),
                        (dict, 'dict'), (list, 'list')):
        if isinstance(value, kind):
            return label
    return 'null' if value is None else 'other'


def response_shape(body, http_status=None):
    result = {'body_type': type_name(body)}
    if isinstance(http_status, int) and not isinstance(http_status, bool):
        result['http_status'] = http_status
    if not isinstance(body, dict):
        return result
    result['return_code_type'] = type_name(body.get('return_code')) if 'return_code' in body else 'missing'
    try:
        code = int(str(body.get('return_code')))
        result['return_code'] = code if abs(code) <= 999999 else 'out_of_range'
    except (ValueError, TypeError):
        pass
    if 'item_inq_rank' not in body:
        result['item_inq_rank_type'] = 'missing'
        return result
    rows = body['item_inq_rank']
    result['item_inq_rank_type'] = type_name(rows)
    if not isinstance(rows, list):
        return result
    result['row_count'] = len(rows)
    result['row_types'] = dict(Counter(type_name(row) for row in rows))
    result['field_types'] = {
        key: dict(Counter(type_name(row[key]) if key in row else 'missing'
                          for row in rows if isinstance(row, dict)))
        for key in MARKET_FIELDS
    }
    return result
