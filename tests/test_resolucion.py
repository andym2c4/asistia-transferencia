"""R1/R2: estado de resolucion por institucion y estimacion de esfuerzo declarada.

Las pruebas de clasificacion y estimacion no tocan la base (son funciones puras): eso permite
fijar el comportamiento de los estados y de los supuestos sin depender del corpus real. Las que si
la usan verifican lo que solo se puede comprobar contra el esquema: que el desglose concilia con el
universo, que el nivel se compara de forma canonica (DT10/DT13/DT17) y que una institucion sin
nivel canonico no se reparte a ningun nivel ni desaparece del total.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from asistia.consolidado.estimacion import (
    DECLARACION_VIGENTE,
    MINUTOS_POR_SEMANA,
    Declaracion,
    Supuestos,
    estimar,
)
from asistia.consolidado.resolucion import (
    ESTADOS_RESOLUCION,
    NIVELES_CANONICOS,
    clasificar,
    estado_resolucion,
    instituciones_sin_nivel_canonico,
    resumen_ugel,
)
from asistia.importar.nexus import importar_nexus

REPO_ROOT = Path(__file__).resolve().parent.parent
RUTA_NEXUS_JUNIO = (
    REPO_ROOT / "data/raw/data_brindada_por_ugel/nexus/nexus 2026-06-01.xlsx"
)
PERIODO = date(2026, 7, 1)
CORTE_NEXUS = date(2026, 6, 1)


# --- Clasificacion de estados (sin base de datos) ---------------------------------------------


def test_sin_reportes_es_sin_recepcion():
    assert clasificar(0, 0, 0, 0, 0) == "SIN_RECEPCION"


def test_reporte_sin_personas_extraidas_no_cuenta_como_revisado():
    """Un documento registrado pero sin filas extraidas no es un mes resuelto ni un mes vacio."""
    assert clasificar(1, 0, 0, 0, 0) == "RECIBIDO_SIN_EXTRAER"


def test_validado_sin_pendientes_es_revisado():
    assert clasificar(2, 2, 40, 0, 0) == "REVISADO"


def test_un_turno_pendiente_impide_declarar_revisada_la_institucion():
    """Dos series (turnos) y solo una validada: la institucion no esta terminada."""
    assert clasificar(2, 1, 40, 0, 0) == "EXTRAIDO_PENDIENTE"


@pytest.mark.parametrize(
    "sin_identidad,alertas_criticas",
    [(1, 0), (0, 1), (3, 2)],
)
def test_identidad_o_alerta_critica_pendiente_impide_revisado(
    sin_identidad, alertas_criticas
):
    assert clasificar(1, 1, 40, sin_identidad, alertas_criticas) == "EXTRAIDO_PENDIENTE"


def test_los_estados_declarados_cubren_todas_las_salidas_de_clasificar():
    salidas = {
        clasificar(r, rv, f, si, ac)
        for r in (0, 1, 2)
        for rv in (0, 1, 2)
        for f in (0, 5)
        for si in (0, 1)
        for ac in (0, 1)
        if rv <= r
    }
    assert salidas <= set(ESTADOS_RESOLUCION)


# --- Estimacion (sin base de datos) -----------------------------------------------------------


class _ResumenFalso:
    """Doble minimo de ResumenNivel: la estimacion solo necesita el desglose y el total."""

    def __init__(self, por_estado):
        self.periodo = PERIODO
        self.nivel = "PRIMARIA"
        self.universo = "universo de prueba"
        self.por_estado = por_estado
        self.instituciones = [None] * sum(por_estado.values())

    @property
    def total(self):
        return len(self.instituciones)


def test_universo_vacio_es_no_aplica_y_nunca_cero_por_ciento():
    e = estimar(_ResumenFalso(dict.fromkeys(ESTADOS_RESOLUCION, 0)))
    assert e.estado_calculo == "NO_APLICA"
    assert e.reduccion_pct is None


def test_todo_revisado_da_reduccion_maxima():
    e = estimar(_ResumenFalso({**dict.fromkeys(ESTADOS_RESOLUCION, 0), "REVISADO": 10}))
    assert e.reduccion_pct == 100.0


def test_sin_declaracion_no_hay_tiempo_absoluto():
    """La reduccion relativa sale de los pesos; el tiempo absoluto exige la declaracion de RRHH."""
    e = estimar(
        _ResumenFalso({**dict.fromkeys(ESTADOS_RESOLUCION, 0), "SIN_RECEPCION": 4})
    )
    assert e.estado_calculo == "ESTIMADO_SIN_TIEMPO_ABSOLUTO"
    assert e.tiempo_manual_min is None and e.tiempo_asistido_min is None
    assert e.reduccion_pct is not None


def test_la_banda_de_sensibilidad_contiene_al_valor_central():
    e = estimar(
        _ResumenFalso(
            {
                **dict.fromkeys(ESTADOS_RESOLUCION, 0),
                "EXTRAIDO_PENDIENTE": 6,
                "SIN_RECEPCION": 4,
            }
        )
    )
    bajo, alto = e.reduccion_pct_banda
    assert bajo <= e.reduccion_pct <= alto
    assert bajo < alto, "una banda degenerada ocultaria la incertidumbre del modelo"


def test_la_estimacion_siempre_viaja_con_sus_supuestos():
    e = estimar(_ResumenFalso({**dict.fromkeys(ESTADOS_RESOLUCION, 0), "REVISADO": 1}))
    assert e.etiqueta == "ESTIMADO_DECLARADO"
    assert e.supuestos.limitaciones, "un numero sin limitaciones no es publicable"


def test_nivel_sin_ninguna_recepcion_lleva_advertencia_explicita():
    """Un nivel donde nadie envio nada igual arroja reduccion > 0 por el supuesto de seguimiento.
    Ese numero no describe trabajo de extraccion evitado y debe decirlo."""
    solo_faltantes = estimar(
        _ResumenFalso({**dict.fromkeys(ESTADOS_RESOLUCION, 0), "SIN_RECEPCION": 2})
    )
    assert solo_faltantes.reduccion_pct > 0
    assert "ATENCION" in solo_faltantes.nota

    con_algo_procesado = estimar(
        _ResumenFalso(
            {**dict.fromkeys(ESTADOS_RESOLUCION, 0), "SIN_RECEPCION": 1, "REVISADO": 1}
        )
    )
    assert "ATENCION" not in con_algo_procesado.nota


def test_declaracion_exige_fuente_y_rango_valido():
    with pytest.raises(ValueError):
        Declaracion(0, 100, "NIVEL_MES", "RRHH", "2026-09-14")
    with pytest.raises(ValueError):
        Declaracion(100, 50, "NIVEL_MES", "RRHH", "2026-09-14")
    with pytest.raises(ValueError):
        Declaracion(100, 200, "POR_SEMANA", "RRHH", "2026-09-14")
    with pytest.raises(ValueError):
        Declaracion(100, 200, "NIVEL_MES", "   ", "2026-09-14")


def test_declaracion_por_ie_escala_con_el_universo():
    resumen = _ResumenFalso(
        {**dict.fromkeys(ESTADOS_RESOLUCION, 0), "EXTRAIDO_PENDIENTE": 10}
    )
    por_ie = Declaracion(30, 60, "IE_MES", "RRHH-1", "2026-09-14")
    por_nivel = Declaracion(30, 60, "NIVEL_MES", "RRHH-1", "2026-09-14")
    assert estimar(resumen, declaracion=por_ie).tiempo_manual_min == (300, 600)
    assert estimar(resumen, declaracion=por_nivel).tiempo_manual_min == (30, 60)


def test_pesos_distintos_cambian_la_estimacion():
    """Los pesos son parametros, no constantes escondidas en el codigo."""
    resumen = _ResumenFalso(
        {**dict.fromkeys(ESTADOS_RESOLUCION, 0), "EXTRAIDO_PENDIENTE": 10}
    )
    base = estimar(resumen).reduccion_pct
    otro = estimar(resumen, supuestos=Supuestos(peso_extraido_pendiente=0.9))
    assert otro.reduccion_pct != base


# --- Contra el esquema real -------------------------------------------------------------------


def test_desglose_concilia_con_el_universo(conn):
    """La suma de los estados debe ser exactamente el universo (METRICAS.md, M04)."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    resumen = estado_resolucion(conn, PERIODO, "PRIMARIA")
    assert resumen.total > 0
    assert resumen.conciliado()
    assert sum(resumen.por_estado.values()) == len(resumen.instituciones)


def test_sin_reportes_todo_el_universo_queda_sin_recepcion(conn):
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    resumen = estado_resolucion(conn, PERIODO, "PRIMARIA")
    assert resumen.por_estado["SIN_RECEPCION"] == resumen.total
    assert resumen.pendientes == resumen.total


def _insertar_ie(conn, cod_mod: str, nombre: str, nivel_modalidad: str) -> int:
    """Institucion minima para escenarios construidos. El corte NEXUS de junio solo trae
    Primaria/CEBA/CEBE, asi que las variantes de texto se construyen en vez de asumirse."""
    local = conn.execute(
        """
        INSERT INTO local_educativo (codlocal_escale, distrito)
        VALUES (%s, 'LUYA') RETURNING local_educativo_id
        """,
        (f"L{cod_mod}",),
    ).fetchone()["local_educativo_id"]
    return conn.execute(
        """
        INSERT INTO institucion_educativa
            (local_educativo_id, cod_mod, anexo, nombre_ie, nivel_modalidad, distrito)
        VALUES (%s, %s, '0', %s, %s, 'LUYA')
        RETURNING institucion_educativa_id
        """,
        (local, cod_mod, nombre, nivel_modalidad),
    ).fetchone()["institucion_educativa_id"]


def test_el_nivel_se_compara_de_forma_canonica_no_por_texto_crudo(conn):
    """DT10/DT13/DT17: 'Inicial - Jardin' y 'Inicial - Programa no escolarizado' son textos
    distintos que deben caer en el mismo universo INICIAL, no en dos universos que no se ven."""
    a = _insertar_ie(conn, "9000001", "JARDIN DE PRUEBA", "Inicial - Jardín")
    b = _insertar_ie(
        conn, "9000002", "PRONOEI DE PRUEBA", "Inicial - Programa no escolarizado"
    )

    resumen = estado_resolucion(conn, PERIODO, "INICIAL")
    ids = {i.institucion_educativa_id for i in resumen.instituciones}
    assert {a, b} <= ids, (
        "ambas variantes de texto deben caer en el mismo universo canonico"
    )
    assert len({i.nivel_modalidad for i in resumen.instituciones}) > 1
    assert resumen.conciliado()


def test_institucion_sin_nivel_canonico_no_se_reparte_ni_se_pierde(conn):
    """La UGEL misma aparece en NEXUS con nivel 'Administracion': no pertenece a ningun nivel
    educativo, pero tampoco puede desaparecer del total sin quedar declarada."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    ugel = _insertar_ie(conn, "9000003", "UGEL LUYA DE PRUEBA", "Administración")

    fuera = instituciones_sin_nivel_canonico(conn)
    ids_fuera = {f["institucion_educativa_id"] for f in fuera}
    assert ugel in ids_fuera

    total_en_niveles = 0
    for nivel in (
        "INICIAL",
        "PRIMARIA",
        "SECUNDARIA",
        "CEBA",
        "CEBE",
        "PRITE",
        "CETPRO",
    ):
        resumen = estado_resolucion(conn, PERIODO, nivel)
        total_en_niveles += resumen.total
        assert not (
            ids_fuera & {i.institucion_educativa_id for i in resumen.instituciones}
        ), f"una institucion sin nivel canonico aparecio dentro de {nivel}"

    con_nivel = conn.execute(
        "SELECT count(*) AS n FROM institucion_educativa "
        "WHERE fn_nivel_canonico(nivel_modalidad) IS NOT NULL"
    ).fetchone()["n"]
    assert total_en_niveles == con_nivel


def test_calendario_ausente_no_se_reporta_como_cero_dias(conn):
    """Vacio y desconocido no son cero (docs/CLAUDE.md)."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    resumen = estado_resolucion(conn, PERIODO, "PRIMARIA")
    sin_calendario = [
        i for i in resumen.instituciones if i.calendario_estado == "AUSENTE"
    ]
    assert sin_calendario
    assert all(i.calendario_dias_sin_determinar is None for i in sin_calendario)


# --- Grano de toda la UGEL (declaracion por UGEL-mes) ------------------------------------------


def test_declaracion_por_ugel_no_se_atribuye_a_un_solo_nivel():
    """Atribuir el tiempo de toda la UGEL a cada nivel y sumarlos daria siete veces el real."""
    un_nivel = _ResumenFalso(
        {**dict.fromkeys(ESTADOS_RESOLUCION, 0), "EXTRAIDO_PENDIENTE": 10}
    )
    e = estimar(un_nivel, declaracion=DECLARACION_VIGENTE)
    assert e.estado_calculo == "NO_APLICA_A_ESTE_GRANO"
    assert e.tiempo_manual_min is None
    # La reduccion relativa del nivel sigue siendo valida: lo que no aplica es el absoluto.
    assert e.reduccion_pct is not None


def test_declaracion_vigente_es_citable_y_esta_en_semanas_de_jornada():
    d = DECLARACION_VIGENTE
    assert d.unidad == "UGEL_MES"
    assert d.fuente and d.fecha
    assert (d.minutos_bajo, d.minutos_alto) == (
        MINUTOS_POR_SEMANA,
        2 * MINUTOS_POR_SEMANA,
    )


def test_resumen_ugel_agrega_cada_institucion_una_sola_vez(conn):
    """El agregado no puede duplicar instituciones ni perder ninguna del universo con nivel."""
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    _insertar_ie(conn, "9000004", "JARDIN AGREGADO", "Inicial - Jardín")

    ugel = resumen_ugel(conn, PERIODO)
    ids = [i.institucion_educativa_id for i in ugel.instituciones]
    assert len(ids) == len(set(ids)), "una institucion aparecio en mas de un nivel"
    assert ugel.conciliado()

    suma_niveles = sum(
        estado_resolucion(conn, PERIODO, n).total for n in NIVELES_CANONICOS
    )
    assert ugel.total == suma_niveles

    con_nivel = conn.execute(
        "SELECT count(*) AS n FROM institucion_educativa "
        "WHERE fn_nivel_canonico(nivel_modalidad) IS NOT NULL"
    ).fetchone()["n"]
    assert ugel.total == con_nivel


def test_tiempo_absoluto_de_la_ugel_se_calcula_una_sola_vez(conn):
    importar_nexus(conn, RUTA_NEXUS_JUNIO, CORTE_NEXUS)
    ugel = resumen_ugel(conn, PERIODO)
    e = estimar(ugel, declaracion=DECLARACION_VIGENTE)
    assert e.estado_calculo == "ESTIMADO"
    # El manual declarado se aplica tal cual al grano UGEL, sin escalar por institucion.
    assert e.tiempo_manual_min == (
        DECLARACION_VIGENTE.minutos_bajo,
        DECLARACION_VIGENTE.minutos_alto,
    )
    assert e.tiempo_asistido_min[0] < e.tiempo_manual_min[0]
