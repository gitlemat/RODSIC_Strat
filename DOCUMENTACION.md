# Documentación Técnica - RODSIC Strat

## 1. Visión General
**RODSIC_Strat** es el microservicio encargado de gestionar, orquestar y ejecutar las estrategias de trading cuantitativo automáticas y semi-automáticas, desacopladas del motor principal (`IB_Core`) y de la frontend (`RODSIC_GUI`).

### Responsabilidades
- **Motores de Estrategia**: Evalúa condiciones de mercado utilizando datos provenientes de InfluxDB o websockets (via `IB_Core`).
- **Estados Persistentes**: Realiza seguimiento del PnL y de órdenes abiertas por estrategia en tiempo real.
- **Configuración Dinámica**: Maneja configuraciones en formato YAML (`strategies.yaml`) que documentan el comportamiento y estado (Activado/Desactivado) para cada símbolo operado.

---

## 2. API REST (Endpoints Clave)
RODSIC_Strat utiliza el pipeline estandarizado `/restAPI/` en sintonía con `IB_Core`.

- **Control de Estrategias**:
  - `GET /restAPI/strategies`: Devuelve el catálogo de estrategias activadas, sus símbolos operables y métricas de PnL en tiempo real.
  - `POST /restAPI/strategies/{strat_name}/{symbol}/toggle`: Activa o desactiva la participación de una estrategia sobre un símbolo explícito. Esto actualiza de forma segura el archivo físico `strategies.yaml`.

- **Control de Sistema**:
  - `GET /restAPI/config`: Devuelve los parámetros de entorno del `.env`.
  - `POST /restAPI/config`: Actualiza el `.env` on-the-fly conservando los comentarios estructurales originales.

---

## 3. Configuración
Variables de entorno críticas en el `.env`:
- `IB_CORE_URL`: URL base para interactuar con IB_Core (ej. `http://localhost:8000`).
- `RECONNECT_INTERVAL`: Política de reconexión.
- `API_PORT`: Puerto donde escucha el servidor FastAPI de RODSIC_Strat (ej. 8002).

---

*Última Actualización: 2026-02-21*
