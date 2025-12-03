import os
import json
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any
import litellm
import pymysql
from pymysql.cursors import DictCursor

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'logs/auditoria_{datetime.now():%Y%m%d}.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- 1. Modelo de Datos Pydantic para Auditoría de Urgencia ---

class AuditoriaUrgenciaResultado(BaseModel):
    id_medico: int = Field(description="ID del médico auditado")
    nombre_medico: str = Field(description="Nombre completo del médico")
    id_persona_paciente: int = Field(description="ID del paciente atendido")
    nombre_paciente: str = Field(description="Nombre completo del paciente")  # NUEVO
    id_evolucion: int = Field(description="ID de la evolución clínica")
    fecha_atencion: str = Field(description="Fecha y hora de la atención")
    cuenta_gestion: int = Field(description="Año de gestión de la cuenta")  # NUEVO
    cuenta_internacion: int = Field(description="Número de internación")  # NUEVO
    diagnostico_urgencia: str = Field(description="Diagnóstico registrado en urgencias")

    cumple_guias: str = Field(description="Sí/No - Cumplimiento de guías internacionales")
    score_calidad: int = Field(
        ge=0, le=100,
        description="Puntuación 0-100 sobre adherencia a guías internacionales"
    )

    guias_aplicables: List[str] = Field(
        description="Lista de guías internacionales aplicables (WHO, AHA, NICE, ERC, etc.)"
    )
    criterios_cumplidos: List[str] = Field(
        description="Criterios de guías internacionales que SÍ cumple"
    )
    criterios_no_cumplidos: List[str] = Field(
        description="Criterios de guías internacionales que NO cumple"
    )

    tratamiento_adecuado: str = Field(
        description="Evaluación del tratamiento según guías"
    )
    tiempo_atencion: str = Field(
        description="Evaluación de la oportunidad de la atención"
    )
    estudios_solicitados: str = Field(
        description="Evaluación de estudios complementarios solicitados"
    )
    medicacion_apropiada: str = Field(
        description="Evaluación de la medicación prescrita (dosis, vía, indicación)"
    )

    hallazgos_criticos: List[str] = Field(
        description="Hallazgos importantes o alertas identificadas"
    )
    recomendaciones: List[str] = Field(
        description="Sugerencias de mejora basadas en guías internacionales"
    )
    comentarios_adicionales: str = Field(
        description="Contexto adicional relevante para la auditoría"
    )


# --- 2. Componente: Cliente MySQL ---

class MCPClient:
    """Cliente para interactuar con MySQL"""
    def __init__(self, query_dir: str = "queries"):
        self.query_dir = query_dir
        self.connection = None
        self._connect()

    def _connect(self):
        """Establece conexión con MySQL"""
        try:
            self.connection = pymysql.connect(
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DATABASE"),
                cursorclass=DictCursor,
                connect_timeout=10
            )

            # CRÍTICO: Aumentar límite de GROUP_CONCAT para capturar evoluciones completas
            # El límite por defecto (1024 bytes) trunca las evoluciones clínicas con JSON
            # Configurar a 10MB (10485760 bytes) para manejar historiales extensos
            with self.connection.cursor() as cursor:
                cursor.execute("SET SESSION group_concat_max_len = 10485760")

            logger.info("Conectado a MySQL (group_concat_max_len configurado a 10MB)")
        except Exception as e:
            logger.error(f"Error al conectar con MySQL: {e}")
            raise

    def _load_query(self, query_name: str) -> str:
        """Carga una plantilla de query SQL desde un archivo .sql."""
        path = os.path.join(self.query_dir, f"{query_name}.sql")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise ValueError(f"Archivo de query no encontrado en: {path}")

    def _execute_query(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """Ejecuta una query contra MySQL"""
        try:
            if not self.connection or not self.connection.open:
                self._connect()

            with self.connection.cursor() as cursor:
                cursor.execute(query)
                results = cursor.fetchall()
                return results if results else []

        except Exception as e:
            logger.error(f"Error al ejecutar query: {e}")
            return None

    def get_todas_atenciones_24h(self) -> Optional[List[Dict]]:
        """Obtiene TODAS las atenciones de urgencias de las últimas 24 horas"""
        query_template = self._load_query("get_todas_atenciones_24h")
        return self._execute_query(query_template)

    def get_detalle_atencion(
        self, persona_numero: int, cuenta_gestion: int, cuenta_internacion: int, cuenta_id: int
    ) -> Optional[Dict]:
        """Obtiene el detalle completo de una atención de urgencias específica"""
        query_template = self._load_query("get_detalle_atencion")
        query_sql = query_template.format(
            persona_numero=persona_numero,
            cuenta_gestion=cuenta_gestion,
            cuenta_internacion=cuenta_internacion,
            cuenta_id=cuenta_id
        )
        results = self._execute_query(query_sql)

        if results and len(results) > 0:
            return results[0]
        return None

    def __del__(self):
        """Cierra la conexión al destruir el objeto"""
        if self.connection and self.connection.open:
            self.connection.close()


# --- 3. Componente: Auditor LLM con OpenRouter ---

class AuditorLLM:
    """Auditor médico usando Claude Sonnet 4.5/4 a través de OpenRouter con LiteLLM"""
    def __init__(self, reintentos: int = 3):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = self.api_key

        model_base = os.getenv("DEFAULT_MODEL")
        model_fallback = os.getenv("FALLBACK_MODEL")

        self.model_principal = f"openrouter/{model_base}"
        self.model_fallback = f"openrouter/{model_fallback}"
        self.reintentos = reintentos

        litellm.drop_params = True
        litellm.set_verbose = False

    def auditar_atencion(
        self,
        historial: str,
        id_evolucion: int,
        fecha_atencion: str,
        diagnostico: str,
        id_persona: int,
        id_medico: int,
        nombre_medico: str,
        nombre_paciente: str,  # NUEVO
        cuenta_gestion: int,   # NUEVO
        cuenta_internacion: int  # NUEVO
    ) -> Optional[AuditoriaUrgenciaResultado]:
        """Audita una atención de urgencias según guías internacionales"""

        prompt_sistema = """
        Eres un experto auditor médico especializado en medicina de urgencias.
        Tu tarea es evaluar si la atención de urgencias proporcionada cumple con guías clínicas
        internacionales reconocidas como:
        - WHO (World Health Organization)
        - AHA (American Heart Association)
        - NICE (National Institute for Health and Care Excellence)
        - ERC (European Resuscitation Council)
        - ACS (American College of Surgeons)
        - ACEP (American College of Emergency Physicians)

        ⚠️ CONTEXTO DE URGENCIAS:
        - Este servicio atiende casos de menor complejidad que emergencias críticas
        - Los tiempos de respuesta pueden ser ligeramente más flexibles que en emergencias
        - Sin embargo, se deben seguir las mismas guías internacionales
        - Evaluar según estándares de calidad apropiados para urgencias

        ⚠️ IMPORTANTE - SOLO EVALÚA EL ACTO MÉDICO, NO LA DOCUMENTACIÓN:

        ✅ SÍ EVALÚA (Acto médico clínico):
        - ¿Se administró el tratamiento correcto según guías? (dosis, vía, medicamento)
        - ¿Se solicitaron los estudios clínicos necesarios? (laboratorios, imágenes)
        - ¿El diagnóstico fue correcto y oportuno según la presentación?
        - ¿Los tiempos de atención cumplieron con lo recomendado?
        - ¿Se realizaron los procedimientos clínicos necesarios?
        - ¿Se dio el seguimiento clínico apropiado?
        - ¿Se prescribieron medicamentos ambulatorios necesarios?

        ❌ NO EVALÚES (Documentación):
        - Si algo está "documentado" o "registrado" en notas
        - Si se "escribió" o no algo en el expediente
        - Completitud de formularios o registros
        - Calidad del llenado de documentación

        REGLA DE ORO:
        - Si una acción clínica aparece en el historial (ej: "se administró adrenalina"), ASUME que se realizó
        - Si NO aparece en el historial, ASUME que NO se realizó
        - Evalúa si lo que se HIZO fue correcto según guías, no si se documentó bien

        ⚠️ IMPORTANTE - INTERPRETACIÓN DE LABORATORIOS:
        El historial tiene DOS secciones de laboratorios:
        1. "RESULTADOS DE LABORATORIO": Laboratorios con resultados ya disponibles
        2. "SOLICITUDES DE LABORATORIO (ÓRDENES MÉDICAS)": TODOS los laboratorios solicitados (con o sin resultado)

        REGLAS DE INTERPRETACIÓN:
        - Si un laboratorio aparece en "SOLICITUDES DE LABORATORIO", el médico SÍ LO SOLICITÓ
        - Un laboratorio puede estar SOLICITADO pero sin resultado aún (ej: urocultivo tarda 48-72h)
        - NO digas "no se solicitó X" si X aparece en la sección de SOLICITUDES
        - La sección de SOLICITUDES es la fuente de verdad sobre qué ordenó el médico
        - La sección de RESULTADOS solo muestra los que ya tienen valores

        Ejemplo correcto:
        - Si ves "Estudio: UROCULTIVO | Fecha solicitud: 2025-12-01" en SOLICITUDES → SÍ se solicitó
        - Aunque no aparezca en RESULTADOS (porque tarda días), el médico SÍ cumplió con solicitarlo

        Solo evalúa como "no solicitado" si el estudio NO aparece en NINGUNA de las dos secciones.

        ⚠️ IMPORTANTE - INTERPRETACIÓN DE TIEMPOS DE OBSERVACIÓN E INTERNACIÓN:
        - Si el paciente fue INTERNADO (pasó a piso/hospitalización), la observación CONTINÚA en internación
        - NO penalices "tiempo insuficiente en urgencias" si hubo decisión de internación
        - Ejemplo: Guías recomiendan 6 horas de observación → 2h en urgencias + internación = CUMPLE (observación continúa)
        - La internación es una decisión CORRECTA para observación prolongada
        - Solo evalúa el tiempo en urgencias si el paciente fue dado de ALTA a domicilio directamente
        - Frases clave que indican internación: "INDICA INTERNACIÓN", "PASA A PISO", "TRASLADO A PISO", "INGRESA A PISO"
        """

        prompt_usuario = f"""
        Analiza la siguiente atención de urgencias y auditala según guías médicas internacionales.

        **Información de la atención:**
        - ID Evolución: {id_evolucion}
        - Fecha de atención: {fecha_atencion}
        - Diagnóstico registrado: {diagnostico}
        - ID Paciente: {id_persona}
        - ID Médico: {id_medico}
        - Médico tratante: {nombre_medico}

        **Historial Clínico del Paciente:**
        {historial}

        **Instrucciones de Evaluación:**

        1. Identifica las guías internacionales que aplican al caso

        2. Evalúa el ACTO MÉDICO CLÍNICO:
           - Diagnóstico: ¿Fue oportuno y certero?
           - Estudios: ¿Los labs/imágenes solicitados fueron apropiados?
           - Tratamiento: ¿Medicamentos/procedimientos administrados correctos según guías?
           - Tiempos: ¿Cumplieron tiempos recomendados? (considerando contexto de urgencias)
           - Seguimiento: ¿El tiempo de observación fue adecuado?
           - Prescripción ambulatoria: ¿Se dieron los medicamentos/dispositivos necesarios al alta?

        3. Ejemplos de lo que SÍ debes evaluar:
           ✅ "Administró adrenalina 0.5mg cuando la dosis correcta es 0.3mg" (dosis incorrecta)
           ✅ "No prescribió EpiPen al alta en un caso de anafilaxia" (falta de tratamiento necesario)
           ✅ "Dio de alta a DOMICILIO a la 1 hora cuando se requieren 4-6 horas" (SOLO si NO fue internado)
           ✅ "No solicitó triptasa sérica en anafilaxia" (SOLO si NO aparece resultado de triptasa en la sección de laboratorios)
           ✅ "No refirió a alergología pese a ser la cuarta reacción" (falta de seguimiento especializado)
           ✅ "Faltó solicitar radiografía de tórax en neumonía" (SOLO si NO aparece resultado de RX tórax en estudios de imagen)

        4. Ejemplos de lo que NO debes evaluar:
           ❌ "No documentó la búsqueda de angioedema" → Si en el historial dice "con angioedema", asume que lo evaluó
           ❌ "Falta documentar criterios de anafilaxia" → Evalúa si el diagnóstico fue correcto, no si escribió los criterios
           ❌ "No se registró educación al paciente" → Evalúa si dio medicamentos necesarios, no si documentó la educación
           ❌ "Completitud de registros insuficiente" → No evalúes documentación
           ❌ "No se solicitó troponina" → Si el historial muestra "Servicio: Troponina I Cuantitativa", SÍ se solicitó
           ❌ "No se realizó hemograma" → Si el historial muestra "Servicio: HEMOGRAMA COMPLETO", SÍ se realizó
           ❌ "Tiempo de observación insuficiente en urgencias" → Si el historial muestra "INDICA INTERNACIÓN" o "PASA A PISO", la observación continúa en piso

        5. Asigna un score de calidad del acto médico (0-100)
        6. Identifica fortalezas y áreas de mejora EN LA PRÁCTICA CLÍNICA
        7. Recomendaciones para mejorar EL ACTO MÉDICO, no la documentación

        **IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido con esta estructura:**
        - cumple_guias: string ("Sí" o "No")
        - score_calidad: integer (0-100)
        - guias_aplicables: array de strings
        - criterios_cumplidos: array de strings
        - criterios_no_cumplidos: array de strings
        - tratamiento_adecuado: string
        - tiempo_atencion: string
        - estudios_solicitados: string
        - medicacion_apropiada: string
        - hallazgos_criticos: array de strings
        - recomendaciones: array de strings
        - comentarios_adicionales: string

        NO incluyas los campos id_medico, nombre_medico, id_persona_paciente, id_evolucion, fecha_atencion, diagnostico_urgencia, nombre_paciente, cuenta_gestion, cuenta_internacion.
        Responde SOLO con el JSON, sin texto adicional.
        """

        modelos = [self.model_principal, self.model_fallback]

        for modelo in modelos:
            for intento in range(self.reintentos):
                try:
                    response = litellm.completion(
                        model=modelo,
                        messages=[
                            {"role": "system", "content": prompt_sistema},
                            {"role": "user", "content": prompt_usuario}
                        ],
                        temperature=0.3,
                    )

                    content = response.choices[0].message.content.strip()
                    if content.startswith("```json"):
                        content = content[7:]
                    if content.startswith("```"):
                        content = content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                    data = json.loads(content)

                    # Agregar campos que conocemos
                    data["id_medico"] = id_medico
                    data["nombre_medico"] = nombre_medico
                    data["id_persona_paciente"] = id_persona
                    data["nombre_paciente"] = nombre_paciente
                    data["id_evolucion"] = id_evolucion
                    data["fecha_atencion"] = str(fecha_atencion)
                    data["cuenta_gestion"] = cuenta_gestion
                    data["cuenta_internacion"] = cuenta_internacion
                    data["diagnostico_urgencia"] = diagnostico or "Pendiente de codificación CIE-9"

                    return AuditoriaUrgenciaResultado(**data)

                except (Exception, ValidationError, json.JSONDecodeError) as e:
                    logger.warning(f"Intento {intento + 1}/{self.reintentos} fallido con {modelo}. Error: {e}")
                    time.sleep(2**intento)

            logger.warning(f"Fallaron todos los intentos con {modelo}, probando siguiente modelo...")

        logger.error(f"Fallaron todos los modelos para la evolución {id_evolucion}")
        return None


# --- 4. Función Auxiliar: Formateo de datos para LLM ---

def formatear_atencion_para_llm(detalle: Dict) -> str:
    """Formatea los datos de la atención en texto estructurado para el LLM"""
    import json as json_module

    # Parsear evoluciones clínicas
    evoluciones = []
    if detalle.get('evoluciones_clinicas'):
        evo_raw = detalle['evoluciones_clinicas']
        if '---EVOLUCION---' in evo_raw:
            evo_parts = evo_raw.split('\n---EVOLUCION---\n')
        else:
            evo_parts = [evo_raw]

        for evo_json in evo_parts:
            try:
                evoluciones.append(json_module.loads(evo_json))
            except json_module.JSONDecodeError:
                pass

    texto = f"""
=================================================================================
ATENCIÓN DE URGENCIAS - DETALLE COMPLETO
=================================================================================

INFORMACIÓN DE LA CUENTA:
- Paciente ID: {detalle.get('persona_numero')}
- Gestión: {detalle.get('cuenta_gestion')}
- Número de Internación: {detalle.get('cuenta_internacion')}
- ID de Cuenta: {detalle.get('cuenta_id')}

=================================================================================
EVOLUCIONES CLÍNICAS
=================================================================================
"""

    for i, evo in enumerate(evoluciones, 1):
        texto += f"\n--- Evolución #{i} ---\n"
        texto += f"Fecha: {evo.get('fecha', 'N/A')}\n"
        texto += f"Tipo: {evo.get('tipo_evento', 'N/A')}\n"
        texto += f"Profesional: {evo.get('profesional', 'N/A')}\n"

        if evo.get('diagnosticos'):
            texto += f"\nDiagnósticos CIE9:\n{evo['diagnosticos']}\n"
        if evo.get('comentario_clinico'):
            texto += f"\nComentario Clínico:\n{evo['comentario_clinico']}\n"
        if evo.get('plan_medico'):
            texto += f"\nPlan Médico:\n{evo['plan_medico']}\n"
        if evo.get('medicamentos_prescritos'):
            texto += f"\nMedicamentos Prescritos:\n{evo['medicamentos_prescritos']}\n"

        texto += "-" * 80 + "\n"

    if detalle.get('signos_vitales'):
        texto += f"""
=================================================================================
SIGNOS VITALES
=================================================================================
{detalle['signos_vitales']}

"""

    if detalle.get('ejecuciones_medicamentos'):
        texto += f"""
=================================================================================
EJECUCIONES DE MEDICAMENTOS (ENFERMERÍA)
=================================================================================
{detalle['ejecuciones_medicamentos']}

"""

    if detalle.get('notas_enfermeria'):
        texto += f"""
=================================================================================
NOTAS DE ENFERMERÍA
=================================================================================
{detalle['notas_enfermeria']}

"""

    if detalle.get('laboratorios'):
        texto += f"""
=================================================================================
RESULTADOS DE LABORATORIO
=================================================================================
{detalle['laboratorios']}

"""

    if detalle.get('estudios_imagen'):
        imagenes = detalle['estudios_imagen'].split('\n---IMAGEN---\n')
        texto += f"""
=================================================================================
ESTUDIOS DE IMAGEN
=================================================================================
"""
        for img in imagenes:
            texto += f"{img}\n{'-'*80}\n"

    if detalle.get('solicitudes_laboratorio'):
        texto += f"""
=================================================================================
SOLICITUDES DE LABORATORIO (ÓRDENES MÉDICAS)
=================================================================================
NOTA: Esta sección muestra TODOS los laboratorios SOLICITADOS por el médico,
independientemente de si ya tienen resultado. Un estudio que aparece aquí
FUE SOLICITADO aunque no tenga resultado en la sección anterior.

{detalle['solicitudes_laboratorio']}

"""

    texto += "=" * 80 + "\n"
    return texto


# --- 5. Componente: Gestor de Estado (simplificado para producción) ---

class GestorDeEstado:
    """Gestiona el estado del proceso de auditoría para permitir reanudación"""
    def __init__(self, archivo_estado: str):
        self.archivo_estado = archivo_estado
        self.estado = self._cargar_estado()

    def _cargar_estado(self) -> Dict:
        if not os.path.exists(self.archivo_estado):
            return {}
        try:
            with open(self.archivo_estado, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning(f"No se pudo leer '{self.archivo_estado}'. Se creará uno nuevo.")
            return {}

    def _guardar_estado(self):
        with open(self.archivo_estado, "w", encoding="utf-8") as f:
            json.dump(self.estado, f, indent=4, ensure_ascii=False)

    def marcar_pendiente(self, id_evolucion: int):
        """Marca una evolución como pendiente"""
        self.estado[str(id_evolucion)] = {"status": "pendiente"}
        self._guardar_estado()

    def marcar_completado(self, id_evolucion: int):
        """Marca una evolución como completada"""
        self.estado[str(id_evolucion)] = {"status": "completado"}
        self._guardar_estado()

    def marcar_fallido(self, id_evolucion: int, error: str = ""):
        """Marca una evolución como fallida"""
        self.estado[str(id_evolucion)] = {"status": "fallido", "error": error}
        self._guardar_estado()

    def esta_procesado(self, id_evolucion: int) -> bool:
        """Verifica si una evolución ya fue procesada"""
        return str(id_evolucion) in self.estado and \
               self.estado[str(id_evolucion)].get("status") == "completado"


# --- 6. Orquestador Principal de Producción ---

class OrquestadorAuditoriaProduccion:
    """Orquesta el proceso completo de auditoría diaria de urgencias"""
    def __init__(self, output_file: str, state_file: str):
        load_dotenv()
        self.mcp_client = MCPClient()
        self.auditor_llm = AuditorLLM()
        self.output_file = output_file
        self.gestor_estado = GestorDeEstado(archivo_estado=state_file)

    def run_auditoria_24h(self):
        """Ejecuta la auditoría de todas las atenciones de las últimas 24 horas"""
        logger.info("="*80)
        logger.info("INICIO DE AUDITORÍA DIARIA - URGENCIAS")
        logger.info("="*80)

        # 1. Obtener TODAS las atenciones de las últimas 24 horas
        logger.info("Obteniendo atenciones de las últimas 24 horas...")
        atenciones = self.mcp_client.get_todas_atenciones_24h()

        if not atenciones or len(atenciones) == 0:
            logger.warning("No se encontraron atenciones en las últimas 24 horas")
            return

        total_atenciones = len(atenciones)
        logger.info(f"Total de atenciones encontradas: {total_atenciones}")

        # 2. Agrupar por médico (para logs y estadísticas)
        medicos_map = {}
        for atencion in atenciones:
            medico_id = atencion['id_medico']
            if medico_id not in medicos_map:
                medicos_map[medico_id] = {
                    'nombre': atencion['nombre_medico'],
                    'atenciones': []
                }
            medicos_map[medico_id]['atenciones'].append(atencion)

        logger.info(f"Total de médicos que atendieron: {len(medicos_map)}")
        for medico_id, info in medicos_map.items():
            logger.info(f"  - {info['nombre']}: {len(info['atenciones'])} atenciones")

        # 3. Procesar cada atención
        logger.info("\nIniciando procesamiento de atenciones...")
        procesadas = 0
        fallidas = 0

        for idx, atencion in enumerate(atenciones, 1):
            # Crear ID único basado en la CUENTA (no en evolución)
            # Esto garantiza que cada atención se procese solo una vez
            id_unico = f"{atencion['cuenta_gestion']}-{atencion['cuenta_internacion']}-{atencion['cuenta_id']}"
            cuenta_formato = f"{atencion['cuenta_gestion']}/{atencion['cuenta_internacion']}"

            # Verificar si ya fue procesada
            if self.gestor_estado.esta_procesado(id_unico):
                logger.info(f"[{idx}/{total_atenciones}] Atención {cuenta_formato} ya procesada. Saltando.")
                procesadas += 1
                continue

            logger.info(f"\n[{idx}/{total_atenciones}] Procesando atención {cuenta_formato}")
            logger.info(f"  Médico: {atencion['nombre_medico']}")
            logger.info(f"  Paciente: {atencion['nombre_paciente']}")
            logger.info(f"  Fecha: {atencion['fecha_atencion']}")

            # 3.1 Obtener detalle completo
            detalle = self.mcp_client.get_detalle_atencion(
                persona_numero=atencion['id_persona_paciente'],
                cuenta_gestion=atencion['cuenta_gestion'],
                cuenta_internacion=atencion['cuenta_internacion'],
                cuenta_id=atencion['cuenta_id']
            )

            if not detalle:
                logger.error(f"  Error al obtener detalle de la atención")
                self.gestor_estado.marcar_fallido(id_unico, "Error al obtener detalle")
                fallidas += 1
                continue

            # 3.2 Formatear para LLM
            historial = formatear_atencion_para_llm(detalle)

            # 3.3 Auditar con IA
            resultado = self.auditor_llm.auditar_atencion(
                historial=historial,
                id_evolucion=atencion.get('id_evolucion', 0),  # Para compatibilidad
                fecha_atencion=str(atencion['fecha_atencion']),
                diagnostico=atencion.get('diagnosticos', ''),
                id_persona=atencion['id_persona_paciente'],
                id_medico=atencion['id_medico'],
                nombre_medico=atencion['nombre_medico'],
                nombre_paciente=atencion['nombre_paciente'],
                cuenta_gestion=atencion['cuenta_gestion'],
                cuenta_internacion=atencion['cuenta_internacion']
            )

            if resultado:
                self.guardar_resultado(resultado)
                self.gestor_estado.marcar_completado(id_unico)
                logger.info(f"  [OK] Auditoría completada. Score: {resultado.score_calidad}/100")
                procesadas += 1
            else:
                self.gestor_estado.marcar_fallido(id_unico, "Error en auditoría LLM")
                logger.error(f"  [ERROR] Auditoría fallida")
                fallidas += 1

        # 4. Resumen final
        logger.info("\n" + "="*80)
        logger.info("RESUMEN DE AUDITORÍA")
        logger.info("="*80)
        logger.info(f"Total de atenciones: {total_atenciones}")
        logger.info(f"Procesadas exitosamente: {procesadas}")
        logger.info(f"Fallidas: {fallidas}")
        logger.info(f"Resultados guardados en: {self.output_file}")
        logger.info("="*80)

    def guardar_resultado(self, resultado: AuditoriaUrgenciaResultado):
        """Guarda un resultado de auditoría en formato JSONL"""
        with open(self.output_file, "a", encoding="utf-8") as f:
            f.write(resultado.model_dump_json() + "\n")


# --- Punto de Entrada ---
if __name__ == "__main__":
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Asegurar carpetas
    os.makedirs("output", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # Archivos de salida
    output_jsonl = os.path.join("output", f"auditoria_urgencias_{timestamp}.jsonl")
    state_file = os.path.join("output", f"tracking_{timestamp}.json")

    logger.info(f"\nARCHIVOS DE SALIDA:")
    logger.info(f"  - JSONL: {output_jsonl}")
    logger.info(f"  - Estado: {state_file}")

    # Ejecutar auditoría
    orquestador = OrquestadorAuditoriaProduccion(
        output_file=output_jsonl,
        state_file=state_file
    )

    orquestador.run_auditoria_24h()

    # Generar reporte HTML automáticamente
    logger.info("\nGENERANDO REPORTE HTML...")
    import subprocess

    try:
        result = subprocess.run(
            ["python", "generar_reporte.py", output_jsonl],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            output_html = output_jsonl.replace('.jsonl', '.html')
            logger.info(f"Reporte HTML generado: {output_html}")
        else:
            logger.warning(f"Error al generar reporte HTML: {result.stderr}")
            logger.info(f"Puedes generarlo manualmente: python generar_reporte.py {output_jsonl}")

    except Exception as e:
        logger.warning(f"No se pudo generar reporte HTML automáticamente: {e}")
        logger.info(f"Puedes generarlo manualmente: python generar_reporte.py {output_jsonl}")

    # Subir archivos a MinIO
    logger.info("\n" + "="*80)
    logger.info("SUBIENDO ARCHIVOS A MINIO")
    logger.info("="*80)

    try:
        from minio_client import upload_auditoria_files

        # Determinar archivo HTML
        output_html = output_jsonl.replace('.jsonl', '.html')

        # Determinar archivo de log
        log_filename = f"logs/auditoria_{datetime.now():%Y%m%d}.log"

        # Extraer fecha del timestamp para organizar en carpetas (formato YYYYMMDD)
        fecha_carpeta = timestamp.split('_')[0]  # Extrae "20251114" de "20251114_153045"

        # Subir archivos
        results = upload_auditoria_files(
            jsonl_path=output_jsonl,
            html_path=output_html if os.path.exists(output_html) else None,
            tracking_path=state_file,
            log_path=log_filename if os.path.exists(log_filename) else None,
            fecha_carpeta=fecha_carpeta
        )

        # Mostrar resultados
        if results["exitosos"]:
            logger.info(f"Archivos subidos exitosamente a MinIO: {len(results['exitosos'])}")
            for archivo in results["exitosos"]:
                logger.info(f"  - {os.path.basename(archivo)}")

        if results["fallidos"]:
            logger.warning(f"Archivos que no se pudieron subir: {len(results['fallidos'])}")
            for archivo in results["fallidos"]:
                logger.warning(f"  - {os.path.basename(archivo)}")

    except ImportError:
        logger.warning("Módulo minio_client no disponible. Saltando subida a MinIO.")
        logger.info("Para habilitar MinIO: pip install minio y configurar variables en .env")
    except Exception as e:
        logger.error(f"Error al subir archivos a MinIO: {e}")
        logger.info("Los archivos están disponibles localmente en la carpeta output/")

    # Enviar reporte por correo electrónico
    logger.info("\n" + "="*80)
    logger.info("ENVIANDO REPORTE POR CORREO ELECTRÓNICO")
    logger.info("="*80)

    try:
        from email_sender import enviar_reporte_por_correo

        # Determinar archivos
        output_html = output_jsonl.replace('.jsonl', '.html')
        log_filename = f"logs/auditoria_{datetime.now():%Y%m%d}.log"

        # Extraer fecha para el asunto del correo
        fecha_reporte = datetime.now().strftime("%Y-%m-%d")

        # Enviar correo
        enviado = enviar_reporte_por_correo(
            jsonl_path=output_jsonl if os.path.exists(output_jsonl) else None,
            html_path=output_html if os.path.exists(output_html) else None,
            tracking_path=state_file if os.path.exists(state_file) else None,
            log_path=log_filename if os.path.exists(log_filename) else None,
            fecha_reporte=fecha_reporte
        )

        if enviado:
            logger.info("Reporte enviado por correo exitosamente")
        else:
            logger.warning("No se pudo enviar el correo (ver logs para detalles)")

    except ImportError:
        logger.warning("Módulo email_sender no disponible. Saltando envío de correo.")
        logger.info("El módulo email_sender.py debe estar en el directorio del proyecto")
    except Exception as e:
        logger.error(f"Error al enviar correo: {e}")
        logger.info("Los archivos están disponibles localmente en la carpeta output/")
