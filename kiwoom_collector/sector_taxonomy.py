"""Conservative display-only bridge to the existing C11 representative taxonomy."""
from .stock_catalog import SECTORS

VERSION = 'c12-sector-bridge-v1'
LEGACY_LINKS = {
    '원전': '원전·전력', '전력': '원전·전력',
    '방산': '방산·우주', '우주항공': '방산·우주',
    '화학': '화학·소재', '철강': '철강·기계', '건설': '건설·인프라',
}
HELD = {'통신', '인터넷·게임'}


def link(name):
    """A label relationship is not evidence approving a stock's classification."""
    if name in SECTORS:
        return dict(sector=name, reason='exact_c11_label')
    if name in LEGACY_LINKS:
        return dict(sector=LEGACY_LINKS[name], reason='c10_narrower_industry')
    return dict(sector=None, reason='legacy_interpretation_required' if name in HELD else 'unsupported_industry')
