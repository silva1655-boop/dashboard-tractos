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
st.set_page_config(page_title="Dashboard Tractos", layout="wide")

# =========================================================
# CONFIG (Google Sheets -> export XLSX)
# =========================================================
# Principal (Faena + Detenciones)
SHEET_ID_DEFAULT = "1d74h12dHeh8nnIi4gTYAQ5TUVOngf2no"
EXCEL_URL_DEFAULT = f"https://docs.google.com/spreadsheets/d/{SHEET_ID_DEFAULT}/export?format=xlsx"
EXCEL_URL = os.getenv("EXCEL_URL", EXCEL_URL_DEFAULT).strip()

SHEET_FAENA = os.getenv("SHEET_FAENA", "Faena")
SHEET_DET = os.getenv("SHEET_DET", "Detenciones")

# Estados (Estado_Flota)
SHEET_ESTADO_ID_DEFAULT = "1LwVmep7Qt-6Q3_emC5NBfCg661oHxKV09L7NUM0NSdg"
ESTADO_URL_DEFAULT = f"https://docs.google.com/spreadsheets/d/{SHEET_ESTADO_ID_DEFAULT}/export?format=xlsx"
ESTADO_URL = os.getenv("ESTADO_URL", ESTADO_URL_DEFAULT).strip()
SHEET_ESTADO = os.getenv("SHEET_ESTADO", "Estado_Flota")  # <- tu nuevo nombre

# Refresco automático (segundos)
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

    # Si por permisos Google devolviera HTML
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
    # match exact
    for c in candidates:
        if c in df.columns:
            return c
    # match normalized lower
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
    """
    Fuerza nombres de columnas únicos:
    Observacion, Observacion__2, Observacion__3 ...
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
    for c in ["Horas Operación", "Horas de operación", "Indisponibilidad [HH]", "Disponibilidad",
              "Target Operación", "Tractos OP", "Tractos Utilizados", "Capacidad_Operadores"]:
        if c in faena.columns:
            faena[c] = pd.to_numeric(faena[c], errors="coerce")

    # DETENCIONES
    det = _to_datetime(det, ["Inicio", "Fin", "Fecha"])
    if "Horas de reparación" in det.columns:
        det["Horas de reparación"] = pd.to_numeric(det["Horas de reparación"], errors="coerce")

    # Normalizar texto clave
    for c in ["Equipo", "Clasificación", "Familia Equipo", "Componente", "Modo de Falla",
              "Tipo", "Nave", "Buque", "Viaje", "Terminal"]:
        if c in det.columns:
            det[c] = det[c].apply(_safe_upper)

    # Derivados
    if "Inicio" in det.columns and det["Inicio"].notna().any():
        det["Mes"] = det["Inicio"].dt.to_period("M").astype(str)

    if "Clasificación" in det.columns:
        det["Clasificación"] = det["Clasificación"].fillna("SIN CLASIFICAR")

    return faena, det

@st.cache_data(ttl=DEFAULT_REFRESH_SEC, show_spinner=False)
def load_estado() -> pd.DataFrame:
    """
    Lee Estado_Flota aunque tenga filas de título arriba (celdas combinadas)
    y aunque existan encabezados duplicados.
    """
    content = download_google_xlsx(ESTADO_URL)

    # Leer crudo sin header
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_ESTADO, header=None)
    raw = raw.dropna(how="all").fillna("")

    # Detectar fila header: debe contener TRACTO/#TRACTO y STATUS
    def _row_text(i):
        return " | ".join([str(x).strip().lower() for x in raw.iloc[i].tolist()])

    header_idx = None
    for i in range(min(len(raw), 60)):
        txt = _row_text(i)
        ok_tracto = ("tracto" in txt)
        ok_status = ("status" in txt)
        if ok_tracto and ok_status:
            header_idx = i
            break

    # Si no detecta, fallback normal
    if header_idx is None:
        df = pd.read_excel(io.BytesIO(content), sheet_name=SHEET_ESTADO)
        df = _normalize_cols(df)
        df.columns = _make_unique_columns(df.columns)
        return df.dropna(how="all")

    # Construir DF con esa fila como encabezados
    hdr = raw.iloc[header_idx].tolist()
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = hdr
    df = df.dropna(how="all")
    df = _normalize_cols(df)

    # Columnas únicas
    df.columns = _make_unique_columns(df.columns)

    # Limpiar strings
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
# UI
# =========================================================
st.title("📊 Dashboard Tractos — Ejecutivo + Análisis (Faenas / Detenciones / Estado)")

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
    st.metric("Disponibilidad promedio (histórico)", "—" if disp_prom is None or pd.isna(disp_prom)
              else f"{disp_prom*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", "."))

st.divider()

# =========================================================
# TABS (estructura profesional)
# =========================================================
tab0, tab1, tab2, tab3, tabU, tab4 = st.tabs(
    ["🏠 Estado General", "📌 Resumen", "🛑 Detenciones", "✅ Disponibilidad (Faena)", "📈 Utilización", "📁 Datos"]
)

# =========================================================
# TAB 0: PORTADA EJECUTIVA (mínimo, potente)
# =========================================================
with tab0:
    st.subheader("🏠 Estado General (Ejecutivo) — lo esencial en una pantalla")

    st.markdown("### 1) Estado actual de flota (hoy)")
    cA, cB, cC, cD = st.columns(4)

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

    if estado.empty or col_tracto is None or col_status is None:
        cA.metric("Flota (hoy)", "—")
        cB.metric("Operativos (hoy)", "—")
        cC.metric("No operativos (hoy)", "—")
        cD.metric("Disponibilidad (hoy)", "—")
        st.info("Para la portada necesito columnas en Estado_Flota: '#Tracto/Tracto' y 'Status'.")
        st.write("Columnas detectadas:", list(estado.columns) if not estado.empty else "—")
    else:
        dfE = estado.copy()
        dfE[col_status] = dfE[col_status].astype(str).str.upper().str.strip()
        dfE[col_tracto] = dfE[col_tracto].astype(str).str.strip()
        if col_ubic is not None and col_ubic in dfE.columns:
            dfE[col_ubic] = dfE[col_ubic].astype(str).str.strip()

        dfE["_operativo"] = dfE[col_status].apply(is_operativo_status)

        total_f = int(dfE[col_tracto].nunique())
        op_f = int(dfE[dfE["_operativo"]][col_tracto].nunique())
        no_op_f = max(total_f - op_f, 0)
        disp_hoy = (op_f / total_f) if total_f else None

        cA.metric("Flota (hoy)", f"{total_f:,}".replace(",", "."))
        cB.metric("Operativos (hoy)", f"{op_f:,}".replace(",", "."))
        cC.metric("No operativos (hoy)", f"{no_op_f:,}".replace(",", "."))
        cD.metric("Disponibilidad (hoy)", "—" if disp_hoy is None else f"{disp_hoy*100:,.1f}%".replace(",", "X").replace(".", ",").replace("X", "."))

        pie_df = pd.DataFrame({"Estado": ["Operativos", "No operativos"], "Cantidad": [op_f, no_op_f]})
        st.plotly_chart(px.pie(pie_df, names="Estado", values="Cantidad", title="Flota hoy (Operativos vs No operativos)"),
                        use_container_width=True)

        if col_ubic is not None and col_ubic in dfE.columns:
            st.markdown("#### Operativos por ubicación (hoy)")
            op_by_loc = dfE[dfE["_operativo"]].groupby(col_ubic)[col_tracto].nunique().reset_index(name="Operativos")
            st.plotly_chart(px.bar(op_by_loc.sort_values("Operativos", ascending=False),
                                   x=col_ubic, y="Operativos", title="Operativos por ubicación"),
                            use_container_width=True)

        st.markdown("#### 🛑 No operativos (para gestión)")
        cols_show = [c for c in [col_tracto, col_status, col_ubic, col_causa, col_fprop, col_obs] if c is not None and c in dfE.columns]
        st.dataframe(dfE[~dfE["_operativo"]][cols_show].head(50), use_container_width=True, height=320)

    st.divider()

    st.markdown("### 2) Cumplimiento operacional (Utilización: Real / Target)")
    dfu = faena_f.copy()
    col_target = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target"])
    col_op = find_first_col(dfu, ["Tractos OP", "Tractos Op", "TractosOP", "Operativos OP"])

    if dfu.empty or col_target is None or col_op is None:
        st.info("No hay datos de Faena filtrados o faltan columnas 'Target Operación' / 'Tractos OP'.")
    else:
        dfu[col_target] = pd.to_numeric(dfu[col_target], errors="coerce")
        dfu[col_op] = pd.to_numeric(dfu[col_op], errors="coerce")
        dfu["Utilización_%"] = (dfu[col_op] / dfu[col_target]) * 100
        dfu["Brecha"] = dfu[col_target] - dfu[col_op]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Target prom.", f"{dfu[col_target].mean():.1f}")
        k2.metric("Real prom. (Tractos OP)", f"{dfu[col_op].mean():.1f}")
        k3.metric("Utilización prom.", f"{dfu['Utilización_%'].mean():.1f}%")
        k4.metric("Brecha prom. (Target-Real)", f"{dfu['Brecha'].mean():.1f}")

        if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any():
            dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date
            g = dfu.groupby("Fecha")[[col_target, col_op]].mean().reset_index()
            st.plotly_chart(
                px.line(g.melt(id_vars=["Fecha"], var_name="Métrica", value_name="Cantidad"),
                        x="Fecha", y="Cantidad", color="Métrica", markers=True,
                        title="Target vs Real (promedio por día)"),
                use_container_width=True
            )

    st.divider()

    st.markdown("### 3) Fallas por tipo (DM / DE / DO) — frecuencia e impacto (HH)")
    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo' en Detenciones.")
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
# TAB 1: RESUMEN (histórico)
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
# TAB 2: DETENCIONES (incluye DM/DE/DO)
# =========================================================
with tab2:
    st.subheader("Detenciones — análisis")

    st.divider()
    st.subheader("DM / DE / DO — Conteo, HH y Top equipos")

    if det_f.empty or "Tipo" not in det_f.columns:
        st.info("No hay detenciones filtradas o falta columna 'Tipo'.")
    else:
        df_dm = det_f.copy()
        df_dm["DMDEDO"] = df_dm["Tipo"].apply(map_dmde_do)

        c1, c2 = st.columns(2)
        count_dm = df_dm.groupby("DMDEDO").size().reset_index(name="Cantidad").sort_values("Cantidad", ascending=False)
        c1.plotly_chart(px.bar(count_dm, x="DMDEDO", y="Cantidad", title="Cantidad por DM/DE/DO"), use_container_width=True)

        if "Horas de reparación" in df_dm.columns:
            hh_dm = df_dm.groupby("DMDEDO")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False)
            c2.plotly_chart(px.bar(hh_dm, x="DMDEDO", y="Horas de reparación", title="HH por DM/DE/DO"), use_container_width=True)

            if "Equipo" in df_dm.columns:
                tipo_sel = st.selectbox("Ver Top equipos por HH para", options=sorted(df_dm["DMDEDO"].dropna().unique()))
                sub = df_dm[df_dm["DMDEDO"] == tipo_sel]
                top_eq = sub.groupby("Equipo")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(10)
                st.plotly_chart(px.bar(top_eq, x="Equipo", y="Horas de reparación", title=f"Top 10 equipos por HH — {tipo_sel}"),
                                use_container_width=True)

    st.divider()
    st.subheader("Análisis por familia / componente / modo de falla")

    cA, cB = st.columns(2)
    if all(col in det_f.columns for col in ["Familia Equipo", "Clasificación"]) and det_f.shape[0] > 0:
        pivot = det_f.pivot_table(index="Familia Equipo", columns="Clasificación", values="Equipo", aggfunc="count", fill_value=0).reset_index()
        cA.plotly_chart(
            px.bar(pivot.melt(id_vars=["Familia Equipo"], var_name="Clasificación", value_name="Cantidad"),
                   x="Familia Equipo", y="Cantidad", color="Clasificación", barmode="stack",
                   title="Cantidad de fallas por Familia y Clasificación"),
            use_container_width=True
        )

    if all(col in det_f.columns for col in ["Equipo", "Clasificación", "Horas de reparación"]) and det_f.shape[0] > 0:
        pe = det_f.groupby(["Equipo", "Clasificación"])["Horas de reparación"].sum().reset_index()
        cB.plotly_chart(px.bar(pe, x="Equipo", y="Horas de reparación", color="Clasificación", barmode="stack",
                               title="HH por Equipo y Clasificación"),
                        use_container_width=True)

    cC, cD = st.columns(2)
    if "Componente" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        comp = det_f.groupby("Componente")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(15)
        cC.plotly_chart(px.bar(comp, x="Componente", y="Horas de reparación", title="Top 15 Componentes por HH"),
                        use_container_width=True)

    if "Modo de Falla" in det_f.columns and "Horas de reparación" in det_f.columns and det_f.shape[0] > 0:
        modo = det_f.groupby("Modo de Falla")["Horas de reparación"].sum().reset_index().sort_values("Horas de reparación", ascending=False).head(15)
        cD.plotly_chart(px.bar(modo, x="Modo de Falla", y="Horas de reparación", title="Top 15 Modos por HH"),
                        use_container_width=True)

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
            g = df.groupby(["Fecha", "Terminal"], dropna=False)["Disp01"].mean().reset_index() if "Terminal" in df.columns else df.groupby("Fecha")["Disp01"].mean().reset_index()
            if "Terminal" in g.columns:
                fig = px.line(g, x="Fecha", y="Disp01", color="Terminal", markers=True, title="Disponibilidad promedio por día")
            else:
                fig = px.line(g, x="Fecha", y="Disp01", markers=True, title="Disponibilidad promedio por día")
            st.plotly_chart(fig, use_container_width=True)

        cA, cB = st.columns(2)
        if "Buque" in df.columns and "Disponibilidad" in df.columns:
            df["Disp01"] = normalize_disponibilidad_to_0_1(df["Disponibilidad"])
            b = df.groupby("Buque")["Disp01"].mean().reset_index().sort_values("Disp01", ascending=False)
            cA.plotly_chart(px.bar(b, x="Buque", y="Disp01", title="Disponibilidad promedio por Buque"),
                            use_container_width=True)

        if "Terminal" in df.columns and "Indisponibilidad [HH]" in df.columns:
            t = df.groupby("Terminal")["Indisponibilidad [HH]"].sum().reset_index().sort_values("Indisponibilidad [HH]", ascending=False)
            cB.plotly_chart(px.bar(t, x="Terminal", y="Indisponibilidad [HH]", title="Indisponibilidad total [HH] por Terminal"),
                            use_container_width=True)

        st.subheader("Tabla Faena filtrada")
        st.dataframe(faena_f, use_container_width=True, height=420)

# =========================================================
# TAB U: UTILIZACIÓN (MODIFICADO: target vs disponibles vs usados + cuello botella)
# =========================================================
with tabU:
    st.subheader("📈 Utilización — Demanda vs Oferta vs Capacidad (cuello de botella)")

    dfu = faena_f.copy()

    # Columnas base
    col_target = find_first_col(dfu, ["Target Operación", "Target Operacion", "Target"])
    col_disp = find_first_col(dfu, ["Tractos OP", "Tractos Op", "TractosOP", "Disponibles", "Operativos OP"])
    col_used = find_first_col(dfu, ["Tractos Utilizados", "Tractos utilizados", "Utilizados", "Tractos Usados"])
    col_ops = find_first_col(dfu, ["Capacidad_Operadores", "Capacidad Operadores", "Operadores", "Capacidad"])

    if dfu.empty or col_target is None or col_disp is None or col_used is None:
        st.info("Faltan columnas para Utilización. Necesito: Target Operación, Tractos OP (disponibles) y Tractos Utilizados.")
        if not dfu.empty:
            st.write("Columnas detectadas:", list(dfu.columns))
        st.stop()

    # Numéricos
    dfu[col_target] = pd.to_numeric(dfu[col_target], errors="coerce")
    dfu[col_disp] = pd.to_numeric(dfu[col_disp], errors="coerce")
    dfu[col_used] = pd.to_numeric(dfu[col_used], errors="coerce")
    if col_ops is not None:
        dfu[col_ops] = pd.to_numeric(dfu[col_ops], errors="coerce")

    # KPIs principales
    dfu["Cumplimiento_Target_%"] = (dfu[col_used] / dfu[col_target]) * 100
    dfu.loc[(dfu[col_target].isna()) | (dfu[col_target] == 0), "Cumplimiento_Target_%"] = pd.NA

    dfu["Uso_Disponibles_%"] = (dfu[col_used] / dfu[col_disp]) * 100
    dfu.loc[(dfu[col_disp].isna()) | (dfu[col_disp] == 0), "Uso_Disponibles_%"] = pd.NA

    # Capacidad real (si existe capacidad de operadores)
    if col_ops is not None:
        dfu["Capacidad_Real"] = dfu[[col_disp, col_ops]].min(axis=1)
    else:
        dfu["Capacidad_Real"] = dfu[col_disp]

    dfu["Utilización_Capacidad_%"] = (dfu[col_used] / dfu["Capacidad_Real"]) * 100
    dfu.loc[(dfu["Capacidad_Real"].isna()) | (dfu["Capacidad_Real"] == 0), "Utilización_Capacidad_%"] = pd.NA

    # Brechas
    dfu["Brecha_Target_Usados"] = dfu[col_target] - dfu[col_used]
    dfu["Déficit_Flota_(Target-Disp)"] = (dfu[col_target] - dfu[col_disp]).clip(lower=0)

    # Diagnóstico cuello de botella
    def _cuello(row):
        t = row.get(col_target)
        d = row.get(col_disp)
        u = row.get(col_used)
        o = row.get(col_ops) if col_ops is not None else None

        if pd.isna(t) or t == 0:
            return "SIN TARGET"
        if pd.isna(d) or d == 0:
            return "SIN FLOTA"

        cap = min(d, o) if (o is not None and pd.notna(o) and o > 0) else d

        # Si no se cumple target:
        if pd.notna(u) and u < t:
            # Capacidad real < target => falta capacidad (flota u operadores)
            if cap < t:
                if (o is not None and pd.notna(o) and o < d and o < t):
                    return "FALTA OPERADORES"
                return "FALTA FLOTA"
            # Si capacidad alcanza el target pero igual no se usó
            return "NO SE USÓ TODO (OPERACIÓN/PLAN)"
        # Cumple o sobrecumple
        return "OK / BALANCEADO"

    dfu["Indicador_Cuello"] = dfu.apply(_cuello, axis=1)

    # KPIs arriba
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Target prom.", "—" if dfu[col_target].mean() != dfu[col_target].mean() else f"{dfu[col_target].mean():.1f}")
    k2.metric("Disponibles prom. (Tractos OP)", "—" if dfu[col_disp].mean() != dfu[col_disp].mean() else f"{dfu[col_disp].mean():.1f}")
    k3.metric("Usados prom. (Tractos Utilizados)", "—" if dfu[col_used].mean() != dfu[col_used].mean() else f"{dfu[col_used].mean():.1f}")

    ct = dfu["Cumplimiento_Target_%"].mean()
    ud = dfu["Uso_Disponibles_%"].mean()
    uc = dfu["Utilización_Capacidad_%"].mean()

    k4.metric("Cumplimiento Target %", "—" if pd.isna(ct) else f"{ct:.1f}%")
    k5.metric("Uso de disponibles %", "—" if pd.isna(ud) else f"{ud:.1f}%")

    st.caption("Definiciones: Cumplimiento = usados/target. Uso disponibles = usados/disponibles. Uso capacidad = usados/min(disponibles, operadores).")

    st.divider()

    # ----- Series por fecha -----
    if "Inicio OP" in dfu.columns and dfu["Inicio OP"].notna().any():
        dfu["Fecha"] = pd.to_datetime(dfu["Inicio OP"], errors="coerce").dt.date

        # Agregación diaria
        agg_cols = [col_target, col_disp, col_used, "Cumplimiento_Target_%", "Uso_Disponibles_%", "Utilización_Capacidad_%", "Déficit_Flota_(Target-Disp)"]
        g = dfu.groupby("Fecha")[agg_cols].mean().reset_index()

        # 1) Cantidades: target vs disponibles vs usados
        fig_qty = px.line(
            g.melt(id_vars=["Fecha"], value_vars=[col_target, col_disp, col_used],
                   var_name="Métrica", value_name="Cantidad"),
            x="Fecha", y="Cantidad", color="Métrica", markers=True,
            title="Target vs Disponibles vs Usados (promedio por día)"
        )
        st.plotly_chart(fig_qty, use_container_width=True)

        # 2) %: cumplimiento vs uso disponibles vs uso capacidad
        fig_pct = px.line(
            g.melt(id_vars=["Fecha"], value_vars=["Cumplimiento_Target_%", "Uso_Disponibles_%", "Utilización_Capacidad_%"],
                   var_name="Métrica", value_name="%"),
            x="Fecha", y="%", color="Métrica", markers=True,
            title="% Cumplimiento / % Uso disponibles / % Uso capacidad (promedio por día)"
        )
        fig_pct.add_hline(y=100, line_dash="dash")
        st.plotly_chart(fig_pct, use_container_width=True)

        # 3) Déficit de flota diario
        fig_def = px.bar(g, x="Fecha", y="Déficit_Flota_(Target-Disp)", title="Déficit de flota (Target - Disponibles) por día")
        st.plotly_chart(fig_def, use_container_width=True)
    else:
        st.info("No hay columna 'Inicio OP' con fechas válidas para series diarias. (Se muestran solo agregados por Terminal/Buque abajo)")

    st.divider()

    # ----- Comparativos por Terminal / Buque -----
    cA, cB = st.columns(2)

    if "Terminal" in dfu.columns:
        gt = dfu.groupby("Terminal")[["Cumplimiento_Target_%", "Uso_Disponibles_%", "Utilización_Capacidad_%"]].mean().reset_index()
        gt = gt.sort_values("Cumplimiento_Target_%", ascending=False)
        cA.plotly_chart(px.bar(gt.melt(id_vars=["Terminal"], var_name="Métrica", value_name="%"),
                               x="Terminal", y="%", color="Métrica", barmode="group",
                               title="Indicadores % por Terminal"),
                        use_container_width=True)

    if "Buque" in dfu.columns:
        gb = dfu.groupby("Buque")[["Cumplimiento_Target_%", "Uso_Disponibles_%", "Utilización_Capacidad_%"]].mean().reset_index()
        gb = gb.sort_values("Cumplimiento_Target_%", ascending=False)
        cB.plotly_chart(px.bar(gb.melt(id_vars=["Buque"], var_name="Métrica", value_name="%"),
                               x="Buque", y="%", color="Métrica", barmode="group",
                               title="Indicadores % por Buque"),
                        use_container_width=True)

    st.divider()

    # ----- Cuello de botella: conteo de días -----
    st.subheader("📍 Cuello de botella (conteo de registros)")
    cuello_cnt = dfu["Indicador_Cuello"].value_counts(dropna=False).reset_index()
    cuello_cnt.columns = ["Indicador_Cuello", "Cantidad"]
    st.plotly_chart(px.bar(cuello_cnt, x="Indicador_Cuello", y="Cantidad", title="Frecuencia de indicador de cuello de botella"),
                    use_container_width=True)

    st.divider()

    # ----- Tabla ejecutiva -----
    st.subheader("Tabla ejecutiva (Utilización)")
    cols_show = []
    for c in ["Inicio OP", "Fecha", "Terminal", "Buque", col_target, col_disp, col_used, col_ops,
              "Capacidad_Real", "Cumplimiento_Target_%", "Uso_Disponibles_%", "Utilización_Capacidad_%",
              "Brecha_Target_Usados", "Déficit_Flota_(Target-Disp)", "Indicador_Cuello", "Disponibilidad"]:
        if c is None:
            continue
        if c in dfu.columns:
            cols_show.append(c)
    st.dataframe(dfu[cols_show], use_container_width=True, height=420)

# =========================================================
# TAB 4: EXPORT
# =========================================================
with tab4:
    st.subheader("Exportar datos filtrados")

    cA, cB = st.columns(2)
    with cA:
        csv_det = det_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Detenciones (CSV)", data=csv_det, file_name="detenciones_filtradas.csv", mime="text/csv")
    with cB:
        csv_faena = faena_f.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar Faena (CSV)", data=csv_faena, file_name="faena_filtrada.csv", mime="text/csv")

    st.caption("Si actualizas los Google Sheets, el dashboard se actualiza solo con el refresco configurado.")

st.caption("Fuente: Google Sheets exportado a XLSX (Faena, Detenciones, Estado_Flota). Dashboard Streamlit.")
