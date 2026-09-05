"""Pin du jeton de téléchargement direct (api/utils/download_token.py)."""
import time

from api.utils.download_token import issue_download_token, verify_download_token

SECRET = "unit-test-secret"


def test_round_trip():
    tok = issue_download_token("kb1", "path/f.pdf", "f.pdf", "application/pdf", secret=SECRET)
    assert verify_download_token(tok, secret=SECRET) == {
        "bucket": "kb1", "name": "path/f.pdf", "filename": "f.pdf", "mimetype": "application/pdf",
    }


def test_tampered_or_wrong_secret_rejected():
    tok = issue_download_token("kb1", "f", "f", "m", secret=SECRET)
    assert verify_download_token(tok + "x", secret=SECRET) is None
    assert verify_download_token(tok, secret="other") is None
    assert verify_download_token("garbage", secret=SECRET) is None


def test_expiry():
    tok = issue_download_token("kb1", "f", "f", "m", secret=SECRET)
    time.sleep(2.1)  # horodatage itsdangerous à la seconde
    assert verify_download_token(tok, max_age=1, secret=SECRET) is None
    assert verify_download_token(tok, max_age=60, secret=SECRET) is not None
