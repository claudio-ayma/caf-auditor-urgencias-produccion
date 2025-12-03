-- ============================================================================
-- Query: Detalle completo de una atención de emergencia
-- ============================================================================
-- Obtiene TODA la información relacionada a una atención específica de emergencia
-- incluyendo triage, evoluciones, medicamentos, ejecuciones, notas, labs, imágenes
--
-- Parámetros:
--   {persona_numero} - ID del paciente
--   {cuenta_gestion} - Año de gestión de la cuenta
--   {cuenta_internacion} - Número de internación
--   {cuenta_id} - ID de la cuenta
-- ============================================================================

SELECT
    -- ========================================================================
    -- INFORMACIÓN BÁSICA DE LA CUENTA
    -- ========================================================================
    '{persona_numero}' AS persona_numero,
    '{cuenta_gestion}' AS cuenta_gestion,
    '{cuenta_internacion}' AS cuenta_internacion,
    '{cuenta_id}' AS cuenta_id,

    -- ========================================================================
    -- 1. INFORMACIÓN DE TRIAGE (TEMPORAL: comentado para testing)
    -- ========================================================================
    NULL AS triage_info,

    -- ========================================================================
    -- 2. EVOLUCIONES CLÍNICAS de esta cuenta
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            JSON_OBJECT(
                'id_evolucion', evo.EvolucionAutonumerico,
                'fecha', evo.PacienteEvolucionFechaHora,
                'profesional', pers.PersonaNombreCompleto,
                'tipo_evento', CASE evo.PacienteEvolucionTipo
                    WHEN 0 THEN 'Evaluación Inicial'
                    WHEN 1 THEN 'Evaluación Inicial'
                    WHEN 2 THEN 'Evolución'
                    WHEN 3 THEN 'Epicrisis'
                    WHEN 4 THEN 'Interconsulta'
                    WHEN 5 THEN 'Reporte Enfermería'
                    WHEN 23 THEN 'Evolución Enfermería'
                    ELSE 'Evolución Clínica'
                END,
                'diagnosticos', (
                    SELECT GROUP_CONCAT(
                        CONCAT(diag.CIE9CMCodigo, '-', cie.CIE9CMDescripcion,
                            CASE diag.PacienteEvolucionProblemaTipo
                                WHEN 1 THEN ' (Principal)'
                                WHEN 2 THEN ' (Secundario)'
                                ELSE ''
                            END
                        ) SEPARATOR ' | '
                    )
                    FROM pacienteevoluciondiagnostico diag
                    LEFT JOIN cie9cm cie ON cie.CIE9CMCodigo = diag.CIE9CMCodigo
                    WHERE diag.PersonaNumero = evo.PersonaNumero
                      AND diag.PacienteEvolucionFechaHora = evo.PacienteEvolucionFechaHora
                ),
                'comentario_clinico', CONCAT_WS('\n',
                    NULLIF(evo.PacienteEvolucionSubjetivo, ''),
                    NULLIF(evo.PacienteEvolucionObjetivo, ''),
                    NULLIF(evo.PacienteEvolucionProblema, ''),
                    NULLIF(evo.PacienteEvolucionComentario, ''),
                    NULLIF(evo.PacienteEvolucionHallazgos, ''),
                    NULLIF(evo.PacienteEvolucionEvFinal, '')  -- FIX v1.2.2: Evaluación final (epicrisis)
                ),
                'plan_medico', CONCAT_WS('\n',
                    NULLIF(evo.PacienteEvolucionPlan, ''),
                    NULLIF(evo.PacienteEvolucionPlanterapeuti, '')
                ),
                'medicamentos_prescritos', (
                    SELECT GROUP_CONCAT(
                        CONCAT_WS(' ',
                            CONVERT(COALESCE(ivartmed.nombregenerico, art.IvDescrip) USING utf8mb4),
                            CONVERT(COALESCE(med.PacienteMedicamentoDosisCombin, '') USING utf8mb4),
                            CONVERT(COALESCE(med.UnidadCodigo, '') USING utf8mb4),
                            CASE med.PacienteMedicamentoFrecUnidad
                                WHEN 1 THEN CONCAT('cada ', med.PacienteMedicamentoFrecuencia, ' horas')
                                WHEN 2 THEN CONCAT('cada ', med.PacienteMedicamentoFrecuencia, ' días')
                                WHEN 3 THEN 'PRN'
                                ELSE ''
                            END,
                            CONCAT('(', CONVERT(vias.Descripcion USING utf8mb4), ')')
                        ) SEPARATOR ' | '
                    )
                    FROM pacienteevolucionmedicamento med
                    LEFT JOIN clinica01.ivarticulosmed ivartmed ON ivartmed.ivcodarticulo = med.MedicamentoCodigo
                    LEFT JOIN clinica01.ivarticulos art ON art.IvcodArticulo = med.MedicamentoCodigo
                    LEFT JOIN clinica01.vias ON clinica01.vias.CodVia = med.ViaEvoCodigo
                    WHERE med.PersonaNumero = evo.PersonaNumero
                      AND med.PacienteEvolucionFechaHora = evo.PacienteEvolucionFechaHora
                ),
                'condicion_alta', CONVERT(ta.Descripcion USING utf8mb4),
                'causa_egreso', evo.PacienteEvolucionCausaEgre,
                'complicaciones', evo.PacienteEvolucionCompliTexto
            )
            SEPARATOR '\n---EVOLUCION---\n'
        )
        FROM pacienteevolucion evo
        LEFT JOIN usuario usr ON usr.UsuarioCodigo = evo.PacienteEvolucionMUsuario
        LEFT JOIN persona pers ON pers.PersonaNumero = usr.UsuarioPersonaCodigo
        LEFT JOIN clinica01.tiposaltas ta ON ta.CodTipoAlta = evo.taCodTipoAlta
        WHERE evo.PacienteEvolucionBFecha = '1000-01-01 00:00:00'
          AND evo.PacienteEvolucionGestion = {cuenta_gestion}
          AND evo.PacienteEvolucionNroInter = {cuenta_internacion}
          AND evo.PacienteEvolucionNroIntId = {cuenta_id}
        ORDER BY evo.PacienteEvolucionFechaHora ASC
    ) AS evoluciones_clinicas,

    -- ========================================================================
    -- 3. SIGNOS VITALES de esta cuenta
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            CONCAT(fecha_registro, ': ', descripcion, ' = ', valor, ' ', COALESCE(unidad, ''))
            SEPARATOR ' | '
        )
        FROM vw_hc_signos_vitales
        WHERE persona_numero = {persona_numero}
          AND cuenta_gestion = {cuenta_gestion}
          AND cuenta_internacion = {cuenta_internacion}
          AND cuenta_id = {cuenta_id}
        ORDER BY fecha_registro ASC
    ) AS signos_vitales,

    -- ========================================================================
    -- 4. EJECUCIONES DE MEDICAMENTOS (desde clinica01)
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            CONCAT(
                'Fecha: ', mc.FechaReg,
                ' | Medicamento: ', COALESCE(ia.IvDescrip, 'No especificado'),
                ' | Cantidad: ', COALESCE(md.Cantidad, ''),
                ' | Unidad: ', COALESCE(md.Unidad, ''),
                ' | Enfermera: ', mc.Usuario,
                ' | Observación: ', COALESCE(mc.glosa, '')
            )
            SEPARATOR '\n'
        )
        FROM clinica01.medicamentosc mc
        LEFT JOIN clinica01.medicamentosd md
            ON md.Gestion = mc.Gestion
            AND md.NroInternacion = mc.NroInternacion
            AND md.NroMedicamento = mc.NroMedicamento
        LEFT JOIN clinica01.ivarticulos ia ON ia.IvcodArticulo = md.IvCodArticulo
        WHERE mc.Gestion = {cuenta_gestion}
          AND mc.NroInternacion = {cuenta_internacion}
        ORDER BY mc.FechaReg ASC
    ) AS ejecuciones_medicamentos,

    -- ========================================================================
    -- 5. NOTAS DE ENFERMERÍA
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            CONCAT(
                'Fecha: ', COALESCE(NotaEnfHoraRealizado, NotaEnfMFecha),
                ' | Usuario: ', NotaEnfMUsuario,
                ' | Nota: ', NotaEnfConclusion
            )
            SEPARATOR '\n'
        )
        FROM notasenfermeria
        WHERE PersonaNumero = {persona_numero}
          AND InterGestion = {cuenta_gestion}
          AND InterNroInternacion = {cuenta_internacion}
          AND InterNroIntID = {cuenta_id}
        ORDER BY COALESCE(NotaEnfHoraRealizado, NotaEnfMFecha) ASC
    ) AS notas_enfermeria,

    -- ========================================================================
    -- 6. RESULTADOS DE LABORATORIO
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            DISTINCT CONCAT(
                'Servicio: ', descripcion_servicio,
                ' | Fecha: ', fecha_orden,
                ' | Lab #', numero_laboratorio,
                ' | Resultados: ', linea_detalle, ': ', resultado, ' ', COALESCE(unidad, ''),
                ' (Ref: ', COALESCE(valor_referencia, 'N/A'), ')'
            )
            SEPARATOR '\n'
        )
        FROM vw_hc_resultados_laboratorio
        WHERE persona_numero = {persona_numero}
          AND cuenta_gestion = {cuenta_gestion}
          AND cuenta_internacion = {cuenta_internacion}
          AND cuenta_id = {cuenta_id}
        ORDER BY fecha_orden ASC, linea_detalle ASC
    ) AS laboratorios,

    -- ========================================================================
    -- 7. ESTUDIOS DE IMAGEN
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            DISTINCT CONCAT(
                'Tipo: ', enc.tipo_estudio,
                ' | Fecha: ', enc.fecha_estudio,
                ' | Solicitante: ', enc.medico_solicitante,
                ' | Informante: ', enc.medico_informante,
                ' | Título: ', det.titulo,
                ' | Hallazgos: ', det.descripcion
            )
            SEPARATOR '\n---IMAGEN---\n'
        )
        FROM vw_hc_resultados_imagenes_encabezado enc
        JOIN vw_hc_resultados_imagenes_detalle det
            ON det.solicitud_codigo = enc.solicitud_codigo
        WHERE enc.persona_numero = {persona_numero}
          AND enc.cuenta_gestion = {cuenta_gestion}
          AND enc.cuenta_internacion = {cuenta_internacion}
          AND enc.cuenta_id = {cuenta_id}
        ORDER BY enc.fecha_estudio ASC
    ) AS estudios_imagen,

    -- ========================================================================
    -- 8. SOLICITUDES DE LABORATORIO (incluye pendientes sin resultado)
    -- ========================================================================
    -- Esta sección muestra TODOS los laboratorios solicitados, independientemente
    -- de si ya tienen resultado o no. Complementa la sección 6 que solo muestra
    -- laboratorios CON resultados.
    -- ========================================================================
    (
        SELECT GROUP_CONCAT(
            DISTINCT CONCAT(
                'Estudio: ', prod.Descripcion,
                ' | Fecha solicitud: ', maestro.PacienteSolicudLaboratorioSFec,
                ' | Codigo: ', prod.CodProdCMF
            )
            SEPARATOR '\n'
        )
        FROM pacientesolicudlaboratorio maestro
        INNER JOIN pacientesolicudlaboratoriolabo det
            ON det.PacienteSolicudLaboratorioCodi = maestro.PacienteSolicudLaboratorioCodi
        INNER JOIN clinica01.productos prod
            ON prod.CodProdCMF = det.productosCodProdCMF
        WHERE maestro.PacienteLaboCodigo = {persona_numero}
          AND maestro.PacienteSolicudLaboratorioGest = {cuenta_gestion}
          AND maestro.PacienteSolicudLaboratorioNroI = {cuenta_internacion}
        ORDER BY maestro.PacienteSolicudLaboratorioSFec ASC
    ) AS solicitudes_laboratorio

FROM DUAL;