"""Herramientas auxiliares disponibles solo con configuración local explícita."""

HERRAMIENTAS = (
    (
        "documentos",
        "Documentos",
        "Cargar fuentes y consultar el historial de recepción.",
    ),
    (
        "cobertura",
        "Cobertura del mes",
        "Consultar el desglose de recepción por nivel y mes.",
    ),
    (
        "cierre",
        "Cierre del proyecto",
        "Revisar lotes, fuentes y decisiones del cierre técnico.",
    ),
    (
        "calendarios",
        "Calendarios",
        "Explorar el catálogo anual de calendarios y sus versiones.",
    ),
    (
        "resultados",
        "Resultados del proyecto",
        "Consultar registros de trabajo y evidencias de evaluación.",
    ),
    (
        "monitoreo",
        "Monitoreo · Isolation Forest",
        "Explorar modelos, cortes y evaluaciones técnicas.",
    ),
    (
        "monitoreo_leyendas",
        "Monitoreo · Leyendas",
        "Comprobar la extracción de códigos y descripciones.",
    ),
    (
        "experimental",
        "Perfiles experimentales",
        "Preparar corpus y experimentos de investigación.",
    ),
)


def es_herramienta(endpoint):
    return endpoint in ENDPOINTS or bool(
        endpoint and endpoint.startswith(("pages.monitoreo", "pages.experimental"))
    )


# El detalle de un reporte, su carga y el calendario institucional pertenecen
# al recorrido operativo. No bloquear por prefijos de URL compartidos.
ENDPOINTS = frozenset(
    "pages." + nombre
    for nombre in (
        "desarrollo",
        *(h[0] for h in HERRAMIENTAS),
        "cargar",
        "cobertura_csv",
        "preparar_cierre",
        "cierre_csv",
        "clasificar_codigo_calendario",
        "trabajo",
        "registro_csv",
        "evidencia_trabajo",
    )
)
