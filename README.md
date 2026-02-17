# Dashboard Tractos (Streamlit)

## Qué muestra
- Portada ejecutiva: Estado actual flota + Utilización + DM/DE/DO + Salud Operacional
- Detenciones: análisis y DM/DE/DO con top equipos
- Disponibilidad (Faena)
- Utilización (Target vs Real) + matriz + brecha
- Export CSV

## Requisitos
- Python 3.12

## Variables de entorno (opcional)
- EXCEL_URL: URL export XLSX del sheet principal
- ESTADO_URL: URL export XLSX del sheet Estado_Flota
- SHEET_FAENA: nombre hoja faena (default Faena)
- SHEET_DET: nombre hoja detenciones (default Detenciones)
- SHEET_ESTADO: nombre hoja estados (default Estado_Flota)
- REFRESH_SEC: refresco (default 120)

## Deploy
- Streamlit Community Cloud: conectar repo y listo.
- Asegurar que ambos Google Sheets estén "Cualquier persona con enlace -> Lector".
