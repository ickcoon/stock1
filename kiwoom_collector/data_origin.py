"""Server environment and acquisition provenance are independent dimensions."""
ORIGINS=('api_response','synthetic_fixture','unverified')


def metadata(environment,origin='unverified'):
    if environment not in ('mock','real') or origin not in ORIGINS:
        raise ValueError('invalid_environment_or_data_origin')
    label=('키움 실전 서버 API 응답' if environment=='real' else '키움 모의 서버 API 응답') if origin=='api_response' else (
        '합성 fixture' if origin=='synthetic_fixture' else '출처 미확인 자료')
    return dict(environment=environment,data_origin=origin,data_origin_label=label)


def validate_record(document):
    origin=document.get('data_origin','unverified')
    metadata(document.get('environment'),origin)
    if origin=='synthetic_fixture' and document.get('synthetic') is not True:
        raise ValueError('synthetic_origin_scope_mismatch')
    if origin=='api_response' and document.get('synthetic') is not False:
        raise ValueError('api_origin_scope_mismatch')
    return origin
