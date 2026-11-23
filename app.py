"""Dashboard de impacto por cierre de oficinas.

Ejecucion: python -m streamlit run app.py
"""
from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from motor import (
    DIAS_LABORALES,
    auditoria_base,
    cargar_base,
    excel_resultados,
    oficinas_cercanas,
    simular,
)

ARCHIVO_BASE = Path(__file__).with_name("BASE CIERRE OFICINAS - BASE TOTAL_v2.csv")

st.set_page_config(
    page_title="Simulador de cierre de oficinas",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container {padding-top: 1.7rem; padding-bottom: 3rem;}
h1 {font-size: 2.15rem !important; color: #073A74;}
h2, h3 {color: #073A74;}
[data-testid="stMetric"] {background: #f3f7fc; border: 1px solid #dbe6f4;
  border-radius: 12px; padding: 12px 16px;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def leer_csv(contenido: bytes) -> pd.DataFrame:
    return cargar_base(BytesIO(contenido))


@st.cache_data(show_spinner=False)
def revisar_calidad(contenido: bytes) -> pd.DataFrame:
    return auditoria_base(leer_csv(contenido))


def fmt_entero(valor: object) -> str:
    if pd.isna(valor):
        return "Sin dato"
    return f"{int(valor):,}".replace(",", ".")


def fmt_horas(valor: object) -> str:
    if pd.isna(valor):
        return "Sin dato"
    return f"{float(valor):.2f}".replace(".", ",") + " h"


def formatear_tabla(df: pd.DataFrame, cambios: dict) -> pd.DataFrame:
    vista = df.rename(columns=cambios).copy()
    for c in vista.select_dtypes(include="number").columns:
        if any(s in c.lower() for s in ("distancia", "participación", "horas", "tiempo")):
            vista[c] = vista[c].round(2)
        elif any(s in c.lower() for s in ("tickets", "estructura", "déficit", "adicional", "personal")):
            vista[c] = vista[c].astype("Int64")
    return vista


def mapa_callejero(origen: dict, receptoras: pd.DataFrame, resumen: pd.DataFrame) -> None:
    lat0, lon0 = float(origen["LATITUD"]), float(origen["LONGITUD"])
    m = folium.Map(location=[lat0, lon0], zoom_start=12, tiles="OpenStreetMap", control_scale=True)
    folium.Marker(
        [lat0, lon0],
        tooltip=f"CIERRE: {escape(str(origen['OFICINA']))}",
        popup=f"<b>Oficina a cerrar:</b> {escape(str(origen['OFICINA']))}",
        icon=folium.Icon(color="red", icon="remove-sign"),
    ).add_to(m)
    puntos = [[lat0, lon0]]
    resumen_idx = resumen.set_index("codigo") if not resumen.empty else pd.DataFrame()
    for _, rec in receptoras.iterrows():
        lat, lon = float(rec["LATITUD"]), float(rec["LONGITUD"])
        p = float(rec["participacion_pct"])
        cod = rec["COD OF"]
        row = resumen_idx.loc[cod] if not resumen.empty else None
        contenido = (
            f"<b>{escape(str(rec['OFICINA']))}</b><br>"
            f"Código: {escape(str(cod))}<br>"
            f"Distancia: {rec['distancia_km']:.2f} km<br>"
            f"Participación: {p:.2f}%<br>"
            f"Tickets VNT recibidos: {fmt_entero(row['ventanilla_recibida']) if row is not None else '—'}<br>"
            f"Tickets PLT recibidos: {fmt_entero(row['plataforma_recibida']) if row is not None else '—'}"
        )
        folium.PolyLine([[lat0, lon0], [lat, lon]], weight=2, opacity=0.45,
                        color="#1478bd", dash_array="6").add_to(m)
        folium.CircleMarker([lat, lon], radius=8 + 12 * p / 100,
                            color="#065cae", fill=True, fill_color="#2b93dd",
                            fill_opacity=.85, popup=folium.Popup(contenido, max_width=350),
                            tooltip=f"{rec['OFICINA']} · {p:.1f}%").add_to(m)
        puntos.append([lat, lon])
    if len(puntos) > 1:
        m.fit_bounds(puntos, padding=(35, 35))
    components.html(m.get_root().render(), height=560, scrolling=False)


def mapa_sin_conexion(origen: dict, receptoras: pd.DataFrame, resumen: pd.DataFrame) -> None:
    fig = go.Figure()
    lat0, lon0 = float(origen["LATITUD"]), float(origen["LONGITUD"])
    for _, rec in receptoras.iterrows():
        fig.add_trace(go.Scatter(
            x=[lon0, float(rec["LONGITUD"])],
            y=[lat0, float(rec["LATITUD"])],
            mode="lines", line=dict(color="#b8cfe5", width=1.7, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ))
    fig.add_trace(go.Scatter(
        x=[lon0], y=[lat0], mode="markers+text", name="Oficina a cerrar",
        text=[str(origen["OFICINA"])], textposition="top center",
        marker=dict(color="#d72638", size=17, symbol="x"),
        hovertemplate="<b>CIERRE: %{text}</b><extra></extra>",
    ))
    if not receptoras.empty:
        names = receptoras["OFICINA"].astype(str).tolist()
        codes = receptoras["COD OF"].astype(str).tolist()
        distances = receptoras["distancia_km"].tolist()
        shares = receptoras["participacion_pct"].tolist()
        hover = [f"{escape(n)} (cód. {c})<br>{d:.2f} km · {p:.2f}%"
                 for n, c, d, p in zip(names, codes, distances, shares)]
        fig.add_trace(go.Scatter(
            x=receptoras["LONGITUD"], y=receptoras["LATITUD"],
            mode="markers+text", name="Oficinas receptoras", text=names,
            textposition="bottom center", textfont=dict(size=10),
            marker=dict(color="#176fba", size=[11 + p * .16 for p in shares],
                        opacity=.9),
            hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
        ))
    fig.update_layout(
        title="Distribución geográfica sin conexión a servidores cartográficos",
        xaxis_title="Longitud", yaxis_title="Latitud",
        xaxis=dict(showgrid=True, gridcolor="#e0e8f3", zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="#e0e8f3", zeroline=False,
                   scaleanchor="x", scaleratio=1),
        height=560, margin=dict(l=0, r=0, t=48, b=0),
        paper_bgcolor="white", plot_bgcolor="#f8fbff",
        legend=dict(orientation="h", y=1.05),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Vista de coordenadas sin mapa de calles; no se solicitan teselas a terceros.")


RENOMBRAR_RESUMEN = {
    "codigo": "Código", "oficina": "Oficina receptora", "division": "División",
    "distancia_km": "Distancia (km)", "participacion_pct": "Participación (%)",
    "ventanilla_actual": "Tickets VNT actuales", "ventanilla_recibida": "Tickets VNT recibidos",
    "ventanilla_futura": "Tickets VNT futuros", "plataforma_actual": "Tickets PLT actuales",
    "plataforma_recibida": "Tickets PLT recibidos", "plataforma_futura": "Tickets PLT futuros",
    "hu_vnt_actual": "Horas VNT actuales", "hu_vnt_futura": "Horas VNT futuras",
    "hu_plt_actual": "Horas PLT actuales", "hu_plt_futura": "Horas PLT futuras",
    "deficit_previo": "Déficit previo", "adicional_ventanilla": "Adicional VNT",
    "adicional_plataforma": "Adicional PLT", "adicional_total": "Adicional total",
    "dictamen_general": "Dictamen general",
}
RENOMBRAR_DETALLE = {
    "codigo": "Código", "oficina": "Oficina", "division": "División",
    "canal": "Canal", "distancia_km": "Distancia (km)",
    "participacion_pct": "Participación (%)", "tickets_actuales": "Tickets actuales",
    "tickets_recibidos": "Tickets recibidos", "tickets_futuros": "Tickets futuros",
    "tiempo_min": "Tiempo (min)", "estructura_actual": "Estructura actual",
    "horas_actuales": "Horas actuales", "horas_futuras": "Horas futuras",
    "hu_referencia": "Horas reportadas base",
    "estructura_requerida_actual": "Estructura requerida actual",
    "estructura_requerida_futura": "Estructura requerida futura",
    "deficit_previo": "Déficit previo", "deficit_total": "Déficit total",
    "adicional_cierre": "Adicional por cierre", "dictamen": "Dictamen",
    "observacion": "Observación",
}


def main() -> None:
    st.title("Simulador de impacto por cierre de oficinas")
    st.caption("Red de oficinas · carga transaccional mensual · horas útiles diarias · dimensionamiento por canal")

    with st.sidebar:
        st.header("1. Base de datos")
        subida = st.file_uploader("Reemplazar CSV (opcional)", type=["csv"],
                                  help="Si no adjuntas otro archivo, se usará la base v2 incluida.")
        if subida is None:
            if not ARCHIVO_BASE.exists():
                st.error("No se encuentra el CSV v2 junto a app.py.")
                st.stop()
            contenido = ARCHIVO_BASE.read_bytes()
            st.caption("Fuente: base v2 incluida en el proyecto")
        else:
            contenido = subida.getvalue()
            st.caption("Fuente: archivo cargado por el usuario")
    try:
        base = leer_csv(contenido)
        auditoria = revisar_calidad(contenido)
    except Exception as exc:
        st.error(f"No se pudo cargar el archivo: {exc}")
        st.stop()

    identificadores = base["COD OF"].tolist()
    nombres = dict(zip(base["COD OF"], base["OFICINA"]))
    divisiones = dict(zip(base["COD OF"], base["DIVISIÓN"]))
    sugerido = identificadores.index("13") if "13" in identificadores else 0
    with st.sidebar:
        st.header("2. Parámetros")
        codigo = st.selectbox("Oficina que se cerrará", options=identificadores,
                              index=sugerido,
                              format_func=lambda c: f"{nombres[c]} · {c} · {divisiones[c]}")
        radio = st.number_input("Radio de búsqueda (km)", min_value=0.1, max_value=200.0,
                                value=3.0, step=0.5, format="%.1f")
        limite = st.number_input("Máximo de receptoras", min_value=1, max_value=100,
                                 value=5, step=1)
        umbral = st.number_input("Umbral de horas útiles/día", min_value=0.25,
                                 max_value=24.0, value=7.0, step=0.25)
        pct_vnt = st.slider("Redistribución ventanilla (%)", 0, 100, 100, 1)
        pct_plt = st.slider("Redistribución plataforma (%)", 0, 100, 100, 1)
        st.caption(f"Días laborales: {DIAS_LABORALES}/mes · Personal no se reasigna · Método 1/distancia")
    try:
        candidatos = oficinas_cercanas(base, codigo, radio)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    opciones = candidatos["COD OF"].tolist()
    textos_candidatos = {
        x["COD OF"]: f"{x['OFICINA']} · {x['distancia_km']:.2f} km · cod. {x['COD OF']}"
        for _, x in candidatos.iterrows()
    }
    with st.sidebar:
        st.header("3. Oficinas receptoras")
        st.caption(f"{len(opciones)} oficinas dentro de {radio:.1f} km. "
                   f"Se proponen hasta {limite} por cercanía.")
        # Key incluye parametros para que el valor predeterminado se actualice al cambiar escenario.
        key_seleccion = f"recep_{hash(contenido)}_{codigo}_{radio}_{limite}"
        seleccion = st.multiselect(
            "Receptoras que participarán",
            options=opciones,
            default=opciones[:int(limite)],
            format_func=lambda c: textos_candidatos[c],
            key=key_seleccion,
            help="Puedes excluir oficinas; las participaciones se recalcularán.",
        )
        if len(seleccion) > limite:
            st.error(f"Seleccionaste {len(seleccion)} oficinas; el máximo es {limite}.")
            st.stop()
        st.info("Cambiar cualquier parámetro recalcula el escenario automáticamente.")
    resultado = simular(
        base, codigo, candidatos, seleccion,
        porcentaje_ventanilla=pct_vnt, porcentaje_plataforma=pct_plt,
        umbral=umbral, dias=DIAS_LABORALES, radio_km=radio,
        max_receptoras=int(limite),
    )
    origen = resultado.origen
    st.subheader(f"Cierre simulado: {origen['OFICINA']}")
    st.caption(f"Código {codigo} · División {origen['DIVISIÓN']} · "
               f"{fmt_entero(origen['TKT VNT'])} tickets VNT y "
               f"{fmt_entero(origen['TKT PLT'])} tickets PLT en origen")

    crecimiento = resultado.detalle["adicional_cierre"].sum(min_count=1)
    oficinas_con_crecimiento = (
        int((resultado.resumen["adicional_total"].fillna(0) > 0).sum())
        if not resultado.resumen.empty else 0
    )
    asignados = resultado.balance["tickets_asignados"].sum(min_count=1)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Receptoras seleccionadas", len(seleccion))
    c2.metric("Tickets redistribuidos conocidos", fmt_entero(asignados))
    c3.metric("Oficinas que crecen", oficinas_con_crecimiento)
    c4.metric("Posiciones adicionales conocidas", fmt_entero(crecimiento))
    if len(seleccion) == 0:
        st.warning("Sin oficinas receptoras seleccionadas. Amplía el radio o selecciona oficinas.")
    if resultado.alertas:
        with st.expander(f"⚠️ Advertencias de simulación ({len(resultado.alertas)})", expanded=True):
            for mensaje in resultado.alertas:
                st.warning(mensaje)
        st.caption("Los totales de posiciones excluyen canales sin datos suficientes; pueden ser mínimos parciales.")

    t1, t2, t3, t4 = st.tabs([
        "1 · Simulación", "2 · Mapa de impacto", "3 · Dimensionamiento", "4 · Detalle y exportación",
    ])
    with t1:
        st.subheader("Balance de transacciones de la oficina cerrada")
        if not resultado.balance.empty:
            vista_bal = resultado.balance.rename(columns={
                "canal": "Canal", "tickets_origen": "Tickets origen",
                "porcentaje_redistribucion": "Redistribución (%)",
                "tickets_planeados": "Tickets a redistribuir",
                "tickets_asignados": "Tickets asignados",
                "tickets_no_redistribuidos": "Tickets que no migran",
                "tickets_sin_receptora": "Tickets sin receptora",
            })
            st.dataframe(vista_bal, use_container_width=True, hide_index=True)
        st.subheader("Oficinas receptoras y situación general")
        if resultado.resumen.empty:
            st.info("No hay receptoras seleccionadas dentro del radio.")
        else:
            cols = ["codigo", "oficina", "distancia_km", "participacion_pct",
                    "ventanilla_recibida", "plataforma_recibida", "adicional_total", "dictamen_general"]
            st.dataframe(formatear_tabla(resultado.resumen[cols], RENOMBRAR_RESUMEN),
                         use_container_width=True, hide_index=True)
        st.caption("Distribución: peso = (1/distancia) / suma de (1/distancia). "
                   "Los tickets se redondean por mayores restos y conservan los totales.")

    with t2:
        st.subheader("Ubicación de la oficina cerrada y las receptoras")
        calles = st.toggle("Ver mapa de calles (OpenStreetMap; requiere Internet)", value=False)
        if calles:
            st.caption("El navegador descargará teselas cartográficas de OpenStreetMap. "
                       "No se envían tickets ni datos de estructura.")
            mapa_callejero(origen, resultado.receptores, resultado.resumen)
        else:
            mapa_sin_conexion(origen, resultado.receptores, resultado.resumen)
        if not resultado.receptores.empty:
            vista = resultado.receptores[["COD OF", "OFICINA", "DIVISIÓN", "distancia_km",
                                         "participacion_pct"]].rename(columns={
                "COD OF": "Código", "OFICINA": "Oficina", "DIVISIÓN": "División",
                "distancia_km": "Distancia (km)", "participacion_pct": "Participación (%)",
            }).copy()
            st.dataframe(vista.round(2), use_container_width=True, hide_index=True)

    with t3:
        st.subheader(f"Horas útiles y estructura requerida · umbral {umbral:g} h/día")
        if resultado.detalle.empty:
            st.info("Selecciona al menos una oficina receptora para ver el dimensionamiento.")
        else:
            canal_seleccionado = st.radio("Canal", ["Ventanilla", "Plataforma", "Ambos"],
                                         horizontal=True)
            datos = resultado.detalle if canal_seleccionado == "Ambos" else (
                resultado.detalle.loc[resultado.detalle["canal"] == canal_seleccionado]
            )
            st.dataframe(formatear_tabla(datos, RENOMBRAR_DETALLE),
                         use_container_width=True, hide_index=True)
            st.subheader("Comparación de horas útiles")
            graf = datos[["oficina", "canal", "horas_actuales", "horas_futuras"]].melt(
                id_vars=["oficina", "canal"],
                value_vars=["horas_actuales", "horas_futuras"],
                var_name="Escenario", value_name="Horas diarias",
            ).dropna(subset=["Horas diarias"])
            if not graf.empty:
                graf["Oficina y canal"] = graf["oficina"] + " · " + graf["canal"]
                graf["Escenario"] = graf["Escenario"].replace({
                    "horas_actuales": "Actual", "horas_futuras": "Proyectado"})
                fig = px.bar(graf, x="Oficina y canal", y="Horas diarias",
                             color="Escenario", barmode="group", text_auto=".2f",
                             color_discrete_map={"Actual": "#9cc7ed", "Proyectado": "#07549b"})
                fig.add_hline(y=umbral, line_width=2, line_dash="dash", line_color="#d63031",
                              annotation_text=f"Umbral {umbral:g} h")
                fig.update_layout(xaxis_tickangle=-25, height=520,
                                  margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig, use_container_width=True)
            st.subheader("Necesidades de estructura por canal")
            agreg = datos.groupby("canal", as_index=False).agg(
                deficit_previo=("deficit_previo", lambda x: x.sum(min_count=1)),
                adicional_cierre=("adicional_cierre", lambda x: x.sum(min_count=1)),
                canales_incompletos=("adicional_cierre", lambda x: int(x.isna().sum())),
            ).rename(columns={"canal": "Canal", "deficit_previo": "Déficit previo",
                              "adicional_cierre": "Adicional por cierre",
                              "canales_incompletos": "Oficinas sin dictamen"})
            st.dataframe(agreg, use_container_width=True, hide_index=True)

    with t4:
        st.subheader("Trazabilidad y detalle por oficina")
        if resultado.resumen.empty:
            st.info("No hay receptoras para examinar.")
        else:
            idx = {str(x["codigo"]): str(x["oficina"]) for _, x in resultado.resumen.iterrows()}
            opcion = st.selectbox("Inspeccionar oficina", list(idx), format_func=lambda c: f"{idx[c]} · {c}")
            detalle_of = resultado.detalle.loc[resultado.detalle["codigo"] == opcion]
            st.dataframe(formatear_tabla(detalle_of, RENOMBRAR_DETALLE),
                         use_container_width=True, hide_index=True)
        with st.expander("Fórmulas y parámetros utilizados"):
            st.latex(r"HU=\frac{Tickets\times Tiempo_{min}}{60\times24\times Estructura}")
            st.latex(r"E_{requerida}=\left\lceil\frac{Tickets\times Tiempo_{min}}{60\times24\times Umbral}\right\rceil")
            st.write("Crecimiento por cierre = déficit proyectado − déficit previo.")
            st.json(resultado.parametros)
        with st.expander(f"Auditoría de calidad de base ({len(auditoria)} incidencias)"):
            if auditoria.empty:
                st.success("No se encontraron incidencias con las reglas de validación.")
            else:
                st.dataframe(auditoria, use_container_width=True, hide_index=True)
        st.subheader("Descargar resultados")
        datos_excel = excel_resultados(resultado, auditoria, candidatos)
        st.download_button(
            label="⬇️ Descargar análisis completo en Excel",
            data=datos_excel,
            file_name=f"simulacion_cierre_oficina_{codigo}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.caption("Incluye resumen ejecutivo, detalle por canal, balance de tickets, parámetros, "
                   "oficinas cercanas, alertas y auditoría de la base.")

    st.divider()
    st.caption("Modelo de planificación operativa. Distancias en línea recta; no se modelan "
               "preferencias reales de clientes, migración digital adicional, tiempos de traslado "
               "ni capacidad física de oficinas. Uso local recomendado con datos corporativos.")


if __name__ == "__main__":
    main()
