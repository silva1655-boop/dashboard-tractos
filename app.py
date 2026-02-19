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

DEFAULT_REFRESH_SEC = int(os.getenv("REFRESH_SEC", "120"))

# =========================================================
# HELPERS
# =========================================================
def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza los nombres de columnas eliminando espacios duplicados
    y recortando espacios al inicio o final. No modifica acentos ni
    símbolos; para una búsqueda más flexible usa ``find_first_col``.
    """
    df = df.copy()
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def _to_datetime(df: pd.DataFrame, cols):
    """
    Convierte en fecha/hora las columnas indicadas, ignorando
    errores. Devuelve una copia del DataFrame.
    """
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _safe_upper(s):
    """
    Devuelve el string en mayúsculas y sin espacios extra; si el valor
    es NaN o None retorna None. Se usa para normalizar texto en
    Detenciones.
    """
    if pd.isna(s):
        return None
    return str(s).strip().upper()


def download_google_xlsx(url: str) -> bytes:
    """
    Descarga un archivo XLSX desde la URL de Google Sheets. Si Google
    responde HTML (por ejemplo, porque no has permitido compartir
    públicamente el Sheet) se lanza una excepción con un mensaje útil.
    """
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
    """
    Devuelve el nombre de la primera columna encontrada en ``df`` que
    coincida con alguno de los ``candidates``. La búsqueda es robusta a
    mayúsculas/minúsculas, acentos, espacios, guiones, underscores,
    paréntesis y porcentajes. Por ejemplo, tanto ``"Termino OP"`` como
    ``"término op"`` o ``"Termino_Op"`` se considerarán equivalentes.

    Si ``df`` está vacío o ``None`` se devuelve ``None``.
    """
    if df is None or df.empty:
        return None

    # Coincidencia exacta primero para preservar nombres originales
    for c in candidates:
        if c in df.columns:
            return c

    def _norm_key(s: str) -> str:
        """
        Normaliza un nombre quitando acentos y caracteres de separación
        (espacios, guiones, underscores, paréntesis, porcentajes). El
        resultado se convierte a minúsculas.
        """
        if s is None:
            return ""
        s = str(s).strip()
        # elimina acentos
        s = "".join(
            c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
        )
        s = s.lower()
        for ch in [" ", "_", "-", "%", "(", ")", ":"]:
            s = s.replace(ch, "")
        return s

    # Construir mapa normalizado → columna original
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = _norm_key(col)
        if key:
            mapping[key] = col

    # Buscar en los candidatos
    for c in candidates:
        key = _norm_key(c)
        if key in mapping:
            return mapping[key]
    return None


def _make_unique_columns(cols):
    """
    Hace únicos los nombres de columnas, añadiendo sufijos ``__n`` si
    aparecen duplicados o nombres vacíos. Esto se usa al leer la hoja
    Estado_Flota cuando los encabezados se extraen manualmente.
    """
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
    """
    Determina si el string parece ser un código de tracto válido. Excluye
    totalizadores y categorías (por ejemplo, "TOTAL", "EN SERVICIO"). Se
    acepta una letra seguida de hasta cuatro dígitos, con o sin espacio.
    """
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
    """
    Convierte porcentajes leídos desde Excel a escala 0..100.

    - Si vienen como 0.8, 1.0 => los pasa a 80, 100
    - Si ya vienen como 80, 100 => los deja igual
    - Si vienen como texto "80%" => los convierte a 80
    - Si vienen como 120 => se considera 120 (p.ej. 120%)
    """
    if series is None:
        return series
    s = series.copy()
    if s.dtype == "object":
        s = s.astype(str).str.replace("%", "", regex=False)
        # en países hispanos se usa la coma como separador decimal
        s = s.str.replace(",", ".", regex=False)
    s = pd.to_numeric(s, errors="coerce")
    # Detectar rango máximo para decidir conversión
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
    """
    Mapea los tipos de detención a las categorías DM/DE/DO. Si no es
    posible determinarlo retorna "SIN CLASIFICAR".
    """
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
    """
    Determina si un estado de flota indica que el equipo está operativo.
    Palabras clave de operativo: EN SERVICIO, OPERATIVO, DISPONIBLE, OK.
    Palabras clave de no operativo: FUERA DE SERVICIO, DETEN, DETENIDO,
    MTTO, MANT, FALLA, BAJA.
    """
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
    """
    Formatea un número con separador de miles (punto) y coma decimal.
    Si el valor es None o NaN devuelve un guión largo.
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _as_upper_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _pct_mean(df: pd.DataFrame, col: str | None):
    """
    Calcula el promedio de una columna que representa porcentajes. Se usa
    ``percent_to_0_100`` para convertir los valores antes de promediar.
    Devuelve ``None`` si la columna no existe o no hay datos válidos.
    """
    if not col or col not in df.columns or df.empty:
        return None
    v = percent_to_0_100(pd.to_numeric(df[col], errors="coerce")).mean()
    return None if pd.isna(v) else float(v)


def _get_faena_kpis_row(row: pd.Series, col_disp, col_cump, col_util, col_hop, col_indisp, col_tgt, col_op):
    """
    Devuelve una tupla ``(disp, cump, util)`` con los KPIs de una fila
    individual de Faena. Se prefieren las columnas reales (columna T/U/V)
    y en su defecto se calcula con fórmulas:

    - Disponibilidad Técnica (T) = Horas Operación / (Horas Operación + Indisponibilidad)
    - Cumplimiento (U) = Tractos OP / Target Operación
    - Utilización (V) = valor de la columna de utilización
    """
    disp = None
    cump = None
    util = None

    # Disponibilidad técnica
    if col_disp and col_disp in row.index and pd.notna(row[col_disp]):
        disp = percent_to_0_100(pd.Series([row[col_disp]])).iloc[0]
    elif col_hop and col_indisp and col_hop in row.index and col_indisp in row.index:
        hop = pd.to_numeric(pd.Series([row[col_hop]]), errors="coerce").iloc[0]
        ind = pd.to_numeric(pd.Series([row[col_indisp]]), errors="coerce").iloc[0]
        if pd.notna(hop) and pd.notna(ind) and (hop + ind) > 0:
            disp = (hop / (hop + ind)) * 100.0

    # Cumplimiento
    if col_cump and col_cump in row.index and pd.notna(row[col_cump]):
        cump = percent_to_0_100(pd.Series([row[col_cump]])).iloc[0]
    elif col_tgt and col_op and col_tgt in row.index and col_op in row.index:
        tgt = pd.to_numeric(pd.Series([row[col_tgt]]), errors="coerce").iloc[0]
        opv = pd.to_numeric(pd.Series([row[col_op]]), errors="coerce").iloc[0]
        if pd.notna(tgt) and tgt > 0 and pd.notna(opv):
            cump = (opv / tgt) * 100.0

    # Utilización
    if col_util and col_util in row.index and pd.notna(row[col_util]):
        util = percent_to_0_100(pd.Series([row[col_util]])).iloc[0]

    return disp, cump, util


def _find_faena_window(row: pd.Series, col_ini: str | None, col_fin: str | None):
    """
    Devuelve una tupla (inicio, fin) de la ventana temporal de la faena. Si
    no existe la columna de término se asume un rango de 24 horas a partir
    del inicio.
    """
    ini = None
    fin = None
    if col_ini and col_ini in row.index:
        ini = pd.to_datetime(row[col_ini], errors="coerce")
    if col_fin and col_fin in row.index:
        fin = pd.to_datetime(row[col_fin], errors="coerce")
    if pd.isna(fin) and pd.notna(ini):
        fin = ini + timedelta(hours=24)  # fallback objetivo
    return ini, fin


def _detenciones_in_window(det: pd.DataFrame, terminal: str | None, ini, fin):
    """
    Cuenta las detenciones y suma sus horas de reparación dentro de la
    ventana ``[ini, fin]`` y opcionalmente filtra por terminal.
    """
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
# LOADERS
# =========================================================
@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_main() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Carga las hojas de Faena y Detenciones desde el archivo principal. Los
    encabezados se normalizan con ``_normalize_cols`` y se convierten
    algunas columnas a numéricas. También se derivan campos como el mes
    en detenciones.
    """
    content = download_google_xlsx(EXCEL_URL)
    faena = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_FAENA)
    det = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_DET)

    faena = _normalize_cols(faena)
    det = _normalize_cols(det)

    # Fechas
    faena = _to_datetime(faena, ["Inicio OP", "Termino OP", "Termino Op", "Término OP"])
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])

    # Convertir columnas numéricas típicas en Faena a valores numéricos
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

    # Detenciones numéricas
    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    # Normalizar texto en detenciones
    for c in [
        "Equipo", "Clasificación", "Familia Equipo", "Componente",
        "Modo de Falla", "Tipo", "Nave", "Buque", "Viaje", "Terminal"
    ]:
        if c in det.columns:
            det[c] = det[c].apply(_safe_upper)

    # Derivados en detenciones
    if "Inicio" in det.columns and det["Inicio"].notna().any():
        det["Mes"] = det["Inicio"].dt.to_period("M").astype(str)

    if "Clasificación" in det.columns:
        det["Clasificación"] = det["Clasificación"].fillna("SIN CLASIFICAR")

    return faena, det


@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_estado() -> pd.DataFrame:
    """
    Carga la hoja Estado_Flota y detecta automáticamente la fila de
    encabezado buscando una fila que contenga las palabras ``tracto`` y
    ``status``. Si no se encuentra se intenta leer de forma estándar. Se
    devuelve un DataFrame normalizado y con nombres de columnas únicos.
    """
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

    # Limpiar espacios en columnas de texto
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
# KPI TOP (MES EN CURSO POR DEFECTO)
# =========================================================
c1, c2, c3, c4, c5 = st.columns(5)

total_det = int(det_f.shape[0])
total_hh = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

# Preferir columnas reales: T/U/V
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
col_utilV = find_first_col(
    faena_f,
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

# fallback para disponibilidad técnica
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

# fallback para cumplimiento
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
# TABS (sin Resumen)
# =========================================================
tab0, tab2, tab3, tabU, tab4 = st.tabs(
    ["🏠 Estado General", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"]
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

        # sólo filas de tractos reales
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

        # =========================================================
        # NUEVO: Tractos por Terminal (operativos / fuera de servicio)
        # =========================================================
        st.divider()
        st.subheader("📍 Tractos disponibles por Terminal (hoy)")

        if col_ubic and col_ubic in dfE.columns:
            # Resumen de tractos por Terminal con listas de tractos
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
- **Operativos (verde)**: estado contiene “EN SERVICIO / OPERATIVO / DISPONIBLE / OK”.
- **Fuera de servicio (rojo)**: estado contiene “FUERA DE SERVICIO / DETENIDO / MTTO / FALLA / BAJA”.
- Esta sección refleja el **estado real actual** desde **Estado_Flota**.
                    """
                )

            # Tabla detallada de tractos por ubicación
            st.divider()
            st.subheader("🚩 Tractos por ubicación (detalle)")
            df_loc = dfE[[col_ubic, col_tracto, col_status]].copy()
            df_loc = df_loc.dropna(subset=[col_tracto])
            # Ordenar por ubicación y tracto
            df_loc = df_loc.sort_values([col_ubic, col_tracto])
            # Renombrar columnas para visualización
            df_loc_display = df_loc.rename(
                columns={col_ubic: "Ubicación", col_tracto: "Tracto", col_status: "Estado"}
            )
            st.dataframe(df_loc_display, use_container_width=True, height=300)
        else:
            st.info(
                "No encontré una columna de Terminal/Ubicación en Estado_Flota. Si existe, nómbrala como 'Ubicación' o 'Terminal'."
            )

        # =========================================================
        # NUEVO: Últimas 3 faenas por Terminal (T/U/V + detenciones)
        # =========================================================
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

                # selector terminal para hacerlo legible
                terms = sorted(dfF["_terminal"].dropna().unique().tolist())
                sel_t = st.selectbox(
                    "Terminal", options=["(Todos)"] + terms, index=0, key="tab0_sel_terminal_faena"
                )

                if sel_t != "(Todos)":
                    dfF_view = dfF[dfF["_terminal"] == sel_t].copy()
                else:
                    dfF_view = dfF.copy()

                # construir resumen: 3 últimas faenas por terminal
                rows = []
                for term, g in dfF_view.groupby("_terminal"):
                    g2 = g.sort_values("_inicio", ascending=False).head(3).copy()
                    for _, r in g2.iterrows():
                        ini, fin = _find_faena_window(r, col_ini, col_fin)
                        # det usa full det (más real que det_f)
                        dcount, dhh = _detenciones_in_window(det, term, ini, fin)
                        disp, cump, util = _get_faena_kpis_row(
                            r,
                            col_disp,
                            col_cump,
                            col_util,
                            col_hop2,
                            col_ind2,
                            col_tgt2,
                            col_op2,
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
                    # formateo
                    df_last["Inicio OP"] = pd.to_datetime(df_last["Inicio OP"], errors="coerce").dt.strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    for c in [
                        "Disponibilidad_Técnica (T)",
                        "Cumplimiento (U)",
                        "Utilización (V)",
                    ]:
                        df_last[c] = pd.to_numeric(df_last[c], errors="coerce")

                    st.dataframe(df_last, use_container_width=True, height=320)

                    # gráfico rápido por terminal (promedio de esas 3)
                    gk = df_last.copy()
                    gk["Disponibilidad_Técnica (T)"] = pd.to_numeric(
                        gk["Disponibilidad_Técnica (T)"], errors="coerce"
                    )
                    gk["Cumplimiento (U)"] = pd.to_numeric(gk["Cumplimiento (U)"], errors="coerce")
                    gk["Utilización (V)"] = pd.to_numeric(gk["Utilización (V)"], errors="coerce")
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
                            melt,
                            x="Terminal",
                            y="%",
                            color="KPI",
                            barmode="group",
                            title="Promedio KPIs (últimas 3 faenas por Terminal)",
                        ),
                        use_container_width=True,
                        key="tab0_bar_last3_kpis",
                    )

                with st.expander("🛈 Cómo se calcula (objetivo y sin ambigüedad)"):
                    st.markdown(
                        """
**Este bloque usa principalmente los valores reales ya calculados en tu tabla (Faena):**

- **Disponibilidad Técnica (T)**  
  - Se toma desde la columna **`Disponibilidad_Tecnica`** u otro alias equivalente.  
  - Si no existe, se calcula:  
    **Disponibilidad Técnica = Horas Operación / (Horas Operación + Indisponibilidad[HH])**.

- **Cumplimiento (U)**  
  - Se toma desde la columna **`Cumplimiento`** si existe.  
  - Si no existe, se calcula:  
    **Cumplimiento = Tractos OP / Target Operación**.

- **Utilización (V)**  
  - Se toma desde la columna de utilización (por ejemplo **`Utilizacion`**).  
  - No se “reinventa” acá: se muestra como **está definido en tu tabla**, que es lo que pediste.

- **Detenciones asociadas a la faena**
  - Se cuentan por **Terminal** y por **ventana de tiempo**: Inicio OP → Término OP.  
  - Si no hay Término OP, se asume **Inicio OP + 24h** (fallback objetivo).
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

        # fallback
        col_hop = find_first_col(
            df,
            [
                "Horas Operación",
                "Horas de operación",
                "Horas Operación ",
                "Horas operacion",
                "Horas de operacion",
            ],
        )
        col_indisp = find_first_col(
            df,
            [
                "Indisponibilidad [HH]",
                "Indisponibilidad",
                "Indisponibilidad [H]",
                "Indisponibilidad[HH]",
                "Indisponibilidad horas",
            ],
        )

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

        # Serie por día
        if col_ini and df[col_ini].notna().any() and "Disponibilidad_Tecnica_%" in df.columns:
            df["Fecha"] = pd.to_datetime(df[col_ini], errors="coerce").dt.date
            g = df.groupby("Fecha")["Disponibilidad_Tecnica_%"].mean().reset_index()
            st.plotly_chart(
                px.line(g, x="Fecha", y="Disponibilidad_Tecnica_%", markers=True, title="Disponibilidad técnica promedio por día (%)"),
                use_container_width=True,
                key="tab3_line_disptec_dia",
            )

        # Promedio por terminal (si existe)
        if col_term and "Disponibilidad_Tecnica_%" in df.columns:
            gt = df.groupby(col_term)["Disponibilidad_Tecnica_%"].mean().reset_index()
            st.plotly_chart(
                px.bar(gt, x=col_term, y="Disponibilidad_Tecnica_%", title="Promedio Disponibilidad Técnica (%) por Terminal"),
                use_container_width=True,
                key="tab3_bar_disptec_terminal",
            )

        st.dataframe(df, use_container_width=True, height=520)

# =========================================================
# TAB U: UTILIZACIÓN (basado en U y V reales)
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

        # preferimos reales
        col_cumpl = find_first_col(
            dfu, ["Cumplimiento", "Cumplimiento (U)", "Cumplimiento %", "Cumplimiento(U)"]
        )
        col_util = find_first_col(
            dfu,
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
        col_disp = find_first_col(
            dfu,
            [
                "Disponibilidad_Tecnica",
                "Disponibilidad_Tecnica_%",
                "Disponibilidad técnica",
                "Disponibilidad tecnica",
                "Disponibilidad tecnica %",
                "Disponibilidad técnica (%)",
            ],
        )

        # fallback mínimos por si faltan
        col_tgt = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target", "Target operaciones"])
        col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op", "Tracto OP", "Tractos operación"])

        # normalizar % a 0..100
        if col_cumpl and col_cumpl in dfu.columns:
            dfu[col_cumpl] = percent_to_0_100(dfu[col_cumpl])
        else:
            # calcular si no existe
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

        # KPIs arriba (promedio según filtros)
        k1, k2, k3 = st.columns(3)
        m_c = _pct_mean(dfu, col_cumpl)
        m_u = _pct_mean(dfu, col_util) if col_util else None
        m_d = _pct_mean(dfu, "_DispTec_%") if "_DispTec_%" in dfu.columns else None

        k1.metric("Cumplimiento (U)", "—" if m_c is None else f"{m_c:.1f}%")
        k2.metric("Utilización (V)", "—" if m_u is None else f"{m_u:.1f}%")
        k3.metric("Disponibilidad Técnica (T)", "—" if m_d is None else f"{m_d:.1f}%")

        with st.expander("🛈 Interpretación sin errores"):
            st.markdown(
                """
- **Cumplimiento (U)**: se toma desde tu tabla (columna U) o se calcula como **Tractos OP / Target** si no existe.
- **Utilización (V)**: se toma desde tu tabla (columna V).  
  > Se muestra tal cual está definida en tu Excel (no se redefine en el dashboard).
- **Disponibilidad Técnica (T)**: se muestra como referencia si existe en tu tabla (columna T).
                """
            )

        st.divider()

        # Serie temporal por día (Cumplimiento y Utilización)
        if col_ini and dfu[col_ini].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu[col_ini], errors="coerce").dt.date

            series_cols = []
            if col_cumpl: series_cols.append(col_cumpl)
            if col_util: series_cols.append(col_util)

            if series_cols:
                g = dfu.groupby("Fecha")[series_cols].mean().reset_index()
                melt = g.melt(id_vars=["Fecha"], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.line(
                        melt,
                        x="Fecha",
                        y="%",
                        color="KPI",
                        markers=True,
                        title="Evolución diaria (promedio)",
                    ),
                    use_container_width=True,
                    key="tabU_line_daily",
                )

        # Promedio por terminal
        if col_term:
            kpis = []
            if col_cumpl: kpis.append(col_cumpl)
            if col_util: kpis.append(col_util)
            if "_DispTec_%" in dfu.columns: kpis.append("_DispTec_%")

            if kpis:
                gt = dfu.groupby(col_term)[kpis].mean().reset_index()
                melt2 = gt.melt(id_vars=[col_term], var_name="KPI", value_name="%")
                st.plotly_chart(
                    px.bar(
                        melt2,
                        x=col_term,
                        y="%",
                        color="KPI",
                        barmode="group",
                        title="Promedio KPIs por Terminal",
                    ),
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
    "Fuente: Google Sheets exportado a XLSX (Faena, Detenciones, Estado_Flota). Dashboard Streamlit."
)
