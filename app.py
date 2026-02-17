# app.py
import os
import io
import re
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
    if s in ("", "TOTAL", "EN SERVICIO", "FUERA DE SERVICIO", "EN MTTO", "EN MANTTO", "ESTADO", "UBICACIÓN", "UBICACION"):
        return False
    if re.match(r"^[A-Z]{1,4}\d{1,4}$", s):
        return True
    if re.match(r"^[A-Z]{1,4}\s?\d{1,4}$", s):
        return True
    return False

def percent_to_0_100(series: pd.Series) -> pd.Series:
    """
    Convierte porcentajes a 0..100.
    Soporta:
      - 0.8 / 1.0 (Excel % -> fracción) => 80 / 100
      - 80 / 100 ya en % => se mantiene
      - "80%" => 80
      - "0,8" => 80
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

def info_bubble(label: str, md: str, key: str):
    """
    Burbuja tipo nube (popover) si existe; si no, fallback a expander.
    """
    if hasattr(st, "popover"):
        with st.popover(f"🫧 {label}", use_container_width=False):
            st.markdown(md)
    else:
        with st.expander(f"🫧 {label}", expanded=False):
            st.markdown(md)

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

    numeric_cols = [
        "Horas Operación", "Horas de operación",
        "Indisponibilidad [HH]", "Disponibilidad",
        "Target Operación", "Target Operacion", "Target",
        "Tractos OP", "Tractos Utilizados",
        "Capacidad_Operadores", "Capacidad Operadores",
        "Capacidad_Real", "Capacidad Real",
        "Utilizacion_demandada_%", "Utilización_demandada_%",
        "Utilizacion_Oferta_%", "Utilización_Oferta_%",
        "Utilizacion_Capacidad_%", "Utilización_Capacidad_%"
    ]
    for c in numeric_cols:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    # Normalizar % si existen en Excel
    for c in ["Utilizacion_demandada_%", "Utilización_demandada_%",
              "Utilizacion_Oferta_%", "Utilización_Oferta_%",
              "Utilizacion_Capacidad_%", "Utilización_Capacidad_%"]:
        if c in faena.columns:
            faena[c] = percent_to_0_100(faena[c])

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
# MAPPERS
# =========================================================
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

    min_date = det["Inicio"].min().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None
    max_date = det["Inicio"].max().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None

    fecha_ini = st.date_input("Desde", value=min_date)
    fecha_fin = st.date_input("Hasta", value=max_date)

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

det_f = filter_det(det, sel_equipos, sel_familias, sel_tipos, sel_naves, fecha_ini, fecha_fin)
faena_f = filter_faena(faena, sel_terminales, sel_buques, fecha_ini, fecha_fin)

# KPIs globales
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

tab0, tab1, tab2, tab3, tabU, tab4 = st.tabs(
    ["🏠 Estado General", "📌 Resumen", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"]
)

# =========================================================
# TAB 0: ESTADO GENERAL
# =========================================================
with tab0:
    st.subheader("🏠 Estado de Tractos (hoy)")

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
        st.info("Para Estado_Flota necesito columnas Tracto y Status.")
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
        cB.metric("En servicio (hoy)", f"{op_f:,}".replace(",", "."))
        cC.metric("Fuera servicio (hoy)", f"{no_op_f:,}".replace(",", "."))
        cD.metric(
            "Disponibilidad (hoy)",
            "—" if disp_hoy is None else f"{disp_hoy*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
        )

        # PIE (verde/rojo correcto)
        pie_df = pd.DataFrame({"Estado": ["En servicio", "Fuera de servicio"], "Cantidad": [op_f, no_op_f]})
        st.plotly_chart(
            px.pie(
                pie_df,
                names="Estado",
                values="Cantidad",
                title="Flota hoy (En servicio vs Fuera de servicio)",
                color="Estado",
                color_discrete_map={"En servicio": "green", "Fuera de servicio": "red"},
            ),
            use_container_width=True,
            key="tab0_pie_estado"
        )

        # Tabla coloreada (tracto/ubic/status)
        show_cols = [c for c in [col_tracto, col_ubic, col_status, col_causa, col_fprop, col_obs] if c and c in dfE.columns]
        df_show = dfE[show_cols].copy()
        df_show["_operativo"] = dfE["_operativo"].values

        def _row_style(row):
            ok = bool(row.get("_operativo", False))
            base = "background-color:#e9f9ee; color:#0f5132;" if ok else "background-color:#fdecec; color:#842029;"
            return [base] * len(row)

        st.markdown("### Listado de tractos (hoy)")
        st.dataframe(
            df_show.style.apply(_row_style, axis=1),
            use_container_width=True,
            height=520
        )

        # Disponibilidad por ubicación (barra con semáforo)
        if col_ubic and col_ubic in dfE.columns:
            grp = dfE.groupby(col_ubic)["_operativo"].agg(Total="size", EnServicio="sum").reset_index()
            grp["Disponibilidad_%"] = (grp["EnServicio"] / grp["Total"]) * 100.0
            grp["Semaforo"] = grp["Disponibilidad_%"].apply(lambda x: "OK" if x >= 95 else ("Medio" if x >= 80 else "Bajo"))
            st.plotly_chart(
                px.bar(
                    grp.sort_values("Disponibilidad_%", ascending=False),
                    x=col_ubic,
                    y="Disponibilidad_%",
                    color="Semaforo",
                    title="Disponibilidad (%) por ubicación",
                    color_discrete_map={"OK": "green", "Medio": "orange", "Bajo": "red"}
                ),
                use_container_width=True,
                key="tab0_bar_ubic"
            )

# =========================================================
# TAB 1: RESUMEN
# =========================================================
with tab1:
    st.subheader("Resumen histórico (según filtros)")

    colA, colB = st.columns(2)

    if "Clasificación" in det_f.columns and det_f.shape[0] > 0:
        dfc = det_f.groupby("Clasificación", dropna=False).size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        colA.plotly_chart(px.bar(dfc, x="Clasificación", y="Cantidad", title="Cantidad de fallas por Clasificación"),
                          use_container_width=True, key="tab1_bar_clasif_count")

        if "Horas de reparación" in det_f.columns:
            dfh = det_f.groupby("Clasificación", dropna=False)["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            colB.plotly_chart(px.bar(dfh, x="Clasificación", y="Horas de reparación", title="HH por Clasificación"),
                              use_container_width=True, key="tab1_bar_clasif_hh")
    else:
        st.info("No hay datos de detenciones con los filtros actuales.")

    st.subheader("Top 10 equipos por HH")
    if all(c in det_f.columns for c in ["Equipo", "Horas de reparación"]) and det_f.shape[0] > 0:
        top = det_f.groupby("Equipo")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(10)
        st.plotly_chart(px.bar(top, x="Equipo", y="Horas de reparación", title="Top 10 equipos por HH"),
                        use_container_width=True, key="tab1_bar_top10_hh")

    st.subheader("Tendencia mensual (HH)")
    if "Mes" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        m = det_f.groupby("Mes")["Horas de reparación"].sum().reset_index()
        st.plotly_chart(px.line(m, x="Mes", y="Horas de reparación", markers=True, title="HH por mes"),
                        use_container_width=True, key="tab1_line_hh_mes")

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

        count_dm = df_dm.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        col_left.plotly_chart(px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM/DE/DO"),
                              use_container_width=True, key="tab2_bar_dm_count")

        if "Horas de reparación" in df_dm.columns:
            hh_dm = df_dm.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            col_right.plotly_chart(px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM/DE/DO"),
                                   use_container_width=True, key="tab2_bar_dm_hh")

        st.divider()
        if "Equipo" in df_dm.columns and "Horas de reparación" in df_dm.columns:
            tipo_opts = sorted(df_dm["DMDEDO"].dropna().unique())
            tipo_sel = st.selectbox("Ver Top equipos por HH para", options=tipo_opts, key="tab2_sel_tipo")

            sub = df_dm[df_dm["DMDEDO"] == tipo_sel]
            top_eq = sub.groupby("Equipo")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(10)
            st.plotly_chart(px.bar(top_eq, x="Equipo", y="Horas de reparación", title=f"Top 10 equipos por HH — {tipo_sel}"),
                            use_container_width=True, key="tab2_bar_top_eq")

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

        info_bubble(
            "Cómo interpretar Disponibilidad",
            """
- **Disponibilidad**: proporción de tiempo disponible para operar.
- Puede venir como **0.85** (fracción) o **85** (porcentaje). El dashboard lo normaliza.
            """,
            key="disp_help"
        )

        if "Inicio OP" in df.columns and df["Inicio OP"].notna().any() and "Disponibilidad" in df.columns:
            df["Fecha"] = pd.to_datetime(df["Inicio OP"], errors="coerce").dt.date
            df["Disp01"] = normalize_disponibilidad_to_0_1(df["Disponibilidad"])
            if "Terminal" in df.columns:
                g = df.groupby(["Fecha", "Terminal"], dropna=False)["Disp01"].mean().reset_index()
                fig = px.line(g, x="Fecha", y="Disp01", color="Terminal", markers=True, title="Disponibilidad promedio por día")
            else:
                g = df.groupby("Fecha")["Disp01"].mean().reset_index()
                fig = px.line(g, x="Fecha", y="Disp01", markers=True, title="Disponibilidad promedio por día")
            st.plotly_chart(fig, use_container_width=True, key="tab3_line_disp")

        st.dataframe(faena_f, use_container_width=True, height=420)

# =========================================================
# TAB U: UTILIZACIÓN (definición 100% alineada a lo que dijiste)
# =========================================================
with tabU:
    st.subheader("📈 Utilización — Demanda vs Oferta vs Operadores (Faena)")

    info_bubble(
        "Definiciones (para evitar errores de interpretación)",
        """
**1) Target (tractos ideales)**: cantidad de tractos definida como ideal para la operación.

**2) Realmente en operación (usados)**: tractos que realmente están en la operación.

**3) Capacidad real (flota disponible)**: cantidad de tractos disponibles realmente para esa operación.

**4) Capacidad operadores**: operadores disponibles para esa operación.

**Indicadores:**
- **Utilización demandada = Usados / Target**
- **Utilización oferta = Usados / Capacidad real**
- **Utilización operadores = Usados / Capacidad operadores**
        """,
        key="util_defs"
    )

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        col_target = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target"])
        col_used = find_first_col(dfu, ["Tractos Utilizados", "Tractos utilizados"])
        col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op"])
        col_cap_real = find_first_col(dfu, ["Capacidad_Real", "Capacidad Real"])
        col_cap_ops = find_first_col(dfu, ["Capacidad_Operadores", "Capacidad Operadores"])

        col_real_used = col_used if col_used is not None else col_op

        col_util_dem = find_first_col(dfu, ["Utilizacion_demandada_%", "Utilización_demandada_%"])
        col_util_oferta = find_first_col(dfu, ["Utilizacion_Oferta_%", "Utilización_Oferta_%"])
        col_util_ops = find_first_col(dfu, ["Utilizacion_Capacidad_%", "Utilización_Capacidad_%"])  # operadores
        col_brecha = find_first_col(dfu, ["Brecha(Target-OP)", "Brecha_(Target-OP)", "Brecha(Target-Usados)", "Brecha_(Target-Usados)"])
        col_indicador = find_first_col(dfu, ["Indicador_cuello_botella", "Indicador cuello botella"])

        # Base numéricos
        for c in [col_target, col_real_used, col_cap_real, col_cap_ops]:
            if c and c in dfu.columns:
                dfu[c] = pd.to_numeric(dfu[c], errors="coerce")

        # Calcular si no existen (0..100)
        if col_util_dem is None and (col_target and col_real_used):
            dfu["Utilizacion_demandada_%"] = (dfu[col_real_used] / dfu[col_target]) * 100.0
            col_util_dem = "Utilizacion_demandada_%"

        if col_brecha is None and (col_target and col_real_used):
            dfu["Brecha(Target-OP)"] = dfu[col_target] - dfu[col_real_used]
            col_brecha = "Brecha(Target-OP)"

        if col_util_oferta is None and (col_cap_real and col_real_used):
            dfu["Utilizacion_Oferta_%"] = (dfu[col_real_used] / dfu[col_cap_real]) * 100.0
            col_util_oferta = "Utilizacion_Oferta_%"

        # OJO: col_util_ops es operadores (antes se llamaba "Capacidad")
        if col_util_ops is None and (col_cap_ops and col_real_used):
            dfu["Utilizacion_Operadores_%"] = (dfu[col_real_used] / dfu[col_cap_ops]) * 100.0
            col_util_ops = "Utilizacion_Operadores_%"
        else:
            # si venía del excel como Utilizacion_Capacidad_% lo tratamos como operadores
            if col_util_ops and col_util_ops in dfu.columns:
                pass

        # Normalizar % (por si vinieron como 0..1 o "80%")
        for c in [col_util_dem, col_util_oferta, col_util_ops]:
            if c and c in dfu.columns:
                dfu[c] = percent_to_0_100(dfu[c])

        # Indicador cuello botella (cuando Usados < Target)
        if col_indicador is None:
            def _cuello(row):
                try:
                    tgt = float(row[col_target]) if col_target else None
                    used = float(row[col_real_used]) if col_real_used else None
                    capr = float(row[col_cap_real]) if col_cap_real else None
                    capo = float(row[col_cap_ops]) if col_cap_ops else None
                except Exception:
                    return ""

                if tgt is None or used is None or pd.isna(tgt) or pd.isna(used):
                    return ""

                if used + 1e-9 < tgt:
                    falta_flota = (capr is not None and (not pd.isna(capr)) and capr + 1e-9 < tgt)
                    falta_ops = (capo is not None and (not pd.isna(capo)) and capo + 1e-9 < tgt)

                    if falta_ops and falta_flota:
                        return "FALTA FLOTA + OPERADORES"
                    if falta_ops:
                        return "FALTA OPERADORES"
                    if falta_flota:
                        return "FALTA FLOTA"
                    return "COORDINACIÓN / RESTRICCIÓN EXTERNA"

                return "BALANCEADO"

            dfu["Indicador_cuello_botella"] = dfu.apply(_cuello, axis=1)
            col_indicador = "Indicador_cuello_botella"

        # KPIs
        def _mean(colname):
            if not colname or colname not in dfu.columns:
                return None
            v = pd.to_numeric(dfu[colname], errors="coerce").mean()
            return None if pd.isna(v) else float(v)

        m_dem = _mean(col_util_dem)
        m_ofer = _mean(col_util_oferta)
        m_ops = _mean(col_util_ops)
        m_bre = _mean(col_brecha)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Utilización demandada (Usados/Target)", "—" if m_dem is None else f"{m_dem:.1f}%")
        k2.metric("Utilización oferta (Usados/Cap. real)", "—" if m_ofer is None else f"{m_ofer:.1f}%")
        k3.metric("Utilización operadores (Usados/Operadores)", "—" if m_ops is None else f"{m_ops:.1f}%")
        k4.metric("Brecha promedio (Target − Usados)", "—" if m_bre is None else f"{m_bre:.2f}")

        # Insight simple (sin IA) para no interpretar mal
        if col_indicador and col_indicador in dfu.columns:
            top_cuello = dfu[col_indicador].value_counts().head(1)
            if len(top_cuello) > 0:
                st.info(f"📌 Cuello más frecuente en el período filtrado: **{top_cuello.index[0]}** (conteo {int(top_cuello.iloc[0])}).")

        st.divider()

        # G1: Target vs Usados
        info_bubble(
            "Cómo leer Target vs Usados",
            """
- **Target**: ideal definido para operar.
- **Usados**: lo que realmente se operó.
- Si **Usados < Target**: hay brecha, luego revisa **Capacidad real** y **Operadores** para ver qué limitó.
            """,
            key="util_g1_help"
        )

        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any() and col_target and col_real_used:
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date
            g = dfu.groupby("Fecha")[[col_target, col_real_used]].mean().reset_index()
            fig1 = px.line(
                g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="Cantidad"),
                x="Fecha", y="Cantidad", color="Métrica", markers=True,
                title="Target (ideal) vs Usados (real) — promedio por día"
            )
            st.plotly_chart(fig1, use_container_width=True, key="tabU_line_target_used")

        # G2: Utilizaciones por Terminal
        if "Terminal" in dfu.columns:
            metrics = [c for c in [col_util_dem, col_util_oferta, col_util_ops] if c and c in dfu.columns]
            if metrics:
                info_bubble(
                    "Cómo leer las 3 utilizaciones",
                    """
- **Demandada** baja: no se llega al ideal (Target).
- **Oferta** cerca de 100%: flota disponible está al límite.
- **Operadores** cerca de 100%: dotación está al límite.
                    """,
                    key="util_g2_help"
                )
                gt = dfu.groupby("Terminal")[metrics].mean().reset_index()
                melt = gt.melt(id_vars=["Terminal"], var_name="Métrica", value_name="Porcentaje")
                fig2 = px.bar(melt, x="Terminal", y="Porcentaje", color="Métrica", barmode="group",
                              title="Utilizaciones promedio por Terminal (%)")
                st.plotly_chart(fig2, use_container_width=True, key="tabU_bar_utils_terminal")

        # G3: Cuello de botella
        if col_indicador and col_indicador in dfu.columns:
            info_bubble(
                "Cómo se calcula el cuello de botella",
                """
Se activa cuando **Usados < Target**:
- Si **Capacidad real < Target** ⇒ Falta flota.
- Si **Operadores < Target** ⇒ Falta operadores.
- Si ambos < Target ⇒ faltan ambos.
- Si ninguno < Target y aun así no se llega ⇒ coordinación/restricción externa.
                """,
                key="util_g3_help"
            )
            bott = dfu.groupby(col_indicador).size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
            fig3 = px.bar(bott, x=col_indicador, y="Cantidad", title="Indicador de cuello de botella (conteo)")
            st.plotly_chart(fig3, use_container_width=True, key="tabU_bar_bottleneck")

        st.divider()
        st.subheader("Tabla de utilización (gestión)")

        cols_show = []
        for c in [
            "Inicio OP", "Terminal", "Buque",
            col_target, col_real_used,
            col_cap_real, col_cap_ops,
            col_util_dem, col_util_oferta, col_util_ops,
            col_brecha, col_indicador,
        ]:
            if c and c in dfu.columns and c not in cols_show:
                cols_show.append(c)

        st.dataframe(dfu[cols_show], use_container_width=True, height=520)

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
