"""
Script de prueba para envio de correo
Ejecutar: python test_email.py
"""

import logging
import sys

# Configurar logging detallado
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Configurar encoding UTF-8 en Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

print("=" * 60)
print("TEST DE ENVIO DE CORREO - URGENCIAS")
print("=" * 60)

try:
    from email_sender import EmailSender, enviar_reporte_por_correo
    from dotenv import load_dotenv
    import os

    load_dotenv()

    # Mostrar configuracion (sin password)
    print(f"\nConfiguracion:")
    print(f"  SMTP Server: {os.getenv('SMTP_SERVER')}")
    print(f"  SMTP Port: {os.getenv('SMTP_PORT')}")
    print(f"  SMTP User: {os.getenv('SMTP_USER')}")
    print(f"  From Email: {os.getenv('SMTP_FROM_EMAIL')}")
    print(f"  Destinatarios: {os.getenv('EMAIL_DESTINATARIOS')}")

    # Crear cliente
    print("\n1. Creando cliente SMTP...")
    sender = EmailSender()
    print("   OK - Cliente creado")

    # Obtener destinatarios
    destinatarios_str = os.getenv("EMAIL_DESTINATARIOS", "")
    destinatarios = [e.strip() for e in destinatarios_str.split(",") if e.strip()]

    if not destinatarios:
        print("\nERROR: No hay destinatarios configurados")
        sys.exit(1)

    print(f"\n2. Enviando correo de prueba a {len(destinatarios)} destinatario(s)...")

    # Crear un HTML de prueba simple
    test_html = "test_email_temp.html"
    with open(test_html, "w", encoding="utf-8") as f:
        f.write("""
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<h1>Correo de Prueba - Auditoria Urgencias</h1>
<p>Este es un correo de prueba del sistema de auditoria.</p>
<p>Si recibes este correo, el envio funciona correctamente.</p>
</body>
</html>
        """)

    # Enviar
    resultado = sender.enviar_reporte_auditoria(
        destinatarios=destinatarios,
        html_path=test_html,
        fecha_reporte="2025-12-01 (PRUEBA)"
    )

    # Limpiar archivo temporal
    if os.path.exists(test_html):
        os.remove(test_html)

    print("\n" + "=" * 60)
    if resultado:
        print("RESULTADO: EXITO - Correo enviado")
        print("Revisa tu bandeja de entrada y SPAM")
    else:
        print("RESULTADO: FALLO - El correo no se envio")
    print("=" * 60)

except Exception as e:
    print(f"\nERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
