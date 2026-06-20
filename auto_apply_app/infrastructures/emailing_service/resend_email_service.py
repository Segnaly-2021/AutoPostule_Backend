import os
import logging
import httpx
from auto_apply_app.application.service_ports.email_service_port import EmailServicePort

logger = logging.getLogger(__name__)


class ResendEmailService(EmailServicePort):
    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv("EMAIL_FROM", "noreply@autopostule.com")
        self.api_url = "https://api.resend.com/emails"

    async def _send(self, to_email: str, subject: str, html_content: str) -> None:
        if not self.api_key:
            logger.warning("RESEND_API_KEY is not set. Email to %s not sent.", to_email)
            return

        payload = {
            "from": f"AutoPostule <{self.from_email}>",
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.api_url, json=payload, headers=headers, timeout=10.0)
                if response.status_code >= 400:
                    logger.error("Resend API error %s: %s", response.status_code, response.text)
            except httpx.RequestError:
                logger.exception("Failed to connect to Resend API")

    async def send_password_reset_email(self, to_email: str, reset_token: str) -> None:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        reset_link = f"{frontend_url}/reset-password?token={reset_token}"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>Demande de réinitialisation de mot de passe</h2>
            <p>Bonjour,</p>
            <p>Nous avons reçu une demande de réinitialisation du mot de passe pour votre compte AutoPostule.</p>
            <p>Cliquez sur le bouton ci-dessous pour choisir un nouveau mot de passe. Ce lien expirera dans 15 minutes.</p>
            <div style="margin: 30px 0;">
                <a href="{reset_link}" style="background-color: #0066ff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Réinitialiser le mot de passe
                </a>
            </div>
            <p style="color: #666; font-size: 14px;">Si vous n'avez pas demandé cette réinitialisation, vous pouvez ignorer cet e-mail en toute sécurité.</p>
        </div>
        """
        await self._send(
            to_email=to_email,
            subject="Réinitialisation de votre mot de passe AutoPostule",
            html_content=html_content,
        )

    async def send_verification_email(self, to_email: str, code: str) -> None:
        """
        Sends a 6-digit verification code. Replaces the previous link-based flow.
        Code is valid for 15 minutes (enforced by the use case / entity).
        """
        # Spacing for readability: '123456' -> '123 456'
        formatted_code = f"{code[:3]} {code[3:]}" if len(code) == 6 else code

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1a1a1a;">
            <h2 style="margin-bottom: 16px;">Confirmez votre adresse e-mail</h2>
            <p>Bonjour,</p>
            <p>Bienvenue sur AutoPostule. Pour activer votre compte, saisissez le code ci-dessous sur la page d'inscription :</p>

            <div style="margin: 32px 0; text-align: center;">
                <div style="display: inline-block; padding: 20px 32px; background-color: #f4f6fb; border: 1px solid #dfe3ee; border-radius: 8px;">
                    <div style="font-family: 'Courier New', monospace; font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #0066ff;">
                        {formatted_code}
                    </div>
                </div>
            </div>

            <p>Ce code est valable pendant <strong>15 minutes</strong>.</p>
            <p style="color: #666; font-size: 14px; margin-top: 32px;">
                Si vous n'avez pas créé de compte AutoPostule, vous pouvez ignorer cet e-mail en toute sécurité.
            </p>
        </div>
        """
        await self._send(
            to_email=to_email,
            subject=f"Votre code de vérification : {code}",
            html_content=html_content,
        )

    async def send_email_changed_notification(self, to_email: str, new_email: str) -> None:
        """
        Security notice sent to the OLD address after the account email is changed,
        so a legitimate owner can react if the change wasn't them.
        """
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1a1a1a;">
            <h2 style="margin-bottom: 16px;">Votre adresse e-mail a été modifiée</h2>
            <p>Bonjour,</p>
            <p>
                L'adresse e-mail associée à votre compte AutoPostule vient d'être changée pour
                <strong>{new_email}</strong>.
            </p>
            <p>
                Si vous êtes à l'origine de cette modification, aucune action n'est requise.
            </p>
            <p style="color: #b00020; font-weight: bold; margin-top: 24px;">
                Si vous n'avez pas effectué ce changement, contactez immédiatement notre support
                pour sécuriser votre compte.
            </p>
        </div>
        """
        await self._send(
            to_email=to_email,
            subject="Votre adresse e-mail AutoPostule a été modifiée",
            html_content=html_content,
        )