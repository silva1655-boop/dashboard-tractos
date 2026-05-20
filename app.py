import os
import io
import re
import unicodedata
from datetime import timedelta
import pandas as pd
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

# ── Solicitudes de Mantenimiento ──────────────────────────
SOLICITUDES_ID_DEFAULT = "1wZtLhAPvbft27aX9EmySSul5h4e7YeeT"
SOLICITUDES_URL_DEFAULT = (
    f"https://docs.google.com/spreadsheets/d/{SOLICITUDES_ID_DEFAULT}/export?format=xlsx"
)
SOLICITUDES_URL = os.getenv("SOLICITUDES_URL", SOLICITUDES_URL_DEFAULT).strip()
SHEET_SOLICITUDES = os.getenv("SHEET_SOLICITUDES", "Sheet1")  # ajustar si el nombre es distinto

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

    def _norm_key(s: str) -> str:
        if s is None:
            return ""
        s = str(s).strip()
        s = "".join(
            c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
        )
        s = s.lower()
        for ch in [" ", "_", "-", "%", "(", ")", ":"]:
            s = s.replace(ch, "")
        return s

    mapping: dict[str, str] = {}
    for col in df.columns:
        key = _norm_key(col)
        if key:
            mapping[key] = col

    for c in candidates:
        key = _norm_key(c)
        if key in mapping:
            return mapping[key]
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
    if s in (
        "", "TOTAL", "EN SERVICIO", "FUERA DE SERVICIO", "EN MTTO",
        "EN MANTTO", "ESTADO", "UBICACIÓN", "UBICACION",
    ):
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


def _as_upper_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _pct_mean(df: pd.DataFrame, col: str | None):
    if not col or col not in df.columns or df.empty:
        return None
    v = percent_to_0_100(pd.to_numeric(df[col], errors="coerce")).mean()
    return None if pd.isna(v) else float(v)


def _get_faena_kpis_row(row: pd.Series, col_disp, col_cump, col_util, col_hop, col_indisp, col_tgt, col_op):
    disp = None
    cump = None
    util = None

    if col_disp and col_disp in row.index and pd.notna(row[col_disp]):
        disp = percent_to_0_100(pd.Series([row[col_disp]])).iloc[0]
    elif col_hop and col_indisp and col_hop in row.index and col_indisp in row.index:
        hop = pd.to_numeric(pd.Series([row[col_hop]]), errors="coerce").iloc[0]
        ind = pd.to_numeric(pd.Series([row[col_indisp]]), errors="coerce").iloc[0]
        if pd.notna(hop) and pd.notna(ind) and (hop + ind) > 0:
            disp = (hop / (hop + ind)) * 100.0

    if col_cump and col_cump in row.index and pd.notna(row[col_cump]):
        cump = percent_to_0_100(pd.Series([row[col_cump]])).iloc[0]
    elif col_tgt and col_op and col_tgt in row.index and col_op in row.index:
        tgt = pd.to_numeric(pd.Series([row[col_tgt]]), errors="coerce").iloc[0]
        opv = pd.to_numeric(pd.Series([row[col_op]]), errors="coerce").iloc[0]
        if pd.notna(tgt) and tgt > 0 and pd.notna(opv):
            cump = (opv / tgt) * 100.0

    if col_util and col_util in row.index and pd.notna(row[col_util]):
        util = percent_to_0_100(pd.Series([row[col_util]])).iloc[0]

    return disp, cump, util


def _find_faena_window(row: pd.Series, col_ini: str | None, col_fin: str | None):
    ini = None
    fin = None
    if col_ini and col_ini in row.index:
        ini = pd.to_datetime(row[col_ini], errors="coerce")
    if col_fin and col_fin in row.index:
        fin = pd.to_datetime(row[col_fin], errors="coerce")
    if pd.isna(fin) and pd.notna(ini):
        fin = ini + timedelta(hours=24)
    return ini, fin


def _detenciones_in_window(det: pd.DataFrame, terminal: str | None, ini, fin):
    if det.empty or "Inicio" not in det.columns:
        return 0, 0.0
    d = det.copy()
    d = d[d["Inicio"].notna()]
    if terminal and "Terminal" in d.columns:
        d = d[_as_upper_series(d["Terminal"]) == str(terminal).strip().upper()]
    if ini is not None and pd.notna(ini):
        d = d[d["Inicio"] >= ini]
    if fin is not None and pd.notna(fin):
        d = d[d["Inicio"] <= fin]
    hh = float(d["Horas de reparación"].sum()) if "Horas de reparación" in d.columns else 0.0
    return int(d.shape[0]), hh


# =========================================================
# SOLICITUDES — HELPERS ESPECÍFICOS
# =========================================================

# Mapeo de estados canónicos
_ESTADO_MAP = {
    # Abierta
    "ABIERTA": "Abierta",
    "ABIERTO": "Abierta",
    "OPEN": "Abierta",
    "NUEVA": "Abierta",
    "NUEVO": "Abierta",
    "PENDIENTE": "Abierta",
    # En planificación
    "EN PLANIFICACION": "En Planificación",
    "EN PLANIFICACIÓN": "En Planificación",
    "PLANIFICACION": "En Planificación",
    "PLANIFICACIÓN": "En Planificación",
    "PLANIFICADO": "En Planificación",
    "PLANIFICADA": "En Planificación",
    "EN PROCESO": "En Planificación",
    "EN PROGRESO": "En Planificación",
    "IN PROGRESS": "En Planificación",
    # Cerrada
    "CERRADA": "Cerrada",
    "CERRADO": "Cerrada",
    "CLOSED": "Cerrada",
    "COMPLETADA": "Cerrada",
    "COMPLETADO": "Cerrada",
    "FINALIZADA": "Cerrada",
    "FINALIZADO": "Cerrada",
    "EJECUTADA": "Cerrada",
    "EJECUTADO": "Cerrada",
    "TERMINADA": "Cerrada",
    "TERMINADO": "Cerrada",
    # Rechazada
    "RECHAZADA": "Rechazada",
    "RECHAZADO": "Rechazada",
    "REJECTED": "Rechazada",
    "CANCELADA": "Rechazada",
    "CANCELADO": "Rechazada",
}

_ESTADO_ORDER = ["Abierta", "En Planificación", "Cerrada", "Rechazada", "Sin Estado"]

_ESTADO_COLORS = {
    "Abierta": "#f59e0b",
    "En Planificación": "#3b82f6",
    "Cerrada": "#22c55e",
    "Rechazada": "#ef4444",
    "Sin Estado": "#9ca3af",
}

_PRIORIDAD_ORDER = ["Crítica", "Alta", "Media", "Baja", "Sin Prioridad"]
_PRIORIDAD_COLORS = {
    "Crítica": "#ef4444",
    "Alta": "#f97316",
    "Media": "#f59e0b",
    "Baja": "#22c55e",
    "Sin Prioridad": "#9ca3af",
}

_PRIORIDAD_MAP = {
    "CRITICA": "Crítica",
    "CRÍTICA": "Crítica",
    "CRITICAL": "Crítica",
    "URGENTE": "Crítica",
    "ALTA": "Alta",
    "HIGH": "Alta",
    "MEDIA": "Media",
    "MEDIUM": "Media",
    "NORMAL": "Media",
    "BAJA": "Baja",
    "LOW": "Baja",
}


def _map_estado_canonico(s) -> str:
    if pd.isna(s) or str(s).strip() == "":
        return "Sin Estado"
    key = str(s).strip().upper()
    return _ESTADO_MAP.get(key, str(s).strip().title())


def _map_prioridad_canonica(s) -> str:
    if pd.isna(s) or str(s).strip() == "":
        return "Sin Prioridad"
    key = str(s).strip().upper()
    return _PRIORIDAD_MAP.get(key, str(s).strip().title())


def _badge_estado(estado: str) -> str:
    """Devuelve un emoji badge según el estado."""
    return {
        "Abierta": "🟡",
        "En Planificación": "🔵",
        "Cerrada": "🟢",
        "Rechazada": "🔴",
    }.get(estado, "⚪")


def _style_solicitudes(row):
    """Colorea filas de la tabla de solicitudes según estado."""
    color_map = {
        "Abierta": ("background-color:#fffbeb; color:#78350f;", "#fef3c7"),
        "En Planificación": ("background-color:#eff6ff; color:#1e3a5f;", "#dbeafe"),
        "Cerrada": ("background-color:#f0fdf4; color:#14532d;", "#dcfce7"),
        "Rechazada": ("background-color:#fff1f2; color:#7f1d1d;", "#ffe4e6"),
    }
    estado = row.get("Estado", "")
    style, _ = color_map.get(estado, ("", ""))
    return [style] * len(row)


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

    faena = _to_datetime(faena, ["Inicio OP", "Termino OP", "Termino Op", "Término OP"])
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])

    num_cols = [
        "Horas Operación", "Horas de operación", "Horas Operación ",
        "Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad [H]", "Indisponibilidad[HH]",
        "Disponibilidad_Tecnica", "Disponibilidad_Tecnica_%", "Disponibilidad técnica", "Disponibilidad técnica %",
        "Target Operación", "Target Operacion", "Target", "Target operaciones",
        "Tractos OP", "Tractos Op", "Tracto OP", "Tractos operación",
        "Tractos Utilizados", "Tractos utilizados", "Tractos Utilizados ",
        "Capacidad_Operadores", "Capacidad Operadores",
        "Capacidad_Real", "Capacidad Real",
        "Utilizacion_demandada_%", "Utilización_demandada_%",
        "Utilizacion_Oferta_%", "Utilización_Oferta_%",
        "Utilizacion_Capacidad_%", "Utilización_Capacidad_%",
        "Cumplimiento", "Cumplimiento %", "Cumplimiento (U)",
        "Utilizacion", "Utilización",
        "Utilización Esperada", "Utilizacion Esperada",
        "N° SEM", "N SEM", "Nº SEM", "Mes", "ANO", "AÑO", "Año"
    ]
    for c in num_cols:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    for c in [
        "Equipo", "Clasificación", "Familia Equipo", "Componente",
        "Modo de Falla", "Tipo", "Nave", "Buque", "Viaje", "Terminal"
    ]:
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
    df = raw.iloc[header_idx + 1 :].copy()
    df.columns = hdr
    df = df.dropna(how="all")
    df = _normalize_cols(df)
    df.columns = _make_unique_columns(df.columns)

    for j in range(df.shape[1]):
        if df.iloc[:, j].dtype == "object":
            df.iloc[:, j] = df.iloc[:, j].astype(str).str.strip()

    return df


@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_solicitudes() -> pd.DataFrame:
    """
    Carga la hoja de Solicitudes de Mantenimiento desde Google Sheets.
    Detecta automáticamente la fila de encabezado buscando palabras clave
    como 'solicitud', 'estado', 'equipo', 'descripcion'.
    Normaliza estados y prioridades a categorías canónicas.
    """
    content = download_google_xlsx(SOLICITUDES_URL)

    # Intentar detectar hoja correcta
    xl = pd.ExcelFile(io.BytesIO(content))
    sheet_names = xl.sheet_names

    # Priorizar nombre configurado, luego cualquier hoja disponible
    target_sheet = SHEET_SOLICITUDES
    if target_sheet not in sheet_names:
        target_sheet = sheet_names[0]

    raw = pd.read_excel(io.BytesIO(content), sheet_name=target_sheet, header=None)
    raw = raw.dropna(how="all").fillna("")

    def _row_text(i):
        return " | ".join([str(x).strip().lower() for x in raw.iloc[i].tolist()])

    # Buscar fila de encabezado
    header_idx = 0
    kw_sets = [
        {"solicitud", "estado"},
        {"equipo", "estado"},
        {"descripcion", "estado"},
        {"descripción", "estado"},
        {"mantenimiento", "estado"},
        {"fecha", "estado"},
        {"solicitud"},
        {"n°", "descripcion"},
        {"n°", "descripción"},
    ]
    for i in range(min(len(raw), 15)):
        txt = _row_text(i)
        for kw_set in kw_sets:
            if all(k in txt for k in kw_set):
                header_idx = i
                break

    hdr = raw.iloc[header_idx].tolist()
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = hdr
    df = df.dropna(how="all")
    df = _normalize_cols(df)
    df.columns = _make_unique_columns(df.columns)

    # Limpiar strings
    for j in range(df.shape[1]):
        if df.iloc[:, j].dtype == "object":
            df.iloc[:, j] = df.iloc[:, j].astype(str).str.strip().replace("nan", "")

    # Normalizar columna Estado
    col_estado = find_first_col(
        df,
        ["Estado", "STATUS", "Status", "Estatus", "Estado solicitud", "Estado Solicitud"],
    )
    if col_estado:
        df["Estado"] = df[col_estado].apply(_map_estado_canonico)
    else:
        df["Estado"] = "Sin Estado"

    # Normalizar columna Prioridad
    col_prio = find_first_col(
        df, ["Prioridad", "Priority", "Urgencia", "Nivel", "Nivel de urgencia"]
    )
    if col_prio:
        df["Prioridad"] = df[col_prio].apply(_map_prioridad_canonica)
    else:
        df["Prioridad"] = "Sin Prioridad"

    # Fechas
    date_candidates = [
        "Fecha", "Fecha Solicitud", "Fecha solicitud", "Fecha de solicitud",
        "Fecha Creación", "Fecha creacion", "Fecha ingreso",
        "Fecha Cierre", "Fecha cierre", "Fecha de cierre",
        "Fecha Inicio", "Fecha inicio",
    ]
    for dc in date_candidates:
        col_d = find_first_col(df, [dc])
        if col_d:
            df[col_d] = pd.to_datetime(df[col_d], errors="coerce")

    # Quitar filas totalmente vacías o que sean encabezados repetidos
    if col_estado:
        df = df[df[col_estado].astype(str).str.upper() != col_estado.upper()]

    df = df.reset_index(drop=True)
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
# SIDEBAR (DEFAULT = MES EN CURSO)
# =========================================================
with st.sidebar:
    st.header("⚙️ Actualización")
    refresh_sec = st.number_input(
        "Refresco automático (segundos)",
        min_value=30,
        max_value=1800,
        value=DEFAULT_REFRESH_SEC,
        step=30,
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

col_dispT = find_first_col(
    faena_f,
    [
        "Disponibilidad_Tecnica",
        "Disponibilidad_Tecnica_%",
        "Disponibilidad técnica",
        "Disponibilidad tecnica",
        "Disponibilidad_Tecnica %",
        "Disponibilidad_Técnica",
        "Disponibilidad_Técnica_%",
        "Disponibilidad tecnica %",
        "Disponibilidad técnica (%)",
    ],
)
col_cumplU = find_first_col(
    faena_f,
    [
        "Cumplimiento",
        "Cumplimiento (U)",
        "Cumplimiento %",
        "Cumplimiento(U)",
    ],
)

col_hop = find_first_col(
    faena_f,
    [
        "Horas Operación",
        "Horas de operación",
        "Horas Operación ",
        "Horas operacion",
        "Horas de operacion",
    ],
)
col_indisp = find_first_col(
    faena_f,
    [
        "Indisponibilidad [HH]",
        "Indisponibilidad",
        "Indisponibilidad [H]",
        "Indisponibilidad[HH]",
        "Indisponibilidad horas",
    ],
)

col_tgt = find_first_col(
    faena_f,
    ["Target Operación", "Target Operacion", "Target", "Target operaciones"],
)
col_op = find_first_col(
    faena_f,
    ["Tractos OP", "Tractos Op", "Tracto OP", "Tractos operación"],
)

disp_tecnica = None
if not faena_f.empty:
    if col_dispT and faena_f[col_dispT].notna().any():
        disp_tecnica = _pct_mean(faena_f, col_dispT)
        if disp_tecnica is not None:
            disp_tecnica = disp_tecnica / 100.0
    elif col_hop and col_indisp:
        hop = pd.to_numeric(faena_f[col_hop], errors="coerce")
        ind = pd.to_numeric(faena_f[col_indisp], errors="coerce")
        denom = hop + ind
        serie = hop / denom.replace({0: pd.NA})
        disp_tecnica = float(serie.mean()) if serie.notna().any() else None

cumpl = None
if not faena_f.empty:
    if col_cumplU and faena_f[col_cumplU].notna().any():
        m = _pct_mean(faena_f, col_cumplU)
        cumpl = None if m is None else (m / 100.0)
    elif col_tgt and col_op:
        tgt = pd.to_numeric(faena_f[col_tgt], errors="coerce")
        opv = pd.to_numeric(faena_f[col_op], errors="coerce")
        serie = opv / tgt.replace({0: pd.NA})
        cumpl = float(serie.mean()) if serie.notna().any() else None

with c1:
    st.metric("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    st.metric("Horas detención (HH)", fmt_num(total_hh, 2))
with c3:
    st.metric("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    st.metric(
        "Disponibilidad técnica",
        "—" if disp_tecnica is None else f"{disp_tecnica * 100:.1f}%",
    )
with c5:
    st.metric(
        "Cumplimiento (OP/Target)", "—" if cumpl is None else f"{cumpl * 100:.1f}%",
    )

st.caption(
    "Por defecto estás viendo el **mes en curso**. Puedes ajustar fechas y filtros en el panel izquierdo."
)
st.divider()

# =========================================================
# TABS
# =========================================================
tab0, tab2, tab3, tabU, tabSol, tab4 = st.tabs(
    [
        "🏠 Estado General",
        "🛑 Detenciones",
        "✅ Disponibilidad (Faena)",
        "📈 Utilización",
        "🔧 Solicitudes de Mantención",
        "📁 Datos",
    ]
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
    col_status = find_first_col(estado, ["Status", "STATUS", "Estatus"])
    col_ubic = find_first_col(estado, ["Ubicación", "Ubicacion", "UBICACIÓN", "UBICACION", "Terminal"])

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
        cD.metric("Disponibilidad (hoy)", "—" if disp_hoy is None else f"{disp_hoy * 100:.1f}%")

        pie_df = pd.DataFrame(
            {"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]}
        )
        fig_pie = px.pie(
            pie_df,
            names="Estado",
            values="Cantidad",
            title="Flota hoy (Operativos vs No operativos)",
            color="Estado",
            color_discrete_map={"Operativos": "green", "No operativos": "red"},
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="tab0_pie_flotahoy")

        st.markdown(
            "### Listado de tractos (hoy) — **verde = operativo** / **rojo = no operativo**"
        )

        cols_show = [col_tracto, col_status]
        if col_ubic and col_ubic in dfE.columns:
            cols_show.insert(1, col_ubic)

        show = dfE[cols_show + ["_operativo"]].copy()

        def _style_row(row):
            if row.get("_operativo", False):
                return ["background-color: #e8f5e9; color: #1b5e20; font-weight: 600;"] * len(row)
            return ["background-color: #ffebee; color: #b71c1c; font-weight: 600;"] * len(row)

        st.dataframe(show.style.apply(_style_row, axis=1), use_container_width=True, height=420)

        st.divider()
        st.subheader("📍 Tractos disponibles por Terminal (hoy)")

        if col_ubic and col_ubic in dfE.columns:
            df_term = dfE.copy()
            df_term["_terminal"] = (
                df_term[col_ubic].astype(str).str.strip().replace({"": "SIN TERMINAL"})
            )
            df_term["_tracto"] = df_term[col_tracto].astype(str).str.strip()

            resumen = []
            for term, g in df_term.groupby("_terminal"):
                ops = sorted(g[g["_operativo"]]["_tracto"].unique().tolist())
                noops = sorted(g[~g["_operativo"]]["_tracto"].unique().tolist())
                resumen.append(
                    {
                        "Terminal": term,
                        "Operativos (tractos)": ", ".join(ops) if ops else "—",
                        "Fuera de servicio (tractos)": ", ".join(noops) if noops else "—",
                        "Operativos": len(ops),
                        "Fuera servicio": len(noops),
                        "Total": len(set(ops + noops)),
                    }
                )
            df_res = pd.DataFrame(resumen).sort_values(["Terminal"])
            st.dataframe(df_res, use_container_width=True, height=280)

            with st.expander("🛈 Cómo se interpreta este bloque"):
                st.markdown(
                    """
- **Operativos (verde)**: estado contiene "EN SERVICIO / OPERATIVO / DISPONIBLE / OK".
- **Fuera de servicio (rojo)**: estado contiene "FUERA DE SERVICIO / DETENIDO / MTTO / FALLA / BAJA".
- Esta sección refleja el **estado real actual** desde **Estado_Flota**.
                    """
                )
        else:
            st.info(
                "No encontré una columna de Terminal/Ubicación en Estado_Flota. Si existe, nómbrala como 'Ubicación' o 'Terminal'."
            )

        st.divider()
        st.subheader("🧾 Últimas 3 faenas por Terminal (KPIs reales + detenciones asociadas)")

        if faena_f.empty:
            st.info("No hay registros de Faena con los filtros actuales.")
        else:
            dfF = faena_f.copy()

            col_ini = find_first_col(dfF, ["Inicio OP"])
            col_fin = find_first_col(dfF, ["Termino OP", "Termino Op", "Término OP"])
            col_term = find_first_col(dfF, ["Terminal"])
            col_buq = find_first_col(dfF, ["Buque"])

            col_disp = find_first_col(
                dfF,
                [
                    "Disponibilidad_Tecnica",
                    "Disponibilidad_Tecnica_%",
                    "Disponibilidad técnica",
                    "Disponibilidad tecnica",
                    "Disponibilidad tecnica %",
                    "Disponibilidad técnica (%)",
                ],
            )
            col_cump = find_first_col(
                dfF, ["Cumplimiento", "Cumplimiento (U)", "Cumplimiento %", "Cumplimiento(U)"]
            )
            col_util = find_first_col(
                dfF,
                [
                    "Utilizacion",
                    "Utilización",
                    "Utilizacion_demandada_%",
                    "Utilización_demandada_%",
                    "Utilizacion_Oferta_%",
                    "Utilización_Oferta_%",
                    "Utilizacion_Capacidad_%",
                    "Utilización_Capacidad_%",
                    "Utilización Esperada",
                    "Utilizacion Esperada",
                ],
            )

            col_hop2 = find_first_col(
                dfF,
                [
                    "Horas Operación",
                    "Horas de operación",
                    "Horas Operación ",
                    "Horas operacion",
                    "Horas de operacion",
                ],
            )
            col_ind2 = find_first_col(
                dfF,
                [
                    "Indisponibilidad [HH]",
                    "Indisponibilidad",
                    "Indisponibilidad [H]",
                    "Indisponibilidad[HH]",
                    "Indisponibilidad horas",
                ],
            )
            col_tgt2 = find_first_col(dfF, ["Target Operación", "Target Operacion", "Target", "Target operaciones"])
            col_op2 = find_first_col(dfF, ["Tractos OP", "Tractos Op", "Tracto OP", "Tractos operación"])

            if not col_ini or not col_term:
                st.info("Para este bloque necesito al menos 'Inicio OP' y 'Terminal' en Faena.")
            else:
                dfF["_inicio"] = pd.to_datetime(dfF[col_ini], errors="coerce")
                dfF = dfF[dfF["_inicio"].notna()].copy()
                dfF["_terminal"] = dfF[col_term].astype(str).str.strip().str.upper()

                terms = sorted(dfF["_terminal"].dropna().unique().tolist())
                sel_t = st.selectbox(
                    "Terminal", options=["(Todos)"] + terms, index=0, key="tab0_sel_terminal_faena"
                )

                if sel_t != "(Todos)":
                    dfF_view = dfF[dfF["_terminal"] == sel_t].copy()
                else:
                    dfF_view = dfF.copy()

                rows = []
                for term, g in dfF_view.groupby("_terminal"):
                    g2 = g.sort_values("_inicio", ascending=False).head(3).copy()
                    for _, r in g2.iterrows():
                        ini, fin = _find_faena_window(r, col_ini, col_fin)
                        dcount, dhh = _detenciones_in_window(det, term, ini, fin)
                        disp, cump, util = _get_faena_kpis_row(
                            r, col_disp, col_cump, col_util, col_hop2, col_ind2, col_tgt2, col_op2,
                        )
                        rows.append(
                            {
                                "Terminal": term,
                                "Inicio OP": ini,
                                "Buque": (r[col_buq] if col_buq and col_buq in dfF.columns else ""),
                                "Disponibilidad_Técnica (T)": disp,
                                "Cumplimiento (U)": cump,
                                "Utilización (V)": util,
                                "Detenciones (n)": dcount,
                                "Detenciones (HH)": dhh,
                            }
                        )

                df_last = pd.DataFrame(rows)
                if df_last.empty:
                    st.info("No hay suficientes faenas para mostrar (revisa filtros/fechas).")
                else:
                    df_last["Inicio OP"] = pd.to_datetime(df_last["Inicio OP"], errors="coerce").dt.strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    for c in ["Disponibilidad_Técnica (T)", "Cumplimiento (U)", "Utilización (V)"]:
                        df_last[c] = pd.to_numeric(df_last[c], errors="coerce")

                    st.dataframe(df_last, use_container_width=True, height=320)

                    gk = df_last.copy()
                    for col_kpi in ["Disponibilidad_Técnica (T)", "Cumplimiento (U)", "Utilización (V)"]:
                        gk[col_kpi] = pd.to_numeric(gk[col_kpi], errors="coerce")
                    gg = (
                        gk.groupby("Terminal")[
                            ["Disponibilidad_Técnica (T)", "Cumplimiento (U)", "Utilización (V)"]
                        ]
                        .mean()
                        .reset_index()
                    )
                    melt = gg.melt(id_vars=["Terminal"], var_name="KPI", value_name="%")

                    st.plotly_chart(
                        px.bar(
                            melt, x="Terminal", y="%", color="KPI", barmode="group",
                            title="Promedio KPIs (últimas 3 faenas por Terminal)",
                        ),
                        use_container_width=True,
                        key="tab0_bar_last3_kpis",
                    )

                with st.expander("🛈 Cómo se calcula"):
                    st.markdown(
                        """
**Disponibilidad Técnica (T)**: columna real o HH Operación / (HH Operación + Indisponibilidad).  
**Cumplimiento (U)**: columna real o Tractos OP / Target.  
**Utilización (V)**: columna real tal como está definida en tu tabla.  
**Detenciones**: filtradas por Terminal y ventana Inicio OP → Término OP (fallback +24h).
                        """
                    )

# =========================================================
# TAB 2: DETENCIONES
# =========================================================
with tab2:
    st.subheader("🛑 Detenciones — análisis")

    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        df_dm = det_f.copy()
        df_dm["DMDEDO"] = df_dm["Tipo"].apply(map_dmde_do)

        col_left, col_right = st.columns(2)

        count_dm = (
            df_dm.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        )
        col_left.plotly_chart(
            px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM / DE / DO"),
            use_container_width=True,
            key="tab2_bar_dm_count",
        )

        if "Horas de reparación" in df_dm.columns:
            hh_dm = (
                df_dm.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values(
                    "Horas de reparación", ascending=False
                )
            )
            col_right.plotly_chart(
                px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM / DE / DO"),
                use_container_width=True,
                key="tab2_bar_dm_hh",
            )

    st.divider()
    st.subheader("📊 Análisis Jackknife de detenciones")
    if det_f.empty:
        st.info("No hay detenciones filtradas para análisis Jackknife.")
    else:
        cat_col = find_first_col(det_f, ["Familia Equipo", "Clasificación", "Tipo"])
        if cat_col:
            dfp = det_f.copy()
            dfp_cat = (
                dfp.groupby(cat_col).size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
            )
            if not dfp_cat.empty:
                total = dfp_cat["Cantidad"].sum()
                dfp_cat["Cumulativo"] = dfp_cat["Cantidad"].cumsum() / total * 100.0
                dfp_cat["Zona"] = pd.cut(
                    dfp_cat["Cumulativo"],
                    bins=[0, 80, 95, 100],
                    labels=["A", "B", "C"],
                    right=True,
                    include_lowest=True,
                ).astype(str)
                dfp_top = dfp_cat.head(10)
                color_map = {"A": "#e41a1c", "B": "#ff7f00", "C": "#4daf4a"}
                dfp_top["Color"] = dfp_top["Zona"].map(color_map)
                fig_jack = go.Figure()
                fig_jack.add_trace(
                    go.Bar(x=dfp_top[cat_col], y=dfp_top["Cantidad"], marker_color=dfp_top["Color"], name="Nº detenciones")
                )
                fig_jack.update_layout(
                    title=f"Jackknife de detenciones por {cat_col}",
                    xaxis_title=cat_col,
                    yaxis_title="Cantidad",
                )
                st.plotly_chart(fig_jack, use_container_width=True, key="tab2_jackknife")
                st.dataframe(
                    dfp_top[[cat_col, "Cantidad", "Zona"]].rename(
                        columns={cat_col: "Categoría", "Cantidad": "Nº detenciones"}
                    ),
                    use_container_width=True,
                    height=250,
                )
                with st.expander("🛈 Interpretación de zonas Jackknife"):
                    st.markdown(
                        """
**Zona A (≤ 80% acumulado)**: mayor concentración de detenciones. Foco principal de mejora.  
**Zona B (80%–95% acumulado)**: impacto medio. Mejoras adicionales moderadas.  
**Zona C (≥ 95% acumulado)**: baja frecuencia, bajo retorno de inversión.
                        """
                    )
        else:
            st.info("No se encontró columna apropiada para análisis Jackknife.")

    st.divider()
    st.subheader("Tabla detenciones filtradas")
    st.dataframe(det_f, use_container_width=True, height=520)

# =========================================================
# TAB 3: DISPONIBILIDAD (FAENA)
# =========================================================
with tab3:
    st.subheader("✅ Disponibilidad por Faena (histórico)")

    if faena_f.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        col_ini = find_first_col(df, ["Inicio OP"])
        col_term = find_first_col(df, ["Terminal"])
        col_disp = find_first_col(
            df,
            [
                "Disponibilidad_Tecnica",
                "Disponibilidad_Tecnica_%",
                "Disponibilidad técnica",
                "Disponibilidad tecnica",
                "Disponibilidad tecnica %",
                "Disponibilidad técnica (%)",
            ],
        )

        col_hop = find_first_col(df, ["Horas Operación", "Horas de operación", "Horas Operación ", "Horas operacion"])
        col_indisp = find_first_col(df, ["Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad [H]", "Indisponibilidad[HH]"])

        if col_disp and df[col_disp].notna().any():
            df["Disponibilidad_Tecnica_%"] = percent_to_0_100(df[col_disp])
        elif col_hop and col_indisp:
            hop = pd.to_numeric(df[col_hop], errors="coerce")
            ind = pd.to_numeric(df[col_indisp], errors="coerce")
            denom = hop + ind
            df["Disponibilidad_Tecnica_%"] = (hop / denom.replace({0: pd.NA})) * 100

        with st.expander("🛈 Qué estoy mostrando aquí"):
            st.write(
                "Se muestra **Disponibilidad Técnica**. Preferimos el valor real de tu tabla (columna T). "
                "Si no existe, se calcula como: **Horas Operación / (Horas Operación + Indisponibilidad[HH])**."
            )

        if col_ini and df[col_ini].notna().any() and "Disponibilidad_Tecnica_%" in df.columns:
            df["Fecha"] = pd.to_datetime(df[col_ini], errors="coerce").dt.date
            g = df.groupby("Fecha")["Disponibilidad_Tecnica_%"].mean().reset_index()
            st.plotly_chart(
                px.line(g, x="Fecha", y="Disponibilidad_Tecnica_%", markers=True, title="Disponibilidad técnica promedio por día (%)"),
                use_container_width=True,
                key="tab3_line_disptec_dia",
            )

        if col_term and "Disponibilidad_Tecnica_%" in df.columns:
            gt = df.groupby(col_term)["Disponibilidad_Tecnica_%"].mean().reset_index()
            st.plotly_chart(
                px.bar(gt, x=col_term, y="Disponibilidad_Tecnica_%", title="Promedio Disponibilidad Técnica (%) por Terminal"),
                use_container_width=True,
                key="tab3_bar_disptec_terminal",
            )

        st.dataframe(df, use_container_width=True, height=520)

# =========================================================
# TAB U: UTILIZACIÓN
# =========================================================
with tabU:
    st.subheader("📈 Utilización y Cumplimiento (según tabla Faena)")

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        col_ini = find_first_col(dfu, ["Inicio OP"])
        col_term = find_first_col(dfu, ["Terminal"])
        col_buq = find_first_col(dfu, ["Buque"])

        col_cumpl = find_first_col(dfu, ["Cumplimiento", "Cumplimiento (U)", "Cumplimiento %", "Cumplimiento(U)"])
        col_util = find_first_col(
            dfu,
            ["Utilizacion", "Utilización", "Utilizacion_demandada_%", "Utilización_demandada_%",
             "Utilizacion_Oferta_%", "Utilización_Oferta_%", "Utilizacion_Capacidad_%",
             "Utilización_Capacidad_%", "Utilización Esperada", "Utilizacion Esperada"],
        )
        col_disp = find_first_col(
            dfu,
            ["Disponibilidad_Tecnica", "Disponibilidad_Tecnica_%", "Disponibilidad técnica",
             "Disponibilidad tecnica", "Disponibilidad tecnica %", "Disponibilidad técnica (%)"],
        )

        col_tgt = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target", "Target operaciones"])
        col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op", "Tracto OP", "Tractos operación"])

        if col_cumpl and col_cumpl in dfu.columns:
            dfu[col_cumpl] = percent_to_0_100(dfu[col_cumpl])
        else:
            if col_tgt and col_op:
                dfu["Cumplimiento_calc"] = (
                    pd.to_numeric(dfu[col_op], errors="coerce")
                    / pd.to_numeric(dfu[col_tgt], errors="coerce").replace({0: pd.NA})
                ) * 100.0
                col_cumpl = "Cumplimiento_calc"

        if col_util and col_util in dfu.columns:
            dfu[col_util] = percent_to_0_100(dfu[col_util])

        if col_disp and col_disp in dfu.columns:
            dfu["_DispTec_%"] = percent_to_0_100(dfu[col_disp])

        k1, k2, k3 = st.columns(3)
        m_c = _pct_mean(dfu, col_cumpl)
        m_u = _pct_mean(dfu, col_util) if col_util else None
        m_d = _pct_mean(dfu, "_DispTec_%") if "_DispTec_%" in dfu.columns else None

        k1.metric("Cumplimiento (U)", "—" if m_c is None else f"{m_c:.1f}%")
        k2.metric("Utilización (V)", "—" if m_u is None else f"{m_u:.1f}%")
        k3.metric("Disponibilidad Técnica (T)", "—" if m_d is None else f"{m_d:.1f}%")

        st.divider()

        if col_ini and dfu[col_ini].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu[col_ini], errors="coerce").dt.date
            series_cols = [c for c in [col_cumpl, col_util] if c]
            if series_cols:
                g = dfu.groupby("Fecha")[series_cols].mean().reset_index()
                melt = g.melt(id_vars=["Fecha"], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.line(melt, x="Fecha", y="%", color="KPI", markers=True, title="Evolución diaria (promedio)"),
                    use_container_width=True,
                    key="tabU_line_daily",
                )

        if col_term:
            kpis = [c for c in [col_cumpl, col_util] if c]
            if "_DispTec_%" in dfu.columns:
                kpis.append("_DispTec_%")
            if kpis:
                gt = dfu.groupby(col_term)[kpis].mean().reset_index()
                melt2 = gt.melt(id_vars=[col_term], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.bar(melt2, x=col_term, y="%", color="KPI", barmode="group", title="Promedio KPIs por Terminal"),
                    use_container_width=True,
                    key="tabU_bar_terminal_kpis",
                )

        st.divider()
        st.subheader("Tabla (gestión)")
        cols_show = []
        for c in [col_ini, col_term, col_buq, col_disp, col_cumpl, col_util, col_tgt, col_op]:
            if c and c in dfu.columns and c not in cols_show:
                cols_show.append(c)
        if "_DispTec_%" in dfu.columns and "_DispTec_%" not in cols_show:
            cols_show.append("_DispTec_%")
        st.dataframe(dfu[cols_show], use_container_width=True, height=520)


# =========================================================
# TAB SOLICITUDES DE MANTENCIÓN
# =========================================================
with tabSol:
    st.subheader("🔧 Solicitudes de Mantención")

    # ── Cargar datos ─────────────────────────────────────
    try:
        sol = load_solicitudes()
    except Exception as e:
        st.error(
            "No pude cargar el archivo de Solicitudes de Mantención. "
            "Asegúrate de compartirlo como 'Cualquier persona con el enlace → Lector'."
        )
        st.code(str(e))
        sol = pd.DataFrame()

    if sol.empty:
        st.info("No hay solicitudes disponibles o el archivo está vacío.")
    else:
        # ── Detectar columnas clave ───────────────────────
        col_id = find_first_col(sol, ["N°", "N", "Nro", "Nro.", "ID", "Número", "Numero", "#", "N° Solicitud"])
        col_fecha = find_first_col(sol, ["Fecha", "Fecha Solicitud", "Fecha solicitud", "Fecha de solicitud", "Fecha Creación", "Fecha creacion"])
        col_equipo = find_first_col(sol, ["Equipo", "Tracto", "#Tracto", "Máquina", "Maquina", "Activo"])
        col_desc = find_first_col(sol, ["Descripción", "Descripcion", "Detalle", "Trabajo", "Falla", "Problema", "Observación"])
        col_solicitante = find_first_col(sol, ["Solicitante", "Quien solicita", "Solicitado por", "Reporta", "Operador"])
        col_responsable = find_first_col(sol, ["Responsable", "Técnico", "Tecnico", "Asignado", "Asignado a", "Mecánico"])
        col_terminal = find_first_col(sol, ["Terminal", "Ubicación", "Ubicacion", "Faena", "Área"])
        col_fecha_cierre = find_first_col(sol, ["Fecha Cierre", "Fecha cierre", "Fecha de cierre", "Cerrado", "Fecha Fin"])
        col_obs = find_first_col(sol, ["Observaciones", "Comentarios", "Notas", "Nota", "Comentario"])

        # ── Sidebar de filtros para Solicitudes ───────────
        st.markdown("#### 🎛️ Filtros rápidos")
        fcol1, fcol2, fcol3 = st.columns(3)

        estados_disponibles = [e for e in _ESTADO_ORDER if e in sol["Estado"].unique()]
        sel_estados_sol = fcol1.multiselect(
            "Estado", options=estados_disponibles, default=estados_disponibles, key="sol_est"
        )

        prioridades_disponibles = [p for p in _PRIORIDAD_ORDER if p in sol["Prioridad"].unique()]
        sel_prio_sol = fcol2.multiselect(
            "Prioridad", options=prioridades_disponibles, default=prioridades_disponibles, key="sol_prio"
        )

        equipos_sol = []
        if col_equipo:
            equipos_sol = sorted([e for e in sol[col_equipo].dropna().unique() if str(e).strip()])
        sel_equipo_sol = fcol3.multiselect("Equipo / Tracto", options=equipos_sol, key="sol_equipo")

        # Filtro de fecha
        if col_fecha and sol[col_fecha].notna().any():
            fdc1, fdc2 = st.columns(2)
            sol_min_date = sol[col_fecha].min().date()
            sol_max_date = sol[col_fecha].max().date()
            sol_desde = fdc1.date_input("Desde (solicitud)", value=sol_min_date, key="sol_desde")
            sol_hasta = fdc2.date_input("Hasta (solicitud)", value=sol_max_date, key="sol_hasta")
        else:
            sol_desde = sol_hasta = None

        # Aplicar filtros
        df_sol = sol.copy()
        if sel_estados_sol:
            df_sol = df_sol[df_sol["Estado"].isin(sel_estados_sol)]
        if sel_prio_sol:
            df_sol = df_sol[df_sol["Prioridad"].isin(sel_prio_sol)]
        if sel_equipo_sol and col_equipo:
            df_sol = df_sol[df_sol[col_equipo].isin(sel_equipo_sol)]
        if col_fecha and sol_desde and sol_hasta:
            df_sol = df_sol[
                (df_sol[col_fecha].dt.date >= sol_desde) & (df_sol[col_fecha].dt.date <= sol_hasta)
            ]

        st.divider()

        # ── KPIs por estado ───────────────────────────────
        st.markdown("#### 📊 Resumen de solicitudes")
        cnt_total = len(df_sol)
        cnt_abierta = len(df_sol[df_sol["Estado"] == "Abierta"])
        cnt_plan = len(df_sol[df_sol["Estado"] == "En Planificación"])
        cnt_cerrada = len(df_sol[df_sol["Estado"] == "Cerrada"])
        cnt_rechazada = len(df_sol[df_sol["Estado"] == "Rechazada"])

        k0, k1, k2, k3, k4 = st.columns(5)
        k0.metric("Total", cnt_total)
        k1.metric("🟡 Abiertas", cnt_abierta)
        k2.metric("🔵 En Planificación", cnt_plan)
        k3.metric("🟢 Cerradas", cnt_cerrada)
        k4.metric("🔴 Rechazadas", cnt_rechazada)

        # Tasa de cierre
        if cnt_total > 0:
            tasa_cierre = cnt_cerrada / cnt_total * 100
            tasa_rechazo = cnt_rechazada / cnt_total * 100
            ta, tb = st.columns(2)
            ta.metric("Tasa de cierre", f"{tasa_cierre:.1f}%")
            tb.metric("Tasa de rechazo", f"{tasa_rechazo:.1f}%")

        st.divider()

        # ── Gráficos ─────────────────────────────────────
        gcol1, gcol2 = st.columns(2)

        # Donut por estado
        with gcol1:
            cnt_estado = df_sol["Estado"].value_counts().reset_index()
            cnt_estado.columns = ["Estado", "Cantidad"]
            colors_donut = [_ESTADO_COLORS.get(e, "#9ca3af") for e in cnt_estado["Estado"]]
            fig_donut = go.Figure(
                go.Pie(
                    labels=cnt_estado["Estado"],
                    values=cnt_estado["Cantidad"],
                    hole=0.55,
                    marker_colors=colors_donut,
                    textinfo="label+percent",
                    hovertemplate="<b>%{label}</b><br>%{value} solicitudes (%{percent})<extra></extra>",
                )
            )
            fig_donut.update_layout(
                title="Distribución por Estado",
                showlegend=False,
                height=320,
                margin=dict(t=40, b=10, l=10, r=10),
            )
            st.plotly_chart(fig_donut, use_container_width=True, key="sol_donut_estado")

        # Barras por prioridad
        with gcol2:
            cnt_prio = df_sol["Prioridad"].value_counts().reindex(_PRIORIDAD_ORDER, fill_value=0).reset_index()
            cnt_prio.columns = ["Prioridad", "Cantidad"]
            cnt_prio = cnt_prio[cnt_prio["Cantidad"] > 0]
            colors_prio = [_PRIORIDAD_COLORS.get(p, "#9ca3af") for p in cnt_prio["Prioridad"]]
            fig_prio = go.Figure(
                go.Bar(
                    x=cnt_prio["Prioridad"],
                    y=cnt_prio["Cantidad"],
                    marker_color=colors_prio,
                    text=cnt_prio["Cantidad"],
                    textposition="outside",
                    hovertemplate="<b>%{x}</b>: %{y}<extra></extra>",
                )
            )
            fig_prio.update_layout(
                title="Solicitudes por Prioridad",
                yaxis_title="Cantidad",
                height=320,
                margin=dict(t=40, b=10, l=10, r=10),
            )
            st.plotly_chart(fig_prio, use_container_width=True, key="sol_bar_prioridad")

        # ── Fila: Equipos + Ranking Solicitantes ──────────
        eq_col1, eq_col2 = st.columns(2)

        # Barras por equipo (Top 10)
        with eq_col1:
            if col_equipo and df_sol[col_equipo].notna().any():
                cnt_eq = (
                    df_sol[df_sol[col_equipo].astype(str).str.strip() != ""]
                    .groupby(col_equipo).size().reset_index(name="Solicitudes")
                    .sort_values("Solicitudes", ascending=False).head(10)
                )
                fig_eq = px.bar(
                    cnt_eq, x=col_equipo, y="Solicitudes",
                    title="🚜 Top 10 Equipos con más solicitudes",
                    color="Solicitudes",
                    color_continuous_scale="Oranges",
                )
                fig_eq.update_layout(height=360, margin=dict(t=40, b=10, l=10, r=10), coloraxis_showscale=False)
                st.plotly_chart(fig_eq, use_container_width=True, key="sol_bar_equipos")
            else:
                st.info("No hay columna de Equipo disponible.")

        # Ranking de solicitantes (horizontal, con medalla)
        with eq_col2:
            if col_solicitante and df_sol[col_solicitante].notna().any():
                cnt_sol_rank = (
                    df_sol[df_sol[col_solicitante].astype(str).str.strip() != ""]
                    .groupby(col_solicitante).size().reset_index(name="Solicitudes")
                    .sort_values("Solicitudes", ascending=False).head(10)
                )
                # Colores degradados: el 1° dorado, 2° plata, 3° bronce, resto azul
                rank_colors = []
                for i in range(len(cnt_sol_rank)):
                    if i == 0:
                        rank_colors.append("#f59e0b")   # 🥇 dorado
                    elif i == 1:
                        rank_colors.append("#94a3b8")   # 🥈 plata
                    elif i == 2:
                        rank_colors.append("#b45309")   # 🥉 bronce
                    else:
                        rank_colors.append("#3b82f6")   # resto azul

                # Invertir para que el 1° quede arriba en barras horizontales
                cnt_sol_rank_inv = cnt_sol_rank.iloc[::-1].copy()
                rank_colors_inv = rank_colors[::-1]

                fig_rank = go.Figure(
                    go.Bar(
                        x=cnt_sol_rank_inv["Solicitudes"],
                        y=cnt_sol_rank_inv[col_solicitante],
                        orientation="h",
                        marker_color=rank_colors_inv,
                        text=cnt_sol_rank_inv["Solicitudes"],
                        textposition="outside",
                        hovertemplate="<b>%{y}</b><br>%{x} solicitudes<extra></extra>",
                    )
                )
                fig_rank.update_layout(
                    title="🏆 Ranking de Solicitantes",
                    xaxis_title="Nº de solicitudes",
                    yaxis_title="",
                    height=360,
                    margin=dict(t=40, b=10, l=10, r=60),
                    yaxis=dict(tickfont=dict(size=12)),
                )
                st.plotly_chart(fig_rank, use_container_width=True, key="sol_rank_solicitantes")

                # Tabla resumen compacta con medallas
                st.markdown("**Detalle del ranking**")
                medallas = {0: "🥇", 1: "🥈", 2: "🥉"}
                ranking_rows = []
                for i, (_, row) in enumerate(cnt_sol_rank.iterrows()):
                    pct = row["Solicitudes"] / cnt_sol_rank["Solicitudes"].sum() * 100
                    # desglose por estado para este solicitante
                    sub = df_sol[df_sol[col_solicitante] == row[col_solicitante]]
                    ab = len(sub[sub["Estado"] == "Abierta"])
                    pl = len(sub[sub["Estado"] == "En Planificación"])
                    ce = len(sub[sub["Estado"] == "Cerrada"])
                    ranking_rows.append({
                        "Pos.": f"{medallas.get(i, str(i+1)+'°')}",
                        "Solicitante": row[col_solicitante],
                        "Total": int(row["Solicitudes"]),
                        "% del total": f"{pct:.1f}%",
                        "🟡 Abiertas": ab,
                        "🔵 Planif.": pl,
                        "🟢 Cerradas": ce,
                    })
                df_rank_display = pd.DataFrame(ranking_rows)
                st.dataframe(df_rank_display, use_container_width=True, height=320, hide_index=True)
            else:
                st.info("No hay columna de Solicitante disponible.")

        # Evolución temporal (si hay fecha)
        if col_fecha and df_sol[col_fecha].notna().any():
            df_ts = df_sol.copy()
            df_ts["_mes"] = df_ts[col_fecha].dt.to_period("M").astype(str)
            ts_data = (
                df_ts.groupby(["_mes", "Estado"]).size().reset_index(name="Cantidad")
            )
            if not ts_data.empty:
                fig_ts = px.bar(
                    ts_data, x="_mes", y="Cantidad", color="Estado",
                    color_discrete_map=_ESTADO_COLORS,
                    title="Solicitudes por mes y estado",
                    labels={"_mes": "Mes"},
                    barmode="stack",
                )
                fig_ts.update_layout(height=320, margin=dict(t=40, b=10, l=10, r=10))
                st.plotly_chart(fig_ts, use_container_width=True, key="sol_ts_mensual")

        st.divider()

        # ── Vistas por estado (pestañas internas) ─────────
        st.markdown("#### 📋 Detalle por estado")
        sub_abierta, sub_plan, sub_cerrada, sub_rechazada, sub_all = st.tabs([
            f"🟡 Abiertas ({cnt_abierta})",
            f"🔵 En Planificación ({cnt_plan})",
            f"🟢 Cerradas ({cnt_cerrada})",
            f"🔴 Rechazadas ({cnt_rechazada})",
            "📚 Historial completo",
        ])

        def _render_solicitudes_tabla(df_sub: pd.DataFrame, estado_label: str, key_suffix: str):
            """Renderiza la tabla de solicitudes con columnas relevantes y estilos."""
            if df_sub.empty:
                st.info(f"No hay solicitudes en estado '{estado_label}' con los filtros actuales.")
                return

            # Seleccionar columnas a mostrar
            cols_display = []
            for c in [col_id, col_fecha, col_equipo, col_terminal, col_prioridad_col, "Prioridad", col_desc,
                      col_solicitante, col_responsable, col_fecha_cierre, col_obs, "Estado"]:
                if c and c in df_sub.columns and c not in cols_display:
                    cols_display.append(c)

            if not cols_display:
                cols_display = df_sub.columns.tolist()

            df_view = df_sub[cols_display].copy()

            # Agregar badge de estado al inicio
            if "Estado" in df_view.columns:
                df_view.insert(0, "🏷️", df_view["Estado"].map(lambda e: _badge_estado(e)))

            # Formatear fechas
            for dc in [col_fecha, col_fecha_cierre]:
                if dc and dc in df_view.columns:
                    try:
                        df_view[dc] = pd.to_datetime(df_view[dc], errors="coerce").dt.strftime("%d/%m/%Y")
                    except Exception:
                        pass

            # Aplicar estilos por fila
            def _row_style(row):
                return _style_solicitudes(row)

            styled = df_view.style.apply(_row_style, axis=1)

            st.dataframe(styled, use_container_width=True, height=min(400, max(200, len(df_view) * 38 + 60)))

            # Botón de descarga
            csv = df_sub.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ Exportar {estado_label} (CSV)",
                data=csv,
                file_name=f"solicitudes_{key_suffix}.csv",
                mime="text/csv",
                key=f"dl_{key_suffix}",
            )

        # Detectar columna prioridad original (para incluir si difiere de "Prioridad" normalizada)
        col_prioridad_col = find_first_col(sol, ["Prioridad", "Priority", "Urgencia", "Nivel"])

        with sub_abierta:
            df_ab = df_sol[df_sol["Estado"] == "Abierta"].copy()
            if not df_ab.empty and col_prioridad_col:
                df_ab = df_ab.sort_values("Prioridad", key=lambda s: s.map(
                    {p: i for i, p in enumerate(_PRIORIDAD_ORDER)}
                ).fillna(99))
            # Alerta visual si hay críticas abiertas
            criticas_abiertas = len(df_ab[df_ab["Prioridad"] == "Crítica"]) if not df_ab.empty else 0
            if criticas_abiertas > 0:
                st.warning(
                    f"⚠️ Hay **{criticas_abiertas}** solicitud(es) de prioridad **Crítica** abiertas que requieren atención inmediata.",
                    icon="🚨",
                )
            _render_solicitudes_tabla(df_ab, "Abiertas", "abiertas")

        with sub_plan:
            df_pl = df_sol[df_sol["Estado"] == "En Planificación"].copy()
            _render_solicitudes_tabla(df_pl, "En Planificación", "planificacion")

        with sub_cerrada:
            df_ce = df_sol[df_sol["Estado"] == "Cerrada"].copy()
            if not df_ce.empty and col_fecha and col_fecha in df_ce.columns:
                df_ce = df_ce.sort_values(col_fecha, ascending=False)
            _render_solicitudes_tabla(df_ce, "Cerradas", "cerradas")

        with sub_rechazada:
            df_re = df_sol[df_sol["Estado"] == "Rechazada"].copy()
            _render_solicitudes_tabla(df_re, "Rechazadas", "rechazadas")

        with sub_all:
            st.markdown("**Historial completo** — todas las solicitudes con filtros aplicados.")

            # Búsqueda libre por texto
            busqueda = st.text_input(
                "🔍 Buscar en descripción / equipo / solicitante",
                placeholder="Escribe para filtrar...",
                key="sol_busqueda",
            )

            df_hist = df_sol.copy()
            if busqueda.strip():
                mask = pd.Series(False, index=df_hist.index)
                for sc in [col_desc, col_equipo, col_solicitante, col_responsable, col_obs, col_id]:
                    if sc and sc in df_hist.columns:
                        mask |= df_hist[sc].astype(str).str.lower().str.contains(
                            busqueda.strip().lower(), na=False
                        )
                df_hist = df_hist[mask]

            st.caption(f"Mostrando **{len(df_hist)}** solicitudes.")
            _render_solicitudes_tabla(df_hist, "Historial completo", "historial")

        st.divider()
        with st.expander("🛈 Cómo funciona esta sección"):
            st.markdown(
                """
**Solicitudes de Mantención** se carga desde el Google Sheet configurado.

**Normalización de estados**: el sistema reconoce automáticamente variantes como "ABIERTA", "OPEN", "PENDIENTE" → **Abierta**;
"EN PLANIFICACION", "EN PROCESO" → **En Planificación**; "CERRADA", "COMPLETADA", "FINALIZADA" → **Cerrada**;
"RECHAZADA", "CANCELADA" → **Rechazada**.

**Prioridades**: se normalizan "CRITICA/URGENTE" → **Crítica**, "ALTA" → **Alta**, "MEDIA/NORMAL" → **Media**, "BAJA" → **Baja**.

**Alerta automática**: si hay solicitudes **Críticas abiertas**, aparece una advertencia visible al ingresar a la pestaña.

**Exportación**: cada sub-pestaña tiene su botón de descarga CSV.

> Si las columnas de tu hoja tienen nombres distintos, nómbralas de forma estándar
> (Estado, Prioridad, Equipo, Descripción, Fecha, Solicitante, Responsable, Fecha Cierre)
> o avísame para agregar el alias específico.
                """
            )

# =========================================================
# TAB 4: EXPORT
# =========================================================
with tab4:
    st.subheader("📁 Exportar datos filtrados")
    cA4, cB4 = st.columns(2)
    with cA4:
        csv_det = det_f.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Descargar Detenciones (CSV)",
            data=csv_det,
            file_name="detenciones_filtradas.csv",
            mime="text/csv",
        )
    with cB4:
        csv_faena = faena_f.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Descargar Faena (CSV)",
            data=csv_faena,
            file_name="faena_filtrada.csv",
            mime="text/csv",
        )

st.caption(
    "Fuente: Google Sheets exportado a XLSX (Faena, Detenciones, Estado_Flota, Solicitudes). Dashboard Streamlit."
)
