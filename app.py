# app.py
import os
import io
import re
from datetime import date
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import requests
from streamlit_autorefresh import st_autorefresh

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(page_title="Dashboard Tractos Navimag", layout="wide")
st.title("📊 Dashboard Tractos Navimag")

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

def parse_percent_like_to_number(series: pd.Series) -> pd.Series:
    """Convierte '85%', '85,2', '0.85' -> numérico (sin escalar)."""
    if series is None:
        return series
    s = series.copy()
    if s.dtype == "object":
        s = s.astype(str).str.replace("%", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")

def kpi_to_0_100(series: pd.Series) -> pd.Series:
    """
    Normalización robusta a 0..100 (NO ratio 0..1).
    Soporta:
      - 0..1 (ej: 0.83) -> 83
      - 0..5 (ej: 1.2, 3) -> 120, 300   (ratio típico de KPI en tu tabla)
      - 0..100/150 -> se deja igual
      - '85%' -> 85
    """
    s = parse_percent_like_to_number(series)
    if s is None:
        return s
    if not s.notna().any():
        return s

    mx = float(s.max())

    # caso ratio (muy típico en tus columnas U,V,T):
    # - disponibilidad técnica suele ser 0..1
    # - cumplimiento/utilización pueden ser 0..3
    if mx <= 5.0:
        return s * 100.0

    # caso porcentaje típico (0..150)
    if mx <= 150.0:
        return s

    # caso 0..10000 (por formatos raros)
    return s / 100.0

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

def fmt_num(x, dec=1):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_pct_0_100(x, dec=1):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:.{dec}f}%"

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

    # Fechas
    faena = _to_datetime(faena, ["Inicio OP", "Termino Op", "Termino OP", "Término OP"])
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])

    # Numéricos Faena
    num_cols = [
        "Horas Operación", "Horas de operación", "Horas Operación ",
        "Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad [H]",
        "Disponibilidad", "Disponibilidad_Tecnica", "Disponibilidad Técnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica %",
        "Target Operación", "Target Operacion", "Target",
        "Tractos OP", "Tractos Op",
        "Tractos Utilizados", "Tractos utilizados",
        "Capacidad_Operadores", "Capacidad Operadores",
        "Capacidad_Real", "Capacidad Real",
        "Utilizacion_demandada_%", "Utilización_demandada_%",
        "Utilizacion_Oferta_%", "Utilización_Oferta_%",
        "Utilizacion_Capacidad_%", "Utilización_Capacidad_%",
        "Cumplimiento",
        "Utilizacion", "Utilización",
        "N° SEM", "N SEM", "Mes", "ANO", "AÑO"
    ]
    for c in num_cols:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    # Detenciones
    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    # Normalizar texto detenciones
    for c in ["Equipo", "Clasificación", "Familia Equipo", "Componente", "Modo de Falla", "Tipo", "Nave", "Buque", "Viaje", "Terminal"]:
        if c in det.columns:
            det[c] = det[c].apply(_safe_upper)

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

def safe_mean_0_100(df: pd.DataFrame, col: str) -> float | None:
    if df is None or df.empty or (col not in df.columns):
        return None
    s = kpi_to_0_100(df[col])
    m = float(s.mean()) if s.notna().any() else None
    return m

# =========================================================
# SIDEBAR (DEFAULT = MES EN CURSO)
# =========================================================
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

    today = pd.Timestamp.today().date()
    first_day_month = pd.Timestamp(today.year, today.month, 1).date()

    # rango por defecto: mes en curso
    det_min = det["Inicio"].min().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else first_day_month
    det_max = det["Inicio"].max().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else today

    default_ini = max(first_day_month, det_min) if det_min else first_day_month
    default_fin = min(today, det_max) if det_max else today

    fecha_ini = st.date_input("Desde", value=default_ini)
    fecha_fin = st.date_input("Hasta", value=default_fin)

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

# Aplicar filtros
det_f = filter_det(det, sel_equipos, sel_familias, sel_tipos, sel_naves, fecha_ini, fecha_fin)
faena_f = filter_faena(faena, sel_terminales, sel_buques, fecha_ini, fecha_fin)

# =========================================================
# KPI TOP (usa columnas de la tabla si existen; si no, calcula)
# =========================================================
c1, c2, c3, c4, c5 = st.columns(5)

total_det = int(det_f.shape[0])
total_hh = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

# DISP TEC: prioriza columna ya calculada en tabla (T) si existe
col_disp_tbl = find_first_col(faena_f, ["Disponibilidad_Tecnica", "Disponibilidad Técnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica %"])
disp_tecnica_0_100 = None
if col_disp_tbl:
    disp_tecnica_0_100 = safe_mean_0_100(faena_f, col_disp_tbl)
else:
    # fallback: calcula desde horas e indisponibilidad
    col_hop = find_first_col(faena_f, ["Horas Operación", "Horas de operación", "Horas Operación "])
    col_indisp = find_first_col(faena_f, ["Indisponibilidad [HH]", "Indisponibilidad"])
    if (not faena_f.empty) and col_hop and col_indisp:
        hop = pd.to_numeric(faena_f[col_hop], errors="coerce")
        ind = pd.to_numeric(faena_f[col_indisp], errors="coerce")
        denom = hop + ind
        serie = (hop / denom.replace({0: pd.NA})) * 100.0
        disp_tecnica_0_100 = float(serie.mean()) if serie.notna().any() else None

# Cumplimiento: usa columna U si existe
col_cumpl = find_first_col(faena_f, ["Cumplimiento"])
cumpl_0_100 = safe_mean_0_100(faena_f, col_cumpl) if col_cumpl else None
if cumpl_0_100 is None:
    # fallback: OP/Target
    col_tgt = find_first_col(faena_f, ["Target Operación", "Target Operacion", "Target"])
    col_op = find_first_col(faena_f, ["Tractos OP", "Tractos Op"])
    if col_tgt and col_op and (not faena_f.empty):
        tgt = pd.to_numeric(faena_f[col_tgt], errors="coerce")
        opv = pd.to_numeric(faena_f[col_op], errors="coerce")
        serie = (opv / tgt.replace({0: pd.NA})) * 100.0
        cumpl_0_100 = float(serie.mean()) if serie.notna().any() else None

# Utilización: usa columna V si existe
col_util = find_first_col(faena_f, ["Utilizacion", "Utilización"])
util_0_100 = safe_mean_0_100(faena_f, col_util) if col_util else None

with c1:
    st.metric("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    st.metric("Horas detención (HH)", fmt_num(total_hh, 2))
with c3:
    st.metric("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    st.metric("Disponibilidad técnica", "—" if disp_tecnica_0_100 is None else fmt_pct_0_100(disp_tecnica_0_100, 1))
with c5:
    st.metric("Cumplimiento (OP/Target)", "—" if cumpl_0_100 is None else fmt_pct_0_100(cumpl_0_100, 1))

st.caption("Por defecto estás viendo el **mes en curso**. Puedes ajustar fechas y filtros en el panel izquierdo.")
st.divider()

# =========================================================
# TABS (sin Resumen)
# =========================================================
tab0, tab2, tab3, tabU, tab4 = st.tabs(
    ["🏠 Estado General", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"]
)

# =========================================================
# TAB 0: ESTADO GENERAL (mejorado)
# =========================================================
with tab0:
    st.subheader("🏠 Estado actual de flota (hoy)")

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

    if estado.empty or col_tracto is None or col_status is None:
        st.info("Para la portada necesito columnas en Estado_Flota: Tracto y Status (y ojalá Ubicación).")
    else:
        dfE = estado.copy()
        dfE[col_tracto] = dfE[col_tracto].astype(str).str.strip()
        dfE[col_status] = dfE[col_status].astype(str).str.strip().str.upper()
        if col_ubic and col_ubic in dfE.columns:
            dfE[col_ubic] = dfE[col_ubic].astype(str).str.strip().str.upper()

        dfE = dfE[dfE[col_tracto].apply(is_valid_tracto_code)].copy()
        dfE["_operativo"] = dfE[col_status].apply(is_operativo_status)

        total_f = int(dfE[col_tracto].nunique())
        op_f = int(dfE[dfE["_operativo"]][col_tracto].nunique())
        no_op_f = max(total_f - op_f, 0)
        disp_hoy = (op_f / total_f) * 100.0 if total_f else None

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Flota (hoy)", f"{total_f:,}".replace(",", "."))
        cB.metric("Operativos (hoy)", f"{op_f:,}".replace(",", "."))
        cC.metric("No operativos (hoy)", f"{no_op_f:,}".replace(",", "."))
        cD.metric("Disponibilidad (hoy)", "—" if disp_hoy is None else fmt_pct_0_100(disp_hoy, 1))

        pie_df = pd.DataFrame({"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]})
        fig_pie = px.pie(
            pie_df, names="Estado", values="Cantidad",
            title="Flota hoy (Operativos vs No operativos)",
            color="Estado",
            color_discrete_map={"Operativos": "green", "No operativos": "red"}
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="tab0_pie_flotahoy")

        # --- Tractos por terminal (Ubicación) ---
        st.markdown("### Tractos por terminal (hoy)")
        if col_ubic and col_ubic in dfE.columns and dfE[col_ubic].notna().any():
            for term in sorted(dfE[col_ubic].dropna().unique()):
                sub = dfE[dfE[col_ubic] == term].copy()
                ops = sorted(sub[sub["_operativo"]][col_tracto].unique())
                noops = sorted(sub[~sub["_operativo"]][col_tracto].unique())

                st.markdown(f"**{term}** — ✅ {len(ops)} operativos | ❌ {len(noops)} fuera")
                st.write(f"✅ Operativos: {', '.join(ops) if ops else '—'}")
                st.write(f"❌ Fuera servicio: {', '.join(noops) if noops else '—'}")
        else:
            st.info("No encuentro columna Ubicación en Estado_Flota para separar por terminal.")

        st.divider()

        # --- Últimas 3 faenas por terminal (desde tabla Faena + detenciones asociadas) ---
        st.subheader("📌 Últimas 3 faenas por terminal (KPI reales desde tu tabla)")

        dfF = faena.copy()
        dfF = _to_datetime(dfF, ["Inicio OP", "Termino Op", "Termino OP", "Término OP"])
        if "Inicio OP" not in dfF.columns or dfF["Inicio OP"].isna().all():
            st.info("No puedo calcular “últimas 3 faenas” porque falta 'Inicio OP' en la tabla Faena.")
        else:
            # columnas KPI (prioridad a tu tabla)
            c_disp = find_first_col(dfF, ["Disponibilidad_Tecnica", "Disponibilidad Técnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica %"])
            c_cum = find_first_col(dfF, ["Cumplimiento"])
            c_utl = find_first_col(dfF, ["Utilizacion", "Utilización"])

            # normalizaciones a % para mostrar
            if c_disp: dfF["_DispTec_%"] = kpi_to_0_100(dfF[c_disp])
            if c_cum:  dfF["_Cumpl_%"]   = kpi_to_0_100(dfF[c_cum])
            if c_utl:  dfF["_Util_%"]    = kpi_to_0_100(dfF[c_utl])

            # si no existen, fallback mínimo
            if not c_disp:
                col_hop0 = find_first_col(dfF, ["Horas Operación", "Horas de operación", "Horas Operación "])
                col_ind0 = find_first_col(dfF, ["Indisponibilidad [HH]", "Indisponibilidad"])
                if col_hop0 and col_ind0:
                    hop = pd.to_numeric(dfF[col_hop0], errors="coerce")
                    ind = pd.to_numeric(dfF[col_ind0], errors="coerce")
                    dfF["_DispTec_%"] = (hop / (hop + ind).replace({0: pd.NA})) * 100.0

            if "Terminal" not in dfF.columns:
                st.info("Falta columna 'Terminal' en Faena para separar por terminal.")
            else:
                dfF["_Terminal"] = dfF["Terminal"].astype(str).str.upper().str.strip()
                dfF = dfF.sort_values("Inicio OP", ascending=False)

                # detenciones: asociar por ventana Inicio..Termino y Terminal (+ Buque si existe)
                det_assoc = det.copy()
                det_assoc = _to_datetime(det_assoc, ["Inicio"])
                has_det_terminal = "Terminal" in det_assoc.columns
                has_det_buque = "Buque" in det_assoc.columns
                has_det_inicio = "Inicio" in det_assoc.columns and det_assoc["Inicio"].notna().any()

                for term in sorted(dfF["_Terminal"].dropna().unique()):
                    last3 = dfF[dfF["_Terminal"] == term].head(3).copy()
                    if last3.empty:
                        continue

                    rows = []
                    for _, r in last3.iterrows():
                        ini = r.get("Inicio OP")
                        fin = r.get("Termino Op") if "Termino Op" in last3.columns else r.get("Termino OP")
                        buq = str(r.get("Buque")).upper().strip() if "Buque" in last3.columns else None

                        dsub = det_assoc.copy()
                        if has_det_inicio and pd.notna(ini):
                            dsub = dsub[dsub["Inicio"] >= ini]
                        if has_det_inicio and pd.notna(fin):
                            dsub = dsub[dsub["Inicio"] <= fin]
                        if has_det_terminal:
                            dsub = dsub[dsub["Terminal"].astype(str).str.upper().str.strip() == term]
                        if has_det_buque and buq and buq != "NAN":
                            dsub = dsub[dsub["Buque"].astype(str).str.upper().str.strip() == buq]

                        det_count = int(len(dsub))
                        det_hh = float(dsub["Horas de reparación"].sum()) if "Horas de reparación" in dsub.columns else 0.0

                        rows.append({
                            "Inicio OP": r.get("Inicio OP"),
                            "Buque": r.get("Buque", ""),
                            "Disp. Técnica": r.get("_DispTec_%", np.nan),
                            "Cumplimiento": r.get("_Cumpl_%", np.nan),
                            "Utilización": r.get("_Util_%", np.nan),
                            "Detenciones": det_count,
                            "HH Detención": det_hh
                        })

                    out = pd.DataFrame(rows)
                    # formato visual
                    st.markdown(f"**{term}**")
                    st.dataframe(
                        out,
                        use_container_width=True,
                        height=160
                    )

            with st.expander("🛈 Cómo se calcula (para evitar malas interpretaciones)"):
                st.markdown(
                    """
- **Disponibilidad Técnica (%)**: se toma desde tu columna **Disponibilidad_Tecnica (T)** si existe.  
  Si no existe, se calcula como: **Horas Operación / (Horas Operación + Indisponibilidad[HH])**.
- **Cumplimiento (%)**: se toma desde tu columna **Cumplimiento (U)** si existe; si viene como ratio (ej 0.83, 1.2, 3), el dashboard lo convierte a % (83%, 120%, 300%).
- **Utilización (%)**: se toma desde tu columna **Utilizacion (V)** si existe; mismo criterio de conversión (ratio -> %).
- **Detenciones y HH**: se asocian a cada faena por ventana de tiempo **Inicio OP → Término OP**, filtrando por **Terminal** y (si existe en detenciones) por **Buque**.
                    """
                )

        st.divider()
        st.markdown("### Listado de tractos (hoy) — verde = operativo / rojo = no operativo")

        cols_show = [col_tracto, col_status]
        if col_ubic and col_ubic in dfE.columns:
            cols_show.insert(1, col_ubic)

        show = dfE[cols_show + ["_operativo"]].copy()

        def _style_row(row):
            if row.get("_operativo", False):
                return ["background-color: #e8f5e9; color: #1b5e20; font-weight: 600;"] * len(row)
            return ["background-color: #ffebee; color: #b71c1c; font-weight: 600;"] * len(row)

        st.dataframe(show.style.apply(_style_row, axis=1), use_container_width=True, height=520)

# =========================================================
# TAB 2: DETENCIONES (con Jackknife)
# =========================================================
with tab2:
    st.subheader("🛑 Detenciones — análisis")

    if det_f.empty:
        st.info("No hay detenciones con los filtros actuales.")
    else:
        # DM/DE/DO
        if "Tipo" in det_f.columns:
            df_dm = det_f.copy()
            df_dm["DMDEDO"] = df_dm["Tipo"].apply(map_dmde_do)

            cL, cR = st.columns(2)
            count_dm = df_dm.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
            cL.plotly_chart(px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM / DE / DO"),
                            use_container_width=True, key="tab2_bar_dm_count")

            if "Horas de reparación" in df_dm.columns:
                hh_dm = df_dm.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
                cR.plotly_chart(px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM / DE / DO"),
                                use_container_width=True, key="tab2_bar_dm_hh")

        st.divider()
        st.subheader("🎯 Jackknife (priorización tipo Pareto)")

        # Selector de dimensión
        dims = []
        for d in ["Equipo", "Componente", "Modo de Falla", "Clasificación", "Familia Equipo"]:
            if d in det_f.columns:
                dims.append(d)

        if ("Horas de reparación" not in det_f.columns) or (len(dims) == 0):
            st.info("Necesito 'Horas de reparación' y al menos una columna categórica (Equipo/Componente/Modo de Falla/...).")
        else:
            dim = st.selectbox("Dimensión para priorizar", dims, index=0)
            topn = st.slider("Top N", 5, 30, 10)

            jdf = det_f.copy()
            jdf["_HH"] = pd.to_numeric(jdf["Horas de reparación"], errors="coerce").fillna(0.0)
            jdf[dim] = jdf[dim].fillna("SIN DATO")

            agg = jdf.groupby(dim)["_HH"].sum().reset_index().sort_values("_HH", ascending=False)
            agg = agg.head(topn).copy()
            total = float(agg["_HH"].sum()) if len(agg) else 0.0
            agg["%"] = (agg["_HH"] / total * 100.0) if total > 0 else 0.0
            agg["% Acum"] = agg["%"].cumsum()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=agg[dim], y=agg["_HH"], name="HH"))
            fig.add_trace(go.Scatter(x=agg[dim], y=agg["% Acum"], name="% acumulado", yaxis="y2", mode="lines+markers"))

            fig.update_layout(
                title=f"Jackknife / Pareto de HH por {dim} (Top {topn})",
                yaxis=dict(title="HH"),
                yaxis2=dict(title="% acumulado", overlaying="y", side="right", range=[0, 100]),
                legend=dict(orientation="h"),
                margin=dict(l=40, r=40, t=60, b=40)
            )
            st.plotly_chart(fig, use_container_width=True, key="tab2_jackknife")

        st.divider()
        st.subheader("Tabla detenciones filtradas")
        st.dataframe(det_f, use_container_width=True, height=520)

# =========================================================
# TAB 3: DISPONIBILIDAD (FAENA) - usa tu T si existe
# =========================================================
with tab3:
    st.subheader("✅ Disponibilidad por Faena (histórico)")

    if faena_f.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        c_disp = find_first_col(df, ["Disponibilidad_Tecnica", "Disponibilidad Técnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica %"])
        if c_disp:
            df["DispTec_%"] = kpi_to_0_100(df[c_disp])
        else:
            col_hop = find_first_col(df, ["Horas Operación", "Horas de operación", "Horas Operación "])
            col_indisp = find_first_col(df, ["Indisponibilidad [HH]", "Indisponibilidad"])
            if col_hop and col_indisp:
                hop = pd.to_numeric(df[col_hop], errors="coerce")
                ind = pd.to_numeric(df[col_indisp], errors="coerce")
                df["DispTec_%"] = (hop / (hop + ind).replace({0: pd.NA})) * 100.0

        with st.expander("🛈 ¿Qué es esta disponibilidad?"):
            st.write("**Disponibilidad técnica (%)**: valor real desde tu tabla (si existe) o calculado como **Horas Operación / (Horas Operación + Indisponibilidad[HH])**.")

        if "Inicio OP" in df.columns and df["Inicio OP"].notna().any() and "DispTec_%" in df.columns:
            df["Fecha"] = pd.to_datetime(df["Inicio OP"], errors="coerce").dt.date
            g = df.groupby("Fecha")["DispTec_%"].mean().reset_index()
            st.plotly_chart(
                px.line(g, x="Fecha", y="DispTec_%", markers=True, title="Disponibilidad técnica promedio por día (%)"),
                use_container_width=True,
                key="tab3_line_disptec_dia"
            )

        # promedio por terminal (para lectura simple)
        if "Terminal" in df.columns and "DispTec_%" in df.columns:
            gt = df.groupby("Terminal")["DispTec_%"].mean().reset_index().sort_values("DispTec_%", ascending=False)
            st.plotly_chart(
                px.bar(gt, x="Terminal", y="DispTec_%", title="Disponibilidad técnica promedio por Terminal (%)"),
                use_container_width=True,
                key="tab3_bar_disptec_terminal"
            )

        st.dataframe(df, use_container_width=True, height=520)

# =========================================================
# TAB U: UTILIZACIÓN (usa U,V reales)
# =========================================================
with tabU:
    st.subheader("📈 Utilización y Cumplimiento (según tabla Faena)")

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        col_cumpl = find_first_col(dfu, ["Cumplimiento"])
        col_util = find_first_col(dfu, ["Utilizacion", "Utilización"])
        col_disp = find_first_col(dfu, ["Disponibilidad_Tecnica", "Disponibilidad Técnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica %"])

        # convertir a 0..100 para mostrar
        if col_cumpl: dfu["_Cumpl_%"] = kpi_to_0_100(dfu[col_cumpl])
        if col_util:  dfu["_Util_%"]  = kpi_to_0_100(dfu[col_util])
        if col_disp:  dfu["_Disp_%"]  = kpi_to_0_100(dfu[col_disp])

        # KPIs del tab
        k1, k2, k3 = st.columns(3)
        m_c = float(dfu["_Cumpl_%"].mean()) if "_Cumpl_%" in dfu.columns and dfu["_Cumpl_%"].notna().any() else None
        m_u = float(dfu["_Util_%"].mean()) if "_Util_%" in dfu.columns and dfu["_Util_%"].notna().any() else None
        m_d = float(dfu["_Disp_%"].mean()) if "_Disp_%" in dfu.columns and dfu["_Disp_%"].notna().any() else None

        k1.metric("Cumplimiento (U)", "—" if m_c is None else fmt_pct_0_100(m_c, 1))
        k2.metric("Utilización (V)", "—" if m_u is None else fmt_pct_0_100(m_u, 1))
        k3.metric("Disponibilidad Técnica (T)", "—" if m_d is None else fmt_pct_0_100(m_d, 1))

        with st.expander("🛈 Interpretación sin errores"):
            st.markdown(
                """
- Si tu Excel guarda `0.83`, eso significa **83%** (ratio).  
- Si guarda `1.20`, eso significa **120%**.  
- Si guarda `3.00`, eso significa **300%**.  

Este dashboard **detecta ratios (0..5) y los convierte a porcentaje** automáticamente para que coincida con lo que tú interpretas en Excel.
                """
            )

        st.divider()

        # series por fecha (simple y entendible)
        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date

            series_cols = []
            if "_Cumpl_%" in dfu.columns: series_cols.append("_Cumpl_%")
            if "_Util_%" in dfu.columns: series_cols.append("_Util_%")
            if "_Disp_%" in dfu.columns: series_cols.append("_Disp_%")

            if series_cols:
                g = dfu.groupby("Fecha")[series_cols].mean().reset_index()
                melt = g.melt(id_vars=["Fecha"], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.line(melt, x="Fecha", y="%", color="KPI", markers=True, title="Evolución diaria de KPI (%)"),
                    use_container_width=True,
                    key="tabU_line_kpis"
                )

        # por terminal (barras agrupadas)
        if "Terminal" in dfu.columns:
            metrics = [c for c in ["_Cumpl_%", "_Util_%", "_Disp_%"] if c in dfu.columns]
            if metrics:
                gt = dfu.groupby("Terminal")[metrics].mean().reset_index()
                melt = gt.melt(id_vars=["Terminal"], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.bar(melt, x="Terminal", y="%", color="KPI", barmode="group",
                           title="Promedios por Terminal (KPI %)"),
                    use_container_width=True,
                    key="tabU_bar_terminal"
                )

        st.divider()
        st.subheader("Tabla (gestión)")

        cols_show = []
        for c in ["Inicio OP", "Terminal", "Buque", col_disp, col_cumpl, col_util, "_Disp_%", "_Cumpl_%", "_Util_%"]:
            if c and c in dfu.columns and c not in cols_show:
                cols_show.append(c)

        st.dataframe(dfu[cols_show], use_container_width=True, height=520)

# =========================================================
# TAB 4: EXPORT
# =========================================================
with tab4:
    st.subheader("📁 Exportar datos filtrados")
    cA4, cB4 = st.columns(2)
    with cA4:
        csv_det = det_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Detenciones (CSV)", data=csv_det,
                           file_name="detenciones_filtradas.csv", mime="text/csv")
    with cB4:
        csv_faena = faena_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Faena (CSV)", data=csv_faena,
                           file_name="faena_filtrada.csv", mime="text/csv")

st.caption("Fuente: Google Sheets exportado a XLSX (Faena, Detenciones, Estado_Flota). Dashboard Streamlit.")
