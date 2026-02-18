# app.py
import os
import io
import re
import datetime as dt
import pandas as pd
import streamlit as st
import plotly.express as px
import requests
from streamlit_autorefresh import st_autorefresh

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(page_title="Dashboard Tractos Navimag", layout="wide")

# =========================================================
# CONFIG (Google Sheets -> export XLSX)
# =========================================================
SHEET_ID_DEFAULT = "1d74h12dHeh8nnIi4gTYAQ5TUVOngf2no"
EXCEL_URL_DEFAULT = f"https://docs.google.com/spreadsheets/d/{SHEET_ID_DEFAULT}/export?format=xlsx"
EXCEL_URL = os.getenv("EXCEL_URL", EXCEL_URL_DEFAULT).strip()

SHEET_FAENA = os.getenv("SHEET_FAENA", "Faena")
SHEET_DET = os.getenv("SHEET_DET", "Detenciones")

SHEET_ESTADO_ID_DEFAULT = "1LwVmep7Qt-6Q3_emC5NBfCg661oHxKV09L7NUM0NSdg"
ESTADO_URL_DEFAULT = f"https://docs.google.com/spreadsheets/d/{SHEET_ESTADO_ID_DEFAULT}/export?format=xlsx"
ESTADO_URL = os.getenv("ESTADO_URL", ESTADO_URL_DEFAULT).strip()
SHEET_ESTADO = os.getenv("SHEET_ESTADO", "Estado_Flota")

DEFAULT_REFRESH_SEC = int(os.getenv("REFRESH_SEC", "120"))

# =========================================================
# HELPERS
# =========================================================
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
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()

    ctype = r.headers.get("Content-Type", "")
    if "text/html" in ctype.lower():
        raise RuntimeError(
            "Google devolvió HTML en vez de .xlsx (permisos). "
            "Asegura: Compartir -> Cualquier persona con el enlace -> Lector."
        )
    return r.content

def find_first_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    norm = {str(col).strip().lower(): col for col in df.columns}
    for c in candidates:
        key = str(c).strip().lower()
        if key in norm:
            return norm[key]
    return None

def normalize_disponibilidad_to_0_1(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().any() and s.max() > 1.5:
        return s / 100.0
    return s

def _make_unique_columns(cols):
    seen = {}
    out = []
    for c in cols:
        base = str(c).strip()
        if base == "" or base.lower() == "nan":
            base = "COL"
        if base not in seen:
            seen[base] = 1
            out.append(base)
        else:
            seen[base] += 1
            out.append(f"{base}__{seen[base]}")
    return out

def is_valid_tracto_code(x: str) -> bool:
    if x is None:
        return False
    s = str(x).strip().upper()
    if s in ("", "TOTAL", "EN SERVICIO", "FUERA DE SERVICIO", "EN MTTO", "EN MANTTO",
             "ESTADO", "UBICACIÓN", "UBICACION"):
        return False
    if re.match(r"^[A-Z]{1,4}\d{1,4}$", s):
        return True
    if re.match(r"^[A-Z]{1,4}\s?\d{1,4}$", s):
        return True
    return False

def percent_to_0_100(series: pd.Series) -> pd.Series:
    """
    Convierte porcentajes a escala 0..100:
    - 0.8 -> 80
    - 80  -> 80
    - "80%" -> 80
    - 8000 -> 80 (por si viniera inflado)
    """
    if series is None:
        return series
    s = series.copy()
    if s.dtype == "object":
        s = s.astype(str).str.replace("%", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().any():
        mx = float(s.max())
        if mx <= 1.5:
            return s * 100.0
        if mx <= 150.0:
            return s
        return s / 100.0
    return s

def map_dmde_do(x):
    if pd.isna(x):
        return "SIN CLASIFICAR"
    s = str(x).strip().upper()
    if s in ["DM", "DE", "DO"]:
        return s
    if "MEC" in s:
        return "DM"
    if "ELEC" in s or "ELÉC" in s:
        return "DE"
    if "OPER" in s:
        return "DO"
    return s

def is_operativo_status(status_str: str) -> bool:
    if status_str is None:
        return False
    s = str(status_str).strip().upper()
    operativo_kw = ["EN SERVICIO", "OPERATIVO", "DISPONIBLE", "OK"]
    noop_kw = ["FUERA DE SERVICIO", "DETEN", "DETENIDO", "MTTO", "MANT", "FALLA", "BAJA"]
    if any(k in s for k in noop_kw):
        return False
    if any(k in s for k in operativo_kw):
        return True
    return False

# =========================================================
# LOADERS
# =========================================================
@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_main() -> tuple[pd.DataFrame, pd.DataFrame]:
    content = download_google_xlsx(EXCEL_URL)

    faena = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_FAENA)
    det = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_DET)

    faena = _normalize_cols(faena)
    det = _normalize_cols(det)

    # FAENA
    faena = _to_datetime(faena, ["Inicio OP", "Termino Op", "Termino OP", "Término OP"])
    for c in [
        "Horas Operación", "Horas de operación", "Indisponibilidad [HH]", "Disponibilidad",
        "Target Operación", "Target Operacion", "Tractos OP", "Tractos Utilizados",
        "Capacidad_Real", "Capacidad Real",
        "Utilizacion_demandada_%", "Utilización_demandada_%",
        "Utilizacion_Oferta_%", "Utilización_Oferta_%"
    ]:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    # DETENCIONES
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])
    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    for c in ["Equipo", "Clasificación", "Familia Equipo", "Componente", "Modo de Falla", "Tipo", "Nave", "Buque", "Viaje", "Terminal"]:
        if c in det.columns:
            det[c] = det[c].apply(_safe_upper)

    if "Inicio" in det.columns and det["Inicio"].notna().any():
        det["Mes"] = det["Inicio"].dt.to_period("M").astype(str)

    if "Clasificación" in det.columns:
        det["Clasificación"] = det["Clasificación"].fillna("SIN CLASIFICAR")

    return faena, det

@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_estado() -> pd.DataFrame:
    content = download_google_xlsx(ESTADO_URL)
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_ESTADO, header=None)
    raw = raw.dropna(how="all").fillna("")

    def _row_text(i):
        return " | ".join([str(x).strip().lower() for x in raw.iloc[i].tolist()])

    header_idx = None
    for i in range(min(len(raw), 80)):
        txt = _row_text(i)
        if ("tracto" in txt) and ("status" in txt):
            header_idx = i
            break

    if header_idx is None:
        df = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_ESTADO)
        df = _normalize_cols(df)
        df.columns = _make_unique_columns(df.columns)
        return df.dropna(how="all")

    hdr = raw.iloc[header_idx].tolist()
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = hdr
    df = df.dropna(how="all")
    df = _normalize_cols(df)
    df.columns = _make_unique_columns(df.columns)

    for j in range(df.shape[1]):
        if df.iloc[:, j].dtype == "object":
            df.iloc[:, j] = df.iloc[:, j].astype(str).str.strip()

    return df

# =========================================================
# FILTERS
# =========================================================
def filter_det(det: pd.DataFrame, equipos, familias, tipos, naves, fecha_ini, fecha_fin):
    df = det.copy()
    if "Inicio" in df.columns and df["Inicio"].notna().any():
        if fecha_ini:
            df = df[df["Inicio"] >= pd.to_datetime(fecha_ini)]
        if fecha_fin:
            df = df[df["Inicio"] <= pd.to_datetime(fecha_fin)]
    if equipos and "Equipo" in df.columns:
        df = df[df["Equipo"].isin(equipos)]
    if familias and "Familia Equipo" in df.columns:
        df = df[df["Familia Equipo"].isin(familias)]
    if tipos and "Tipo" in df.columns:
        df = df[df["Tipo"].isin(tipos)]
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

# =========================================================
# UI HEADER
# =========================================================
st.title("📊 Dashboard Tractos Navimag")

with st.sidebar:
    st.header("⚙️ Actualización")
    refresh_sec = st.number_input(
        "Refresco automático (segundos)",
        min_value=30, max_value=1800, value=DEFAULT_REFRESH_SEC, step=30
    )
    st.caption("Lee Google Sheets exportado a XLSX y se refresca solo.")
    st_autorefresh(interval=refresh_sec * 1000, key="autorefresh")

    st.divider()
    st.header("🎛️ Filtros (histórico)")

    try:
        faena, det = load_main()
    except Exception as e:
        st.error("No pude leer el Google Sheet principal. Revisa permisos (público lector).")
        st.code(str(e))
        st.stop()

    # =========================
    # DEFAULT: MES EN CURSO
    # =========================
    today = dt.date.today()
    first_day_month = today.replace(day=1)

    # si no hay datos, dejamos hoy/mes actual igual
    min_data = det["Inicio"].min().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else first_day_month
    max_data = det["Inicio"].max().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else today

    # default = desde inicio del mes actual (o min_data si es posterior)
    default_ini = max(first_day_month, min_data)
    # default = hoy, pero si hoy pasa el max_data, usar max_data
    default_fin = min(today, max_data)

    fecha_ini = st.date_input("Desde", value=default_ini, help="Por defecto: inicio del mes en curso. Puedes cambiar a meses anteriores.")
    fecha_fin = st.date_input("Hasta", value=default_fin, help="Por defecto: hoy (o último dato disponible).")

    equipos = sorted([e for e in det["Equipo"].dropna().unique()]) if "Equipo" in det.columns else []
    familias = sorted([f for f in det["Familia Equipo"].dropna().unique()]) if "Familia Equipo" in det.columns else []
    tipos = sorted([t for t in det["Tipo"].dropna().unique()]) if "Tipo" in det.columns else []
    naves = sorted([n for n in det["Nave"].dropna().unique()]) if "Nave" in det.columns else []

    sel_equipos = st.multiselect("Equipo", equipos)
    sel_familias = st.multiselect("Familia equipo", familias)
    sel_tipos = st.multiselect("Tipo (DM/DE/DO u otros)", tipos)
    sel_naves = st.multiselect("Nave", naves)

    st.divider()
    terminales = sorted([t for t in faena["Terminal"].dropna().astype(str).unique()]) if "Terminal" in faena.columns else []
    buques = sorted([b for b in faena["Buque"].dropna().astype(str).unique()]) if "Buque" in faena.columns else []
    sel_terminales = st.multiselect("Terminal (Faena)", terminales)
    sel_buques = st.multiselect("Buque (Faena)", buques)

# Aplicar filtros histórico
det_f = filter_det(det, sel_equipos, sel_familias, sel_tipos, sel_naves, fecha_ini, fecha_fin)
faena_f = filter_faena(faena, sel_terminales, sel_buques, fecha_ini, fecha_fin)

# KPIs top globales (histórico filtrado)
c1, c2, c3, c4 = st.columns(4)

total_det = int(det_f.shape[0])
total_hh = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

disp_prom = None
if "Disponibilidad" in faena_f.columns and faena_f["Disponibilidad"].notna().any():
    disp_prom = float(normalize_disponibilidad_to_0_1(faena_f["Disponibilidad"]).mean())

with c1:
    st.metric("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    st.metric("Horas detención (HH)", f"{total_hh:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    st.metric("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    st.metric(
        "Disponibilidad promedio (histórico)",
        "—" if disp_prom is None or pd.isna(disp_prom)
        else f"{disp_prom*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
    )

st.divider()

# =========================================================
# TABS
# =========================================================
tab0, tab1, tab2, tab3, tabU, tab4 = st.tabs(
    ["🏠 Estado General", "📌 Resumen", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"]
)

# =========================================================
# TAB 0: ESTADO GENERAL
# =========================================================
with tab0:
    st.subheader("🏠 Estado General")

    st.markdown("### 1) Estado actual de flota (hoy)")
    try:
        estado = load_estado()
        estado = _normalize_cols(estado)
    except Exception as e:
        st.error("No pude cargar la hoja de ESTADOS. Revisa permisos público lector del sheet de estados.")
        st.code(str(e))
        estado = pd.DataFrame()

    col_tracto = find_first_col(estado, ["#Tracto", "Tracto", "# Tracto", "TRACTO"])
    col_status = find_first_col(estado, ["Status", "STATUS"])
    col_ubic = find_first_col(estado, ["Ubicación", "Ubicacion", "UBICACIÓN", "UBICACION"])
    col_causa = find_first_col(estado, ["Causa", "CAUSA"])
    col_fprop = find_first_col(estado, ["F.Propuesta", "F Propuesta", "F. Propuesta", "FPROPUESTA"])
    col_obs = find_first_col(estado, ["Observacion", "Observación", "OBSERVACION", "OBSERVACIÓN"])

    cA, cB, cC, cD = st.columns(4)

    if estado.empty or col_tracto is None or col_status is None:
        cA.metric("Flota (hoy)", "—")
        cB.metric("Operativos (hoy)", "—")
        cC.metric("No operativos (hoy)", "—")
        cD.metric("Disponibilidad (hoy)", "—")
        st.info("Para la portada necesito columnas en Estado_Flota: Tracto y Status.")
    else:
        dfE = estado.copy()
        dfE[col_tracto] = dfE[col_tracto].astype(str).str.strip()
        dfE[col_status] = dfE[col_status].astype(str).str.strip().str.upper()

        dfE = dfE[dfE[col_tracto].apply(is_valid_tracto_code)].copy()
        dfE["_operativo"] = dfE[col_status].apply(is_operativo_status)

        total_f = int(dfE[col_tracto].nunique())
        op_f = int(dfE[dfE["_operativo"]][col_tracto].nunique())
        no_op_f = max(total_f - op_f, 0)
        disp_hoy = (op_f / total_f) if total_f else None

        cA.metric("Flota (hoy)", f"{total_f:,}".replace(",", "."))
        cB.metric("Operativos (hoy)", f"{op_f:,}".replace(",", "."))
        cC.metric("No operativos (hoy)", f"{no_op_f:,}".replace(",", "."))
        cD.metric(
            "Disponibilidad (hoy)",
            "—" if disp_hoy is None else f"{disp_hoy*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
        )

        # Pie (no coloreamos manualmente; streamlit/plotly decide)
        pie_df = pd.DataFrame({"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]})
        st.plotly_chart(
            px.pie(pie_df, names="Estado", values="Cantidad", title="Flota hoy (Operativos vs No operativos)"),
            use_container_width=True
        )

        # Tabla con color rojo/verde por estado
        st.markdown("### Listado de tractos (Ubicación / Status)")

        cols_show = []
        for c in [col_tracto, col_ubic, col_status, col_causa, col_fprop, col_obs]:
            if c is not None and c in dfE.columns and c not in cols_show:
                cols_show.append(c)

        view = dfE[cols_show].copy()
        # crea columna Estado simple para colorear
        view["Estado_UI"] = view[col_status].apply(lambda x: "EN SERVICIO" if is_operativo_status(x) else "FUERA DE SERVICIO")

        def _style_estado(val):
            s = str(val).upper()
            if "EN SERVICIO" in s:
                return "background-color: #d1fae5; color: #065f46; font-weight: 700;"
            return "background-color: #fee2e2; color: #991b1b; font-weight: 700;"

        st.dataframe(
            view.style.applymap(_style_estado, subset=["Estado_UI"]),
            use_container_width=True,
            height=420
        )

    st.divider()

    st.markdown("### 2) Cumplimiento operacional (Target vs Usados)")
    dfu0 = faena_f.copy()
    col_target0 = find_first_col(dfu0, ["Target Operación", "Target Operacion", "Target"])
    col_used0 = find_first_col(dfu0, ["Tractos Utilizados", "Tractos utilizados"])
    col_op0 = find_first_col(dfu0, ["Tractos OP", "Tractos Op"])
    col_used_real0 = col_used0 if col_used0 is not None else col_op0

    if dfu0.empty or col_target0 is None or col_used_real0 is None:
        st.info("No hay datos de Faena filtrados o faltan columnas Target/Tractos usados.")
    else:
        dfu0[col_target0] = pd.to_numeric(dfu0[col_target0], errors="coerce")
        dfu0[col_used_real0] = pd.to_numeric(dfu0[col_used_real0], errors="coerce")
        dfu0["Cumplimiento_%"] = (dfu0[col_used_real0] / dfu0[col_target0]) * 100
        dfu0["Brecha"] = dfu0[col_target0] - dfu0[col_used_real0]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Target prom.", "—" if pd.isna(dfu0[col_target0].mean()) else f"{dfu0[col_target0].mean():.1f}")
        k2.metric("Usados prom.", "—" if pd.isna(dfu0[col_used_real0].mean()) else f"{dfu0[col_used_real0].mean():.1f}")
        k3.metric("Cumplimiento prom.", "—" if pd.isna(dfu0["Cumplimiento_%"].mean()) else f"{dfu0['Cumplimiento_%'].mean():.1f}%")
        k4.metric("Brecha prom.", "—" if pd.isna(dfu0["Brecha"].mean()) else f"{dfu0['Brecha'].mean():.1f}")

        if "Inicio OP" in dfu0.columns and dfu0["Inicio OP"].notna().any():
            dfu0["Fecha"] = pd.to_datetime(dfu0["Inicio OP"], errors="coerce").dt.date
            g = dfu0.groupby("Fecha")[[col_target0, col_used_real0]].mean().reset_index()
            st.plotly_chart(
                px.line(
                    g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="Cantidad"),
                    x="Fecha", y="Cantidad", color="Métrica", markers=True,
                    title="Target vs Usados (promedio por día)"
                ),
                use_container_width=True
            )

    st.divider()

    st.markdown("### 3) Fallas por tipo (DM / DE / DO) — frecuencia e impacto (HH)")
    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        dfx = det_f.copy()
        dfx["DMDEDO"] = dfx["Tipo"].apply(map_dmde_do)

        cL, cR = st.columns(2)
        cnt = dfx.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        cL.plotly_chart(px.bar(cnt, x="DMDEDO", y="Cantidad", title="Cantidad por DM/DE/DO"), use_container_width=True)

        if "Horas de reparación" in dfx.columns:
            hh = dfx.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            cR.plotly_chart(px.bar(hh, x="DMDEDO", y="Horas de reparación", title="HH por DM/DE/DO"), use_container_width=True)

# =========================================================
# TAB 1: RESUMEN
# =========================================================
with tab1:
    st.subheader("Resumen histórico (según filtros)")
    colA, colB = st.columns(2)

    if "Clasificación" in det_f.columns and det_f.shape[0] > 0:
        dfc = det_f.groupby("Clasificación", dropna=False).size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        colA.plotly_chart(px.bar(dfc, x="Clasificación", y="Cantidad", title="Cantidad de fallas por Clasificación"),
                          use_container_width=True)

        if "Horas de reparación" in det_f.columns:
            dfh = det_f.groupby("Clasificación", dropna=False)["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            colB.plotly_chart(px.bar(dfh, x="Clasificación", y="Horas de reparación", title="HH por Clasificación"),
                              use_container_width=True)
    else:
        st.info("No hay datos de detenciones con los filtros actuales.")

    st.subheader("Top 10 equipos por HH")
    if all(c in det_f.columns for c in ["Equipo", "Horas de reparación"]) and det_f.shape[0] > 0:
        top = det_f.groupby("Equipo")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(10)
        st.plotly_chart(px.bar(top, x="Equipo", y="Horas de reparación", title="Top 10 equipos por HH"),
                        use_container_width=True)

    st.subheader("Tendencia mensual (HH)")
    if "Mes" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        m = det_f.groupby("Mes")["Horas de reparación"].sum().reset_index()
        st.plotly_chart(px.line(m, x="Mes", y="Horas de reparación", markers=True, title="HH por mes"),
                        use_container_width=True)

# =========================================================
# TAB 2: DETENCIONES
# =========================================================
with tab2:
    st.subheader("Detenciones — análisis")

    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        df_dm = det_f.copy()
        df_dm["DMDEDO"] = df_dm["Tipo"].apply(map_dmde_do)

        col_left, col_right = st.columns(2)

        count_dm = (
            df_dm.groupby("DMDEDO")
            .size()
            .reset_index(name="Cantidad")
            .sort_values("Cantidad", ascending=False)
        )
        col_left.plotly_chart(
            px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM / DE / DO"),
            use_container_width=True,
            key="det_bar_dm_count_unique"
        )

        if "Horas de reparación" in df_dm.columns:
            hh_dm = (
                df_dm.groupby("DMDEDO")["Horas de reparación"]
                .sum()
                .reset_index()
                .sort_values("Horas de reparación", ascending=False)
            )
            col_right.plotly_chart(
                px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM / DE / DO"),
                use_container_width=True,
                key="det_bar_dm_hh_unique"
            )

        st.divider()

        if "Equipo" in df_dm.columns and "Horas de reparación" in df_dm.columns:
            tipo_opts = sorted(df_dm["DMDEDO"].dropna().unique())
            tipo_sel = st.selectbox("Ver Top equipos por HH para", options=tipo_opts, key="det_sel_tipo_unique")
            sub = df_dm[df_dm["DMDEDO"] == tipo_sel]
            top_eq = (
                sub.groupby("Equipo")["Horas de reparación"]
                .sum()
                .reset_index()
                .sort_values("Horas de reparación", ascending=False)
                .head(10)
            )
            st.plotly_chart(
                px.bar(top_eq, x="Equipo", y="Horas de reparación", title=f"Top 10 equipos por HH — {tipo_sel}"),
                use_container_width=True,
                key="det_bar_top_unique"
            )

    st.subheader("Tabla detenciones filtradas")
    st.dataframe(det_f, use_container_width=True, height=420)

# =========================================================
# TAB 3: DISPONIBILIDAD (FAENA)
# =========================================================
with tab3:
    st.subheader("Disponibilidad por Faena (histórico)")

    if faena_f.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        if "Inicio OP" in df.columns and df["Inicio OP"].notna().any() and "Disponibilidad" in df.columns:
            df["Fecha"] = pd.to_datetime(df["Inicio OP"], errors="coerce").dt.date
            df["Disp01"] = normalize_disponibilidad_to_0_1(df["Disponibilidad"])
            if "Terminal" in df.columns:
                g = df.groupby(["Fecha", "Terminal"], dropna=False)["Disp01"].mean().reset_index()
                fig = px.line(g, x="Fecha", y="Disp01", color="Terminal", markers=True, title="Disponibilidad promedio por día")
            else:
                g = df.groupby("Fecha")["Disp01"].mean().reset_index()
                fig = px.line(g, x="Fecha", y="Disp01", markers=True, title="Disponibilidad promedio por día")
            st.plotly_chart(fig, use_container_width=True)

        cA3, cB3 = st.columns(2)
        if "Buque" in df.columns and "Disponibilidad" in df.columns:
            df["Disp01"] = normalize_disponibilidad_to_0_1(df["Disponibilidad"])
            b = df.groupby("Buque")["Disp01"].mean().reset_index().sort_values("Disp01", ascending=False)
            cA3.plotly_chart(px.bar(b, x="Buque", y="Disp01", title="Disponibilidad promedio por Buque"),
                             use_container_width=True)

        if "Terminal" in df.columns and "Indisponibilidad [HH]" in df.columns:
            t = df.groupby("Terminal")["Indisponibilidad [HH]"].sum().reset_index().sort_values("Indisponibilidad [HH]", ascending=False)
            cB3.plotly_chart(px.bar(t, x="Terminal", y="Indisponibilidad [HH]", title="Indisponibilidad total [HH] por Terminal"),
                             use_container_width=True)

        st.subheader("Tabla Faena filtrada")
        st.dataframe(faena_f, use_container_width=True, height=420)

# =========================================================
# TAB U: UTILIZACIÓN (solo 2 KPI)
# =========================================================
with tabU:
    st.subheader("📈 Utilización — Demanda y Flota (Faena)")

    st.info(
        "Definiciones (para evitar confusiones):\n"
        "- Cumplimiento de demanda (%) = Tractos usados / Target operación.\n"
        "- Utilización de flota (%) = Tractos usados / Tractos disponibles operativos.\n"
        "Interpretación rápida:\n"
        "- Cumplimiento bajo => no se alcanzó lo solicitado.\n"
        "- Utilización flota baja con cumplimiento bajo => sobró flota (el cuello NO es flota) o hay coordinación.\n"
        "- Utilización flota alta y cumplimiento bajo => falta flota (capacidad real insuficiente).\n"
    )

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        col_target = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target"])
        col_used = find_first_col(dfu, ["Tractos Utilizados", "Tractos utilizados"])
        col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op"])
        col_cap_real = find_first_col(dfu, ["Capacidad_Real", "Capacidad Real"])

        col_real_used = col_used if col_used is not None else col_op

        if col_target is not None:
            dfu[col_target] = pd.to_numeric(dfu[col_target], errors="coerce")
        if col_real_used is not None:
            dfu[col_real_used] = pd.to_numeric(dfu[col_real_used], errors="coerce")
        if col_cap_real is not None:
            dfu[col_cap_real] = pd.to_numeric(dfu[col_cap_real], errors="coerce")

        # Calcular siempre (robusto)
        if col_target is not None and col_real_used is not None:
            dfu["Cumplimiento_demandada_%"] = (dfu[col_real_used] / dfu[col_target]) * 100.0
        else:
            dfu["Cumplimiento_demandada_%"] = pd.NA

        if col_cap_real is not None and col_real_used is not None:
            dfu["Utilizacion_flota_%"] = (dfu[col_real_used] / dfu[col_cap_real]) * 100.0
        else:
            dfu["Utilizacion_flota_%"] = pd.NA

        # Normalizar por si venían como % en Excel
        dfu["Cumplimiento_demandada_%"] = percent_to_0_100(dfu["Cumplimiento_demandada_%"])
        dfu["Utilizacion_flota_%"] = percent_to_0_100(dfu["Utilizacion_flota_%"])

        # Brecha
        if col_target is not None and col_real_used is not None:
            dfu["Brecha(Target-Usados)"] = dfu[col_target] - dfu[col_real_used]
        else:
            dfu["Brecha(Target-Usados)"] = pd.NA

        # KPI solo 2 (más brecha si quieres como apoyo)
        k1, k2, k3 = st.columns(3)

        def _mean(series):
            v = pd.to_numeric(series, errors="coerce").mean()
            return None if pd.isna(v) else float(v)

        m_dem = _mean(dfu["Cumplimiento_demandada_%"])
        m_flo = _mean(dfu["Utilizacion_flota_%"])
        m_bre = _mean(dfu["Brecha(Target-Usados)"])

        k1.metric("Cumplimiento de demanda (Usados/Target)", "—" if m_dem is None else f"{m_dem:.1f}%")
        k2.metric("Utilización de flota (Usados/Disponibles)", "—" if m_flo is None else f"{m_flo:.1f}%")
        k3.metric("Brecha promedio (Target − Usados)", "—" if m_bre is None else f"{m_bre:.2f}")

        st.divider()

        # 1) Target vs Usados por día
        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any() and col_target and col_real_used:
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date
            g = dfu.groupby("Fecha")[[col_target, col_real_used]].mean().reset_index()
            st.plotly_chart(
                px.line(
                    g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="Cantidad"),
                    x="Fecha", y="Cantidad", color="Métrica", markers=True,
                    title="Target Operación vs Tractos usados (promedio por día)"
                ),
                use_container_width=True,
                key="tabU_line_target_used"
            )

        # 2) Cumplimiento vs Utilización (scatter)
        if col_cap_real and col_target and col_real_used:
            tmp = dfu.copy()
            tmp["Cumplimiento_%"] = pd.to_numeric(tmp["Cumplimiento_demandada_%"], errors="coerce")
            tmp["UtilFlota_%"] = pd.to_numeric(tmp["Utilizacion_flota_%"], errors="coerce")

            fig = px.scatter(
                tmp,
                x="UtilFlota_%",
                y="Cumplimiento_%",
                color="Terminal" if "Terminal" in tmp.columns else None,
                hover_data=[c for c in ["Buque", "Inicio OP", col_target, col_real_used, col_cap_real, "Brecha(Target-Usados)"] if c in tmp.columns],
                title="Mapa: Utilización de flota (%) vs Cumplimiento de demanda (%)"
            )
            fig.add_hline(y=95, line_dash="dash")
            fig.add_vline(x=85, line_dash="dash")
            st.plotly_chart(fig, use_container_width=True, key="tabU_scatter_map")

        # 3) Promedios por terminal
        if "Terminal" in dfu.columns:
            gt = dfu.groupby("Terminal")[["Cumplimiento_demandada_%", "Utilizacion_flota_%"]].mean().reset_index()
            melt = gt.melt(id_vars=["Terminal"], var_name="Métrica", value_name="Porcentaje")
            st.plotly_chart(
                px.bar(melt, x="Terminal", y="Porcentaje", color="Métrica", barmode="group",
                       title="Promedios por Terminal: Cumplimiento vs Utilización de flota"),
                use_container_width=True,
                key="tabU_bar_terminal"
            )

        st.divider()
        st.subheader("Tabla de utilización (gestión)")

        cols_show = []
        for c in [
            "Inicio OP", "Terminal", "Buque",
            col_target, col_real_used, col_cap_real,
            "Cumplimiento_demandada_%", "Utilizacion_flota_%",
            "Brecha(Target-Usados)",
            "Disponibilidad"
        ]:
            if c and c in dfu.columns and c not in cols_show:
                cols_show.append(c)

        st.dataframe(dfu[cols_show], use_container_width=True, height=480)

# =========================================================
# TAB 4: EXPORT
# =========================================================
with tab4:
    st.subheader("Exportar datos filtrados")

    cA4, cB4 = st.columns(2)
    with cA4:
        csv_det = det_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Detenciones (CSV)", data=csv_det,
                           file_name="detenciones_filtradas.csv", mime="text/csv")
    with cB4:
        csv_faena = faena_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Faena (CSV)", data=csv_faena,
                           file_name="faena_filtrada.csv", mime="text/csv")

    st.caption("Si actualizas los Google Sheets, el dashboard se actualiza solo con el refresco configurado.")

st.caption("Fuente: Google Sheets exportado a XLSX (Faena, Detenciones, Estado_Flota). Dashboard Streamlit.")
