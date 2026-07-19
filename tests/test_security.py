from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from backend.app.config import get_settings
from backend.app.security import validate_telegram_init_data


def make_init_data(user_id: int = 777001) -> str:
    settings = get_settings()
    values = {
        'auth_date': str(int(time.time())),
        'query_id': 'AAHdF6IQAAAAAN0XohDhrOrc',
        'user': json.dumps({'id': user_id, 'first_name': 'Test', 'username': 'tester'}, separators=(',', ':')),
    }
    check_string = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', settings.bot_token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_telegram_init_data() -> None:
    result = validate_telegram_init_data(make_init_data())
    assert result['user']['id'] == 777001


def test_invalid_telegram_init_data_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_telegram_init_data(make_init_data() + 'broken')
    assert exc.value.status_code == 401
