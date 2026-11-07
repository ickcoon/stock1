"""Read deployment credentials from the actual program root, without fallback."""
from io import StringIO
from pathlib import Path
from dotenv.parser import parse_stream
from dotenv import dotenv_values

PROJECT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT / ('stock1-dev.env' if PROJECT.name == 'stock1-dev' else '.env')


def values(path=None):
    source = Path(path) if path is not None else ENV_FILE
    try:
        text = source.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ValueError('credential_file_unreadable') from None
    # Validate before dotenv can log a malformed source line. Never interpolate
    # inherited process variables into credentials.
    if any(binding.error for binding in parse_stream(StringIO(text))):
        raise ValueError('credential_file_invalid')
    return {key: value or '' for key, value in
            dotenv_values(stream=StringIO(text), interpolate=False).items()}
