import os
import io
import re
import pandas as pd
import streamlit as st
import plotly.express as px
import requests
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Dashboard Tractos", layout="wide")

# ======================================
# CONFIG
# ======================================
# Tu Google Sheet (export directo a XLSX)
SHEET_ID_DEFAULT = "1d74h12dHeh8nnIi4gTYAQ5TUVOngf2no"
EXCEL_URL_DEFAULT = f"https://docs.google.com/spreadsheets/d/{SHEET_ID_DEFAULT}/export?format=xlsx"

# Puedes sobrescribir por variable de entorno si quieres
EXCEL_URL = os.getenv("EXCEL_URL", EXCEL_URL_DEFAULT).strip()

# Refresco automático (segundos)
DEFAULT_REFRESH_SEC = int(os.getenv("REFRESH_SEC", "120"))  # 2 min

# Hojas importantes (exactas)
SHEET_FAENA = "Faena"
SHEET_DET = "Detenciones"


# ======================================
# HELPERS
# ======================================
def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def _to_datetime(df: pd.DataFrame, cols):
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _safe_upper(s):
    if pd.isna(s):
        return None
    return str(s).strip().upper()


def download_google_xlsx(url: str) -> bytes:
    """
    Descarga el XLSX exportado desde Google Sheets.
    (Para export de Google Sheets normalmente NO hay pantalla intermedia.)
    """
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content


def _read_excel_bytes(excel_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    faena = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=SHEET_FAENA)
    det = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=SHEET_DET)
    return faena, det


@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    content = download_google_xlsx(EXCEL_URL)
    faena, det = _read_excel_bytes(content)

    faena = _normalize_cols(faena)
    det = _normalize_cols(det)

    # --- Limpieza mínima (sin modificar el excel, solo para análisis) ---
    # FAENA
    faena = _to_datetime(faena, ["Inicio OP", "Termino Op"])
    for c in ["Horas Operación", "Indisponibilidad [HH]", "Disponibilidad"]:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    # DETENCIONES
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])
    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    # Normalizar texto clave (solo para agrupar)
    for c in ["Equipo", "Clasificación", "Familia Equipo", "Componente", "Modo de Falla", "Tipo", "Nave", "Viaje"]:
        if c in det.columns:
            det[c] = det[c].apply(_safe_upper)

    # Derivados
    if "Inicio" in det.columns and det["Inicio"].notna().any():
        det["Año_calc"] = det["Inicio"].dt.year
        det["Mes_calc"] = det["Inicio"].dt.month
        det["Semana_calc"] = det["Inicio"].dt.isocalendar().week.astype("Int64")

    if "Clasificación" in det.columns:
        det["Clasificación"] = det["Clasificación"].fillna("SIN CLASIFICAR")

    return faena, det


def kpi_card(label, value, help_text=None):
    st.metric(label, value)
    if help_text:
        st.caption(help_text)


def filter_det(det: pd.DataFrame, equipos, familias, clasif, naves, fecha_ini, fecha_fin):
    df = det.copy()
    if "Inicio" in df.columns and df["Inicio"].notna().any():
        if fecha_ini:
            df = df[df["Inicio"] >= pd.to_datetime(fecha_ini)]
        if fecha_fin:
            df = df[df["Inicio"] <= pd.to_datetime(fecha_fin)]
    if equipos:
        df = df[df["Equipo"].isin(equipos)]
    if familias and "Familia Equipo" in df.columns:
        df = df[df["Familia Equipo"].isin(familias)]
    if clasif and "Clasificación" in df.columns:
        df = df[df["Clasificación"].isin(clasif)]
    if naves and "Nave" in df.columns:
        df = df[df["Nave"].isin(naves)]
    return df


def filter_faena(faena: pd.DataFrame, terminales, buques, fecha_ini, fecha_fin):
    df = faena.copy()
    if "Inicio OP" in df.columns and df["Inicio OP"].notna().any():
        if fecha_ini:
            df = df[df["Inicio OP"] >= pd.to_datetime(fecha_ini)]
        if fecha_fin:
            df = df[df["Inicio OP"] <= pd.to_datetime(fecha_fin)]
    if terminales and "Terminal" in df.columns:
        df = df[df["Terminal"].astype(str).str.upper().isin([t.upper() for t in terminales])]
    if buques and "Buque" in df.columns:
        df = df[df["Buque"].astype(str).str.upper().isin([b.upper() for b in buques])]
    return df


# ======================================
# UI
# ======================================
st.title("📊 Dashboard Tractos — Faenas, Detenciones y Disponibilidad")

with st.sidebar:
    st.header("⚙️ Actualización")
    refresh_sec = st.number_input(
        "Refresco automático (segundos)",
        min_value=30, max_value=1800, value=DEFAULT_REFRESH_SEC, step=30
    )
    st.caption("Lee el Google Sheet exportado a XLSX y se refresca solo.")
    st_autorefresh(interval=refresh_sec * 1000, key="autorefresh")

    st.divider()
    st.header("🎛️ Filtros")

    # Cargar datos una vez para armar filtros
    try:
        faena, det = load_data()
    except Exception as e:
        st.error("No pude leer el Google Sheet. Revisa que el link esté público (lector).")
        st.code(str(e))
        st.stop()

    # Rango fechas basado en detenciones
    min_date = det["Inicio"].min().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None
    max_date = det["Inicio"].max().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None

    fecha_ini = st.date_input("Desde", value=min_date)
    fecha_fin = st.date_input("Hasta", value=max_date)

    equipos = sorted([e for e in det["Equipo"].dropna().unique()]) if "Equipo" in det.columns else []
    familias = sorted([f for f in det["Familia Equipo"].dropna().unique()]) if "Familia Equipo" in det.columns else []
    clasif = sorted([c for c in det["Clasificación"].dropna().unique()]) if "Clasificación" in det.columns else []
    naves = sorted([n for n in det["Nave"].dropna().unique()]) if "Nave" in det.columns else []

    sel_equipos = st.multiselect("Equipo", equipos)
    sel_familias = st.multiselect("Familia equipo", familias)
    sel_clasif = st.multiselect("Clasificación", clasif)
    sel_naves = st.multiselect("Nave", naves)

    st.divider()
    terminales = sorted([t for t in faena["Terminal"].dropna().astype(str).unique()]) if "Terminal" in faena.columns else []
    buques = sorted([b for b in faena["Buque"].dropna().astype(str).unique()]) if "Buque" in faena.columns else []
    sel_terminales = st.multiselect("Terminal (Faena)", terminales)
    sel_buques = st.multiselect("Buque (Faena)", buques)

# Aplicar filtros
det_f = filter_det(det, sel_equipos, sel_familias, sel_clasif, sel_naves, fecha_ini, fecha_fin)
faena_f = filter_faena(faena, sel_terminales, sel_buques, fecha_ini, fecha_fin)

# ======================================
# KPIs
# ======================================
c1, c2, c3, c4 = st.columns(4)

total_det = int(det_f.shape[0])
total_hh = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

disp_prom = None
if "Disponibilidad" in faena_f.columns and faena_f["Disponibilidad"].notna().any():
    disp_prom = float(faena_f["Disponibilidad"].mean())

with c1:
    kpi_card("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    kpi_card("Horas detención (HH)", f"{total_hh:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    kpi_card("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    if disp_prom is None:
        kpi_card("Disponibilidad promedio", "—", "No hay datos válidos en Faena filtrada")
    else:
        kpi_card("Disponibilidad promedio", f"{disp_prom*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", "."))

st.divider()

# ======================================
# TABS
# ======================================
tab1, tab2, tab3, tabU, tab4 = st.tabs(["📌 Resumen", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"])

# -------- TAB 1: RESUMEN
with tab1:
    colA, colB = st.columns([1, 1])

    if "Clasificación" in det_f.columns and det_f.shape[0] > 0:
        dfc = (
            det_f.groupby("Clasificación", dropna=False)
            .size()
            .reset_index(name="Cantidad")
            .sort_values("Cantidad", ascending=False)
        )
        fig = px.bar(dfc, x="Clasificación", y="Cantidad", title="Cantidad de fallas por Clasificación")
        colA.plotly_chart(fig, use_container_width=True)

        if "Horas de reparación" in det_f.columns:
            dfh = (
                det_f.groupby("Clasificación", dropna=False)["Horas de reparación"]
                .sum()
                .reset_index()
                .sort_values("Horas de reparación", ascending=False)
            )
            fig2 = px.bar(dfh, x="Clasificación", y="Horas de reparación", title="Horas de detención (HH) por Clasificación")
            colB.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No hay datos de detenciones con los filtros actuales.")

    st.subheader("Top 10 equipos por HH (detención)")
    if "Equipo" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        top = (
            det_f.groupby("Equipo")["Horas de reparación"]
            .sum()
            .reset_index()
            .sort_values("Horas de reparación", ascending=False)
            .head(10)
        )
        fig3 = px.bar(top, x="Equipo", y="Horas de reparación", title="Top 10 equipos por HH")
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.caption("No disponible: faltan columnas o no hay registros en el filtro.")

    st.subheader("Tendencia mensual de HH")
    if "Inicio" in det_f.columns and "Horas de reparación" in det_f.columns and det_f["Inicio"].notna().any():
        tmp = det_f.copy()
        tmp["Mes"] = tmp["Inicio"].dt.to_period("M").astype(str)
        m = tmp.groupby("Mes")["Horas de reparación"].sum().reset_index()
        fig4 = px.line(m, x="Mes", y="Horas de reparación", markers=True, title="HH por mes")
        st.plotly_chart(fig4, use_container_width=True)

# -------- TAB 2: DETENCIONES
with tab2:
    st.subheader("Detalle y análisis de detenciones")

    c1, c2 = st.columns([1, 1])

    if all(col in det_f.columns for col in ["Familia Equipo", "Clasificación"]) and det_f.shape[0] > 0:
        pivot = det_f.pivot_table(
            index="Familia Equipo", columns="Clasificación",
            values="Equipo", aggfunc="count", fill_value=0
        ).reset_index()

        fig = px.bar(
            pivot.melt(id_vars=["Familia Equipo"], var_name="Clasificación", value_name="Cantidad"),
            x="Familia Equipo", y="Cantidad", color="Clasificación", barmode="stack",
            title="Cantidad de fallas por Familia equipo y Clasificación"
        )
        c1.plotly_chart(fig, use_container_width=True)

    if all(col in det_f.columns for col in ["Equipo", "Clasificación", "Horas de reparación"]) and det_f.shape[0] > 0:
        pe = det_f.groupby(["Equipo", "Clasificación"])["Horas de reparación"].sum().reset_index()
        fig2 = px.bar(pe, x="Equipo", y="Horas de reparación", color="Clasificación", barmode="stack",
                      title="HH por Equipo y Clasificación")
        c2.plotly_chart(fig2, use_container_width=True)

    st.subheader("Top Componentes / Modos de Falla")
    c3, c4 = st.columns(2)

    if "Componente" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        comp = (
            det_f.groupby("Componente")["Horas de reparación"]
            .sum()
            .reset_index()
            .sort_values("Horas de reparación", ascending=False)
            .head(15)
        )
        figc = px.bar(comp, x="Componente", y="Horas de reparación", title="Top 15 Componentes por HH")
        c3.plotly_chart(figc, use_container_width=True)

    if "Modo de Falla" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        modo = (
            det_f.groupby("Modo de Falla")["Horas de reparación"]
            .sum()
            .reset_index()
            .sort_values("Horas de reparación", ascending=False)
            .head(15)
        )
        figm = px.bar(modo, x="Modo de Falla", y="Horas de reparación", title="Top 15 Modos de falla por HH")
        c4.plotly_chart(figm, use_container_width=True)

    st.subheader("Tabla de detenciones filtradas")
    st.dataframe(det_f, use_container_width=True, height=420)

# -------- TAB 3: DISPONIBILIDAD (FAENA)
with tab3:
    st.subheader("Disponibilidad por Faena")

    if faena_f.shape[0] == 0:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        if "Inicio OP" in df.columns and df["Inicio OP"].notna().any() and "Disponibilidad" in df.columns:
            df["Fecha"] = df["Inicio OP"].dt.date
            g = df.groupby(["Fecha", "Terminal"], dropna=False)["Disponibilidad"].mean().reset_index()
            fig = px.line(g, x="Fecha", y="Disponibilidad", color="Terminal", markers=True,
                          title="Disponibilidad promedio por día (según Faena)")
            st.plotly_chart(fig, use_container_width=True)

        c1, c2 = st.columns(2)

        if "Buque" in df.columns and "Disponibilidad" in df.columns:
            b = df.groupby("Buque")["Disponibilidad"].mean().reset_index().sort_values("Disponibilidad", ascending=False)
            figb = px.bar(b, x="Buque", y="Disponibilidad", title="Disponibilidad promedio por Buque")
            c1.plotly_chart(figb, use_container_width=True)

        if "Terminal" in df.columns and "Indisponibilidad [HH]" in df.columns:
            t = df.groupby("Terminal")["Indisponibilidad [HH]"].sum().reset_index().sort_values("Indisponibilidad [HH]", ascending=False)
            figt = px.bar(t, x="Terminal", y="Indisponibilidad [HH]", title="Indisponibilidad total [HH] por Terminal")
            c2.plotly_chart(figt, use_container_width=True)

        st.subheader("Tabla Faena filtrada")
        st.dataframe(faena_f, use_container_width=True, height=420)
with tabU:
    st.subheader("📈 Utilización vs Target (Faena)")

    dfu = faena_f.copy()

    # Nombres esperados (según tu Excel)
    col_target = "Target Operación"
    col_op = "Tractos OP"

    # Si por algún motivo vienen con espacios/diferencias, intentamos encontrarlas igual
    def find_col(df, wanted):
        if wanted in df.columns:
            return wanted
        w = wanted.strip().lower()
        for c in df.columns:
            if str(c).strip().lower() == w:
                return c
        return None

    col_target = find_col(dfu, col_target)
    col_op = find_col(dfu, col_op)

    if dfu.shape[0] == 0:
        st.info("No hay registros de Faena con los filtros actuales.")
    elif (col_target is None) or (col_op is None):
        st.error("No encontré las columnas necesarias en 'Faena'.")
        st.write("Columnas encontradas:", list(dfu.columns))
        st.stop()
    else:
        # Asegurar numérico
        dfu[col_target] = pd.to_numeric(dfu[col_target], errors="coerce")
        dfu[col_op] = pd.to_numeric(dfu[col_op], errors="coerce")

        # Cálculos
        dfu["Cumplimiento_%"] = (dfu[col_op] / dfu[col_target]) * 100
        dfu["Brecha_(Target-OP)"] = dfu[col_target] - dfu[col_op]

        # KPIs
        k1, k2, k3, k4 = st.columns(4)
        target_avg = dfu[col_target].mean()
        op_avg = dfu[col_op].mean()
        cum_avg = dfu["Cumplimiento_%"].mean()
        brecha_avg = dfu["Brecha_(Target-OP)"].mean()

        k1.metric("Target promedio", "—" if pd.isna(target_avg) else f"{target_avg:.1f}")
        k2.metric("Tractos OP promedio", "—" if pd.isna(op_avg) else f"{op_avg:.1f}")
        k3.metric("Cumplimiento promedio", "—" if pd.isna(cum_avg) else f"{cum_avg:.1f}%")
        k4.metric("Brecha promedio (Target - OP)", "—" if pd.isna(brecha_avg) else f"{brecha_avg:.1f}")

        st.divider()

        # Gráfico 1: Target vs Tractos OP (por fecha si existe Inicio OP)
        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date
            g = dfu.groupby(["Fecha"], dropna=False)[[col_target, col_op]].mean().reset_index()

            fig = px.line(
                g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="Cantidad"),
                x="Fecha", y="Cantidad", color="Métrica", markers=True,
                title="Target Operación vs Tractos OP (promedio por día)"
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            # Si no hay fecha, mostramos un resumen global
            g = pd.DataFrame({
                "Métrica": ["Target Operación", "Tractos OP"],
                "Cantidad": [dfu[col_target].mean(), dfu[col_op].mean()]
            })
            fig = px.bar(g, x="Métrica", y="Cantidad", title="Target Operación vs Tractos OP (promedio)")
            st.plotly_chart(fig, use_container_width=True)

        # Gráfico 2: Cumplimiento % por Terminal / Buque si existen
        c1, c2 = st.columns(2)

        if "Terminal" in dfu.columns:
            gt = dfu.groupby("Terminal")["Cumplimiento_%"].mean().reset_index().sort_values("Cumplimiento_%", ascending=False)
            c1.plotly_chart(px.bar(gt, x="Terminal", y="Cumplimiento_%", title="Cumplimiento % promedio por Terminal"), use_container_width=True)

        if "Buque" in dfu.columns:
            gb = dfu.groupby("Buque")["Cumplimiento_%"].mean().reset_index().sort_values("Cumplimiento_%", ascending=False)
            c2.plotly_chart(px.bar(gb, x="Buque", y="Cumplimiento_%", title="Cumplimiento % promedio por Buque"), use_container_width=True)

        st.subheader("Tabla Utilización (Faena) filtrada")
        cols_show = [c for c in ["Inicio OP", "Terminal", "Buque", col_target, col_op, "Cumplimiento_%", "Brecha_(Target-OP)", "Disponibilidad"] if c in dfu.columns]
        st.dataframe(dfu[cols_show], use_container_width=True, height=420)

# -------- TAB 4: DATOS / EXPORT
with tab4:
    st.subheader("Exportar datos filtrados")

    c1, c2 = st.columns(2)
    with c1:
        csv_det = det_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Detenciones (CSV)", data=csv_det, file_name="detenciones_filtradas.csv", mime="text/csv")
    with c2:
        csv_faena = faena_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Faena (CSV)", data=csv_faena, file_name="faena_filtrada.csv", mime="text/csv")

    st.caption("Si actualizas el Google Sheet, el dashboard se actualiza solo con el refresco configurado.")

st.caption("Fuente: Google Sheets exportado a XLSX (pestañas 'Faena' y 'Detenciones'). Dashboard en Streamlit.")
