"""Estimacion del esfuerzo de RRHH a partir del estado de resolucion.

NO ES UNA MEDICION. M01 (tiempo activo manual vs. asistido) exige cronometrar tareas pareadas y
sigue `NO_MEDIDO`: nada de este modulo la completa. Lo que se produce aqui es una estimacion con
parametros declarados, bajo la etiqueta `ESTIMADO_DECLARADO`, que debe presentarse siempre junto a
sus supuestos y su banda de sensibilidad. Convertir esta salida en "el sistema reduce X %" sin
mostrar los supuestos seria exactamente lo que docs/CLAUDE.md prohibe.

El modelo es deliberadamente simple y auditable:

    esfuerzo_relativo = Sum(unidades_en_estado * peso_del_estado) / unidades_totales
    reduccion_estimada = 100 * (1 - esfuerzo_relativo)

Es decir: se supone que el tiempo humano es **proporcional a las unidades que todavia requieren
intervencion**, ponderadas por cuanto trabajo deja cada estado. Ese supuesto de proporcionalidad es
la debilidad principal del modelo y esta declarado en `Supuestos.limitaciones`.

Los pesos no son resultados: son juicios que se hacen explicitos para poder discutirlos y para
variarlos en la sensibilidad. El unico camino para sustituirlos por evidencia es la sesion de
medicion de M01 (docs/runbooks/SESION_OBSERVADA_RRHH.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .resolucion import ESTADOS_RESOLUCION, GRANO_UGEL, ResumenNivel

ETIQUETA = "ESTIMADO_DECLARADO"

# Conversion de la jornada usada para traducir semanas declaradas a minutos. Es un supuesto
# explicito, no un dato observado: si la jornada real de RRHH difiere, el absoluto cambia
# proporcionalmente.
HORAS_POR_JORNADA = 8
DIAS_LABORABLES_POR_SEMANA = 5
MINUTOS_POR_SEMANA = HORAS_POR_JORNADA * DIAS_LABORABLES_POR_SEMANA * 60  # 2400

# Unidades posibles del tiempo manual declarado por RRHH. Mientras no se confirme cual usa la
# declaracion, el calculo absoluto queda NO_CALCULABLE y solo se publica la reduccion relativa.
UNIDADES_DECLARACION = ("IE_MES", "NIVEL_MES", "UGEL_MES")


@dataclass
class Supuestos:
    """Pesos del modelo y sus limitaciones. Todo consumidor debe poder leer esto junto al numero."""

    peso_sin_recepcion: float = 0.60
    peso_recibido_sin_extraer: float = 0.45
    peso_extraido_pendiente: float = 0.30
    peso_revisado: float = 0.0
    justificacion: dict[str, str] = field(
        default_factory=lambda: {
            "SIN_RECEPCION": (
                "La institucion no envio nada: el sistema no extrae ni concilia, pero si dice de "
                "inmediato quien falta, trabajo que a mano exige cotejar listas. Se supone que "
                "evita parte del seguimiento, no todo."
            ),
            "RECIBIDO_SIN_EXTRAER": (
                "El documento llego y esta registrado con su procedencia, pero las personas aun no "
                "estan extraidas: queda casi todo el trabajo de lectura."
            ),
            "EXTRAIDO_PENDIENTE": (
                "Datos extraidos y alertas propuestas; a RRHH le queda decidir sobre lo senalado. "
                "Es el estado donde el sistema mas descarga trabajo y tambien el mas incierto."
            ),
            "REVISADO": (
                "Unidad terminada dentro del sistema, con salida descargable. No se le atribuye "
                "esfuerzo residual."
            ),
        }
    )
    limitaciones: tuple[str, ...] = (
        (
            "Supone que el tiempo humano es proporcional al numero de unidades pendientes; no lo "
            "es necesariamente (una sola institucion conflictiva puede costar mas que veinte "
            "simples)."
        ),
        "Los pesos son juicios declarados, no observaciones cronometradas.",
        (
            "No modela el trabajo compartido de nivel/mes (consolidar, exportar, tramitar) que "
            "ocurre una sola vez y no escala con el numero de instituciones."
        ),
        "No modela aprendizaje del operador ni la curva de adopcion.",
        (
            "No contabiliza aparte el esfuerzo humano ya invertido en llevar cada unidad hasta su "
            "estado actual (cargar el documento, reintentar una carga fallida): los pesos suponen "
            "que ese costo esta incluido en el residual del estado, no que sea cero."
        ),
        (
            "Compara un tiempo manual declarado para un mes TERMINADO contra el residual de un mes "
            "que no lo esta: al corte no hay ninguna unidad revisada, de modo que la cifra describe "
            "trabajo desplazado por el sistema, no un mes cerrado mas rapido."
        ),
        "n=0 sesiones observadas: ninguna cifra de aqui esta validada por RRHH.",
    )

    def peso(self, estado: str) -> float:
        return {
            "SIN_RECEPCION": self.peso_sin_recepcion,
            "RECIBIDO_SIN_EXTRAER": self.peso_recibido_sin_extraer,
            "EXTRAIDO_PENDIENTE": self.peso_extraido_pendiente,
            "REVISADO": self.peso_revisado,
        }[estado]


@dataclass
class Declaracion:
    """Tiempo manual declarado por RRHH. No es una medicion: es lo que alguien afirmo."""

    minutos_bajo: int
    minutos_alto: int
    unidad: str
    fuente: str
    fecha: str

    def __post_init__(self):
        if self.unidad not in UNIDADES_DECLARACION:
            raise ValueError(
                f"unidad debe ser una de {UNIDADES_DECLARACION}, no {self.unidad!r}"
            )
        if self.minutos_bajo <= 0 or self.minutos_alto < self.minutos_bajo:
            raise ValueError("el rango declarado debe ser positivo y creciente")
        if not self.fuente.strip() or not self.fecha.strip():
            raise ValueError("una declaracion sin fuente ni fecha no es citable")


def semanas(bajo: float, alto: float, **kwargs) -> Declaracion:
    """Declaracion expresada en semanas de jornada completa, convertida a minutos."""
    return Declaracion(
        minutos_bajo=round(bajo * MINUTOS_POR_SEMANA),
        minutos_alto=round(alto * MINUTOS_POR_SEMANA),
        **kwargs,
    )


# Declaracion vigente de tiempo manual. Lo que se afirmo, no lo que se midio:
# "aprox una semana y media completa de las 4 del mes para lograr ese consolidado para todas las
# instituciones" (usuario del proyecto, refiriendo el trabajo de RRHH de la UGEL Luya, 2026-09-14).
# El rango 1-2 semanas envuelve ese "aproximadamente"; el centro coincide con 1,5 semanas.
DECLARACION_VIGENTE = semanas(
    1.0,
    2.0,
    unidad="UGEL_MES",
    fuente="Declaracion del usuario en sesion, refiriendo a RRHH UGEL Luya (no cronometrada)",
    fecha="2026-09-14",
)


@dataclass
class Estimacion:
    etiqueta: str
    periodo: str
    nivel: str
    universo: str
    unidades: int
    por_estado: dict[str, int]
    esfuerzo_relativo: float | None
    reduccion_pct: float | None
    reduccion_pct_banda: tuple[float, float] | None
    tiempo_manual_min: tuple[int, int] | None
    tiempo_asistido_min: tuple[float, float] | None
    estado_calculo: str
    supuestos: Supuestos
    declaracion: Declaracion | None
    nota: str


_SENSIBILIDAD = (
    0.5  # +-50% sobre cada peso para la banda; declarado, no ajustado a posteriori
)


def estimar(
    resumen: ResumenNivel,
    supuestos: Supuestos | None = None,
    declaracion: Declaracion | None = None,
) -> Estimacion:
    """Estimacion de esfuerzo para un nivel-periodo ya calculado por `estado_resolucion`.

    Sin unidades en el universo devuelve `NO_APLICA` (denominador cero), nunca 0 %.
    Sin declaracion de tiempo manual, la parte absoluta queda `NO_CALCULABLE` pero la reduccion
    relativa si se publica, porque solo depende de los pesos declarados.
    """
    supuestos = supuestos or Supuestos()
    base = dict.fromkeys(ESTADOS_RESOLUCION, 0) | resumen.por_estado
    unidades = resumen.total

    if unidades == 0:
        return Estimacion(
            etiqueta=ETIQUETA,
            periodo=resumen.periodo.isoformat(),
            nivel=resumen.nivel,
            universo=resumen.universo,
            unidades=0,
            por_estado=base,
            esfuerzo_relativo=None,
            reduccion_pct=None,
            reduccion_pct_banda=None,
            tiempo_manual_min=None,
            tiempo_asistido_min=None,
            estado_calculo="NO_APLICA",
            supuestos=supuestos,
            declaracion=declaracion,
            nota="No hay instituciones de este nivel en el universo: denominador cero.",
        )

    def relativo(factor: float) -> float:
        total = sum(
            base[e] * min(1.0, supuestos.peso(e) * factor) for e in ESTADOS_RESOLUCION
        )
        return total / unidades

    central = relativo(1.0)
    optimista = relativo(1.0 - _SENSIBILIDAD)  # el sistema descarga mas de lo supuesto
    pesimista = relativo(1.0 + _SENSIBILIDAD)  # descarga menos

    reduccion = 100 * (1 - central)
    banda = (100 * (1 - pesimista), 100 * (1 - optimista))

    manual = asistido = None
    estado = "ESTIMADO"
    nota = (
        "Reduccion relativa derivada de los pesos declarados y del estado de resolucion real. "
        "No es tiempo medido."
    )
    if base["SIN_RECEPCION"] == unidades:
        # Sin un solo documento recibido, la cifra no describe trabajo de extraccion evitado:
        # depende por completo del supuesto de que saber quien falta ahorra seguimiento.
        nota += (
            " ATENCION: ninguna institucion de este nivel envio documento en el periodo. La "
            "reduccion mostrada proviene enteramente del supuesto sobre SIN_RECEPCION "
            "(peso_sin_recepcion), no de trabajo de extraccion o revision realmente evitado; "
            "no debe presentarse como rendimiento del sistema sobre este nivel."
        )
    if declaracion is None:
        estado = "ESTIMADO_SIN_TIEMPO_ABSOLUTO"
        nota += (
            " Falta la declaracion de tiempo manual de RRHH (y su unidad): el tiempo absoluto "
            "queda NO_CALCULABLE."
        )
    elif declaracion.unidad == "UGEL_MES" and resumen.nivel != GRANO_UGEL:
        # Un tiempo declarado para TODA la UGEL no puede atribuirse a un solo nivel: sumar los
        # siete niveles daria siete veces el tiempo real. Solo el resumen agregado lo admite.
        estado = "NO_APLICA_A_ESTE_GRANO"
        nota += (
            f" La declaracion es por UGEL-mes y este resumen es del nivel {resumen.nivel}: el "
            "tiempo absoluto no se calcula aqui para no multiplicarlo por cada nivel. Usar el "
            "resumen agregado (resumen_ugel) para el tiempo de toda la UGEL."
        )
    else:
        escala = unidades if declaracion.unidad == "IE_MES" else 1
        manual = (declaracion.minutos_bajo * escala, declaracion.minutos_alto * escala)
        # Banda mas ancha honesta: el extremo bajo usa el supuesto optimista sobre el minimo
        # declarado, el alto usa el pesimista sobre el maximo declarado.
        asistido = (manual[0] * optimista, manual[1] * pesimista)

    return Estimacion(
        etiqueta=ETIQUETA,
        periodo=resumen.periodo.isoformat(),
        nivel=resumen.nivel,
        universo=resumen.universo,
        unidades=unidades,
        por_estado=base,
        esfuerzo_relativo=round(central, 4),
        reduccion_pct=round(reduccion, 1),
        reduccion_pct_banda=(round(banda[0], 1), round(banda[1], 1)),
        tiempo_manual_min=manual,
        tiempo_asistido_min=asistido,
        estado_calculo=estado,
        supuestos=supuestos,
        declaracion=declaracion,
        nota=nota,
    )
