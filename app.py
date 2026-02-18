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

# Semáforo
TH_RED = 90.0
TH_GREEN = 95.0

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
    """
    Convierte porcentajes leídos desde Excel a escala 0..100.
    - Si vienen como 0.8, 1.0 => los pasa a 80, 100
    - Si ya vienen como 80, 100 => los deja igual
    - Si vienen como texto "80%" => los convierte a 80
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

def classify_semaforo(v_pct: float) -> str:
    if pd.isna(v_pct):
        return "SIN DATO"
    if v_pct < TH_RED:
        return "🟥 <90%"
    if v_pct < TH_GREEN:
        return "🟨 90-95%"
    return "🟩 ≥95%"

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

    faena = _to_datetime(faena, ["Inicio OP", "Termino Op", "Termino OP", "Término OP"])
    for c in faena.columns:
        # convertir a numérico donde aplique sin romper strings
        if any(k in str(c) for k in ["Horas", "Indisponibilidad", "Disponibilidad", "Target", "Tractos", "Capacidad", "Utilizacion", "Utilización", "Brecha", "Cumplimiento"]):
            faena[c] = pd.to_numeric(faena[c], errors="ignore")

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

    # Defaults: mes en curso (si hay datos)
    today = dt.date.today()
    first_day_month = today.replace(day=1)

    min_date = det["Inicio"].min().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None
    max_date = det["Inicio"].max().date() if "Inicio" in det.columns and det["Inicio"].notna().any() else None

    default_ini = first_day_month
    default_fin = today
    if min_date:
        default_ini = max(min_date, first_day_month)
    if max_date:
        default_fin = min(max_date, today)
        if min_date and default_ini > default_fin:
            default_ini = min_date

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
# KPIs TOP (resumen rápido)
# =========================================================
c1, c2, c3, c4 = st.columns(4)

total_det = int(det_f.shape[0])
total_hh_det = float(det_f["Horas de reparación"].sum()) if "Horas de reparación" in det_f.columns else 0.0
equipos_afectados = int(det_f["Equipo"].nunique()) if "Equipo" in det_f.columns else 0

# Disponibilidad técnica (promedio) desde faena: si existe Horas Operación e Indisponibilidad[HH]
disp_tecnica_prom = None
col_horas_op = find_first_col(faena_f, ["Horas Operación", "Horas de operación", "Horas Operación "])
col_indisp = find_first_col(faena_f, ["Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad[HH]", "Indisponibilidad HH"])

if (not faena_f.empty) and col_horas_op and col_indisp:
    a = pd.to_numeric(faena_f[col_horas_op], errors="coerce")
    b = pd.to_numeric(faena_f[col_indisp], errors="coerce")
    disp = (a / (a + b)).replace([pd.NA, pd.NaT], pd.NA)
    disp_tecnica_prom = float(disp.mean()) if disp.notna().any() else None

with c1:
    st.metric("Detenciones (registros)", f"{total_det:,}".replace(",", "."))
with c2:
    st.metric("Horas detención (HH)", f"{total_hh_det:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    st.metric("Equipos con detención", f"{equipos_afectados:,}".replace(",", "."))
with c4:
    st.metric(
        "Disponibilidad técnica (prom. período)",
        "—" if disp_tecnica_prom is None else f"{disp_tecnica_prom*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
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

        # Solo filas de tractos reales
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

        pie_df = pd.DataFrame({"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]})
        st.plotly_chart(
            px.pie(pie_df, names="Estado", values="Cantidad", title="Flota hoy (Operativos vs No operativos)"),
            use_container_width=True,
            key="tab0_pie_flotahoy"
        )

        if col_ubic is not None and col_ubic in dfE.columns:
            st.markdown("#### Disponibilidad hoy por ubicación")
            grp = dfE.groupby(col_ubic)["_operativo"].agg(Total="size", Operativos="sum").reset_index()
            grp["Disponibilidad_%"] = (grp["Operativos"] / grp["Total"]) * 100
            st.plotly_chart(
                px.bar(grp.sort_values("Disponibilidad_%", ascending=False),
                       x=col_ubic, y="Disponibilidad_%", title="Disponibilidad (%) por ubicación"),
                use_container_width=True,
                key="tab0_bar_disp_ubic"
            )

        st.markdown("#### Listado de tractos (hoy) — Ubicación y Estado")
        show_cols = []
        for c in [col_tracto, col_ubic, col_status, col_causa, col_fprop, col_obs]:
            if c and c in dfE.columns and c not in show_cols:
                show_cols.append(c)

        df_show = dfE[show_cols].copy()
        # Col auxiliar para colorear
        df_show["_operativo"] = dfE["_operativo"].values

        def _style_rows(row):
            ok = bool(row.get("_operativo", False))
            # verde si operativo, rojo si no
            bg = "#d1fae5" if ok else "#fee2e2"
            return [f"background-color: {bg}"] * len(row)

        # dataframe sin la col auxiliar
        styled = df_show.style.apply(_style_rows, axis=1)
        styled = styled.format(na_rep="")

        st.dataframe(styled, use_container_width=True, height=420)

    st.divider()

    st.markdown("### 2) Fallas por tipo (DM / DE / DO) — frecuencia e impacto (HH)")
    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        dfx = det_f.copy()
        dfx["DMDEDO"] = dfx["Tipo"].apply(map_dmde_do)

        cL, cR = st.columns(2)
        cnt = dfx.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        cL.plotly_chart(px.bar(cnt, x="DMDEDO", y="Cantidad", title="Cantidad por DM/DE/DO"),
                        use_container_width=True, key="tab0_bar_dm_count")

        if "Horas de reparación" in dfx.columns:
            hh = dfx.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            cR.plotly_chart(px.bar(hh, x="DMDEDO", y="Horas de reparación", title="HH por DM/DE/DO"),
                            use_container_width=True, key="tab0_bar_dm_hh")

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

        count_dm = (
            df_dm.groupby("DMDEDO")
            .size()
            .reset_index(name="Cantidad")
            .sort_values("Cantidad", ascending=False)
        )

        col_left.plotly_chart(
            px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM / DE / DO"),
            use_container_width=True,
            key="tab2_bar_dm_count"
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
                key="tab2_bar_dm_hh"
            )

            st.divider()

            if "Equipo" in df_dm.columns:
                tipo_opts = sorted(df_dm["DMDEDO"].dropna().unique())
                tipo_sel = st.selectbox("Ver Top equipos por HH para", options=tipo_opts, key="tab2_sel_dm_tipo")

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
                    key="tab2_bar_top10_by_dm"
                )

    st.subheader("Tabla detenciones filtradas")
    st.dataframe(det_f, use_container_width=True, height=420)

# =========================================================
# TAB 3: DISPONIBILIDAD (FAENA)
# =========================================================
with tab3:
    st.subheader("Disponibilidad técnica (Faena) — histórico")

    if faena_f.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        df = faena_f.copy()

        col_hop = find_first_col(df, ["Horas Operación", "Horas de operación"])
        col_ind = find_first_col(df, ["Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad[HH]"])

        if col_hop and col_ind:
            hop = pd.to_numeric(df[col_hop], errors="coerce")
            ind = pd.to_numeric(df[col_ind], errors="coerce")
            df["Disponibilidad_Tecnica_%"] = percent_to_0_100(hop / (hop + ind))

            with st.expander("ℹ️ ¿Qué es Disponibilidad Técnica?"):
                st.write("Mide cuánto tiempo el sistema estuvo operando versus el total (operación + detención).")
                st.write("Fórmula: Horas Operación / (Horas Operación + Indisponibilidad[HH]).")

            if "Inicio OP" in df.columns and df["Inicio OP"].notna().any():
                df["Fecha"] = pd.to_datetime(df["Inicio OP"], errors="coerce").dt.date
                if "Terminal" in df.columns:
                    g = df.groupby(["Fecha", "Terminal"], dropna=False)["Disponibilidad_Tecnica_%"].mean().reset_index()
                    fig = px.line(g, x="Fecha", y="Disponibilidad_Tecnica_%", color="Terminal", markers=True,
                                  title="Disponibilidad técnica promedio por día (%)")
                else:
                    g = df.groupby("Fecha")["Disponibilidad_Tecnica_%"].mean().reset_index()
                    fig = px.line(g, x="Fecha", y="Disponibilidad_Tecnica_%", markers=True,
                                  title="Disponibilidad técnica promedio por día (%)")
                st.plotly_chart(fig, use_container_width=True, key="tab3_line_disp_tecnica")

            if "Terminal" in df.columns:
                gt = df.groupby("Terminal")["Disponibilidad_Tecnica_%"].mean().reset_index()
                gt["Semáforo"] = gt["Disponibilidad_Tecnica_%"].apply(classify_semaforo)
                st.plotly_chart(
                    px.bar(gt.sort_values("Disponibilidad_Tecnica_%", ascending=False),
                           x="Terminal", y="Disponibilidad_Tecnica_%", color="Semáforo",
                           title="Disponibilidad técnica promedio por Terminal (%)"),
                    use_container_width=True,
                    key="tab3_bar_disp_terminal"
                )
        else:
            st.info("Para Disponibilidad Técnica necesito columnas: Horas Operación y Indisponibilidad[HH].")

        st.subheader("Tabla Faena filtrada")
        st.dataframe(faena_f, use_container_width=True, height=420)

# =========================================================
# TAB U: UTILIZACIÓN (CLARO Y SIN OPERADORES)
# =========================================================
with tabU:
    st.subheader("📈 Utilización y Cumplimiento — por Terminal (simple y sin confusión)")

    dfu = faena_f.copy()
    if dfu.empty:
        st.info("No hay registros de Faena con los filtros actuales.")
    else:
        # columnas base
        col_terminal = find_first_col(dfu, ["Terminal"])
        col_target = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target Operac"])
        col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op"])
        col_used = find_first_col(dfu, ["Tractos Utilizados", "Tractos utilizados"])
        col_cap_real = find_first_col(dfu, ["Capacidad_Real", "Capacidad Real"])
        col_hop = find_first_col(dfu, ["Horas Operación", "Horas de operación"])
        col_ind = find_first_col(dfu, ["Indisponibilidad [HH]", "Indisponibilidad", "Indisponibilidad[HH]"])

        # numéricos
        for c in [col_target, col_op, col_used, col_cap_real, col_hop, col_ind]:
            if c and c in dfu.columns:
                dfu[c] = pd.to_numeric(dfu[c], errors="coerce")

        # 1) Cumplimiento demanda = Tractos OP / Target
        if col_target and col_op:
            dfu["Cumplimiento_%"] = percent_to_0_100(dfu[col_op] / dfu[col_target])
        else:
            dfu["Cumplimiento_%"] = pd.NA

        # 2) Utilización flota = Tractos Utilizados / Capacidad Real
        if col_used and col_cap_real:
            dfu["Utilizacion_Flota_%"] = percent_to_0_100(dfu[col_used] / dfu[col_cap_real])
        else:
            dfu["Utilizacion_Flota_%"] = pd.NA

        # 3) Disponibilidad técnica = Horas Operación / (Horas Operación + Indisp HH)
        if col_hop and col_ind:
            dfu["Disponibilidad_Tecnica_%"] = percent_to_0_100(dfu[col_hop] / (dfu[col_hop] + dfu[col_ind]))
        else:
            dfu["Disponibilidad_Tecnica_%"] = pd.NA

        with st.expander("ℹ️ ¿Cómo interpretar estas métricas? (sin enredos)"):
            st.markdown(
                """
**Cumplimiento Demanda (%)**  
- Mide si se cumplió lo planificado.  
- Fórmula: **Tractos OP / Target Operación**.

**Utilización Flota (%)**  
- Mide cuánto de la flota disponible realmente se ocupó.  
- Fórmula: **Tractos Utilizados / Capacidad Real**.

**Disponibilidad Técnica (%)**  
- Mide tiempo operativo versus total (operación + detención).  
- Fórmula: **Horas Operación / (Horas Operación + Indisponibilidad[HH])**.

**Semáforo**  
- 🟥 < 90%  
- 🟨 90–<95%  
- 🟩 ≥ 95%
                """
            )

        # KPIs globales (promedio del período filtrado)
        k1, k2, k3, k4 = st.columns(4)

        def _mean_pct(colname):
            v = pd.to_numeric(dfu[colname], errors="coerce") if colname in dfu.columns else pd.Series(dtype=float)
            v = v.dropna()
            return None if v.empty else float(v.mean())

        m_disp = _mean_pct("Disponibilidad_Tecnica_%")
        m_cump = _mean_pct("Cumplimiento_%")
        m_util = _mean_pct("Utilizacion_Flota_%")
        sum_ind = float(pd.to_numeric(dfu[col_ind], errors="coerce").sum()) if col_ind else 0.0

        k1.metric("Disponibilidad técnica", "—" if m_disp is None else f"{m_disp:.1f}%")
        k2.metric("Cumplimiento demanda", "—" if m_cump is None else f"{m_cump:.1f}%")
        k3.metric("Utilización flota", "—" if m_util is None else f"{m_util:.1f}%")
        k4.metric("Indisponibilidad [HH]", f"{sum_ind:,.1f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.divider()

        # Resumen por terminal (lo más entendible)
        if col_terminal:
            gt = dfu.groupby(col_terminal, dropna=False).agg(
                Disponibilidad_Tecnica_pct=("Disponibilidad_Tecnica_%", "mean"),
                Cumplimiento_pct=("Cumplimiento_%", "mean"),
                Utilizacion_Flota_pct=("Utilizacion_Flota_%", "mean"),
                Indisp_HH=(col_ind, "sum") if col_ind else ("Disponibilidad_Tecnica_%", "size"),
            ).reset_index()

            gt["Semáforo Disp."] = gt["Disponibilidad_Tecnica_pct"].apply(classify_semaforo)
            gt["Semáforo Cumpl."] = gt["Cumplimiento_pct"].apply(classify_semaforo)
            gt["Semáforo Util."] = gt["Utilizacion_Flota_pct"].apply(classify_semaforo)

            st.markdown("### Semáforo por Terminal (promedios del período)")
            st.dataframe(gt.sort_values("Disponibilidad_Tecnica_pct", ascending=False), use_container_width=True, height=320)

            st.markdown("### Gráficos por Terminal (claros para cualquier persona)")

            cA, cB, cC = st.columns(3)

            cA.plotly_chart(
                px.bar(gt.sort_values("Disponibilidad_Tecnica_pct", ascending=False),
                       x=col_terminal, y="Disponibilidad_Tecnica_pct", color="Semáforo Disp.",
                       title="Disponibilidad técnica (%)"),
                use_container_width=True,
                key="tabU_bar_disp_terminal"
            )
            cB.plotly_chart(
                px.bar(gt.sort_values("Cumplimiento_pct", ascending=False),
                       x=col_terminal, y="Cumplimiento_pct", color="Semáforo Cumpl.",
                       title="Cumplimiento demanda (%)"),
                use_container_width=True,
                key="tabU_bar_cump_terminal"
            )
            cC.plotly_chart(
                px.bar(gt.sort_values("Utilizacion_Flota_pct", ascending=False),
                       x=col_terminal, y="Utilizacion_Flota_pct", color="Semáforo Util.",
                       title="Utilización flota (%)"),
                use_container_width=True,
                key="tabU_bar_util_terminal"
            )
        else:
            st.info("Para mostrar por Terminal necesito la columna 'Terminal' en la hoja Faena.")

        st.divider()
        st.subheader("Tabla base (para auditoría / gestión)")

        cols_show = []
        for c in [
            "Inicio OP", col_terminal, "Buque",
            col_target, col_op, col_used, col_cap_real,
            col_hop, col_ind,
            "Disponibilidad_Tecnica_%", "Cumplimiento_%", "Utilizacion_Flota_%"
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
