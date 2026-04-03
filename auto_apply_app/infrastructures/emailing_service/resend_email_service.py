import os
import httpx
from auto_apply_app.application.service_ports.email_service_port import EmailServicePort

class ResendEmailService(EmailServicePort):
    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        
        # ✅ UPDATED: The default is now your verified domain
        self.from_email = os.getenv("EMAIL_FROM", "noreply@autopostule.com") 
        self.api_url = "https://api.resend.com/emails"

    async def send_password_reset_email(self, to_email: str, reset_token: str) -> None:
        if not self.api_key:
            print("⚠️ WARNING: RESEND_API_KEY is not set. Email not sent.")
            return

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

        payload = {
            "from": f"AutoPostule <{self.from_email}>", 
            "to": [to_email],
            "subject": "Réinitialisation de votre mot de passe AutoPostule", # ✅ Translated subject
            "html": html_content
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code >= 400:
                    print(f"❌ Resend API Error: {response.status_code} - {response.text}")
                    
            except httpx.RequestError as e:
                print(f"❌ Failed to connect to Resend API: {str(e)}")