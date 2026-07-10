"""Mailer SMTP minimal du panel (invitations RAG + sièges code).

Env-driven (ADMIN_SMTP_*), dégradation propre : non configuré ou échec
d'envoi -> False + warning, jamais d'exception vers l'appelant (les liens
restent affichés au front en fallback). Le mailer upstream
(api/utils/web_utils.py) dépend du contexte Quart — non réutilisable ici.
"""
import logging
from email.header import Header
from email.mime.text import MIMEText

import aiosmtplib

from management.server.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.SMTP_HOST)


async def send_mail(to: str, subject: str, body_text: str) -> bool:
    if not is_configured():
        logger.warning("SMTP non configuré (ADMIN_SMTP_HOST vide) — email non envoyé à %s", to)
        return False
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    try:
        # TLS modes: port 465 = implicit TLS (TLS handshake immediately),
        # port 587 = STARTTLS (plaintext EHLO, then STARTTLS upgrade).
        # Standard providers: Gmail/O365/SES/SendGrid all use port 587 + STARTTLS.
        implicit_tls = settings.SMTP_TLS and settings.SMTP_PORT == 465
        starttls = settings.SMTP_TLS and not implicit_tls

        smtp = aiosmtplib.SMTP(
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            use_tls=implicit_tls,
            start_tls=starttls,
            timeout=10,
        )
        await smtp.connect()
        try:
            if settings.SMTP_USERNAME:
                await smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            await smtp.send_message(msg)
        finally:
            await smtp.quit()
        return True
    except Exception as e:
        logger.warning("envoi email à %s échoué: %s", to, e)
        return False
