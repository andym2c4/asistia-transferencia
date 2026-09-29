"""Estado de resolucion por institucion educativa y periodo.

Responde "que falta para cerrar este mes" con un desglose exhaustivo y mutuamente excluyente por
institucion, y lo agrega por nivel. Es la pieza de calculo que el desglose de M04
(docs/METRICAS.md) define, **no** la tasa de M04: esa exige un manifiesto de unidades esperadas
validado contra el padron, que no existe todavia. Aqui el denominador es el universo operativo
conocido por NEXUS al corte, declarado explicitamente en `ResumenNivel.universo` para que ningun
consumidor lo confunda con cobertura validada de la UGEL.

Reglas que este modulo respeta (docs/CLAUDE.md, docs/METRICAS.md):

- Universo y numerador usan la misma definicion de nivel (`fn_nivel_canonico` sobre la institucion,
  no sobre el texto declarado en el documento), para que el numerador sea siempre subconjunto del
  denominador. Filtrar por nivel restringe ambos componentes.
- Las instituciones cuyo `nivel_modalidad` no tiene nivel canonico (p. ej. "Administracion") no se
  reparten a ningun nivel ni se descartan en silencio: se cuentan aparte como no evaluables.
- Vacio, pendiente y desconocido no se convierten en cero ni en "resuelto". Un calendario ausente
  se informa como AUSENTE, no como cero dias.
- Las rectificaciones no multiplican unidades: se toma la ultima version de cada serie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg

from asistia.niveles import NIVELES

# Niveles canonicos que devuelve `fn_nivel_canonico`. Una institucion cae en uno o en ninguno.
NIVELES_CANONICOS = tuple(NIVELES)

# Nombre del grano agregado de toda la UGEL, distinto de cualquier nivel.
GRANO_UGEL = "UGEL"

# Desglose exhaustivo y mutuamente excluyente. El orden es el del avance del trabajo.
ESTADOS_RESOLUCION = (
    "SIN_RECEPCION",
    "RECIBIDO_SIN_EXTRAER",
    "EXTRAIDO_PENDIENTE",
    "REVISADO",
)

UNIVERSO_DECLARADO = (
    "Instituciones educativas conocidas por NEXUS al corte, con nivel canonico resuelto. "
    "No es el manifiesto de unidades esperadas validado contra el padron que exige M04."
)


@dataclass
class EstadoInstitucion:
    """Estado de una institucion en un periodo. `estado` es uno de ESTADOS_RESOLUCION."""

    institucion_educativa_id: int
    cod_mod: str
    anexo: str
    nombre_ie: str
    nivel_modalidad: str
    estado: str
    reportes: int
    reportes_revisados: int
    version_maxima: int | None
    filas: int
    sin_identidad: int
    alertas_pendientes: int
    alertas_criticas: int
    calendario_estado: str
    calendario_dias_sin_determinar: int | None

    @property
    def pendiente(self) -> bool:
        return self.estado != "REVISADO"


@dataclass
class ResumenNivel:
    periodo: date
    nivel: str
    universo: str = UNIVERSO_DECLARADO
    instituciones: list[EstadoInstitucion] = field(default_factory=list)
    por_estado: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.instituciones)

    @property
    def pendientes(self) -> int:
        return sum(1 for i in self.instituciones if i.pendiente)

    @property
    def filas_sin_identidad(self) -> int:
        return sum(i.sin_identidad for i in self.instituciones)

    @property
    def alertas_pendientes(self) -> int:
        return sum(i.alertas_pendientes for i in self.instituciones)

    def conciliado(self) -> bool:
        """La suma del desglose debe ser exactamente el universo (METRICAS.md, M04)."""
        return sum(self.por_estado.values()) == self.total


def clasificar(
    reportes: int,
    reportes_revisados: int,
    filas: int,
    sin_identidad: int,
    alertas_criticas: int,
) -> str:
    """Estado de una institucion a partir de sus conteos. Separado del SQL para poder probarlo
    sin base de datos y para que el orden de las condiciones quede a la vista.

    "Revisado" exige que TODAS las series del mes esten validadas: una institucion con un turno
    revisado y otro pendiente no esta terminada.
    """
    if reportes == 0:
        return "SIN_RECEPCION"
    if filas == 0:
        # Documento recibido y registrado, pero sin personas extraidas todavia.
        return "RECIBIDO_SIN_EXTRAER"
    if reportes_revisados == reportes and sin_identidad == 0 and alertas_criticas == 0:
        return "REVISADO"
    return "EXTRAIDO_PENDIENTE"


_SQL_ESTADO = """
WITH universo AS (
    SELECT ie.institucion_educativa_id, ie.cod_mod, ie.anexo, ie.nombre_ie, ie.nivel_modalidad
    FROM institucion_educativa ie
    WHERE fn_nivel_canonico(ie.nivel_modalidad) = %(nivel)s
),
-- Ultima version de cada serie del periodo. Una serie sin id (permitido por el esquema) cuenta
-- como su propia unidad en vez de desaparecer del conteo.
ultimos AS (
    SELECT DISTINCT ON (COALESCE(r.reporte_asistencia_serie_id::text, r.reporte_asistencia_id::text))
           r.reporte_asistencia_id, r.institucion_educativa_id, r.estado, r.version
    FROM reporte_asistencia r
    WHERE r.periodo = %(periodo)s AND r.estado NOT IN ('RECHAZADO','HISTORICA')
    ORDER BY COALESCE(r.reporte_asistencia_serie_id::text, r.reporte_asistencia_id::text),
             r.version DESC
),
por_ie AS (
    SELECT u.institucion_educativa_id,
           count(*) AS reportes,
           count(*) FILTER (WHERE u.estado = 'VALIDADO') AS reportes_revisados,
           max(u.version) AS version_maxima,
           COALESCE(sum((SELECT count(*) FROM trabajador_en_reporte t
                         WHERE t.reporte_asistencia_id = u.reporte_asistencia_id)), 0)::bigint
               AS filas,
           COALESCE(sum((SELECT count(*) FROM trabajador_en_reporte t
                         WHERE t.reporte_asistencia_id = u.reporte_asistencia_id
                           AND (t.trabajador_id IS NULL OR t.rol_laboral_id IS NULL))), 0)::bigint
               AS sin_identidad,
           COALESCE(sum((SELECT count(*) FROM validacion_reporte v
                         WHERE v.reporte_asistencia_id = u.reporte_asistencia_id
                           AND v.estado = 'PENDIENTE')), 0)::bigint AS alertas_pendientes,
           COALESCE(sum((SELECT count(*) FROM validacion_reporte v
                         WHERE v.reporte_asistencia_id = u.reporte_asistencia_id
                           AND v.estado = 'PENDIENTE' AND v.severidad = 'ERROR')), 0)::bigint
               AS alertas_criticas
    FROM ultimos u
    GROUP BY u.institucion_educativa_id
),
-- Version de calendario aplicable al anio: VIGENTE manda sobre BORRADOR; si no hay ninguna, el
-- estado es AUSENTE y los dias sin determinar quedan NULL (desconocido), nunca 0.
calendario AS (
    SELECT DISTINCT ON (cl.institucion_educativa_id)
           cl.institucion_educativa_id, cv.calendarizacion_version_id, cv.estado
    FROM calendarizacion_local cl
    JOIN calendarizacion_version cv USING (calendarizacion_local_id)
    WHERE cl.anio = %(anio)s AND cv.estado NOT IN ('RECHAZADA','HISTORICA')
    ORDER BY cl.institucion_educativa_id,
             (cv.estado = 'VIGENTE') DESC,
             (COALESCE(cv.procedencia_extraccion->>'tipo','') <> 'DERIVADO_2025') DESC, cv.version DESC
)
SELECT un.*,
       COALESCE(p.reportes, 0) AS reportes,
       COALESCE(p.reportes_revisados, 0) AS reportes_revisados,
       p.version_maxima,
       COALESCE(p.filas, 0) AS filas,
       COALESCE(p.sin_identidad, 0) AS sin_identidad,
       COALESCE(p.alertas_pendientes, 0) AS alertas_pendientes,
       COALESCE(p.alertas_criticas, 0) AS alertas_criticas,
       COALESCE(c.estado, 'AUSENTE') AS calendario_estado,
       CASE WHEN c.calendarizacion_version_id IS NULL THEN NULL ELSE
            (SELECT count(*) FROM dia_calendarizacion d
             WHERE d.calendarizacion_version_id = c.calendarizacion_version_id
               AND d.estado_captura <> 'REGISTRADO') END AS calendario_dias_sin_determinar
FROM universo un
LEFT JOIN por_ie p USING (institucion_educativa_id)
LEFT JOIN calendario c USING (institucion_educativa_id)
ORDER BY un.nombre_ie, un.cod_mod, un.anexo
"""


def estado_resolucion(
    conn: psycopg.Connection, periodo: date, nivel: str
) -> ResumenNivel:
    """Estado de cada institucion del nivel en el periodo, con el desglose conciliado.

    `nivel` es un nivel canonico (el que devuelve `fn_nivel_canonico`): INICIAL, PRIMARIA,
    SECUNDARIA, CEBA, CEBE, PRITE o CETPRO.
    """
    filas = conn.execute(
        _SQL_ESTADO,
        {"periodo": periodo, "nivel": nivel, "anio": periodo.year},
    ).fetchall()

    instituciones = []
    for f in filas:
        estado = clasificar(
            f["reportes"],
            f["reportes_revisados"],
            f["filas"],
            f["sin_identidad"],
            f["alertas_criticas"],
        )
        instituciones.append(
            EstadoInstitucion(
                institucion_educativa_id=f["institucion_educativa_id"],
                cod_mod=f["cod_mod"],
                anexo=f["anexo"],
                nombre_ie=f["nombre_ie"],
                nivel_modalidad=f["nivel_modalidad"],
                estado=estado,
                reportes=f["reportes"],
                reportes_revisados=f["reportes_revisados"],
                version_maxima=f["version_maxima"],
                filas=f["filas"],
                sin_identidad=f["sin_identidad"],
                alertas_pendientes=f["alertas_pendientes"],
                alertas_criticas=f["alertas_criticas"],
                calendario_estado=f["calendario_estado"],
                calendario_dias_sin_determinar=f["calendario_dias_sin_determinar"],
            )
        )

    por_estado = {e: 0 for e in ESTADOS_RESOLUCION}
    for i in instituciones:
        por_estado[i.estado] += 1

    return ResumenNivel(
        periodo=periodo,
        nivel=nivel,
        instituciones=instituciones,
        por_estado=por_estado,
    )


def resumen_ugel(conn: psycopg.Connection, periodo: date) -> ResumenNivel:
    """Universo agregado de todos los niveles canonicos en un periodo.

    Necesario para cualquier cifra declarada al grano de toda la UGEL (por ejemplo un tiempo manual
    de "el consolidado de todas las instituciones"): atribuir ese total a cada nivel por separado y
    luego sumarlos daria siete veces el valor real. Cada institucion aparece una sola vez porque
    `fn_nivel_canonico` asigna a lo sumo un nivel; las que no tienen nivel canonico no estan aqui ni
    en ningun nivel, y se consultan con `instituciones_sin_nivel_canonico`.
    """
    instituciones: list[EstadoInstitucion] = []
    for nivel in NIVELES_CANONICOS:
        instituciones.extend(estado_resolucion(conn, periodo, nivel).instituciones)

    por_estado = dict.fromkeys(ESTADOS_RESOLUCION, 0)
    for i in instituciones:
        por_estado[i.estado] += 1

    return ResumenNivel(
        periodo=periodo,
        nivel=GRANO_UGEL,
        instituciones=instituciones,
        por_estado=por_estado,
    )


def instituciones_sin_nivel_canonico(conn: psycopg.Connection) -> list[dict]:
    """Instituciones cuyo `nivel_modalidad` no mapea a ningun nivel canonico.

    No pertenecen al universo de ningun nivel y por eso no aparecen en `estado_resolucion`. Se
    exponen para que queden declaradas en vez de desaparecer del total (docs/CLAUDE.md: mantener
    visibles pendientes y exclusiones).
    """
    return conn.execute(
        """
        SELECT institucion_educativa_id, cod_mod, anexo, nombre_ie, nivel_modalidad
        FROM institucion_educativa
        WHERE fn_nivel_canonico(nivel_modalidad) IS NULL
        ORDER BY nombre_ie
        """
    ).fetchall()
