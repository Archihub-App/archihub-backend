import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.example.com')
EMAIL_PORT = os.environ.get('EMAIL_PORT', 587)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'your-email@example.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', 'your-password')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', True)

def send_email(to_email, subject, body):
    try:
        smtp_host = EMAIL_HOST
        smtp_port = EMAIL_PORT
        smtp_user = EMAIL_HOST_USER
        smtp_password = EMAIL_HOST_PASSWORD
        use_tls = EMAIL_USE_TLS

        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls() if use_tls else None

        server.login(smtp_user, smtp_password) if smtp_user and smtp_password else None
        server.sendmail(smtp_user, to_email, msg.as_string())
        server.quit()

        print("Email enviado exitosamente")
    except Exception as e:
        print(f"Error: {str(e)}")