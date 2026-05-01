"""
_build_capacity_plan.py

Generates capacity_plan.md and capacity_plan.json from:
  1. Observed /health metrics  (real, limited: 8 requests)
  2. service_profile.json      (architectural latency estimates)
  3. Cloud pricing tables       (AWS/GCP/Azure, ~2025 list prices)
  4. Business parameters        (configurable via BUSINESS_* constants)

Run: python _build_capacity_plan.py
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

# ── BUSINESS PARAMETERS (edit before running) ──────────────────────────────────
PEAK_CONCURRENT_USERS   = 50          # X — concurrent users at peak hour
MAX_ACCEPTABLE_P95_MS   = 10_000      # Y — SLA latency ceiling (ms)
MONTHLY_GROWTH_PCT      = 20          # Z — % user growth per month
AVAILABILITY_TARGET_PCT = 99.9        # %
PLANNING_HORIZON_MONTHS = 12

# ── OBSERVED DATA (from GET /health after 8 requests) ─────────────────────────
OBS_TOTAL_REQUESTS = 8
OBS_P95_MS         = 18_228    # real observed (mix of /health + /chat + Tavily)
OBS_P50_MS         = 1.36      # dominated by fast /health calls
OBS_RAM_USED_MB    = 11_882
OBS_RAM_TOTAL_MB   = 16_069
OBS_CPU_CORES      = 16
OBS_CPU_AVG_PCT    = 7.3
OBS_GPU_AVAILABLE  = False

# ── ARCHITECTURAL ESTIMATES (from service_profile.json) ───────────────────────
ARCH_CHAT_P50_NO_SEARCH  = 3_500    # ms
ARCH_CHAT_P95_NO_SEARCH  = 8_000
ARCH_CHAT_P50_SEARCH     = 6_500
ARCH_CHAT_P95_SEARCH     = 14_000
SEARCH_ACTIVATION_RATE   = 0.40     # fraction of /chat requests that invoke Tavily
MISTRAL_TOKENS_PER_S     = 40       # approximate output token generation rate

WEIGHTED_P50 = (
    (1 - SEARCH_ACTIVATION_RATE) * ARCH_CHAT_P50_NO_SEARCH
    + SEARCH_ACTIVATION_RATE * ARCH_CHAT_P50_SEARCH
)
WEIGHTED_P95 = (
    (1 - SEARCH_ACTIVATION_RATE) * ARCH_CHAT_P95_NO_SEARCH
    + SEARCH_ACTIVATION_RATE * ARCH_CHAT_P95_SEARCH
)

# Uvicorn sync threadpool default (anyio): 40 threads
THREADS_PER_WORKER    = 40
# Max concurrent /chat requests before new ones queue
# At WEIGHTED_P95 latency, throughput per replica:
RPS_PER_REPLICA = round(THREADS_PER_WORKER / (WEIGHTED_P95 / 1_000), 2)

# ── USAGE MODEL ────────────────────────────────────────────────────────────────
AVG_REQUESTS_PER_USER_PER_DAY  = 15
AVG_INPUT_TOKENS_PER_REQUEST   = 1_500   # msg + history (grows with sessions)
AVG_OUTPUT_TOKENS_PER_REQUEST  = 800
MONTHLY_REQUESTS               = PEAK_CONCURRENT_USERS * AVG_REQUESTS_PER_USER_PER_DAY * 30

# Effective RPS at peak (assume all users active in a 2h window)
PEAK_RPS = round(PEAK_CONCURRENT_USERS / ((WEIGHTED_P50 / 1_000) + 20), 3)  # 20s think time

# ── CAPACITY CALCULATIONS ──────────────────────────────────────────────────────
MIN_REPLICAS_FOR_SLA      = 2          # N+1 for 99.9%
REPLICAS_FOR_PEAK_LOAD    = math.ceil(PEAK_RPS / RPS_PER_REPLICA)
RECOMMENDED_REPLICAS      = max(MIN_REPLICAS_FOR_SLA, REPLICAS_FOR_PEAK_LOAD + 1)

# Growth projection
def users_at_month(m: int) -> int:
    return round(PEAK_CONCURRENT_USERS * ((1 + MONTHLY_GROWTH_PCT / 100) ** m))

def replicas_at_month(m: int) -> int:
    u = users_at_month(m)
    rps = round(u / ((WEIGHTED_P50 / 1_000) + 20), 3)
    return max(2, math.ceil(rps / RPS_PER_REPLICA) + 1)

# ── INSTANCE SPECS ─────────────────────────────────────────────────────────────
# I/O-bound, no GPU needed. Key constraint: threads need RAM.
# ~50MB/concurrent request * 40 threads + 500MB Python overhead
RAM_PER_REPLICA_GB = 4     # 2GB working + 2GB buffer
VCPU_PER_REPLICA   = 2
STORAGE_GB         = 20

# ── CLOUD PRICING (monthly, on-demand, Linux, ~2025 list prices) ───────────────
PRICING = {
    "aws": {
        "instance":       "t3.large",
        "spec":           "2 vCPU / 8 GB RAM",
        "on_demand":      60.74,    # $/month
        "reserved_1yr":  37.67,    # $/month (38% off)
        "reserved_3yr":  25.55,    # $/month (58% off)
        "spot":           9.11,    # $/month ~85% off (volatile)
        "lb":             18.00,   # ALB base + moderate traffic
    },
    "gcp": {
        "instance":       "e2-standard-2",
        "spec":           "2 vCPU / 8 GB RAM",
        "on_demand":      48.91,
        "reserved_1yr":  32.77,    # CUD 1yr (~33% off)
        "reserved_3yr":  24.46,    # CUD 3yr (~50% off)
        "spot":           7.34,    # preemptible
        "lb":             18.25,   # Cloud Load Balancer
    },
    "azure": {
        "instance":       "D2s_v5",
        "spec":           "2 vCPU / 8 GB RAM",
        "on_demand":      70.08,
        "reserved_1yr":  42.05,    # (~40% off)
        "reserved_3yr":  28.03,    # (~60% off)
        "spot":           14.02,   # ~80% off
        "lb":             16.00,   # Azure Load Balancer
    },
}

FIREBASE_MONTHLY  = 8.00    # Blaze plan at this scale (~ops + storage)
MISTRAL_INPUT_PER_MTK  = 2.00   # $/M input tokens (Mistral Large ~2025)
MISTRAL_OUTPUT_PER_MTK = 6.00   # $/M output tokens
TAVILY_PER_SEARCH = 0.010        # $/search (paid tier)

mistral_monthly = (
    MONTHLY_REQUESTS * AVG_INPUT_TOKENS_PER_REQUEST  / 1_000_000 * MISTRAL_INPUT_PER_MTK
    + MONTHLY_REQUESTS * AVG_OUTPUT_TOKENS_PER_REQUEST / 1_000_000 * MISTRAL_OUTPUT_PER_MTK
)
tavily_monthly = MONTHLY_REQUESTS * SEARCH_ACTIVATION_RATE * TAVILY_PER_SEARCH

# ── SCENARIOS ──────────────────────────────────────────────────────────────────
SCENARIOS = {
    "conservative": {
        "replicas": RECOMMENDED_REPLICAS + 1,
        "pricing_model": "on_demand",
        "autoscale_min": RECOMMENDED_REPLICAS + 1,
        "autoscale_max": (RECOMMENDED_REPLICAS + 1) * 4,
        "autoscale_trigger": "p95_latency > 6000ms OR active_threads > 70%",
        "safety_margin_pct": 100,
        "risk": "low",
        "rationale": "N+2 redundancy, all on-demand, scale at 70% capacity threshold",
    },
    "optimized": {
        "replicas": RECOMMENDED_REPLICAS,
        "pricing_model": "70% reserved_1yr + 30% on_demand",
        "autoscale_min": RECOMMENDED_REPLICAS,
        "autoscale_max": RECOMMENDED_REPLICAS * 4,
        "autoscale_trigger": "p95_latency > 7500ms OR active_threads > 75%",
        "safety_margin_pct": 50,
        "risk": "medium",
        "rationale": "N+1 redundancy, mixed pricing, scale at 75% capacity",
    },
    "aggressive": {
        "replicas": MIN_REPLICAS_FOR_SLA,
        "pricing_model": "50% spot + 50% on_demand",
        "autoscale_min": MIN_REPLICAS_FOR_SLA,
        "autoscale_max": MIN_REPLICAS_FOR_SLA * 5,
        "autoscale_trigger": "p95_latency > 8500ms OR active_threads > 85%",
        "safety_margin_pct": 10,
        "risk": "high",
        "rationale": "Minimum viable replicas, spot instances (eviction risk), late scaling trigger",
    },
}


def scenario_cost(scenario: dict, cloud: str) -> dict:
    pricing = PRICING[cloud]
    n = scenario["replicas"]
    model = scenario["pricing_model"]

    if "spot" in model and "on_demand" in model:
        spot_frac = float(model.split("%")[0]) / 100
        on_dem_frac = 1 - spot_frac
        compute = n * (spot_frac * pricing["spot"] + on_dem_frac * pricing["on_demand"])
    elif "reserved_1yr" in model and "on_demand" in model:
        res_frac = float(model.split("%")[0]) / 100
        on_dem_frac = 1 - res_frac
        compute = n * (res_frac * pricing["reserved_1yr"] + on_dem_frac * pricing["on_demand"])
    elif "reserved_1yr" in model:
        compute = n * pricing["reserved_1yr"]
    elif "on_demand" in model or model == "on_demand":
        compute = n * pricing["on_demand"]
    else:
        compute = n * pricing["on_demand"]

    infra_total = compute + pricing["lb"] + FIREBASE_MONTHLY
    grand_total = infra_total + mistral_monthly + tavily_monthly
    return {
        "compute_monthly": round(compute, 2),
        "lb_monthly": pricing["lb"],
        "firebase_monthly": FIREBASE_MONTHLY,
        "mistral_monthly": round(mistral_monthly, 2),
        "tavily_monthly": round(tavily_monthly, 2),
        "infra_subtotal": round(infra_total, 2),
        "grand_total": round(grand_total, 2),
        "instance_type": pricing["instance"],
        "instance_spec": pricing["spec"],
    }


# ── BUILD DATA STRUCTURE ────────────────────────────────────────────────────────

growth_table = {
    str(m): {
        "users": users_at_month(m),
        "replicas_needed": replicas_at_month(m),
    }
    for m in [1, 2, 3, 6, 9, 12]
}

plan = {
    "meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_sources": [
            "service_profile.json (architectural latency estimates)",
            f"/health endpoint ({OBS_TOTAL_REQUESTS} requests observed)",
            "AWS/GCP/Azure ~2025 list pricing",
        ],
        "warning": (
            "Stress test results directories are empty — no Locust runs executed. "
            "Latency figures are architectural estimates, not measured under load. "
            "Run stress_tests/run_all.ps1 and regenerate this plan for data-driven numbers."
        ),
    },

    "business_parameters": {
        "peak_concurrent_users":    PEAK_CONCURRENT_USERS,
        "max_acceptable_p95_ms":    MAX_ACCEPTABLE_P95_MS,
        "monthly_growth_pct":       MONTHLY_GROWTH_PCT,
        "availability_target_pct":  AVAILABILITY_TARGET_PCT,
        "planning_horizon_months":  PLANNING_HORIZON_MONTHS,
        "avg_requests_per_user_per_day": AVG_REQUESTS_PER_USER_PER_DAY,
        "monthly_requests":         MONTHLY_REQUESTS,
    },

    "performance_profile": {
        "observed": {
            "total_requests_sampled": OBS_TOTAL_REQUESTS,
            "p50_ms": OBS_P50_MS,
            "p95_ms": OBS_P95_MS,
            "note": "p50 dominated by fast /health calls; p95 reflects /chat + Tavily worst-case",
            "ram_used_mb": OBS_RAM_USED_MB,
            "ram_total_mb": OBS_RAM_TOTAL_MB,
            "cpu_cores": OBS_CPU_CORES,
            "cpu_avg_pct_idle": OBS_CPU_AVG_PCT,
            "gpu_available": OBS_GPU_AVAILABLE,
        },
        "architectural_estimates": {
            "chat_p50_no_search_ms": ARCH_CHAT_P50_NO_SEARCH,
            "chat_p95_no_search_ms": ARCH_CHAT_P95_NO_SEARCH,
            "chat_p50_with_search_ms": ARCH_CHAT_P50_SEARCH,
            "chat_p95_with_search_ms": ARCH_CHAT_P95_SEARCH,
            "search_activation_rate": SEARCH_ACTIVATION_RATE,
            "weighted_p50_ms": round(WEIGHTED_P50),
            "weighted_p95_ms": round(WEIGHTED_P95),
        },
        "bottleneck_classification": {
            "primary":   "I/O — Mistral API latency (65% of total response time)",
            "secondary": "I/O — Tavily Search (20% when activated)",
            "tertiary":  "I/O — Firestore read (growing with session history)",
            "cpu_bound": False,
            "gpu_bound": False,
            "memory_bound": False,
            "network_bound": True,
        },
        "capacity_per_replica": {
            "thread_pool_size": THREADS_PER_WORKER,
            "max_rps_theoretical": RPS_PER_REPLICA,
            "max_rps_practical": round(RPS_PER_REPLICA * 0.75, 2),  # 75% efficiency
            "concurrent_requests_at_capacity": THREADS_PER_WORKER,
        },
        "tokens_per_second": {
            "avg_output_tok_per_request": AVG_OUTPUT_TOKENS_PER_REQUEST,
            "daily_total_tokens": MONTHLY_REQUESTS // 30 * (AVG_INPUT_TOKENS_PER_REQUEST + AVG_OUTPUT_TOKENS_PER_REQUEST),
            "avg_tps_at_peak": round(PEAK_RPS * AVG_OUTPUT_TOKENS_PER_REQUEST, 1),
            "max_sustained_tps_per_replica": round(RPS_PER_REPLICA * 0.75 * AVG_OUTPUT_TOKENS_PER_REQUEST, 1),
        },
    },

    "production_projection": {
        "recommended_replicas": RECOMMENDED_REPLICAS,
        "min_replicas_for_availability": MIN_REPLICAS_FOR_SLA,
        "replicas_for_peak_rps": REPLICAS_FOR_PEAK_LOAD,
        "peak_rps": PEAK_RPS,
        "instance_specification": {
            "vcpu": VCPU_PER_REPLICA,
            "ram_gb": RAM_PER_REPLICA_GB,
            "storage_gb": STORAGE_GB,
            "gpu": "none (I/O-bound workload; GPU inference runs on Mistral API servers)",
            "network": "1 Gbps standard",
            "rationale": (
                "2 vCPU sufficient — FastAPI + Python overhead is < 5% CPU under load. "
                f"4 GB RAM = 40 concurrent threads × ~50 MB/request + 500 MB Python baseline. "
                "No local GPU needed — LLM inference delegated to Mistral API."
            ),
        },
        "autoscaling": {
            "metric_primary":   "custom: active_sync_threads_pct > 70%",
            "metric_secondary": "request_queue_depth > 5",
            "metric_tertiary":  "p95_latency_ms > 8000",
            "note": (
                "CPU-based autoscaling is WRONG for this workload (I/O-bound). "
                "Scale on thread saturation or latency degradation. "
                "Use Locust scenario 2 breakpoint to calibrate exact thresholds."
            ),
            "scale_out_cooldown_s": 60,
            "scale_in_cooldown_s": 300,
            "warm_up_period_s": 30,
        },
        "growth_forecast": growth_table,
    },

    "scenarios": {
        name: {
            **cfg,
            "costs": {
                cloud: scenario_cost(cfg, cloud)
                for cloud in ["aws", "gcp", "azure"]
            },
        }
        for name, cfg in SCENARIOS.items()
    },

    "external_api_costs": {
        "mistral_api": {
            "model": "mistral-large-latest",
            "input_price_per_M_tokens": MISTRAL_INPUT_PER_MTK,
            "output_price_per_M_tokens": MISTRAL_OUTPUT_PER_MTK,
            "monthly_input_tokens": MONTHLY_REQUESTS * AVG_INPUT_TOKENS_PER_REQUEST,
            "monthly_output_tokens": MONTHLY_REQUESTS * AVG_OUTPUT_TOKENS_PER_REQUEST,
            "monthly_cost_usd": round(mistral_monthly, 2),
            "note": "Dominates total cost — OPTIMIZE THIS FIRST (caching, history truncation, smaller model for simple queries)",
        },
        "tavily_api": {
            "searches_per_month": round(MONTHLY_REQUESTS * SEARCH_ACTIVATION_RATE),
            "price_per_search_usd": TAVILY_PER_SEARCH,
            "monthly_cost_usd": round(tavily_monthly, 2),
            "note": "Cache search results for identical queries to reduce cost by 20-40%",
        },
        "firebase_firestore": {
            "monthly_reads": MONTHLY_REQUESTS,
            "monthly_writes": MONTHLY_REQUESTS * 2,
            "monthly_cost_usd": FIREBASE_MONTHLY,
            "note": "Blaze plan required above free tier limits at this scale",
        },
    },

    "finops": {
        "recommended_purchase_model": "70% 1-year reserved + 30% on-demand",
        "rationale": (
            "Base load is predictable (reserved gives 38-40% savings). "
            "30% on-demand covers bursts without spot eviction risk. "
            "Avoid 100% spot for LLM chatbot — mid-conversation eviction = bad UX."
        ),
        "scheduling_policy": {
            "is_24x7": True,
            "rationale": "LLM chatbot sessions can start at any time; users expect instant responses",
            "cost_optimization": (
                "If usage analytics show < 5% traffic between 02:00-08:00 UTC, "
                "consider scaling in to min_replicas during that window. "
                "Saves ~25% compute at cost of slower cold-start for early users."
            ),
        },
        "budget_alerts": [
            {"threshold_pct": 50,  "action": "info",    "channel": "email"},
            {"threshold_pct": 80,  "action": "warning",  "channel": "slack + email"},
            {"threshold_pct": 100, "action": "critical", "channel": "pagerduty"},
            {"note": "Set monthly budget = 1.2x optimized scenario grand total"},
        ],
        "critical_metrics_post_deploy": [
            {"metric": "p95_latency_ms",          "threshold": MAX_ACCEPTABLE_P95_MS, "alert": "SLA breach"},
            {"metric": "error_rate_pct",           "threshold": 1.0,                  "alert": "Service degradation"},
            {"metric": "active_threads_pct",       "threshold": 80.0,                 "alert": "Scale-out trigger"},
            {"metric": "mistral_tokens_per_day",   "threshold": MONTHLY_REQUESTS * (AVG_INPUT_TOKENS_PER_REQUEST + AVG_OUTPUT_TOKENS_PER_REQUEST) / 30 * 1.5, "alert": "Cost spike"},
            {"metric": "firestore_read_latency_p95_ms", "threshold": 500,             "alert": "History growth degradation"},
            {"metric": "ram_used_pct",             "threshold": 85.0,                 "alert": "Memory pressure"},
        ],
        "top_3_cost_optimizations": [
            {
                "priority": 1,
                "action": "Implement conversation history truncation (last 20 messages)",
                "current_cost_impact": "High — history grows unbounded, inflating Mistral input tokens",
                "estimated_savings_pct": 25,
                "effort": "low",
                "code_change": "get_session_history() returns history[-20:]",
            },
            {
                "priority": 2,
                "action": "Add Redis cache for Tavily search results (TTL 24h)",
                "current_cost_impact": f"~${round(tavily_monthly, 0)}/month on Tavily API",
                "estimated_savings_pct": 30,
                "effort": "medium",
                "code_change": "Wrap TavilySearchResults with Redis cache by query hash",
            },
            {
                "priority": 3,
                "action": "Route simple queries to mistral-small (3x cheaper)",
                "current_cost_impact": f"~${round(mistral_monthly, 0)}/month all on mistral-large",
                "estimated_savings_pct": 40,
                "effort": "medium",
                "code_change": "Classify query complexity, use mistral-small for 'simple' category",
            },
        ],
    },
}

# ── WRITE JSON ─────────────────────────────────────────────────────────────────
json_path = Path("capacity_plan.json")
json_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Written: {json_path}")

# ── BUILD MARKDOWN ─────────────────────────────────────────────────────────────

def fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.1f}s"
    return f"{ms:.0f}ms"


def fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


md_lines: list[str] = []
A = md_lines.append


def H1(t): A(f"# {t}\n")
def H2(t): A(f"\n## {t}\n")
def H3(t): A(f"\n### {t}\n")
def P(t):  A(f"{t}\n")
def HR():  A("\n---\n")


H1("Capacity Plan — chatbot-infraestructura")
A(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n")
A(f"*Machine-readable data: [`capacity_plan.json`](capacity_plan.json)*\n")

A("> **DATA SOURCE NOTICE:** Los directorios `stress_tests/results/` están vacíos —")
A("> las pruebas de Locust no se ejecutaron aún. Los números de latencia son")
A("> **estimaciones arquitectónicas** de `service_profile.json` más datos observados")
A("> en `/health` (8 requests). Ejecutar `stress_tests/run_all.ps1` y regenerar este")
A("> plan con `python _build_capacity_plan.py` para obtener cifras medidas bajo carga.\n")

HR()

# ── 1. PERFIL DE RENDIMIENTO ──────────────────────────────────────────────────
H2("1. Perfil de Rendimiento Actual")

H3("1.1 Datos observados")
A("| Métrica | Valor | Fuente |")
A("|---|---|---|")
A(f"| Requests muestreados | {OBS_TOTAL_REQUESTS} | `GET /health` endpoint |")
A(f"| p50 latencia (mixta) | {fmt_ms(OBS_P50_MS)} | Dominado por `/health` (2ms) |")
A(f"| p95 latencia (mixta) | {fmt_ms(OBS_P95_MS)} | Refleja `/chat` + Tavily worst-case |")
A(f"| RAM del host | {OBS_RAM_USED_MB:,} MB / {OBS_RAM_TOTAL_MB:,} MB ({OBS_RAM_USED_MB/OBS_RAM_TOTAL_MB*100:.0f}%) | psutil |")
A(f"| CPU cores (host) | {OBS_CPU_CORES} cores, avg {OBS_CPU_AVG_PCT}% | psutil (idle) |")
A(f"| GPU | {'Disponible' if OBS_GPU_AVAILABLE else 'No disponible'} | pynvml |")
A("")

H3("1.2 Throughput máximo sostenible (estimado arquitectónico)")
A("| Métrica | Sin búsqueda (60%) | Con búsqueda (40%) | Ponderado |")
A("|---|---|---|---|")
A(f"| p50 latencia `/chat` | {fmt_ms(ARCH_CHAT_P50_NO_SEARCH)} | {fmt_ms(ARCH_CHAT_P50_SEARCH)} | {fmt_ms(WEIGHTED_P50)} |")
A(f"| p95 latencia `/chat` | {fmt_ms(ARCH_CHAT_P95_NO_SEARCH)} | {fmt_ms(ARCH_CHAT_P95_SEARCH)} | {fmt_ms(WEIGHTED_P95)} |")
A(f"| RPS por réplica (teórico) | {THREADS_PER_WORKER/(ARCH_CHAT_P95_NO_SEARCH/1000):.2f} | {THREADS_PER_WORKER/(ARCH_CHAT_P95_SEARCH/1000):.2f} | **{RPS_PER_REPLICA}** |")
A(f"| RPS por réplica (práctico, 75%) | — | — | **{round(RPS_PER_REPLICA*0.75,2)}** |")
A("")
P("*Cálculo: threadpool de uvicorn = 40 threads. Throughput = threads / latencia_p95. "
  "La eficiencia práctica es 75% del teórico por overhead de serialización, autenticación Firebase y scheduling.*")

H3("1.3 Clasificación del cuello de botella")
A("```")
A("PROFILING DEL REQUEST /chat (path crítico con búsqueda)")
A("")
A("Step                         Latencia    % total    Tipo")
A("─────────────────────────────────────────────────────────")
A("Firebase token verify          80-350ms      3%     I/O (Google Auth)")
A("Firestore READ (historial)      50-200ms      2%     I/O (crece con historial)")
A("Mistral API — 1ª llamada     2000-6000ms     42%     I/O (GPU en Mistral servers)")
A("Tavily Search API            1500-4000ms     23%     I/O (web crawl)")
A("Mistral API — 2ª llamada     1500-5000ms     28%     I/O (GPU en Mistral servers)")
A("Firestore WRITE x2            160-600ms       2%     I/O")
A("")
A("VEREDITO: 100% I/O-bound. El cuello de botella es la latencia de Mistral API.")
A("          Agregar más CPU o RAM al servidor NO mejora el throughput.")
A("          La única forma de escalar es: más réplicas | caché | modelo más rápido.")
A("```")

H3("1.4 Tokens por segundo")
peak_tps = round(PEAK_RPS * AVG_OUTPUT_TOKENS_PER_REQUEST, 1)
max_tps  = round(RPS_PER_REPLICA * 0.75 * AVG_OUTPUT_TOKENS_PER_REQUEST, 1)
A(f"| Métrica | Valor |")
A(f"|---|---|")
A(f"| Tokens output promedio por request | {AVG_OUTPUT_TOKENS_PER_REQUEST} |")
A(f"| RPS pico estimado ({PEAK_CONCURRENT_USERS} usuarios) | {PEAK_RPS} req/s |")
A(f"| Tokens/segundo en pico | **{peak_tps} tok/s** |")
A(f"| Tokens/segundo máx por réplica (75% efficiency) | **{max_tps} tok/s** |")
A(f"| Total tokens mensuales | {MONTHLY_REQUESTS*(AVG_INPUT_TOKENS_PER_REQUEST+AVG_OUTPUT_TOKENS_PER_REQUEST):,} |")

HR()

# ── 2. PROYECCIÓN PRODUCCIÓN ──────────────────────────────────────────────────
H2("2. Proyección para Producción")

H3("2.1 Parámetros de negocio")
A(f"| Parámetro | Valor usado | Variable |")
A(f"|---|---|---|")
A(f"| Usuarios concurrentes en hora pico | **{PEAK_CONCURRENT_USERS}** | `X` |")
A(f"| Latencia p95 máxima aceptable | **{fmt_ms(MAX_ACCEPTABLE_P95_MS)}** | `Y` |")
A(f"| Crecimiento mensual esperado | **{MONTHLY_GROWTH_PCT}%** | `Z` |")
A(f"| Disponibilidad requerida | **{AVAILABILITY_TARGET_PCT}%** | — |")
A(f"| Requests/mes estimados | {MONTHLY_REQUESTS:,} | — |")
A("")
P("*Para recalcular con otros valores, editar las constantes al inicio de `_build_capacity_plan.py` y ejecutar `python _build_capacity_plan.py`.*")

H3("2.2 Número mínimo de réplicas")
A(f"| Criterio | Réplicas |")
A(f"|---|---|")
A(f"| Disponibilidad 99.9% (N+1) | {MIN_REPLICAS_FOR_SLA} |")
A(f"| Carga pico {PEAK_CONCURRENT_USERS} usuarios ({PEAK_RPS} rps) | {REPLICAS_FOR_PEAK_LOAD} |")
A(f"| **Recomendado (N+1 + buffer 33%)** | **{RECOMMENDED_REPLICAS}** |")

H3("2.3 Especificación por réplica")
A(f"| Recurso | Valor | Justificación |")
A(f"|---|---|---|")
A(f"| vCPU | {VCPU_PER_REPLICA} | FastAPI I/O-bound, < 5% CPU bajo carga |")
A(f"| RAM | {RAM_PER_REPLICA_GB} GB | 40 threads × 50 MB/req + 500 MB Python overhead |")
A(f"| Almacenamiento | {STORAGE_GB} GB | OS + packages + logs (sin almacenamiento de datos) |")
A(f"| GPU | **Ninguna** | LLM inference en Mistral cloud — NO se necesita GPU local |")
A(f"| Red | 1 Gbps estándar | Payloads JSON pequeños (<50 KB por request) |")

H3("2.4 Configuración de auto-scaling")
A("```yaml")
A("# NO usar CPU-based autoscaling (workload es I/O-bound, CPU siempre baja)")
A("# Escalar en: saturación de threads O latencia degradada")
A("")
A("trigger_scale_out:")
A("  - metric: active_sync_threads_pct")
A("    threshold: 70%")
A("    evaluation_periods: 2")
A("    period_seconds: 60")
A("  - metric: p95_latency_ms")
A("    threshold: 8000")
A("    evaluation_periods: 3")
A("    period_seconds: 60")
A("")
A(f"min_replicas: {RECOMMENDED_REPLICAS}")
A(f"max_replicas: {RECOMMENDED_REPLICAS * 4}")
A("scale_out_cooldown: 60s")
A("scale_in_cooldown:  300s")
A("warm_up_period:     30s   # LangChain lazy singleton init")
A("```")

H3("2.5 Proyección de crecimiento")
A("| Mes | Usuarios | Réplicas necesarias | Costo estimado (AWS optimizado) |")
A("|---|---|---|---|")
for m_str, g in growth_table.items():
    m = int(m_str)
    n_replicas = g["replicas_needed"]
    aws_p    = PRICING["aws"]
    res_frac = 0.70
    cost = (n_replicas * (res_frac * aws_p["reserved_1yr"] + (1 - res_frac) * aws_p["on_demand"])
            + aws_p["lb"] + FIREBASE_MONTHLY + mistral_monthly + tavily_monthly)
    A(f"| Mes {m:2d} | {g['users']:,} usuarios | {n_replicas} réplicas | {fmt_usd(cost)}/mes |")

HR()

# ── 3. ESCENARIOS ─────────────────────────────────────────────────────────────
H2("3. Escenarios de Infraestructura")

for scen_name, scen in plan["scenarios"].items():
    emoji = {"conservative": "🛡️", "optimized": "⚖️", "aggressive": "⚡"}[scen_name]
    label = {"conservative": "CONSERVADOR", "optimized": "OPTIMIZADO", "aggressive": "AGRESIVO"}[scen_name]
    H3(f"3.{list(SCENARIOS).index(scen_name)+1} {emoji} {label}")
    A(f"**{scen['rationale']}**\n")

    A(f"| Parámetro | Valor |")
    A(f"|---|---|")
    A(f"| Réplicas base | {scen['replicas']} |")
    A(f"| Modelo de compra | {scen['pricing_model']} |")
    A(f"| Autoscaling | min={scen['autoscale_min']}, max={scen['autoscale_max']} |")
    A(f"| Trigger de escala | `{scen['autoscale_trigger']}` |")
    A(f"| Margen de seguridad | {scen['safety_margin_pct']}% |")
    A(f"| Riesgo operativo | **{scen['risk'].upper()}** |")
    A("")

    A("**Costo mensual por proveedor:**\n")
    A("| Proveedor | Instancia | Compute | LB | Firebase | Mistral | Tavily | **TOTAL** |")
    A("|---|---|---|---|---|---|---|---|")
    for cloud, costs in scen["costs"].items():
        A(f"| {cloud.upper()} | {costs['instance_type']} {costs['instance_spec']} "
          f"| {fmt_usd(costs['compute_monthly'])} "
          f"| {fmt_usd(costs['lb_monthly'])} "
          f"| {fmt_usd(costs['firebase_monthly'])} "
          f"| {fmt_usd(costs['mistral_monthly'])} "
          f"| {fmt_usd(costs['tavily_monthly'])} "
          f"| **{fmt_usd(costs['grand_total'])}** |")
    A("")

HR()

# ── 4. FINOPS ─────────────────────────────────────────────────────────────────
H2("4. Recomendaciones FinOps")

H3("4.1 Modelo de compra óptimo")
A("```")
A("RECOMENDACIÓN: 70% 1-year Reserved + 30% On-Demand")
A("")
A("Razón:")
A("  ✓ La carga base es predecible → Reserved da 38-40% descuento sobre base")
A("  ✓ 30% On-Demand cubre bursts sin riesgo de evicción de Spot")
A("  ✗ NO usar 100% Spot → evicción durante conversación activa = UX terrible")
A("  ✗ NO usar 100% On-Demand → costo innecesariamente alto en carga base")
A("")
A("Ahorro vs full On-Demand (escenario optimizado, AWS):")
opt_aws_costs = plan["scenarios"]["optimized"]["costs"]["aws"]
A(f"  On-Demand total: ~{fmt_usd(opt_aws_costs['grand_total'] / 0.70 * 1.0)}/mes")
A(f"  Optimizado:       {fmt_usd(opt_aws_costs['grand_total'])}/mes")
A("```")

H3("4.2 Política de scheduling")
A("> El servicio debe ser **24/7** — las sesiones de chat pueden iniciarse en cualquier zona horaria.")
A("> Sin embargo, si el análisis de uso muestra < 5% de tráfico entre 02:00–08:00 UTC:")
A("> ")
A("> - Configurar scale-in a `min_replicas = 1` durante esa ventana")
A("> - Potencial ahorro: ~25% del costo de compute (6h/24h = 25%)")
A("> - Trade-off: primera request en el día puede tardar +30s por cold start del AgentExecutor")

H3("4.3 Alertas de presupuesto")
A("| Umbral | Acción | Canal |")
A("|---|---|---|")
optimized_total = plan["scenarios"]["optimized"]["costs"]["aws"]["grand_total"]
A(f"| {fmt_usd(optimized_total*0.5)}/mes (50%) | Info — revisar tendencia | Email |")
A(f"| {fmt_usd(optimized_total*0.8)}/mes (80%) | Warning — investigar spike | Slack + Email |")
A(f"| {fmt_usd(optimized_total*1.0)}/mes (100%) | Critical — alertar equipo | PagerDuty |")
A(f"| {fmt_usd(optimized_total*1.2)}/mes (120%) | Emergency — suspender tests/bots | SMS + PagerDuty |")
A("")
A(f"> Budget mensual recomendado para alertas: **{fmt_usd(optimized_total * 1.2)}** (1.2× escenario optimizado)\n")

H3("4.4 Métricas críticas post-despliegue")
A("| Métrica | Threshold | Severidad | Por qué |")
A("|---|---|---|---|")
for m in plan["finops"]["critical_metrics_post_deploy"]:
    thresh = m["threshold"]
    val = f"{thresh:.0f}" if isinstance(thresh, float) else str(thresh)
    A(f"| `{m['metric']}` | > {val} | {m['alert']} | — |")

H3("4.5 Top 3 optimizaciones de costo (ordenadas por ROI)")
for opt in plan["finops"]["top_3_cost_optimizations"]:
    A(f"**#{opt['priority']} — {opt['action']}**\n")
    A(f"- Impacto actual: {opt['current_cost_impact']}")
    A(f"- Ahorro estimado: **{opt['estimated_savings_pct']}%**")
    A(f"- Esfuerzo: {opt['effort']}")
    A(f"- Cambio de código: `{opt['code_change']}`\n")

HR()

# ── SUMMARY TABLE ──────────────────────────────────────────────────────────────
H2("Resumen Ejecutivo")
A("| | 🛡️ Conservador | ⚖️ Optimizado | ⚡ Agresivo |")
A("|---|---|---|---|")
for field, label in [
    ("replicas", "Réplicas base"),
    ("safety_margin_pct", "Margen de seguridad"),
    ("risk", "Riesgo"),
]:
    row = [plan["scenarios"][s][field] for s in ["conservative", "optimized", "aggressive"]]
    A(f"| {label} | {row[0]} | {row[1]} | {row[2]} |")

A("| Costo/mes AWS | "
  + " | ".join(fmt_usd(plan["scenarios"][s]["costs"]["aws"]["grand_total"]) for s in plan["scenarios"])
  + " |")
A("| Costo/mes GCP | "
  + " | ".join(fmt_usd(plan["scenarios"][s]["costs"]["gcp"]["grand_total"]) for s in plan["scenarios"])
  + " |")
A("| Costo/mes Azure | "
  + " | ".join(fmt_usd(plan["scenarios"][s]["costs"]["azure"]["grand_total"]) for s in plan["scenarios"])
  + " |")
A("")

A("> **Recomendación:** Escenario **OPTIMIZADO** en AWS con instancias `t3.large`.")
A("> Ejecutar las pruebas de estrés y recalcular el plan una vez se tenga el breakpoint real.")
A(f"> La optimización #1 (truncar historial a 20 mensajes) puede reducir el costo de Mistral")
A(f"> en ~25%, bajando el grand total de {fmt_usd(opt_aws_costs['grand_total'])} a")
A(f"> ~{fmt_usd(opt_aws_costs['grand_total'] * 0.82)}/mes antes de cualquier cambio de infraestructura.")

# ── WRITE MARKDOWN ─────────────────────────────────────────────────────────────
md_path = Path("capacity_plan.md")
md_path.write_text("\n".join(md_lines), encoding="utf-8")
print(f"Written: {md_path}")
print(f"\nBusiness parameters used:")
print(f"  X = {PEAK_CONCURRENT_USERS} concurrent users at peak")
print(f"  Y = {MAX_ACCEPTABLE_P95_MS}ms p95 SLA")
print(f"  Z = {MONTHLY_GROWTH_PCT}% monthly growth")
print(f"\nTo recalculate: edit constants at top of _build_capacity_plan.py and re-run.")
