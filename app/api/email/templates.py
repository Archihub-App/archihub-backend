def forgot_password_template(link):
    return f"""
    <html>
    <body>
        <p>Hola,</p>
        <p>Tienes un día para restablecer tu contraseña, haz clic en el siguiente enlace:</p>
        <a href="{link}">Restablecer contraseña</a>
        <p>Si no solicitaste un restablecimiento de contraseña, ignora este correo.</p>
    </body>
    </html>
    """
    
def new_user_verification_template(link):
    return f"""
    <html>
    <body>
        <p>Hola,</p>
        <p>Gracias por registrarte, haz clic en el siguiente enlace para verificar tu cuenta:</p>
        <a href="{link}">Verificar cuenta</a>
    </body>
    </html>
    """