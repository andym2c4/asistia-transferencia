"""IF con cruces y ablación: reserva por institución, estrés sin etiquetas reales."""

from __future__ import annotations

import copy
import hashlib
import io
import math
import platform

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.feature_selection import VarianceThreshold
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from .reglas import BASICAS, VARIABLES, agregar, evaluar

VERSION = "IF_CRUCES_DIARIOS_2"
SEMILLA = 20260915
CUPO = 0.10
ESCENARIOS = (
    "presencia_sin_actividad",
    "no_laborable_con_actividad",
    "remuneracion",
    "mixto",
)
METODOS = {
    "if_cruces": "Isolation Forest con cruces",
    "if_basico": "Isolation Forest sin cruces",
    "referencia": "Mediana/IQR",
    "reglas": "Reglas directas",
}


def particion(identidad):
    clave = f"{SEMILLA}:{identidad['cod_mod']}:{identidad['anexo']}"
    valor = int(hashlib.sha256(clave.encode()).hexdigest()[:8], 16) / 2**32
    return (
        "ENTRENAMIENTO" if valor < 0.7 else "VALIDACION" if valor < 0.85 else "PRUEBA"
    )


def matriz(filas, features):
    return np.array(
        [[r["datos"]["features"][f] for f in features] for r in filas], dtype=float
    )


def ajustar(filas, features, arboles, muestra):
    x = matriz(filas, features)
    pipeline = Pipeline(
        [
            ("imputacion", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("variables", VarianceThreshold()),
            (
                "modelo",
                IsolationForest(
                    n_estimators=arboles,
                    max_samples=min(muestra, len(filas)),
                    contamination="auto",
                    random_state=SEMILLA,
                    n_jobs=1,
                ),
            ),
        ]
    )
    pipeline.fit(x)
    z = pipeline.named_steps["imputacion"].transform(x)
    q25, q75 = np.quantile(z, [0.25, 0.75], axis=0)
    return {
        "pipeline": pipeline,
        "features": list(features),
        "mediana": np.median(z, axis=0),
        "escala": np.where(q75 > q25, q75 - q25, 1.0),
        "variables_constantes": [
            f for i, f in enumerate(features) if np.ptp(z[:, i]) == 0
        ],
        "variables_activas": [f for i, f in enumerate(features) if np.ptp(z[:, i]) > 0],
    }


def puntuar(paquete, filas):
    x = matriz(filas, paquete["features"])
    z = paquete["pipeline"].named_steps["imputacion"].transform(x)
    return -paquete["pipeline"].score_samples(x), np.max(
        np.abs(z - paquete["mediana"]) / paquete["escala"], axis=1
    )


def score_reglas(filas):
    # Suma de tasas observadas; no asigna una categoría a los faltantes.
    campos = [f for f in VARIABLES if f.startswith("tasa_")]
    return np.array(
        [sum(r["datos"]["features"][f] or 0 for f in campos) for r in filas]
    )


def perturbacion(perfil, escenario):
    """Copia diaria coherente; no cambia calendario, período, identidad ni denominadores."""
    dias = copy.deepcopy(perfil["datos"]["dias"])
    cambios = []
    casos = ESCENARIOS[:-1] if escenario == "mixto" else (escenario,)
    for caso in casos:
        candidatos = [
            d
            for d in dias
            if not d["exclusion"]
            and (
                d["cruce_evaluable"]
                and d["actividad_esperada"] is False
                and d["declaracion"] != "PRESENCIA"
                if caso == "presencia_sin_actividad"
                else d["cruce_evaluable"]
                and d["actividad_esperada"] is True
                and d["declaracion"] != "NO_LABORABLE"
                if caso == "no_laborable_con_actividad"
                else d["cruce_evaluable"]
                and d["remuneracion_evaluable"]
                and d["calendario"].get("es_remunerado") is False
                and d["asistencia"].get("es_remunerado") is False
            )
        ]
        for d in candidatos[: max(1, math.ceil(len(candidatos) * 0.25))]:
            anterior = d["asistencia"]
            codigo, pago = {
                "presencia_sin_actividad": ("A", True),
                "no_laborable_con_actividad": ("F", False),
                "remuneracion": ("L", True),
            }[caso]
            ca = {
                "estado_asistencia_codigo": codigo,
                "es_remunerado": pago,
                "tipo_dia": "Copia artificial de prueba",
            }
            cambios.append(
                {"clave": d["clave"], "caso": caso, "antes": anterior, "despues": ca}
            )
            d["asistencia"] = ca
            d.update(evaluar(d["calendario"], ca))
    nuevo = {**perfil, "datos": {**agregar(dias), "dias": dias}}
    return nuevo, cambios


def preparar_estres(filas, semilla):
    salida = []
    for n, escenario in enumerate(ESCENARIOS):
        rng = np.random.default_rng(semilla + n)
        copias, cambios, indices = [], [], []
        for i in rng.permutation(len(filas)):
            copia, detalle = perturbacion(filas[i], escenario)
            if detalle:
                copias.append(copia)
                indices.append(int(i))
                cambios.append(
                    {
                        "perfil_base": str(filas[i]["perfil_id"]),
                        "antes": filas[i]["datos"]["features"],
                        "despues": copia["datos"]["features"],
                        "dias_modificados": len({c["clave"] for c in detalle}),
                    }
                )
            if len(copias) >= max(1, math.ceil(len(filas) * 0.2)):
                break
        salida.append(
            {
                "escenario": escenario,
                "filas": copias,
                "indices": indices,
                "cambios": cambios,
            }
        )
    return salida


def medir_estres(paquete, filas, estres, *, basico=None):
    base, referencia = puntuar(paquete, filas)
    originales = {
        "if_cruces": base,
        "referencia": referencia,
        "reglas": score_reglas(filas),
    }
    if basico:
        originales["if_basico"] = puntuar(basico, filas)[0]
    resultado = []
    for e in estres:
        copias = e["filas"]
        r = {
            "escenario": e["escenario"],
            "base_sin_etiquetas": len(filas),
            "copias": len(copias),
            "cambios": e["cambios"],
            "metodos": {},
        }
        if copias:
            if_score, ref_score = puntuar(paquete, copias)
            alterados = {
                "if_cruces": if_score,
                "referencia": ref_score,
                "reglas": score_reglas(copias),
            }
            if basico:
                alterados["if_basico"] = puntuar(basico, copias)[0]
            k = math.ceil(CUPO * (len(filas) + len(copias)))
            for metodo, original in originales.items():
                combinado = np.concatenate([original, alterados[metodo]])
                # Originales primero ante empate; nunca etiquetas para desempatar a favor de las copias.
                orden = np.argsort(-combinado, kind="stable")[:k]
                r["metodos"][metodo] = {
                    "cupo": k,
                    "copias_en_cupo": int(np.sum(orden >= len(filas))),
                    "pares_aumenta": int(
                        np.sum(alterados[metodo] > original[e["indices"]])
                    ),
                    "denominador": len(copias),
                }
        resultado.append(r)
    return resultado


def entrenar(perfiles):
    grupos = {
        g: [
            r
            for r in perfiles
            if r["datos"]["apto_modelo"] and particion(r["identidad"]) == g
        ]
        for g in ("ENTRENAMIENTO", "VALIDACION", "PRUEBA")
    }
    informe = {
        "version": VERSION,
        "semilla": SEMILLA,
        "presupuesto_revision": CUPO,
        "features": list(VARIABLES),
        "features_basicas": list(BASICAS),
        "particiones": {
            g: {
                "perfiles": len(rs),
                "instituciones": len({r["institucion_educativa_id"] for r in rs}),
                "ids": [str(r["perfil_id"]) for r in rs],
            }
            for g, rs in grupos.items()
        },
        "particion_metodo": "SHA256(20260915:cod_mod:anexo), 70/15/15 por institución; todos sus meses juntos",
        "alcance_evaluacion": "Comprobación técnica retrospectiva del corpus conocido, con separación computacional por institución; no una validación independiente de campo.",
        "utilidad_RRHH": "NO_MEDIDO",
        "precision_real": "NO_MEDIDO",
        "recall_real": "NO_MEDIDO",
        "python": platform.python_version(),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "joblib": joblib.__version__,
        "limites": [
            "Comparación entre instituciones del corpus, no predicción de meses futuros.",
            "Sin etiquetas independientes: el estrés mide reacción a cambios artificiales, no exactitud real.",
            "El modelo DRE anterior usa otra población/variables; la ablación diaria permite una comparación común.",
            "Cupo 10 % de perfiles puntuables por mes, no proporción real de irregularidades.",
            "Puntuaciones no son probabilidades ni órdenes de pago. Una alerta exige cotejar fuentes y excepciones.",
        ],
    }
    if (
        len(grupos["ENTRENAMIENTO"]) < 20
        or min(len(grupos[g]) for g in ("VALIDACION", "PRUEBA")) < 5
    ):
        return (
            None,
            {
                **informe,
                "estado": "SIN_MODELO",
                "motivo": "Se requieren 20 perfiles de entrenamiento y 5 en cada reserva por institución.",
            },
            [],
        )
    train, valid, test = (grupos[g] for g in ("ENTRENAMIENTO", "VALIDACION", "PRUEBA"))
    estres_valid = preparar_estres(valid, SEMILLA + 100)
    busqueda, candidatos = [], []
    for arboles in (100, 300):
        for muestra in (64, 256):
            try:
                paquete = ajustar(train, list(VARIABLES), arboles, muestra)
            except ValueError as e:
                # Sin variación las reglas siguen disponibles; nunca fabricar entrenamiento.
                if "variance threshold" not in str(e):
                    raise
                return (
                    None,
                    {
                        **informe,
                        "estado": "SIN_MODELO",
                        "motivo": "Las variables de entrenamiento no tienen variación.",
                    },
                    [],
                )
            medicion = medir_estres(paquete, valid, estres_valid)
            capturas = sum(
                r["metodos"].get("if_cruces", {}).get("copias_en_cupo", 0)
                for r in medicion
            )
            copias = sum(r["copias"] for r in medicion)
            busqueda.append(
                {
                    "arboles": arboles,
                    "muestra": muestra,
                    "copias_en_cupo": capturas,
                    "copias": copias,
                }
            )
            candidatos.append((capturas, -arboles, -muestra, paquete, medicion))
    if not any(b["copias"] for b in busqueda):
        return (
            None,
            {
                **informe,
                "estado": "SIN_MODELO",
                "motivo": "La reserva de validación no permite construir los escenarios de contraste.",
            },
            [],
        )
    _, a, m, ganador, _ = max(candidatos, key=lambda c: c[:3])
    config = {
        "n_estimators": -a,
        "max_samples": min(-m, len(train)),
        "muestra_propuesta": -m,
    }
    try:
        basico = ajustar(train, list(BASICAS), -a, -m)
    except ValueError as e:
        if "variance threshold" not in str(e):
            raise
        basico = None
    informe.update(
        estado="ENTRENADO",
        configuracion=config,
        busqueda=busqueda,
        criterio_seleccion="Máxima suma de copias en cupo en validación; empate por menos árboles y menor muestra.",
        ablacion="Misma configuración seleccionada y población; se retiran solo las variables de cruces."
        if basico
        else "NO_CALCULABLE: variables básicas sin variación en entrenamiento.",
        variables_constantes=ganador["variables_constantes"],
        variables_activas=ganador["variables_activas"],
        imputacion=ganador["pipeline"].named_steps["imputacion"].statistics_.tolist(),
        validacion=medir_estres(ganador, valid, estres_valid, basico=basico),
    )
    # Solo después de cerrar configuración se evalúa la reserva de prueba.
    informe["prueba"] = medir_estres(
        ganador, test, preparar_estres(test, SEMILLA + 200), basico=basico
    )
    resultados, por_mes = [], {}
    for mes in sorted({str(r["periodo"])[:7] for r in perfiles}):
        filas = [
            r
            for r in perfiles
            if str(r["periodo"])[:7] == mes and r["datos"]["apto_modelo"]
        ]
        if not filas:
            continue
        s, b = puntuar(ganador, filas)
        reglas = score_reglas(filas)
        bas = puntuar(basico, filas)[0] if basico else [None] * len(filas)
        orden = sorted(
            range(len(filas)),
            key=lambda i: (
                -s[i],
                filas[i]["identidad"]["cod_mod"],
                filas[i]["identidad"]["anexo"],
            ),
        )
        ranks = {i: pos + 1 for pos, i in enumerate(orden)}
        k = math.ceil(CUPO * len(filas))
        for i, r in enumerate(filas):
            resultados.append(
                {
                    "reporte_id": str(r["perfil_id"]),
                    "score_if": float(s[i]),
                    "score_referencia": float(b[i]),
                    "score_reglas": float(reglas[i]),
                    "score_basico": float(bas[i]) if bas[i] is not None else None,
                    "puesto": ranks[i],
                    "en_cupo": ranks[i] <= k,
                    "particion": particion(r["identidad"]),
                }
            )
        por_mes[mes] = {
            "reportes_puntuados": len(filas),
            "cupo_propuesto": k,
            "empates_en_umbral": int(sum(v == s[orden[k - 1]] for v in s)),
        }
    informe["por_mes_origen"] = por_mes
    paquete = {"completo": ganador, "basico": basico, "configuracion": informe}
    buffer = io.BytesIO()
    joblib.dump(paquete, buffer, compress=3)
    modelo = buffer.getvalue()
    informe["modelo_sha256"] = hashlib.sha256(modelo).hexdigest()
    return modelo, informe, resultados


def inferir(modelo, perfiles):
    """Solo bytes internos conservados por el servidor, nunca una subida del usuario."""
    paquete = joblib.load(io.BytesIO(modelo))
    if paquete["configuracion"]["sklearn"] != sklearn.__version__:
        raise ValueError(
            "Recupera la versión de scikit-learn conservada antes de inferir."
        )
    return puntuar(paquete["completo"], perfiles)[0]
