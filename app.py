# app.py
import os
import io
import re
from datetime import date
import pandas as pd
import streamlit as st
import plotly.express as px
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

def percent_to_0_100(series: pd.Series) -> pd.Series:
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
        if mx > 150.0:
            return s / 100.0
    return s

def normalize_disponibilidad_to_0_100(series: pd.Series) -> pd.Series:
    return percent_to_0_100(series)

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

# ===== NUEVO: tomar columna por “letra Excel” (A=0, B=1, …, N=13, O=14, P=15)
def col_by_excel_letter(df: pd.DataFrame, letter: str) -> str | None:
    if df is None or df.empty:
        return None
    letter = str(letter).strip().upper()
    if not re.match(r"^[A-Z]$", letter):
        return None
    idx = ord(letter) - ord("A")
    if 0 <= idx < len(df.columns):
        return df.columns[idx]
    return None

# ===== NUEVO: convertir a % correcto para mostrar (ratio->% / % queda igual)
def to_percent_display(series: pd.Series) -> pd.Series:
    """
    Si la serie viene como:
    - ratio típico (0..3 aprox): 0.83 => 83%
    - 0..1: 0.78 => 78%
    - 0..100: 78 => 78%
    - texto "78%": 78
    """
    if series is None:
        return series
    s = series.copy()
    if s.dtype == "object":
        s = s.astype(str).str.replace("%", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
    s = pd.to_numeric(s, errors="coerce")
    if not s.notna().any():
        return s
    mx = float(s.max())
    # ratio o fracción
    if mx <= 3.5:
        return s * 100.0
    # ya es porcentaje
    if mx <= 150.0:
        return s
    # si viene como 7800 por formato raro
    return s / 100.0

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

    # Numéricos Faena (incluye tus columnas nuevas)
    num_cols = [
        "Horas Operación", "Horas de operación", "Horas Operación ",
        "Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad [H]",
        "Disponibilidad",
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
        "Disponibilidad_Tecnica", "Disponibilidad Tecnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica",
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

    # Derivados detenciones
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

def fmt_num(x, dec=1):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")

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
# KPI TOP
# =========================================================
c1, c2, c3, c4, c5 = st.columns(5)

total_det = int(det_f.shape[0])
total_hh = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

# ===== CORREGIDO: tomar DISP TEC desde columna N (si existe)
col_disptec_N = col_by_excel_letter(faena_f, "N")
disp_tecnica = None
if (not faena_f.empty) and col_disptec_N and faena_f[col_disptec_N].notna().any():
    disp_pct = to_percent_display(faena_f[col_disptec_N])
    disp_tecnica = float(disp_pct.mean()) / 100.0  # guardo ratio 0..1 para mostrar
else:
    # fallback anterior (no lo elimino)
    col_hop = find_first_col(faena_f, ["Horas Operación", "Horas de operación", "Horas Operación "])
    col_indisp = find_first_col(faena_f, ["Indisponibilidad [HH]", "Indisponibilidad"])
    if (not faena_f.empty) and col_hop and col_indisp:
        hop = pd.to_numeric(faena_f[col_hop], errors="coerce")
        ind = pd.to_numeric(faena_f[col_indisp], errors="coerce")
        denom = hop + ind
        serie = hop / denom.replace({0: pd.NA})
        disp_tecnica = float(serie.mean()) if serie.notna().any() else None

# ===== CORREGIDO: Cumplimiento desde columna O (ratio OP/Target)
col_cumpl_O = col_by_excel_letter(faena_f, "O")
cumpl = None
if (not faena_f.empty) and col_cumpl_O and faena_f[col_cumpl_O].notna().any():
    c_pct = to_percent_display(faena_f[col_cumpl_O])  # ratio->%
    cumpl = float(c_pct.mean()) / 100.0               # ratio 0..1
else:
    # fallback anterior (no lo elimino)
    col_cumpl = find_first_col(faena_f, ["Cumplimiento"])
    col_tgt = find_first_col(faena_f, ["Target Operación", "Target Operacion", "Target"])
    col_op = find_first_col(faena_f, ["Tractos OP", "Tractos Op"])
    if (not faena_f.empty):
        if col_cumpl and col_cumpl in faena_f.columns and faena_f[col_cumpl].notna().any():
            c_pct = to_percent_display(faena_f[col_cumpl])
            cumpl = float(c_pct.mean()) / 100.0
        elif col_tgt and col_op:
            tgt = pd.to_numeric(faena_f[col_tgt], errors="coerce")
            opv = pd.to_numeric(faena_f[col_op], errors="coerce")
            serie = opv / tgt.replace({0: pd.NA})
            cumpl = float(serie.mean()) if serie.notna().any() else None

# “Utilización” desde columna P (solo si la ocupas en KPI)
col_util_P = col_by_excel_letter(faena_f, "P")
util_ratio = None
if (not faena_f.empty) and col_util_P and faena_f[col_util_P].notna().any():
    u_pct = to_percent_display(faena_f[col_util_P])
    util_ratio = float(u_pct.mean()) / 100.0

with c1:
    st.metric("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    st.metric("Horas detención (HH)", fmt_num(total_hh, 2))
with c3:
    st.metric("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    st.metric("Disponibilidad técnica", "—" if disp_tecnica is None else f"{disp_tecnica*100:.1f}%")
with c5:
    st.metric("Cumplimiento (OP/Target)", "—" if cumpl is None else f"{cumpl*100:.1f}%")

st.caption("Por defecto estás viendo el **mes en curso**. Puedes ajustar fechas y filtros en el panel izquierdo.")
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
        st.info("Para la portada necesito columnas en Estado_Flota: Tracto y Status.")
    else:
        dfE = estado.copy()
        dfE[col_tracto] = dfE[col_tracto].astype(str).str.strip()
        dfE[col_status] = dfE[col_status].astype(str).str.strip().str.upper()
        if col_ubic and col_ubic in dfE.columns:
            dfE[col_ubic] = dfE[col_ubic].astype(str).str.strip()

        dfE = dfE[dfE[col_tracto].apply(is_valid_tracto_code)].copy()

        dfE["_operativo"] = dfE[col_status].apply(is_operativo_status)
        total_f = int(dfE[col_tracto].nunique())
        op_f = int(dfE[dfE["_operativo"]][col_tracto].nunique())
        no_op_f = max(total_f - op_f, 0)
        disp_hoy = (op_f / total_f) if total_f else None

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Flota (hoy)", f"{total_f:,}".replace(",", "."))
        cB.metric("Operativos (hoy)", f"{op_f:,}".replace(",", "."))
        cC.metric("No operativos (hoy)", f"{no_op_f:,}".replace(",", "."))
        cD.metric("Disponibilidad (hoy)", "—" if disp_hoy is None else f"{disp_hoy*100:.1f}%")

        pie_df = pd.DataFrame(
            {"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]}
        )
        fig_pie = px.pie(
            pie_df, names="Estado", values="Cantidad",
            title="Flota hoy (Operativos vs No operativos)",
            color="Estado",
            color_discrete_map={"Operativos": "green", "No operativos": "red"}
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="tab0_pie_flotahoy")

        st.markdown("### Listado de tractos (hoy) — verde = operativo / rojo = no operativo")

        cols_show = [col_tracto, col_status]
        if col_ubic and col_ubic in dfE.columns:
            cols_show.insert(1, col_ubic)

        show = dfE[cols_show + ["_operativo"]].copy()

        def _style_row(row):
            if row.get("_operativo", False):
                return ["background-color: #e8f5e9; color: #1b5e20; font-weight: 600;"] * len(row)
            return ["background-color: #ffebee; color: #b71c1c; font-weight: 600;"] * len(row)

        st.dataframe(
            show.style.apply(_style_row, axis=1),
            use_container_width=True,
            height=520
        )

# =========================================================
# TAB 1: RESUMEN  (sin cambios)
# =========================================================
with tab1:
    st.subheader("📌 Resumen (según filtros)")

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

# =========================================================
# TAB 2: DETENCIONES (sin cambios)
# =========================================================
with tab2:
    st.subheader("🛑 Detenciones — análisis")

    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        df_dm = det_f.copy()
        df_dm["DMDEDO"] = df_dm["Tipo"].apply(map_dmde_do)

        col_left, col_right = st.columns(2)

        count_dm = df_dm.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        col_left.plotly_chart(
            px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM / DE / DO"),
            use_container_width=True,
            key="tab2_bar_dm_count"
        )

        if "Horas de reparación" in df_dm.columns:
            hh_dm = df_dm.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            col_right.plotly_chart(
                px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM / DE / DO"),
                use_container_width=True,
                key="tab2_bar_dm_hh"
            )

    st.divider()
    st.subheader("Tabla detenciones filtradas")
    st.dataframe(det_f, use_container_width=True, height=500)

# =========================================================
# TAB 3: DISPONIBILIDAD (FAENA)
# =========================================================
with tab3:
    st.subheader("✅ Disponibilidad por Faena (histórico)")

    if faena_f.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        # ===== CORREGIDO: usar columna N
        col_disptec = col_by_excel_letter(df, "N")
        if col_disptec and df[col_disptec].notna().any():
            df["_DispTec_%"] = to_percent_display(df[col_disptec])

            with st.expander("🛈 ¿Qué es esta disponibilidad? (importante)"):
                st.write("Mostramos **Disponibilidad Técnica** desde tu **tabla Faena** (columna N).")

            if "Inicio OP" in df.columns and df["Inicio OP"].notna().any():
                df["Fecha"] = pd.to_datetime(df["Inicio OP"], errors="coerce").dt.date
                g = df.groupby("Fecha")["_DispTec_%"].mean().reset_index()
                st.plotly_chart(
                    px.line(g, x="Fecha", y="_DispTec_%", markers=True, title="Disponibilidad técnica promedio por día (%)"),
                    use_container_width=True,
                    key="tab3_line_disptec_dia"
                )

        st.dataframe(df, use_container_width=True, height=500)

# =========================================================
# TAB U: UTILIZACIÓN
# =========================================================
with tabU:
    st.subheader("📈 Utilización — (según tabla Faena)")

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        # ===== CORREGIDO: columnas por letra (N/O/P)
        col_disptec = col_by_excel_letter(dfu, "N")  # Disponibilidad_Tecnica
        col_cumpl   = col_by_excel_letter(dfu, "O")  # Cumplimiento
        col_util    = col_by_excel_letter(dfu, "P")  # Utilizacion

        # Si por alguna razón no existen, fallback a nombre (no borro tu lógica)
        if col_cumpl is None:
            col_cumpl = find_first_col(dfu, ["Cumplimiento"])
        if col_util is None:
            col_util = find_first_col(dfu, ["Utilizacion", "Utilización"])
        if col_disptec is None:
            col_disptec = find_first_col(dfu, ["Disponibilidad_Tecnica", "Disponibilidad Tecnica", "Disponibilidad_Tecnica_%", "Disponibilidad Técnica"])

        # Normalizar para mostrar (ojo: cumplimiento es ratio en tu archivo)
        if col_cumpl and col_cumpl in dfu.columns:
            dfu["_Cumpl_%"] = to_percent_display(dfu[col_cumpl])
        if col_util and col_util in dfu.columns:
            dfu["_Util_%"] = to_percent_display(dfu[col_util])
        if col_disptec and col_disptec in dfu.columns:
            dfu["_DispTec_%"] = to_percent_display(dfu[col_disptec])

        k1, k2, k3 = st.columns(3)
        m_c = float(dfu["_Cumpl_%"].mean()) if "_Cumpl_%" in dfu.columns and dfu["_Cumpl_%"].notna().any() else None
        m_u = float(dfu["_Util_%"].mean()) if "_Util_%" in dfu.columns and dfu["_Util_%"].notna().any() else None
        m_d = float(dfu["_DispTec_%"].mean()) if "_DispTec_%" in dfu.columns and dfu["_DispTec_%"].notna().any() else None

        k1.metric("Cumplimiento (O)", "—" if m_c is None else f"{m_c:.1f}%")
        k2.metric("Utilización (P)", "—" if m_u is None else f"{m_u:.1f}%")
        k3.metric("Disponibilidad Técnica (N)", "—" if m_d is None else f"{m_d:.1f}%")

        with st.expander("🛈 Interpretación sin errores"):
            st.markdown(
                """
- **Disponibilidad Técnica (N)**: valor leído desde tu tabla Faena (si viene como 0.78 lo muestro como 78%).
- **Cumplimiento (O)**: tu columna es **ratio OP/Target** (ej: 0.83 = 83%, 1.66 = 166%).
- **Utilización (P)**: se muestra como % tal cual está en tu tabla.
                """
            )

        st.divider()

        # gráfico por fecha si existe
        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date
            cols_plot = [c for c in ["_Cumpl_%", "_Util_%", "_DispTec_%"] if c in dfu.columns]
            if cols_plot:
                g = dfu.groupby("Fecha")[cols_plot].mean().reset_index()
                melt = g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="%")
                st.plotly_chart(
                    px.line(melt, x="Fecha", y="%", color="Métrica", markers=True, title="Series por día (%)"),
                    use_container_width=True,
                    key="tabU_line_series"
                )

        st.subheader("Tabla (gestión)")
        cols_show = []
        for c in ["Inicio OP", "Terminal", "Buque", col_disptec, col_cumpl, col_util, "Target Operación", "Tractos OP", "_DispTec_%", "_Cumpl_%", "_Util_%"]:
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
