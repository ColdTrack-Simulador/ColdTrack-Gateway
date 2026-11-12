import os
import smtplib
import ssl
from email.message import EmailMessage


class SMTPMailer:
    def __init__(self):
        self.host = os.environ.get("COLDTRACK_SMTP_HOST")
        self.username = os.environ.get("COLDTRACK_SMTP_USERNAME")
        self.password = os.environ.get("COLDTRACK_SMTP_PASSWORD")
        self.sender = os.environ.get("COLDTRACK_SMTP_FROM") or self.username
        self.port = int(os.environ.get("COLDTRACK_SMTP_PORT", "587"))
        if not all((self.host, self.username, self.password, self.sender)):
            raise ValueError("Configurá host, usuario y contraseña SMTP.")

    def send(self, item):
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = item["recipient"]
        message["Subject"] = item["subject"]
        message.set_content(item["body"])
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.username, self.password)
            smtp.send_message(message)
