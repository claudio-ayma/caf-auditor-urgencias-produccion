"""
Cliente MinIO para almacenamiento de archivos de auditoría de URGENCIAS
Permite subir archivos de auditoría (JSONL, HTML, JSON, logs) a MinIO
"""

import os
import logging
from datetime import datetime
from typing import Optional, List
from minio import Minio
from minio.error import S3Error
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

logger = logging.getLogger(__name__)


class MinIOClient:
    """Cliente para interactuar con MinIO y almacenar archivos de auditoría de urgencias"""

    def __init__(self):
        """Inicializa conexión con MinIO usando variables de entorno"""
        # Configuración desde .env
        self.endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        self.access_key = os.getenv("MINIO_ACCESS_KEY")
        self.secret_key = os.getenv("MINIO_SECRET_KEY")
        self.bucket_name = os.getenv("MINIO_BUCKET_NAME", "auditoria-urgencias")
        self.use_ssl = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

        # Validar credenciales
        if not self.access_key or not self.secret_key:
            raise ValueError(
                "MINIO_ACCESS_KEY y MINIO_SECRET_KEY son requeridos en el archivo .env"
            )

        # Crear cliente MinIO
        try:
            self.client = Minio(
                endpoint=self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.use_ssl
            )
            logger.info(f"Cliente MinIO inicializado: {self.endpoint}")

            # Verificar/crear bucket
            self._ensure_bucket_exists()

        except Exception as e:
            logger.error(f"Error al inicializar cliente MinIO: {e}")
            raise

    def _ensure_bucket_exists(self):
        """Verifica que el bucket exista, si no lo crea"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                logger.info(f"Creando bucket: {self.bucket_name}")
                self.client.make_bucket(self.bucket_name)
                logger.info(f"Bucket creado: {self.bucket_name}")
            else:
                logger.info(f"Bucket existe: {self.bucket_name}")
        except S3Error as e:
            logger.error(f"Error al verificar/crear bucket: {e}")
            raise

    def upload_file(
        self,
        file_path: str,
        object_name: Optional[str] = None,
        metadata: Optional[dict] = None,
        prefix: Optional[str] = None
    ) -> bool:
        """
        Sube un archivo a MinIO

        Args:
            file_path: Ruta local del archivo a subir
            object_name: Nombre del objeto en MinIO (si None, usa nombre del archivo)
            metadata: Metadatos adicionales para el archivo
            prefix: Prefijo/carpeta para organizar archivos (ej: "20251114/")

        Returns:
            True si se subió exitosamente, False en caso contrario
        """
        if not os.path.exists(file_path):
            logger.error(f"Archivo no encontrado: {file_path}")
            return False

        # Si no se especifica object_name, usar nombre del archivo
        if object_name is None:
            object_name = os.path.basename(file_path)

        # Agregar prefijo si se especifica (para organizar en carpetas)
        if prefix:
            # Asegurar que el prefijo termine con /
            if not prefix.endswith('/'):
                prefix += '/'
            object_name = prefix + object_name

        try:
            # Obtener tamaño del archivo
            file_size = os.path.getsize(file_path)

            # Determinar content type basado en extensión
            content_type = self._get_content_type(file_path)

            # Agregar metadata default
            if metadata is None:
                metadata = {}

            metadata.update({
                "upload_date": datetime.now().isoformat(),
                "original_path": file_path,
                "file_size": str(file_size)
            })

            # Subir archivo
            logger.info(f"Subiendo archivo a MinIO: {file_path} -> {object_name}")
            self.client.fput_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                file_path=file_path,
                content_type=content_type,
                metadata=metadata
            )

            logger.info(f"Archivo subido exitosamente: {object_name} ({file_size} bytes)")
            return True

        except S3Error as e:
            logger.error(f"Error al subir archivo a MinIO: {e}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado al subir archivo: {e}")
            return False

    def upload_multiple_files(self, file_paths: List[str], prefix: Optional[str] = None) -> dict:
        """
        Sube múltiples archivos a MinIO

        Args:
            file_paths: Lista de rutas de archivos a subir
            prefix: Prefijo/carpeta para organizar archivos (ej: "20251114/")

        Returns:
            Diccionario con resultados: {"exitosos": [...], "fallidos": [...]}
        """
        results = {
            "exitosos": [],
            "fallidos": []
        }

        for file_path in file_paths:
            success = self.upload_file(file_path, prefix=prefix)
            if success:
                results["exitosos"].append(file_path)
            else:
                results["fallidos"].append(file_path)

        logger.info(
            f"Carga múltiple completada: "
            f"{len(results['exitosos'])} exitosos, "
            f"{len(results['fallidos'])} fallidos"
        )

        return results

    def _get_content_type(self, file_path: str) -> str:
        """Determina el content type basado en la extensión del archivo"""
        extension = os.path.splitext(file_path)[1].lower()

        content_types = {
            ".html": "text/html",
            ".json": "application/json",
            ".jsonl": "application/jsonl",
            ".log": "text/plain",
            ".txt": "text/plain",
            ".pdf": "application/pdf",
            ".csv": "text/csv"
        }

        return content_types.get(extension, "application/octet-stream")

    def list_files(self, prefix: Optional[str] = None) -> List[str]:
        """
        Lista archivos en el bucket

        Args:
            prefix: Prefijo para filtrar archivos (opcional)

        Returns:
            Lista de nombres de archivos
        """
        try:
            objects = self.client.list_objects(
                bucket_name=self.bucket_name,
                prefix=prefix,
                recursive=True
            )

            file_list = [obj.object_name for obj in objects]
            logger.info(f"Archivos encontrados: {len(file_list)}")
            return file_list

        except S3Error as e:
            logger.error(f"Error al listar archivos: {e}")
            return []

    def get_file_url(self, object_name: str, expires_hours: int = 24) -> Optional[str]:
        """
        Genera URL presignada para acceder al archivo

        Args:
            object_name: Nombre del objeto en MinIO
            expires_hours: Horas de validez de la URL

        Returns:
            URL presignada o None si hay error
        """
        try:
            from datetime import timedelta
            url = self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=timedelta(hours=expires_hours)
            )
            return url
        except S3Error as e:
            logger.error(f"Error al generar URL presignada: {e}")
            return None


# --- Función Helper para uso fácil ---

def upload_auditoria_files(
    jsonl_path: Optional[str] = None,
    html_path: Optional[str] = None,
    tracking_path: Optional[str] = None,
    log_path: Optional[str] = None,
    fecha_carpeta: Optional[str] = None
) -> dict:
    """
    Función helper para subir archivos de auditoría de urgencias a MinIO

    Args:
        jsonl_path: Ruta del archivo JSONL
        html_path: Ruta del archivo HTML
        tracking_path: Ruta del archivo de tracking
        log_path: Ruta del archivo de log
        fecha_carpeta: Fecha en formato YYYYMMDD para organizar en carpetas (ej: "20251114")

    Returns:
        Diccionario con resultados de carga
    """
    try:
        minio_client = MinIOClient()

        files_to_upload = []
        if jsonl_path and os.path.exists(jsonl_path):
            files_to_upload.append(jsonl_path)
        if html_path and os.path.exists(html_path):
            files_to_upload.append(html_path)
        if tracking_path and os.path.exists(tracking_path):
            files_to_upload.append(tracking_path)
        if log_path and os.path.exists(log_path):
            files_to_upload.append(log_path)

        if not files_to_upload:
            logger.warning("No hay archivos para subir a MinIO")
            return {"exitosos": [], "fallidos": []}

        # Construir prefijo con fecha si se proporciona
        prefix = f"{fecha_carpeta}/" if fecha_carpeta else None

        if prefix:
            logger.info(f"Organizando archivos en MinIO bajo la carpeta: {prefix}")

        results = minio_client.upload_multiple_files(files_to_upload, prefix=prefix)
        return results

    except Exception as e:
        logger.error(f"Error en upload_auditoria_files: {e}")
        return {"exitosos": [], "fallidos": files_to_upload if 'files_to_upload' in locals() else []}


# --- Testing ---

if __name__ == "__main__":
    # Configurar logging para testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        # Test de conexión
        print("Probando conexión con MinIO...")
        client = MinIOClient()

        # Listar archivos
        print("\nArchivos en bucket:")
        files = client.list_files()
        if files:
            for f in files[:10]:  # Mostrar máximo 10
                print(f"  - {f}")
        else:
            print("  (bucket vacío)")

        print("\nTest completado exitosamente")

    except Exception as e:
        print(f"\nError en test: {e}")
