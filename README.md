# Dashboard Tractos (Streamlit)

Dashboard online para:
- Portada ejecutiva: flota operativa hoy + ubicación + fuera de servicio (acción)
- Utilización: solicitado (Target) vs usado (Tractos OP), brecha y semáforo
- Detenciones: DM/DE/DO, Pareto por HH, componentes y modos de falla
- Disponibilidad por faena
- Export de datos filtrados

## Deploy
- Subir repo a GitHub
- Streamlit Community Cloud: New app -> seleccionar repo -> main file: app.py

## Variables de entorno (opcional)
- EXCEL_URL: URL de export XLSX del sheet principal (Faena/Detenciones)
- ESTADO_URL: URL de export XLSX del sheet de Estado_Flota
- SHEET_FAENA / SHEET_DET / SHEET_ESTADO
- REFRESH_SEC: refresco en segundos (default 120)

## Requisito
Asegurar que ambos Google Sheets estén:
Compartir -> Cualquier persona con el enlace -> Lector
