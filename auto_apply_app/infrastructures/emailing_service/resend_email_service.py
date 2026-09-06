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

    async def _send(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        reply_to: str | None = None,
        unsubscribe_url: str | None = None,
    ) -> bool:
        """Send one email. Returns True only if Resend accepted it.

        The return value is load-bearing for lifecycle mail. This used to return
        None on success AND on failure, which is fine for transactional mail --
        the caller has nothing to do about a bounced password reset anyway -- but
        a scheduled campaign writes a 'sent' row afterwards so nobody is emailed
        twice. Logging that row on a failed send would silently drop the message
        forever, since the log is also what suppresses a retry.
        """
        if not self.api_key:
            logger.warning("RESEND_API_KEY is not set. Email to %s not sent.", to_email)
            return False

        payload = {
            "from": f"AutoPostule <{self.from_email}>",
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        if reply_to:
            # An email that asks a question needs somewhere for the answer to go.
            payload["reply_to"] = reply_to
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if unsubscribe_url:
            # Required for lawful marketing mail in the EU, and mailbox providers
            # penalise bulk sends without it.
            payload["headers"] = {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.api_url, json=payload, headers=headers, timeout=10.0)
                if response.status_code >= 400:
                    logger.error("Resend API error %s: %s", response.status_code, response.text)
                    return False
                return True
            except httpx.RequestError:
                logger.exception("Failed to connect to Resend API")
                return False

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
    # ------------------------------------------------------------------
    # Lifecycle mail
    #
    # Unlike the three above, these are sent by a scheduled job rather than by
    # something the user just did. Two consequences: the send has to report
    # whether it worked (see _send), and the marketing one carries an
    # unsubscribe -- it is promotional mail to someone who did not ask for it.
    # ------------------------------------------------------------------

    @staticmethod
    def _layout(body_html: str, footer_html: str = "") -> str:
        """Shared shell. The three transactional bodies each repeat this wrapper;
        new mail should not add a fourth copy."""
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #1a1a1a;">
            {body_html}
            <hr style="border: none; border-top: 1px solid #e6e8ef; margin: 32px 0 16px;">
            <p style="color: #8a8f9c; font-size: 12px; line-height: 1.6;">
                AutoPostule — vos candidatures, automatisées.
                {footer_html}
            </p>
        </div>
        """

    @staticmethod
    def _button(href: str, label: str) -> str:
        return f"""
            <div style="margin: 30px 0;">
                <a href="{href}" style="background-color: #0066ff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                    {label}
                </a>
            </div>
        """

    async def send_new_customer_checkin(self, to_email: str, first_name: str) -> bool:
        """Three days after a plan is bought: thank them, and ask a real question.

        Deliberately asks for a reply rather than linking to a form -- the answer
        rate is the point, and a reply costs the reader nothing. That is why a
        reply_to is passed; without it the question would land at noreply@.
        """
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        # `or`, not getenv's default: the messaging Job's deploy always passes
        # EMAIL_REPLY_TO, so an unset secret arrives as "" and the default would
        # never fire -- leaving an email that asks for a reply with no reply_to
        # header, so the answers land at noreply@ and are lost.
        reply_to = os.getenv("EMAIL_REPLY_TO") or "contact@autopostule.com"
        greeting = f"Bonjour {first_name}," if first_name else "Bonjour,"

        body = f"""
            <h2 style="color: #1a1a1a;">Vos premières candidatures</h2>
            <p style="line-height: 1.7;">{greeting}</p>
            <p style="line-height: 1.7;">
                Merci de votre confiance. Depuis trois jours, votre agent travaille pour vous :
                il parcourt les offres, rédige vos lettres de motivation et postule en votre nom.
            </p>
            <p style="line-height: 1.7;">
                Nous aimerions savoir comment cela se passe. Les offres correspondent-elles à ce
                que vous recherchez ? Les lettres de motivation vous ressemblent-elles ?
            </p>
            <p style="line-height: 1.7;">
                Répondez simplement à ce message : chaque retour est lu, et beaucoup de ce que
                vous utilisez aujourd'hui vient de retours comme le vôtre.
            </p>
            {self._button(f"{frontend_url}/job-search/dashboard", "Voir mes candidatures")}
            <p style="line-height: 1.7;">Bien à vous,<br>L'équipe AutoPostule</p>
        """
        return await self._send(
            to_email=to_email,
            subject="Vos premières candidatures avec AutoPostule",
            html_content=self._layout(body),
            reply_to=reply_to,
        )

    async def send_free_account_reminder(
        self, to_email: str, first_name: str, unsubscribe_url: str
    ) -> bool:
        """Three days after signup, to someone who has not started a search.

        This one is marketing, so the unsubscribe link is not optional -- it is
        rendered in the body AND sent as a List-Unsubscribe header, because some
        clients only honour one of the two.
        """
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        greeting = f"Bonjour {first_name}," if first_name else "Bonjour,"

        body = f"""
            <h2 style="color: #1a1a1a;">Ce qu'AutoPostule peut faire pour votre recherche</h2>
            <p style="line-height: 1.7;">{greeting}</p>
            <p style="line-height: 1.7;">
                Vous avez créé un compte AutoPostule il y a quelques jours, sans encore lancer
                de recherche. Voici, en quelques lignes, ce que notre agent fait à votre place.
            </p>
            <p style="line-height: 1.7;">
                Il parcourt les offres qui correspondent à votre profil sur l'APEC, Welcome to
                the Jungle et HelloWork. Il rédige une lettre de motivation propre à chaque
                annonce, à partir de votre CV. Puis il postule directement sur la plateforme.
            </p>
            <p style="line-height: 1.7;">
                Vous gardez la main : vous choisissez les métiers, les types de contrat et les
                localisations. En formule Premium, vous relisez et validez chaque candidature
                avant l'envoi.
            </p>
            {self._button(f"{frontend_url}/#pricing", "Découvrir les formules")}
            <p style="line-height: 1.7;">Bien à vous,<br>L'équipe AutoPostule</p>
        """
        footer = (
            f'<br><a href="{unsubscribe_url}" style="color: #8a8f9c;">'
            "Se désabonner de ces messages</a>"
        )
        return await self._send(
            to_email=to_email,
            subject="Ce qu'AutoPostule peut faire pour votre recherche",
            html_content=self._layout(body, footer_html=footer),
            unsubscribe_url=unsubscribe_url,
        )
