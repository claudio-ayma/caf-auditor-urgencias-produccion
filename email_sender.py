"""
Módulo de Envío de Correos para Sistema de Auditoría de URGENCIAS
Envía reportes de auditoría por correo electrónico con archivos adjuntos
"""

import os
import logging
import smtplib
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from email.utils import formataddr
from email.policy import SMTP as SMTP_POLICY
from typing import List, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class EmailSender:
    """Cliente SMTP para envío de reportes de auditoría de urgencias"""

    def __init__(self):
        """Inicializa configuración SMTP desde variables de entorno"""
        self.smtp_server = os.getenv("SMTP_SERVER", "mail.correo-caf.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.smtp_user = os.getenv("SMTP_USER")

        # Leer contraseña desde .env
        password_raw = os.getenv("SMTP_PASSWORD")
        if password_raw:
            # Eliminar comillas si las tiene (por si acaso)
            password_raw = password_raw.strip('"').strip("'")
            # La contraseña ya está en UTF-8 si el archivo .env está en UTF-8
            self.smtp_password = password_raw
        else:
            self.smtp_password = None

        self.from_email = os.getenv("SMTP_FROM_EMAIL", self.smtp_user)
        self.from_name = os.getenv("SMTP_FROM_NAME", "Sistema de Auditoría CAF")

        # Validar credenciales
        if not self.smtp_user or not self.smtp_password:
            raise ValueError(
                "SMTP_USER y SMTP_PASSWORD son requeridos en .env para enviar correos"
            )

        logger.info(f"Cliente SMTP configurado: {self.smtp_server}:{self.smtp_port}")

    def enviar_reporte_auditoria(
        self,
        destinatarios: List[str],
        jsonl_path: Optional[str] = None,
        html_path: Optional[str] = None,
        tracking_path: Optional[str] = None,
        log_path: Optional[str] = None,
        fecha_reporte: Optional[str] = None
    ) -> bool:
        """
        Envía reporte de auditoría por correo con archivos adjuntos

        Args:
            destinatarios: Lista de correos electrónicos destinatarios
            jsonl_path: Ruta del archivo JSONL (datos)
            html_path: Ruta del archivo HTML (reporte visual)
            tracking_path: Ruta del archivo de tracking
            log_path: Ruta del archivo de log
            fecha_reporte: Fecha del reporte (YYYY-MM-DD), si None usa fecha actual

        Returns:
            True si se envió exitosamente, False en caso contrario
        """
        if not destinatarios:
            logger.warning("No hay destinatarios configurados, saltando envío de correo")
            return False

        try:
            # Crear mensaje con policy que maneja UTF-8
            msg = MIMEMultipart('alternative', policy=SMTP_POLICY)

            # Configurar headers con encoding UTF-8 correcto
            msg['From'] = formataddr((self.from_name, self.from_email))
            msg['To'] = ", ".join(destinatarios)
            msg['Subject'] = self._generar_asunto(fecha_reporte)

            # Cuerpo del correo (HTML + texto plano)
            html_body = self._generar_cuerpo_html(
                jsonl_path, html_path, tracking_path, log_path, fecha_reporte
            )
            text_body = self._generar_cuerpo_texto(fecha_reporte)

            msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            # Adjuntar archivos - SOLO HTML (más liviano y práctico)
            archivos_adjuntos = []

            if html_path and os.path.exists(html_path):
                self._adjuntar_archivo(msg, html_path)
                archivos_adjuntos.append(os.path.basename(html_path))
            else:
                logger.warning("No se encontró archivo HTML para adjuntar")
                return False

            # Los archivos JSONL, tracking y logs están disponibles en el servidor
            # No es necesario enviarlos por correo (reducir tamaño del email)

            # Enviar correo
            logger.info(f"Enviando correo a {len(destinatarios)} destinatario(s)...")
            logger.info(f"Archivos adjuntos: {len(archivos_adjuntos)}")

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.set_debuglevel(0)  # Cambiar a 1 para debug detallado

                # FIX: Usar AUTH PLAIN que maneja mejor caracteres especiales
                try:
                    # Intento 1: login() normal
                    server.login(self.smtp_user, self.smtp_password)
                    logger.info("Autenticacion SMTP exitosa (login normal)")
                except (UnicodeEncodeError, UnicodeDecodeError, smtplib.SMTPAuthenticationError) as auth_err:
                    # Intento 2: AUTH LOGIN manual con base64
                    logger.warning(f"Login normal fallo ({type(auth_err).__name__}), intentando AUTH LOGIN manual...")

                    # Codificar credenciales en base64 (UTF-8)
                    user_b64 = base64.b64encode(self.smtp_user.encode('utf-8')).decode('ascii')
                    pass_b64 = base64.b64encode(self.smtp_password.encode('utf-8')).decode('ascii')

                    # AUTH LOGIN con verificacion de respuestas
                    code, resp = server.docmd("AUTH LOGIN")
                    if code != 334:
                        raise smtplib.SMTPAuthenticationError(code, f"AUTH LOGIN fallo: {resp}")

                    code, resp = server.docmd(user_b64)
                    if code != 334:
                        raise smtplib.SMTPAuthenticationError(code, f"Usuario rechazado: {resp}")

                    code, resp = server.docmd(pass_b64)
                    if code != 235:
                        raise smtplib.SMTPAuthenticationError(code, f"Password rechazado: {resp}")

                    logger.info("Autenticacion SMTP exitosa (AUTH LOGIN manual)")

                # Enviar usando sendmail para mejor compatibilidad
                server.sendmail(
                    self.from_email,
                    destinatarios,
                    msg.as_string()
                )

            logger.info(f"Correo enviado exitosamente")
            for dest in destinatarios:
                logger.info(f"  - {dest}")

            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("Error de autenticación SMTP - Verificar usuario/contraseña")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"Error SMTP al enviar correo: {e}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado al enviar correo: {e}")
            return False

    def _adjuntar_archivo(self, msg: MIMEMultipart, file_path: str):
        """Adjunta un archivo al mensaje de correo"""
        try:
            with open(file_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())

            encoders.encode_base64(part)

            # Codificar nombre de archivo correctamente para UTF-8
            filename = os.path.basename(file_path)
            part.add_header(
                'Content-Disposition',
                'attachment',
                filename=('utf-8', '', filename)
            )
            msg.attach(part)
            logger.debug(f"Archivo adjuntado: {filename}")

        except Exception as e:
            logger.warning(f"No se pudo adjuntar {file_path}: {e}")

    def _generar_asunto(self, fecha_reporte: Optional[str]) -> str:
        """Genera el asunto del correo"""
        if not fecha_reporte:
            fecha_reporte = datetime.now().strftime("%Y-%m-%d")

        return f"Reporte de Auditoria de Urgencias - {fecha_reporte}"

    def _generar_cuerpo_texto(self, fecha_reporte: Optional[str]) -> str:
        """Genera cuerpo del correo en texto plano"""
        if not fecha_reporte:
            fecha_reporte = datetime.now().strftime("%Y-%m-%d")

        return f"""
Reporte de Auditoria de Urgencias
Clinica Foianini - {fecha_reporte}

Este correo contiene los reportes de auditoria de las atenciones de urgencias.

Archivos adjuntos:
- HTML: Reporte visual interactivo

Para visualizar el reporte completo, abra el archivo HTML adjunto en su navegador.

Los archivos JSONL, tracking y logs estan disponibles en MinIO.

---
Sistema de Auditoria Automatizada
Clinica Foianini
        """.strip()

    def _generar_cuerpo_html(
        self,
        jsonl_path: Optional[str],
        html_path: Optional[str],
        tracking_path: Optional[str],
        log_path: Optional[str],
        fecha_reporte: Optional[str]
    ) -> str:
        """Genera cuerpo del correo en HTML"""
        if not fecha_reporte:
            fecha_reporte = datetime.now().strftime("%Y-%m-%d")

        # Calcular tamaño del archivo HTML
        archivo_info = "Reporte HTML interactivo"
        if html_path and os.path.exists(html_path):
            size_mb = os.path.getsize(html_path) / (1024 * 1024)
            archivo_info = f'Reporte HTML interactivo ({size_mb:.2f} MB)'

        return f"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reporte de Auditoria</title>
    <style>
        /* Reset y base */
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #1f2937;
            background-color: #f3f4f6;
            padding: 0;
            margin: 0;
        }}

        /* Contenedor principal - ESTILO MOBILE COMPACTO */
        .email-wrapper {{
            max-width: 600px;
            margin: 0 auto;
            background-color: #f3f4f6;
            padding: 20px 10px;
        }}

        .container {{
            background-color: #ffffff;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }}

        /* Header con gradiente - COLOR DIFERENTE PARA URGENCIAS (verde-azul) */
        .header {{
            background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%);
            color: white;
            text-align: center;
            padding: 30px 20px;
        }}

        .header h1 {{
            font-size: 22px;
            font-weight: 700;
            margin: 0 0 8px 0;
        }}

        .header p {{
            font-size: 14px;
            opacity: 0.95;
            margin: 0;
        }}

        /* Contenido */
        .content {{
            padding: 24px 20px;
        }}

        .content p {{
            margin: 0 0 16px 0;
            color: #4b5563;
            font-size: 15px;
        }}

        /* Caja destacada - COLOR URGENCIAS */
        .highlight {{
            background: linear-gradient(135deg, #ccfbf1 0%, #99f6e4 100%);
            border-left: 4px solid #0d9488;
            padding: 16px;
            border-radius: 8px;
            margin: 20px 0;
        }}

        .highlight strong {{
            color: #0f766e;
            display: block;
            margin-bottom: 6px;
            font-size: 15px;
        }}

        .highlight p {{
            margin: 0;
            color: #1f2937;
            font-size: 14px;
        }}

        /* Seccion de archivo adjunto */
        .attachment-box {{
            background-color: #f9fafb;
            border: 2px dashed #d1d5db;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            margin: 20px 0;
        }}

        .attachment-box h3 {{
            color: #0f766e;
            font-size: 16px;
            margin: 0 0 12px 0;
        }}

        .attachment-icon {{
            font-size: 36px;
            margin-bottom: 8px;
        }}

        .attachment-info {{
            color: #6b7280;
            font-size: 14px;
        }}

        /* Lista de caracteristicas */
        .features {{
            margin: 24px 0;
        }}

        .features h3 {{
            color: #1f2937;
            font-size: 16px;
            margin: 0 0 12px 0;
        }}

        .features ul {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}

        .features li {{
            padding: 8px 0 8px 28px;
            position: relative;
            color: #4b5563;
            font-size: 14px;
            line-height: 1.5;
        }}

        .features li:before {{
            content: "OK";
            position: absolute;
            left: 0;
            color: #10b981;
            font-weight: bold;
            font-size: 12px;
        }}

        /* Footer */
        .footer {{
            background-color: #f9fafb;
            text-align: center;
            padding: 20px;
            border-top: 1px solid #e5e7eb;
        }}

        .footer p {{
            margin: 4px 0;
            color: #6b7280;
            font-size: 13px;
        }}

        /* Responsive para pantallas muy pequenas */
        @media only screen and (max-width: 480px) {{
            .email-wrapper {{
                padding: 10px 5px;
            }}

            .content {{
                padding: 20px 16px;
            }}

            .header h1 {{
                font-size: 20px;
            }}
        }}
    </style>
</head>
<body>
    <div class="email-wrapper">
        <div class="container">
            <!-- Header -->
            <div class="header">
                <h1>Reporte de Auditoria de Urgencias</h1>
                <p>Clinica Foianini - {fecha_reporte}</p>
            </div>

            <!-- Contenido -->
            <div class="content">
                <p>Estimado/a,</p>

                <p>Se ha completado la auditoria medica automatizada de las atenciones de <strong>urgencias</strong> del dia <strong>{fecha_reporte}</strong>.</p>

                <!-- Destacado -->
                <div class="highlight">
                    <strong>Como visualizar el reporte</strong>
                    <p>Descargue el archivo HTML adjunto y abralo en su navegador web para acceder al reporte completo con filtros interactivos por medico.</p>
                </div>

                <!-- Archivo adjunto -->
                <div class="attachment-box">
                    <div class="attachment-icon">HTML</div>
                    <h3>Archivo Adjunto</h3>
                    <div class="attachment-info">
                        {archivo_info}
                    </div>
                </div>

                <!-- Caracteristicas del reporte -->
                <div class="features">
                    <h3>El reporte incluye:</h3>
                    <ul>
                        <li>Evaluacion segun guias clinicas internacionales (WHO, AHA, NICE, ERC, ACEP, ACS)</li>
                        <li>Scores de calidad (0-100) por cada atencion</li>
                        <li>Criterios cumplidos y no cumplidos</li>
                        <li>Hallazgos criticos y recomendaciones especificas</li>
                        <li>Resumen ejecutivo agrupado por medico</li>
                        <li>Filtros interactivos para analisis detallado</li>
                    </ul>
                </div>
            </div>

            <!-- Footer -->
            <div class="footer">
                <p><strong>Sistema de Auditoria Automatizada - URGENCIAS</strong></p>
                <p>Clinica Foianini</p>
                <p>Este es un correo automatico - No responder</p>
            </div>
        </div>
    </div>
</body>
</html>
        """.strip()


# --- Función Helper para uso en main.py ---

def enviar_reporte_por_correo(
    jsonl_path: Optional[str] = None,
    html_path: Optional[str] = None,
    tracking_path: Optional[str] = None,
    log_path: Optional[str] = None,
    fecha_reporte: Optional[str] = None
) -> bool:
    """
    Función helper para enviar reporte de auditoría de urgencias por correo

    Args:
        jsonl_path: Ruta del archivo JSONL
        html_path: Ruta del archivo HTML
        tracking_path: Ruta del archivo de tracking
        log_path: Ruta del archivo de log
        fecha_reporte: Fecha del reporte (YYYY-MM-DD)

    Returns:
        True si se envió exitosamente, False en caso contrario
    """
    try:
        # Obtener destinatarios desde .env
        destinatarios_str = os.getenv("EMAIL_DESTINATARIOS", "")
        if not destinatarios_str:
            logger.warning("No hay destinatarios configurados (EMAIL_DESTINATARIOS en .env)")
            return False

        # Parsear lista de destinatarios (separados por coma)
        destinatarios = [email.strip() for email in destinatarios_str.split(",") if email.strip()]

        if not destinatarios:
            logger.warning("Lista de destinatarios vacía")
            return False

        # Crear cliente de correo y enviar
        email_sender = EmailSender()
        return email_sender.enviar_reporte_auditoria(
            destinatarios=destinatarios,
            jsonl_path=jsonl_path,
            html_path=html_path,
            tracking_path=tracking_path,
            log_path=log_path,
            fecha_reporte=fecha_reporte
        )

    except Exception as e:
        logger.error(f"Error en enviar_reporte_por_correo: {e}")
        return False


# --- Testing ---

if __name__ == "__main__":
    # Configurar encoding UTF-8 en Windows
    import sys
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except:
            pass

    # Configurar logging para testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("Test de envio de correo")
    print("=" * 80)

    # Test de configuración
    try:
        sender = EmailSender()
        print(f"Cliente SMTP configurado correctamente")
        print(f"   Servidor: {sender.smtp_server}:{sender.smtp_port}")
        print(f"   Usuario: {sender.smtp_user}")
        print(f"   Remitente: {sender.from_name} <{sender.from_email}>")
    except Exception as e:
        print(f"Error al configurar cliente SMTP: {e}")
        print("\nVerifica que las siguientes variables esten en .env:")
        print("   SMTP_USER, SMTP_PASSWORD, EMAIL_DESTINATARIOS")
