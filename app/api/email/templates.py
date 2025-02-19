from flask_babel import _

def forgot_password_template(link):
    return _(f"""
    <html>
    <body>
        <p>Hello,</p>
        <p>You have one day to reset your password, click on the following link:</p>
        <a href="{link}">Reset password</a>
        <p>If you did not request a password reset, please ignore this email.</p>
    </body>
    </html>
    """)

def new_user_verification_template(link):
    return _(f"""
    <html>
    <body>
        <p>Hello,</p>
        <p>Thank you for registering, click on the following link to verify your account:</p>
        <a href="{link}">Verify account</a>
    </body>
    </html>
    """)