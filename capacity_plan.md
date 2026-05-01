# Capacity Plan — chatbot-infraestructura

*Generated: 2026-05-01 05:42 UTC*

*Machine-readable data: [`capacity_plan.json`](capacity_plan.json)*

> **DATA SOURCE NOTICE:** Los directorios `stress_tests/results/` están vacíos —
> las pruebas de Locust no se ejecutaron aún. Los números de latencia son
> **estimaciones arquitectónicas** de `service_profile.json` más datos observados
> en `/health` (8 requests). Ejecutar `stress_tests/run_all.ps1` y regenerar este
> plan con `python _build_capacity_plan.py` para obtener cifras medidas bajo carga.


---


## 1. Perfil de Rendimiento Actual


### 1.1 Datos observados

| Métrica | Valor | Fuente |
|---|---|---|
| Requests muestreados | 8 | `GET /health` endpoint |
| p50 latencia (mixta) | 1ms | Dominado por `/health` (2ms) |
| p95 latencia (mixta) | 18.2s | Refleja `/chat` + Tavily worst-case |
| RAM del host | 11,882 MB / 16,069 MB (74%) | psutil |
| CPU cores (host) | 16 cores, avg 7.3% | psutil (idle) |
| GPU | No disponible | pynvml |


### 1.2 Throughput máximo sostenible (estimado arquitectónico)

| Métrica | Sin búsqueda (60%) | Con búsqueda (40%) | Ponderado |
|---|---|---|---|
| p50 latencia `/chat` | 3.5s | 6.5s | 4.7s |
| p95 latencia `/chat` | 8.0s | 14.0s | 10.4s |
| RPS por réplica (teórico) | 5.00 | 2.86 | **3.85** |
| RPS por réplica (práctico, 75%) | — | — | **2.89** |

*Cálculo: threadpool de uvicorn = 40 threads. Throughput = threads / latencia_p95. La eficiencia práctica es 75% del teórico por overhead de serialización, autenticación Firebase y scheduling.*


### 1.3 Clasificación del cuello de botella

```
PROFILING DEL REQUEST /chat (path crítico con búsqueda)

Step                         Latencia    % total    Tipo
─────────────────────────────────────────────────────────
Firebase token verify          80-350ms      3%     I/O (Google Auth)
Firestore READ (historial)      50-200ms      2%     I/O (crece con historial)
Mistral API — 1ª llamada     2000-6000ms     42%     I/O (GPU en Mistral servers)
Tavily Search API            1500-4000ms     23%     I/O (web crawl)
Mistral API — 2ª llamada     1500-5000ms     28%     I/O (GPU en Mistral servers)
Firestore WRITE x2            160-600ms       2%     I/O

VEREDITO: 100% I/O-bound. El cuello de botella es la latencia de Mistral API.
          Agregar más CPU o RAM al servidor NO mejora el throughput.
          La única forma de escalar es: más réplicas | caché | modelo más rápido.
```

### 1.4 Tokens por segundo

| Métrica | Valor |
|---|---|
| Tokens output promedio por request | 800 |
| RPS pico estimado (50 usuarios) | 2.024 req/s |
| Tokens/segundo en pico | **1619.2 tok/s** |
| Tokens/segundo máx por réplica (75% efficiency) | **2310.0 tok/s** |
| Total tokens mensuales | 51,750,000 |

---


## 2. Proyección para Producción


### 2.1 Parámetros de negocio

| Parámetro | Valor usado | Variable |
|---|---|---|
| Usuarios concurrentes en hora pico | **50** | `X` |
| Latencia p95 máxima aceptable | **10.0s** | `Y` |
| Crecimiento mensual esperado | **20%** | `Z` |
| Disponibilidad requerida | **99.9%** | — |
| Requests/mes estimados | 22,500 | — |

*Para recalcular con otros valores, editar las constantes al inicio de `_build_capacity_plan.py` y ejecutar `python _build_capacity_plan.py`.*


### 2.2 Número mínimo de réplicas

| Criterio | Réplicas |
|---|---|
| Disponibilidad 99.9% (N+1) | 2 |
| Carga pico 50 usuarios (2.024 rps) | 1 |
| **Recomendado (N+1 + buffer 33%)** | **2** |

### 2.3 Especificación por réplica

| Recurso | Valor | Justificación |
|---|---|---|
| vCPU | 2 | FastAPI I/O-bound, < 5% CPU bajo carga |
| RAM | 4 GB | 40 threads × 50 MB/req + 500 MB Python overhead |
| Almacenamiento | 20 GB | OS + packages + logs (sin almacenamiento de datos) |
| GPU | **Ninguna** | LLM inference en Mistral cloud — NO se necesita GPU local |
| Red | 1 Gbps estándar | Payloads JSON pequeños (<50 KB por request) |

### 2.4 Configuración de auto-scaling

```yaml
# NO usar CPU-based autoscaling (workload es I/O-bound, CPU siempre baja)
# Escalar en: saturación de threads O latencia degradada

trigger_scale_out:
  - metric: active_sync_threads_pct
    threshold: 70%
    evaluation_periods: 2
    period_seconds: 60
  - metric: p95_latency_ms
    threshold: 8000
    evaluation_periods: 3
    period_seconds: 60

min_replicas: 2
max_replicas: 8
scale_out_cooldown: 60s
scale_in_cooldown:  300s
warm_up_period:     30s   # LangChain lazy singleton init
```

### 2.5 Proyección de crecimiento

| Mes | Usuarios | Réplicas necesarias | Costo estimado (AWS optimizado) |
|---|---|---|---|
| Mes  1 | 60 usuarios | 2 réplicas | $380.68/mes |
| Mes  2 | 72 usuarios | 2 réplicas | $380.68/mes |
| Mes  3 | 86 usuarios | 2 réplicas | $380.68/mes |
| Mes  6 | 149 usuarios | 3 réplicas | $425.27/mes |
| Mes  9 | 258 usuarios | 4 réplicas | $469.86/mes |
| Mes 12 | 446 usuarios | 6 réplicas | $559.05/mes |

---


## 3. Escenarios de Infraestructura


### 3.1 🛡️ CONSERVADOR

**N+2 redundancy, all on-demand, scale at 70% capacity threshold**

| Parámetro | Valor |
|---|---|
| Réplicas base | 3 |
| Modelo de compra | on_demand |
| Autoscaling | min=3, max=12 |
| Trigger de escala | `p95_latency > 6000ms OR active_threads > 70%` |
| Margen de seguridad | 100% |
| Riesgo operativo | **LOW** |

**Costo mensual por proveedor:**

| Proveedor | Instancia | Compute | LB | Firebase | Mistral | Tavily | **TOTAL** |
|---|---|---|---|---|---|---|---|
| AWS | t3.large 2 vCPU / 8 GB RAM | $182.22 | $18.00 | $8.00 | $175.50 | $90.00 | **$473.72** |
| GCP | e2-standard-2 2 vCPU / 8 GB RAM | $146.73 | $18.25 | $8.00 | $175.50 | $90.00 | **$438.48** |
| AZURE | D2s_v5 2 vCPU / 8 GB RAM | $210.24 | $16.00 | $8.00 | $175.50 | $90.00 | **$499.74** |


### 3.2 ⚖️ OPTIMIZADO

**N+1 redundancy, mixed pricing, scale at 75% capacity**

| Parámetro | Valor |
|---|---|
| Réplicas base | 2 |
| Modelo de compra | 70% reserved_1yr + 30% on_demand |
| Autoscaling | min=2, max=8 |
| Trigger de escala | `p95_latency > 7500ms OR active_threads > 75%` |
| Margen de seguridad | 50% |
| Riesgo operativo | **MEDIUM** |

**Costo mensual por proveedor:**

| Proveedor | Instancia | Compute | LB | Firebase | Mistral | Tavily | **TOTAL** |
|---|---|---|---|---|---|---|---|
| AWS | t3.large 2 vCPU / 8 GB RAM | $89.18 | $18.00 | $8.00 | $175.50 | $90.00 | **$380.68** |
| GCP | e2-standard-2 2 vCPU / 8 GB RAM | $75.22 | $18.25 | $8.00 | $175.50 | $90.00 | **$366.97** |
| AZURE | D2s_v5 2 vCPU / 8 GB RAM | $100.92 | $16.00 | $8.00 | $175.50 | $90.00 | **$390.42** |


### 3.3 ⚡ AGRESIVO

**Minimum viable replicas, spot instances (eviction risk), late scaling trigger**

| Parámetro | Valor |
|---|---|
| Réplicas base | 2 |
| Modelo de compra | 50% spot + 50% on_demand |
| Autoscaling | min=2, max=10 |
| Trigger de escala | `p95_latency > 8500ms OR active_threads > 85%` |
| Margen de seguridad | 10% |
| Riesgo operativo | **HIGH** |

**Costo mensual por proveedor:**

| Proveedor | Instancia | Compute | LB | Firebase | Mistral | Tavily | **TOTAL** |
|---|---|---|---|---|---|---|---|
| AWS | t3.large 2 vCPU / 8 GB RAM | $69.85 | $18.00 | $8.00 | $175.50 | $90.00 | **$361.35** |
| GCP | e2-standard-2 2 vCPU / 8 GB RAM | $56.25 | $18.25 | $8.00 | $175.50 | $90.00 | **$348.00** |
| AZURE | D2s_v5 2 vCPU / 8 GB RAM | $84.10 | $16.00 | $8.00 | $175.50 | $90.00 | **$373.60** |


---


## 4. Recomendaciones FinOps


### 4.1 Modelo de compra óptimo

```
RECOMENDACIÓN: 70% 1-year Reserved + 30% On-Demand

Razón:
  ✓ La carga base es predecible → Reserved da 38-40% descuento sobre base
  ✓ 30% On-Demand cubre bursts sin riesgo de evicción de Spot
  ✗ NO usar 100% Spot → evicción durante conversación activa = UX terrible
  ✗ NO usar 100% On-Demand → costo innecesariamente alto en carga base

Ahorro vs full On-Demand (escenario optimizado, AWS):
  On-Demand total: ~$543.83/mes
  Optimizado:       $380.68/mes
```

### 4.2 Política de scheduling

> El servicio debe ser **24/7** — las sesiones de chat pueden iniciarse en cualquier zona horaria.
> Sin embargo, si el análisis de uso muestra < 5% de tráfico entre 02:00–08:00 UTC:
> 
> - Configurar scale-in a `min_replicas = 1` durante esa ventana
> - Potencial ahorro: ~25% del costo de compute (6h/24h = 25%)
> - Trade-off: primera request en el día puede tardar +30s por cold start del AgentExecutor

### 4.3 Alertas de presupuesto

| Umbral | Acción | Canal |
|---|---|---|
| $190.34/mes (50%) | Info — revisar tendencia | Email |
| $304.54/mes (80%) | Warning — investigar spike | Slack + Email |
| $380.68/mes (100%) | Critical — alertar equipo | PagerDuty |
| $456.82/mes (120%) | Emergency — suspender tests/bots | SMS + PagerDuty |

> Budget mensual recomendado para alertas: **$456.82** (1.2× escenario optimizado)


### 4.4 Métricas críticas post-despliegue

| Métrica | Threshold | Severidad | Por qué |
|---|---|---|---|
| `p95_latency_ms` | > 10000 | SLA breach | — |
| `error_rate_pct` | > 1 | Service degradation | — |
| `active_threads_pct` | > 80 | Scale-out trigger | — |
| `mistral_tokens_per_day` | > 2587500 | Cost spike | — |
| `firestore_read_latency_p95_ms` | > 500 | History growth degradation | — |
| `ram_used_pct` | > 85 | Memory pressure | — |

### 4.5 Top 3 optimizaciones de costo (ordenadas por ROI)

**#1 — Implement conversation history truncation (last 20 messages)**

- Impacto actual: High — history grows unbounded, inflating Mistral input tokens
- Ahorro estimado: **25%**
- Esfuerzo: low
- Cambio de código: `get_session_history() returns history[-20:]`

**#2 — Add Redis cache for Tavily search results (TTL 24h)**

- Impacto actual: ~$90.0/month on Tavily API
- Ahorro estimado: **30%**
- Esfuerzo: medium
- Cambio de código: `Wrap TavilySearchResults with Redis cache by query hash`

**#3 — Route simple queries to mistral-small (3x cheaper)**

- Impacto actual: ~$176.0/month all on mistral-large
- Ahorro estimado: **40%**
- Esfuerzo: medium
- Cambio de código: `Classify query complexity, use mistral-small for 'simple' category`


---


## Resumen Ejecutivo

| | 🛡️ Conservador | ⚖️ Optimizado | ⚡ Agresivo |
|---|---|---|---|
| Réplicas base | 3 | 2 | 2 |
| Margen de seguridad | 100 | 50 | 10 |
| Riesgo | low | medium | high |
| Costo/mes AWS | $473.72 | $380.68 | $361.35 |
| Costo/mes GCP | $438.48 | $366.97 | $348.00 |
| Costo/mes Azure | $499.74 | $390.42 | $373.60 |

> **Recomendación:** Escenario **OPTIMIZADO** en AWS con instancias `t3.large`.
> Ejecutar las pruebas de estrés y recalcular el plan una vez se tenga el breakpoint real.
> La optimización #1 (truncar historial a 20 mensajes) puede reducir el costo de Mistral
> en ~25%, bajando el grand total de $380.68 a
> ~$312.16/mes antes de cualquier cambio de infraestructura.