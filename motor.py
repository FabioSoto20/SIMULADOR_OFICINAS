"""Motor auditable del simulador de cierre de oficinas.

No depende de Streamlit: permite pruebas unitarias y reutilizacion futura.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

DIAS_LABORALES = 24
RADIO_TIERRA_KM = 6371.0088
CANALS = {
    "Ventanilla": {
        "tickets": "TKT VNT",
        "tma": "TIEMPO ATENCION VNT",
        "estructura": "EST AU",
        "hu_reportada": "HORAS UTILES VNT",
    },
    "Plataforma": {
        "tickets": "TKT PLT",
        "tma": "TIEMPO ATENCION PLT",
        "estructura": "EST EBP",
        "hu_reportada": "HORAS UTILES PLT",
    },
}
REQUIRED_COLUMNS = [
    "COD OF", "OFICINA", "DIVISIÓN", "LATITUD", "LONGITUD",
    *[c for cols in CANALS.values() for c in cols.values()],
]
NUMERIC_COLS = list(dict.fromkeys([
    "LATITUD", "LONGITUD", *[c for cols in CANALS.values() for c in cols.values()]
]))
COLUMNS_DETAIL = [
    "codigo", "oficina", "division", "canal", "distancia_km", "participacion_pct",
    "tickets_actuales", "tickets_recibidos", "tickets_futuros", "tiempo_min",
    "estructura_actual", "horas_actuales", "horas_futuras", "hu_referencia",
    "estructura_requerida_actual", "estructura_requerida_futura", "deficit_previo",
    "deficit_total", "adicional_cierre", "dictamen", "observacion",
]


def _number_series(col: pd.Series) -> pd.Series:
    """Convierte coma decimal y marcadores de ausencia; no presupone que vacios sean 0."""
    s = col.astype(str).str.strip().str.replace("\u00a0", "", regex=False)
    s = s.replace({"": np.nan, "#N/A": np.nan, "#NA": np.nan,
                   "N/A": np.nan, "NA": np.nan, "nan": np.nan,
                   "None": np.nan, "NULL": np.nan, "-": np.nan})
    return pd.to_numeric(s.str.replace(",", ".", regex=False), errors="coerce")


def cargar_base(csv: str | Path | BinaryIO) -> pd.DataFrame:
    """Lee la base v2 y valida su esquema sin ocultar valores faltantes."""
    df = pd.read_csv(csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    faltan = sorted(set(REQUIRED_COLUMNS) - set(df.columns))
    if faltan:
        raise ValueError("Faltan columnas: " + ", ".join(faltan))
    df["COD OF"] = df["COD OF"].str.strip()
    df["OFICINA"] = df["OFICINA"].str.strip()
    df["DIVISIÓN"] = df["DIVISIÓN"].str.strip()
    if df["COD OF"].eq("").any():
        raise ValueError("La base contiene COD OF vacios")
    if df["COD OF"].duplicated().any():
        duplicated = df.loc[df["COD OF"].duplicated(), "COD OF"].tolist()
        raise ValueError(f"COD OF repetidos: {duplicated[:8]}")
    for c in NUMERIC_COLS:
        df[c] = _number_series(df[c])
    # TKT y estructura deben ser cantidades enteras y no negativas.
    for c in ("TKT VNT", "TKT PLT", "EST AU", "EST EBP"):
        bad = df[c].notna() & ((df[c] < 0) | ((df[c] - df[c].round()).abs() > 1e-7))
        df.loc[bad, c] = np.nan
    for c in ("TIEMPO ATENCION VNT", "TIEMPO ATENCION PLT"):
        df.loc[df[c].notna() & (df[c] <= 0), c] = np.nan
    geo = (
        df["LATITUD"].between(-90, 90) & df["LONGITUD"].between(-180, 180)
        & ~((df["LATITUD"] == 0) & (df["LONGITUD"] == 0))
    )
    df.loc[~geo, ["LATITUD", "LONGITUD"]] = np.nan
    return df


def distancia_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en linea recta sobre la esfera terrestre, kilometros."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def oficinas_cercanas(base: pd.DataFrame, codigo_cierre: str, radio_km: float) -> pd.DataFrame:
    if not (math.isfinite(radio_km) and radio_km > 0):
        raise ValueError("El radio debe ser positivo")
    origen = base.loc[base["COD OF"] == str(codigo_cierre)]
    if origen.empty:
        raise ValueError("Oficina de cierre no encontrada")
    origen = origen.iloc[0]
    if pd.isna(origen["LATITUD"]) or pd.isna(origen["LONGITUD"]):
        raise ValueError("La oficina a cerrar no tiene coordenadas validas")
    validas = base.loc[
        (base["COD OF"] != str(codigo_cierre)) & base["LATITUD"].notna()
        & base["LONGITUD"].notna()
    ].copy()
    validas["distancia_km"] = validas.apply(
        lambda f: distancia_haversine(float(origen["LATITUD"]), float(origen["LONGITUD"]),
                                     float(f["LATITUD"]), float(f["LONGITUD"])), axis=1,
    )
    return (validas.loc[validas["distancia_km"] <= radio_km + 1e-10]
            .sort_values(["distancia_km", "COD OF"], kind="stable").reset_index(drop=True))


def pesos_cercania(distancias: list[float]) -> list[float]:
    """Peso 1/d; si hay distancia cero, solo esas oficinas participan."""
    if not distancias:
        return []
    if any(d < 0 or not math.isfinite(d) for d in distancias):
        raise ValueError("Distancias invalidas")
    ceros = [d < 1e-9 for d in distancias]
    if any(ceros):
        n = sum(ceros)
        return [1.0 / n if zero else 0.0 for zero in ceros]
    inversos = [1.0 / d for d in distancias]
    total = math.fsum(inversos)
    return [w / total for w in inversos]


def redistribuir_enteros(total: int, pesos: list[float], codigos: list[str],
                         distancias: list[float]) -> list[int]:
    """Metodo de mayores restos con desempate por distancia y codigo."""
    if not pesos:
        return []
    if total < 0 or len(pesos) != len(codigos) or len(pesos) != len(distancias):
        raise ValueError("Parametros de distribucion invalidos")
    cuotas = [total * w for w in pesos]
    asignados = [int(math.floor(q)) for q in cuotas]
    pendientes = total - sum(asignados)
    orden = sorted(range(len(pesos)),
                   key=lambda i: (-(cuotas[i] - asignados[i]), distancias[i], str(codigos[i])))
    for i in orden[:pendientes]:
        asignados[i] += 1
    assert sum(asignados) == total
    return asignados


def _numero_valido(n: object, positivo: bool = False) -> bool:
    try:
        x = float(n)
        return math.isfinite(x) and (x > 0 if positivo else x >= 0)
    except (ValueError, TypeError):
        return False


def _personas_requeridas(tickets: int, tiempo_min: float, umbral: float,
                          dias: int) -> int:
    # La tolerancia protege un resultado matematicamente entero de errores float.
    return max(0, math.ceil(tickets * tiempo_min / (60 * dias * umbral) - 1e-10))


def _calcular_canal(fila: pd.Series, canal: str, recibidos: int | None,
                    umbral: float, dias: int) -> dict:
    cols = CANALS[canal]
    tk, tma, estructura = fila[cols["tickets"]], fila[cols["tma"]], fila[cols["estructura"]]
    hu_ref = fila[cols["hu_reportada"]]
    tk_valido = _numero_valido(tk) and float(tk).is_integer()
    tma_valido = _numero_valido(tma, positivo=True)
    estructura_valida = _numero_valido(estructura, positivo=True) and float(estructura).is_integer()
    observaciones = []
    if not tk_valido:
        observaciones.append("Tickets actuales sin dato valido")
    if not tma_valido:
        observaciones.append("Tiempo de atencion sin dato valido")
    if not estructura_valida:
        observaciones.append("Estructura sin dato valido o igual a cero")
    if recibidos is None:
        observaciones.append("Tickets de origen sin dato; no se puede proyectar")
    tkt_actual = int(tk) if tk_valido else None
    tkt_futuro = tkt_actual + recibidos if tkt_actual is not None and recibidos is not None else None
    est = int(estructura) if estructura_valida else None
    tma_float = float(tma) if tma_valido else None
    hu_actual = None
    hu_futura = None
    req_actual = None
    req_futura = None
    deficit_previo = None
    deficit_total = None
    adicionales = None
    dictamen = "Sin datos suficientes"
    if tk_valido and tma_valido and estructura_valida:
        hu_actual = tkt_actual * tma_float / (60 * dias * est)
        req_actual = _personas_requeridas(tkt_actual, tma_float, umbral, dias)
        deficit_previo = max(0, req_actual - est)
        if _numero_valido(hu_ref) and abs(hu_actual - float(hu_ref)) > 0.10:
            observaciones.append("Horas de referencia difieren mas de 0,10 h del calculo")
        if tkt_futuro is not None:
            hu_futura = tkt_futuro * tma_float / (60 * dias * est)
            req_futura = _personas_requeridas(tkt_futuro, tma_float, umbral, dias)
            deficit_total = max(0, req_futura - est)
            adicionales = deficit_total - deficit_previo
            if adicionales > 0 and deficit_previo > 0:
                dictamen = "Deficit previo + crecimiento por cierre"
            elif adicionales > 0:
                dictamen = "Crecimiento por cierre"
            elif deficit_previo > 0:
                dictamen = "Deficit previo; no crece por cierre"
            else:
                dictamen = "Sin crecimiento"
    return {
        "tickets_actuales": tkt_actual,
        "tickets_recibidos": recibidos,
        "tickets_futuros": tkt_futuro,
        "tiempo_min": tma_float,
        "estructura_actual": est,
        "horas_actuales": hu_actual,
        "horas_futuras": hu_futura,
        "hu_referencia": float(hu_ref) if _numero_valido(hu_ref) else None,
        "estructura_requerida_actual": req_actual,
        "estructura_requerida_futura": req_futura,
        "deficit_previo": deficit_previo,
        "deficit_total": deficit_total,
        "adicional_cierre": adicionales,
        "dictamen": dictamen,
        "observacion": "; ".join(observaciones),
    }


def _resumen_oficina(detalle: pd.DataFrame) -> pd.DataFrame:
    cols = ["codigo", "oficina", "division", "distancia_km", "participacion_pct",
            "ventanilla_actual", "ventanilla_recibida", "ventanilla_futura",
            "plataforma_actual", "plataforma_recibida", "plataforma_futura",
            "hu_vnt_actual", "hu_vnt_futura", "hu_plt_actual", "hu_plt_futura",
            "deficit_previo", "adicional_ventanilla", "adicional_plataforma",
            "adicional_total", "dictamen_general"]
    registros = []
    for codigo, g in detalle.groupby("codigo", sort=False):
        v = g.loc[g["canal"] == "Ventanilla"].iloc[0]
        p = g.loc[g["canal"] == "Plataforma"].iloc[0]
        conocidos = g["adicional_cierre"].dropna()
        incompleto = len(conocidos) < 2
        tiene_crecimiento = (conocidos > 0).any()
        deficit = g["deficit_previo"].fillna(0).sum()
        if tiene_crecimiento:
            dictamen = "Deficit previo + cierre" if deficit > 0 else "Crecimiento por cierre"
        elif deficit > 0:
            dictamen = "Deficit previo"
        else:
            dictamen = "Sin crecimiento"
        if incompleto:
            dictamen += " (informacion parcial)" if tiene_crecimiento or deficit > 0 else " (no concluyente)"
        registros.append({
            "codigo": codigo, "oficina": v["oficina"], "division": v["division"],
            "distancia_km": v["distancia_km"], "participacion_pct": v["participacion_pct"],
            "ventanilla_actual": v["tickets_actuales"], "ventanilla_recibida": v["tickets_recibidos"],
            "ventanilla_futura": v["tickets_futuros"],
            "plataforma_actual": p["tickets_actuales"], "plataforma_recibida": p["tickets_recibidos"],
            "plataforma_futura": p["tickets_futuros"],
            "hu_vnt_actual": v["horas_actuales"], "hu_vnt_futura": v["horas_futuras"],
            "hu_plt_actual": p["horas_actuales"], "hu_plt_futura": p["horas_futuras"],
            "deficit_previo": g["deficit_previo"].sum(min_count=1),
            "adicional_ventanilla": v["adicional_cierre"],
            "adicional_plataforma": p["adicional_cierre"],
            "adicional_total": g["adicional_cierre"].sum(min_count=1),
            "dictamen_general": dictamen,
        })
    return pd.DataFrame(registros, columns=cols)


@dataclass
class Resultado:
    origen: dict
    detalle: pd.DataFrame
    resumen: pd.DataFrame
    receptores: pd.DataFrame
    balance: pd.DataFrame
    alertas: list[str]
    parametros: dict


def simular(base: pd.DataFrame, codigo_cierre: str, candidatos: pd.DataFrame,
            codigos_receptoras: list[str], porcentaje_ventanilla: float = 100.0,
            porcentaje_plataforma: float = 100.0, umbral: float = 7.0,
            dias: int = DIAS_LABORALES, radio_km: float = 3.0,
            max_receptoras: int = 5) -> Resultado:
    if not 0 < umbral <= 24 or dias <= 0:
        raise ValueError("Umbral o dias laborales invalidos")
    if not all(0 <= x <= 100 for x in (porcentaje_ventanilla, porcentaje_plataforma)):
        raise ValueError("Los porcentajes deben estar entre 0 y 100")
    if len(codigos_receptoras) > max_receptoras:
        raise ValueError("Se excedio el maximo de receptoras")
    if len(set(codigos_receptoras)) != len(codigos_receptoras):
        raise ValueError("No se pueden repetir receptoras")
    if not set(codigos_receptoras).issubset(set(candidatos["COD OF"])):
        raise ValueError("Hay oficinas receptoras fuera del radio")
    origen = base.loc[base["COD OF"] == str(codigo_cierre)]
    if origen.empty:
        raise ValueError("Oficina de cierre no encontrada")
    origen = origen.iloc[0]
    receptoras = candidatos.loc[candidatos["COD OF"].isin(codigos_receptoras)].copy()
    receptoras = receptoras.sort_values(["distancia_km", "COD OF"]).reset_index(drop=True)
    distancias = receptoras["distancia_km"].astype(float).tolist()
    codigos = receptoras["COD OF"].tolist()
    pesos = pesos_cercania(distancias)
    receptoras["participacion_pct"] = [x * 100 for x in pesos]
    pct = {"Ventanilla": porcentaje_ventanilla, "Plataforma": porcentaje_plataforma}
    distribuciones = {}
    balance = []
    alertas = []
    if not codigos:
        alertas.append("No hay oficinas receptoras seleccionadas: amplie el radio o seleccione alguna.")
    for canal, cfg in CANALS.items():
        t_origen = origen[cfg["tickets"]]
        disponible = _numero_valido(t_origen) and float(t_origen).is_integer()
        if not disponible and pct[canal] > 0:
            plan = None
            asignaciones = [None] * len(codigos)
            asignado = None
            alerta = f"{canal}: la oficina de cierre no tiene tickets validos; no se simula este canal."
            alertas.append(alerta)
        else:
            plan = int(math.floor((int(t_origen) if disponible else 0) * pct[canal] / 100 + 0.5))
            asignaciones = redistribuir_enteros(plan, pesos, codigos, distancias)
            asignado = sum(asignaciones)
        distribuciones[canal] = asignaciones
        balance.append({
            "canal": canal,
            "tickets_origen": int(t_origen) if disponible else None,
            "porcentaje_redistribucion": pct[canal],
            "tickets_planeados": plan,
            "tickets_asignados": asignado,
            "tickets_no_redistribuidos": (int(t_origen) - plan) if disponible and plan is not None else None,
            "tickets_sin_receptora": (plan - asignado) if plan is not None and asignado is not None else None,
        })
    registros = []
    for i, (_, fila) in enumerate(receptoras.iterrows()):
        for canal in CANALS:
            calculo = _calcular_canal(fila, canal, distribuciones[canal][i], umbral, dias)
            registro = {
                "codigo": fila["COD OF"], "oficina": fila["OFICINA"],
                "division": fila["DIVISIÓN"], "canal": canal,
                "distancia_km": fila["distancia_km"],
                "participacion_pct": fila["participacion_pct"],
                **calculo,
            }
            if calculo["observacion"]:
                alertas.append(f"{fila['OFICINA']} (cod. {fila['COD OF']}), {canal}: {calculo['observacion']}")
            registros.append(registro)
    detalle = pd.DataFrame(registros, columns=COLUMNS_DETAIL)
    resumen = _resumen_oficina(detalle)
    parametros = {
        "Oficina cerrada": str(origen["OFICINA"]),
        "Codigo oficina cerrada": str(codigo_cierre),
        "Radio de busqueda (km)": radio_km,
        "Maximo receptoras": max_receptoras,
        "Receptoras seleccionadas": len(codigos),
        "Umbral (h/dia)": umbral,
        "Dias laborales al mes": dias,
        "Redistribucion ventanilla (%)": porcentaje_ventanilla,
        "Redistribucion plataforma (%)": porcentaje_plataforma,
        "Metodo": "Inverso a distancia Haversine, con asignacion por mayores restos",
        "Reasignacion personal": "No",
    }
    return Resultado(
        origen=origen.to_dict(), detalle=detalle, resumen=resumen,
        receptores=receptoras, balance=pd.DataFrame(balance),
        alertas=alertas, parametros=parametros,
    )


def auditoria_base(base: pd.DataFrame) -> pd.DataFrame:
    report = []
    for canal, cols in CANALS.items():
        for _, row in base.iterrows():
            issues = []
            t, m, e = row[cols["tickets"]], row[cols["tma"]], row[cols["estructura"]]
            if not _numero_valido(t):
                issues.append("Tickets no disponibles")
            if not _numero_valido(m, positivo=True):
                issues.append("Tiempo de atencion no disponible")
            if not _numero_valido(e, positivo=True):
                issues.append("Estructura no disponible o cero")
            if all((_numero_valido(t), _numero_valido(m, positivo=True), _numero_valido(e, positivo=True))):
                hu = float(t) * float(m) / (60 * DIAS_LABORALES * float(e))
                rep = row[cols["hu_reportada"]]
                if _numero_valido(rep) and abs(hu - float(rep)) > .10:
                    issues.append("Diferencia >0,1 h frente a HU reportada")
            if issues:
                report.append({"codigo": row["COD OF"], "oficina": row["OFICINA"],
                               "canal": canal, "observacion": "; ".join(issues)})
    for _, row in base.loc[base["LATITUD"].isna() | base["LONGITUD"].isna()].iterrows():
        report.append({"codigo": row["COD OF"], "oficina": row["OFICINA"],
                       "canal": "Geografia", "observacion": "Coordenadas no disponibles"})
    return pd.DataFrame(report, columns=["codigo", "oficina", "canal", "observacion"])


def excel_resultados(resultado: Resultado, auditoria: pd.DataFrame,
                     candidatos: pd.DataFrame) -> bytes:
    """Workbook en memoria: hoja ejecutiva, detalle, trazabilidad y alertas."""
    out = BytesIO()
    hojas = {
        "Resumen": resultado.resumen,
        "Detalle canales": resultado.detalle,
        "Balance tickets": resultado.balance,
        "Parametros": pd.DataFrame(list(resultado.parametros.items()), columns=["Parametro", "Valor"]),
        "Oficinas cercanas": candidatos[["COD OF", "OFICINA", "DIVISIÓN", "distancia_km"]].assign(
            seleccionada=lambda d: d["COD OF"].isin(resultado.receptores["COD OF"])),
        "Alertas simulacion": pd.DataFrame({"Alerta": resultado.alertas}),
        "Calidad base": auditoria,
    }
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for hoja, data in hojas.items():
            data.to_excel(writer, sheet_name=hoja, index=False)
            ws = writer.sheets[hoja]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for c in ws[1]:
                c.fill = PatternFill("solid", fgColor="073A74")
                c.font = Font(color="FFFFFF", bold=True)
                c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[1].height = 30
            for j, name in enumerate(data.columns, start=1):
                col = get_column_letter(j)
                width = max(len(str(name)) + 2, 13)
                if len(data) > 0:
                    subset = data[name].dropna().astype(str).str.len()
                    if len(subset):
                        width = max(width, min(int(subset.quantile(.85)) + 2, 52))
                ws.column_dimensions[col].width = min(width, 55)
                if name in ("distancia_km", "horas_actuales", "horas_futuras",
                            "hu_referencia", "tiempo_min", "participacion_pct"):
                    for cells in ws.iter_cols(min_col=j, max_col=j, min_row=2):
                        for cell in cells:
                            cell.number_format = "0.00"
    return out.getvalue()
