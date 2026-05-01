#!/usr/bin/env python3
"""
generate_test_payloads.py

Generates two JSONL datasets for stress-testing the POST /chat endpoint:

  test_payloads.jsonl       — 1 000 requests (60/25/10/5 distribution)
  test_payloads_burst.jsonl —   200 requests  (burst peak: complex + extreme)

Each line is a valid JSON object with the full /chat payload plus correlation
fields (expected_category, estimated_tokens_in, index).

Token estimate: 1 token ≈ 4 characters (GPT/Mistral tokenizer heuristic).
Target sizes:
  simple   < 100 tok  → < 400 chars
  medium   100-500     → 400-2 000 chars
  complex  500-2 000   → 2 000-8 000 chars
  extreme  > 2 000     → > 8 000 chars
"""

from __future__ import annotations

import json
import random
import textwrap
from pathlib import Path

RNG = random.Random(42)   # fixed seed → reproducible dataset

# ── output paths ──────────────────────────────────────────────────────────────
OUT_NORMAL = Path("test_payloads.jsonl")
OUT_BURST  = Path("test_payloads_burst.jsonl")

# ── helpers ───────────────────────────────────────────────────────────────────

def tok(text: str) -> int:
    return max(1, len(text) // 4)

def entry(message: str, category: str, idx: int, prefix: str = "load") -> dict:
    return {
        "message": message,
        "session_id": f"test-{prefix}-{idx:04d}",
        "expected_category": category,
        "estimated_tokens_in": tok(message),
        "index": idx,
    }

def pick(*seq):
    return RNG.choice(seq)

def picks(seq, k=1):
    return RNG.sample(list(seq), k)

def rint(a, b):
    return RNG.randint(a, b)

def indent(text: str, spaces: int = 2) -> str:
    return textwrap.indent(textwrap.dedent(text).strip(), " " * spaces)

# ── vocabulary ────────────────────────────────────────────────────────────────

AWS  = ["EC2", "S3", "RDS", "Lambda", "EKS", "ECS", "Fargate", "CloudFront",
        "Route 53", "VPC", "IAM", "SQS", "SNS", "DynamoDB", "ElastiCache",
        "CloudWatch", "CloudTrail", "Config", "WAF", "Cognito", "API Gateway",
        "Step Functions", "EventBridge", "Kinesis", "Redshift", "Aurora",
        "Secrets Manager", "ACM", "ALB", "Transit Gateway", "CodePipeline",
        "CodeBuild", "CodeDeploy", "ECR", "Batch", "Glue", "EMR", "Athena"]

GCP  = ["GKE", "Cloud Run", "Cloud Functions", "BigQuery", "Cloud SQL",
        "Firestore", "Cloud Storage", "Pub/Sub", "Dataflow", "Dataproc",
        "Cloud Armor", "Cloud Load Balancing", "Cloud Build", "Artifact Registry",
        "Cloud Monitoring", "Cloud Logging", "Secret Manager", "Cloud KMS",
        "Memorystore", "Spanner", "Bigtable", "Vertex AI", "Apigee"]

AZURE = ["AKS", "Azure Functions", "App Service", "Azure SQL", "Cosmos DB",
         "Blob Storage", "Service Bus", "Event Hub", "Azure Monitor",
         "Log Analytics", "Azure DevOps", "Container Registry", "Key Vault",
         "Virtual Network", "Application Gateway", "Front Door",
         "Azure Cache for Redis", "Azure Databricks", "Synapse Analytics",
         "Azure AD", "Managed Identity"]

K8S  = ["HorizontalPodAutoscaler", "VerticalPodAutoscaler", "StatefulSet",
        "DaemonSet", "Deployment", "Ingress", "NetworkPolicy", "RBAC",
        "PersistentVolume", "ConfigMap", "ResourceQuota", "LimitRange",
        "PodDisruptionBudget", "Taints y Tolerations", "Node Affinity",
        "Init Containers", "Sidecar", "Helm", "Kustomize", "ArgoCD",
        "cert-manager", "external-dns", "Karpenter", "Istio", "Cilium"]

TF   = ["módulos", "state remoto", "workspaces", "providers", "data sources",
        "locals", "for_each", "count", "dynamic blocks", "depends_on",
        "lifecycle", "import", "terragrunt", "Terraform Cloud", "atlantis",
        "checkov", "tfsec", "drift detection", "moved block", "refactoring"]

CICD = ["GitHub Actions", "GitLab CI/CD", "Jenkins", "CircleCI", "ArgoCD",
        "Flux CD", "Tekton", "Drone CI", "Spinnaker", "Concourse"]

OBS  = ["Prometheus", "Grafana", "Alertmanager", "Loki", "Tempo", "Jaeger",
        "OpenTelemetry", "Datadog", "New Relic", "Dynatrace", "ELK Stack",
        "Fluentd", "Fluent Bit", "Victoria Metrics", "Thanos"]

SEC  = ["mTLS", "OPA/Gatekeeper", "Falco", "Trivy", "Snyk", "Vault",
        "SOPS", "sealed-secrets", "RBAC", "Pod Security Standards",
        "Network Policies", "IRSA", "Workload Identity", "SBOM"]

CLOUD_ALL = AWS + GCP + AZURE

ENVS    = ["producción", "staging", "desarrollo", "QA", "pre-producción"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
           "sa-east-1", "eu-central-1", "ap-northeast-1"]
LANGS   = ["Python", "Go", "Node.js", "Java", "Rust", "TypeScript"]
DBS     = ["PostgreSQL", "MySQL", "MongoDB", "Redis", "Cassandra", "ClickHouse"]
SIZES   = ["10", "50", "100", "500", "1000", "5000"]
NODES   = ["3", "5", "10", "20", "50"]

# ── SIMPLE generators (< 400 chars) ──────────────────────────────────────────

def _simple() -> str:
    templates = [
        lambda: f"¿Cuál es la diferencia entre {pick(*AWS)} y {pick(*AWS)}?",
        lambda: f"¿Cómo configuro un bucket de {pick('S3','Cloud Storage','Blob Storage')} como estático website?",
        lambda: f"¿Qué es {pick(*K8S)} en Kubernetes y cuándo usarlo?",
        lambda: f"¿Cuál es la mejor práctica para gestionar secretos en {pick('AWS','GCP','Azure')}?",
        lambda: f"¿Cómo escalo horizontalmente un {pick('Deployment','StatefulSet','DaemonSet')} en Kubernetes?",
        lambda: f"Explícame brevemente qué hace {pick(*TF)} en Terraform.",
        lambda: f"¿Qué comando de kubectl uso para ver los logs de un pod que crashea?",
        lambda: f"¿Cuándo usar {pick(*AWS)} en lugar de {pick(*AWS)}?",
        lambda: f"¿Cómo funciona el garbage collection de imágenes en {pick(*CICD)}?",
        lambda: f"¿Qué es un Service Mesh y para qué sirve?",
        lambda: f"¿Diferencia entre RollingUpdate y Recreate en un Deployment de Kubernetes?",
        lambda: f"¿Cómo habilito HTTPS en {pick('ALB','Application Gateway','Cloud Load Balancing')}?",
        lambda: f"¿Qué significa OOMKilled en un pod de Kubernetes?",
        lambda: f"¿Cuál es el comando para hacer rollback de un Helm release?",
        lambda: f"¿Cómo configuro un cronjob en Kubernetes?",
        lambda: f"¿Qué es GitOps y cuáles son sus ventajas frente a CI/CD tradicional?",
        lambda: f"¿Cómo funciona el autoscaling en {pick('EKS','GKE','AKS')}?",
        lambda: f"¿Diferencia entre ConfigMap y Secret en Kubernetes?",
        lambda: f"¿Qué es un PodDisruptionBudget y cuándo necesito uno?",
        lambda: f"¿Cómo verifico si un certificado TLS está próximo a vencer en {pick('ACM','cert-manager')}?",
        lambda: f"¿Qué son los taints y tolerations en Kubernetes?",
        lambda: f"¿Cómo hago un terraform plan solo para un recurso específico?",
        lambda: f"¿Cuál es la diferencia entre terraform destroy y terraform apply -destroy?",
        lambda: f"¿Cómo configuro retención de logs en {pick('CloudWatch','Cloud Logging','Log Analytics')}?",
        lambda: f"¿Qué es un Sidecar container y cuándo tiene sentido usarlo?",
        lambda: f"¿Cómo funciona {pick(*OBS)} para monitoreo de Kubernetes?",
        lambda: f"¿Cuál es el límite de tamaño de un Secret en Kubernetes y cómo lo rodeo?",
        lambda: f"¿Cómo expongo un servicio interno de Kubernetes al exterior sin Ingress?",
        lambda: f"¿Diferencia entre ECS y EKS en AWS?",
        lambda: f"¿Cómo veo el consumo de recursos por namespace en Kubernetes?",
        lambda: f"¿Qué es Karpenter y en qué se diferencia del cluster-autoscaler?",
        lambda: f"¿Cómo funciona el health check de {pick('ALB','NLB','Cloud Load Balancing')}?",
        lambda: f"¿Cuál es la diferencia entre ReplicaSet y Deployment?",
        lambda: f"¿Qué es IRSA en AWS EKS y por qué es mejor que los node IAM roles?",
        lambda: f"¿Cómo hago debug de un pod en estado Pending en Kubernetes?",
        lambda: f"¿Qué es un Ingress Controller y cuáles son los más usados?",
        lambda: f"¿Cuándo usar {pick('SQS','Pub/Sub','Service Bus')} vs {pick('Kinesis','Dataflow','Event Hub')}?",
        lambda: f"¿Cómo configuro multi-region en {pick('DynamoDB','Spanner','Cosmos DB')}?",
        lambda: f"¿Qué es un Service Account en Kubernetes y cómo se diferencia del IAM?",
        lambda: f"¿Cómo funciona el rate limiting en {pick('API Gateway','Apigee','Azure API Management')}?",
        lambda: f"¿Qué son los Network Policies en Kubernetes y cómo los aplico?",
        lambda: f"¿Diferencia entre Liveness y Readiness probe en Kubernetes?",
        lambda: f"¿Cómo optimizo el costo de {pick(*AWS)} en {pick(*ENVS)}?",
        lambda: f"¿Qué es Helm y cuándo lo usaría en lugar de kubectl apply directo?",
        lambda: f"¿Cómo funciona el DNS interno de Kubernetes?",
        lambda: f"¿Qué herramienta uso para detectar misconfiguraciones en Terraform?",
        lambda: f"¿Cómo hago que un pod solo corra en nodos con GPU en Kubernetes?",
        lambda: f"¿Cuál es la diferencia entre un LoadBalancer Service y un NodePort?",
        lambda: f"¿Qué es Workload Identity en GKE?",
        lambda: f"¿Cómo configuro alertas de costo en {pick('AWS','GCP','Azure')}?",
    ]
    return RNG.choice(templates)()

# ── MEDIUM generators (400-2000 chars) ───────────────────────────────────────

def _medium() -> str:
    templates = [
        lambda: f"""
            Estoy migrando una aplicación monolítica en {pick(*LANGS)} a microservicios
            en {pick('EKS','GKE','AKS')}. El monolito tiene {rint(5,50)} módulos acoplados
            y usa {pick(*DBS)} como base de datos. El equipo tiene {rint(2,10)} devs y
            queremos hacer la migración de forma incremental en {rint(3,12)} meses.

            ¿Cuál es la estrategia recomendada para extraer los servicios sin romper
            producción? ¿Usarías strangler fig pattern o big bang? ¿Cómo gestiono
            las transacciones distribuidas cuando parto la base de datos?
        """,
        lambda: f"""
            Tenemos un cluster de {pick('EKS','GKE','AKS')} en {pick(*REGIONS)} con
            {pick(*NODES)} nodos m5.xlarge. En las últimas semanas notamos que durante
            el horario pico ({rint(9,12)}:00-{rint(14,18)}:00 UTC) el cluster llega al
            80% de CPU y algunos pods quedan en Pending porque no hay recursos.

            Actualmente tenemos cluster-autoscaler configurado con min={rint(2,5)},
            max={rint(10,20)}. El scale-up tarda unos {rint(3,8)} minutos lo que es
            demasiado para nuestra SLA.

            ¿Cómo puedo reducir el tiempo de scale-up? ¿Vale la pena migrar a Karpenter?
            ¿Debería usar Overprovisioning? Explícame las opciones con sus trade-offs.
        """,
        lambda: f"""
            Necesito diseñar la estrategia de disaster recovery para nuestra plataforma
            en {pick('AWS','GCP','Azure')}. Tenemos:
            - {pick(*DBS)} con {rint(100,5000)} GB de datos
            - {rint(5,30)} microservicios en Kubernetes
            - Assets estáticos en {pick('S3','Cloud Storage','Blob Storage')}
            - RPO objetivo: {pick('15 minutos','1 hora','4 horas')}
            - RTO objetivo: {pick('30 minutos','2 horas','4 horas')}
            - Budget mensual para DR: ~${rint(500,5000)}

            ¿Qué arquitectura de DR me recomiendas (warm standby, pilot light, active-active)?
            ¿Cómo implemento los backups cross-region? ¿Cómo testeo el DR sin afectar producción?
        """,
        lambda: f"""
            Implementamos {pick(*CICD)} como pipeline de CI/CD. El pipeline actual tarda
            {rint(15,45)} minutos en completarse: build ({rint(5,15)}min), tests ({rint(5,20)}min),
            push a registry ({rint(2,5)}min), deploy ({rint(3,8)}min).

            El equipo se queja de que el feedback loop es muy lento. ¿Qué estrategias
            de optimización me recomiendas para bajarlo a menos de {rint(8,15)} minutos?
            ¿Cómo puedo paralelizar los stages? ¿Qué tests puedo correr solo en el merge
            a main y cuáles en cada PR?
        """,
        lambda: f"""
            Necesito implementar una estrategia de gestión de secretos para {rint(5,20)}
            microservicios en Kubernetes. Actualmente los secretos están en ConfigMaps
            en texto plano (lo sé, está mal). Los servicios corren en {pick('EKS','GKE','AKS')}.

            Estoy evaluando:
            1. Kubernetes Secrets + KMS encryption at rest
            2. HashiCorp Vault con Agent Injector
            3. External Secrets Operator con {pick('AWS Secrets Manager','GCP Secret Manager','Azure Key Vault')}
            4. SOPS + Sealed Secrets

            ¿Cuáles son los trade-offs de cada opción? ¿Cuál recomiendas para un equipo
            de {rint(5,30)} personas sin experiencia previa en Vault?
        """,
        lambda: f"""
            Tenemos una API en {pick(*LANGS)} que procesa {pick(*SIZES)}k requests/día
            con picos de hasta {pick(*SIZES)} rps. La latencia p99 actual es de
            {rint(200,2000)}ms y queremos llevarla a menos de {rint(50,150)}ms.

            Stack actual:
            - {pick('EC2','Cloud Run','App Service')} con {rint(2,10)} instancias
            - {pick(*DBS)} sin read replicas
            - Sin caché
            - {pick(*OBS)} para monitoreo

            ¿Dónde debería empezar a optimizar? ¿Vale la pena migrar a {pick('Lambda','Cloud Functions','Azure Functions')}
            para aprovechar el autoscaling? ¿Qué patrones de caché me recomiendas?
        """,
        lambda: f"""
            Necesito configurar observabilidad completa para {rint(10,50)} microservicios
            en Kubernetes. Quiero métricas, logs y traces correlacionados. Actualmente no
            tenemos nada y estamos en {pick('AWS','GCP','Azure')}.

            ¿Me puedes ayudar a diseñar el stack de observabilidad? Estoy considerando
            {pick('Prometheus+Grafana','Datadog','New Relic')} para métricas,
            {pick('Loki','ELK Stack','Cloud Logging')} para logs,
            y {pick('Jaeger','Tempo','Datadog APM')} para traces.

            ¿Cómo implemento trace context propagation entre servicios? ¿Cómo correlaciono
            un trace ID con los logs en {pick('Loki','ELK Stack')}?
        """,
        lambda: f"""
            Migramos de una base de datos monolítica {pick(*DBS)} a {pick('DynamoDB','Firestore','Cosmos DB')}.
            El esquema actual tiene {rint(50,200)} tablas, {rint(5,50)} GB de datos y
            {rint(10,100)} queries complejos con múltiples JOINs.

            El mayor problema: {rint(15,40)} de esos queries tienen más de 3 JOINs y
            algunos usan window functions. El equipo no tiene experiencia con bases de datos NoSQL.

            ¿Cómo modelo las relaciones en NoSQL? ¿Qué queries simplemente no pueden
            migrar y qué alternativas tengo? ¿Cómo hago la migración con zero downtime?
        """,
        lambda: f"""
            Implementamos {pick(*SEC)} para seguridad en nuestro cluster de Kubernetes
            pero seguimos teniendo findings en nuestros escaneos de seguridad con {pick('Falco','Trivy','Snyk')}.

            Los principales problemas son:
            - {rint(5,30)} imágenes con CVEs críticos en producción
            - Pods corriendo como root
            - Capabilities no necesarias habilitadas
            - Secrets con acceso excesivo

            ¿Cuál es el orden correcto para atacar estos problemas sin romper producción?
            ¿Cómo implemento Pod Security Standards de forma gradual?
        """,
        lambda: f"""
            Tengo que reducir el costo de infraestructura en {pick('AWS','GCP','Azure')} en
            un {rint(20,40)}% sin degradar la disponibilidad. El gasto actual es ~${rint(5000,50000)}/mes.

            Los principales costos son:
            - Compute ({rint(40,60)}% del total): {pick('EC2','GKE nodes','AKS nodes')}
            - Storage ({rint(10,20)}%): {pick('S3','Cloud Storage','Blob Storage')} y {pick(*DBS)}
            - Transfer ({rint(10,15)}%): egress cross-region y CDN
            - Managed services ({rint(10,20)}%): {pick(*AWS)}, {pick(*AWS)}

            ¿Por dónde empiezo? ¿Qué herramienta uso para análisis de costos?
            ¿Cómo evalúo Reserved Instances vs Spot vs On-demand para cada workload?
        """,
        lambda: f"""
            Necesito implementar multi-tenancy en nuestra plataforma SaaS que corre
            en Kubernetes. Tenemos {rint(10,500)} clientes con requisitos de aislamiento
            diferentes: algunos aceptan shared pods, otros requieren namespaces dedicados,
            y {rint(2,10)} clientes enterprise necesitan clusters dedicados.

            ¿Cómo modelo esto en Kubernetes? ¿Uso un cluster por tenant, namespace por
            tenant o algo intermedio? ¿Cómo gestiono el networking isolation con
            NetworkPolicies? ¿Cómo escala esto a {rint(1000,5000)} tenants?
        """,
        lambda: f"""
            Estoy evaluando mover nuestro data pipeline de {pick('batch','streaming')} de
            on-premise a {pick('AWS','GCP','Azure')}. Procesamos {rint(100,10000)} GB/día
            de eventos en {pick('Kafka','RabbitMQ','ActiveMQ')}.

            El pipeline actual usa {pick('Spark','Flink','Storm')} y tiene una latencia
            de {pick('minutos','horas')} end-to-end. Necesitamos reducirla a {pick('segundos','sub-minuto')}.

            ¿Qué servicio gestionado de {pick('AWS','GCP','Azure')} me recomiendas?
            ¿Cómo migro sin downtime? ¿Cuál es el costo estimado comparado con
            on-premise?
        """,
        lambda: f"""
            Configuramos {pick(*CICD)} pero el equipo de seguridad nos pide implementar
            supply chain security completa. Necesitamos:
            - Firma de imágenes (cosign/sigstore)
            - SBOM generación y verificación
            - Análisis de vulnerabilidades antes del deploy
            - Policy enforcement en el cluster

            ¿Cómo integro esto en el pipeline sin que tarde más de {rint(3,8)} minutos?
            ¿Qué política de severidades bloqueantes vs warnings recomiendas para empezar?
        """,
        lambda: f"""
            Tenemos problemas de performance en {pick(*DBS)} en producción. Las queries
            más lentas tardan hasta {rint(5,30)} segundos y afectan a los usuarios.
            Tenemos {rint(100,1000)} transacciones/segundo en hora pico.

            El DBA identificó que el problema principal es N+1 queries desde el ORM
            ({pick('SQLAlchemy','Hibernate','Prisma','GORM')}), falta de índices en
            {rint(5,20)} tablas, y una query de reporting que hace full table scan
            sobre {rint(10,500)} millones de filas.

            ¿Cuál es el orden de optimización? ¿Cuándo tiene sentido agregar una
            read replica? ¿Cómo implemento query result caching con {pick('Redis','ElastiCache','Memorystore')}?
        """,
        lambda: f"""
            Necesito diseñar un sistema de feature flags para {rint(10,50)} microservicios.
            Queremos hacer canary deployments donde el {rint(1,10)}% del tráfico va a la
            nueva versión y poder hacer rollback en menos de 30 segundos.

            Estamos en {pick('EKS','GKE','AKS')} con {pick('Istio','Linkerd','Cilium')} como
            service mesh. ¿Uso el traffic splitting del service mesh o implemento algo
            en la aplicación? ¿Qué herramienta de feature flags me recomiendas
            ({pick('LaunchDarkly','Unleash','Flagsmith','Flipt')})?
        """,
    ]
    msg = RNG.choice(templates)()
    # clean up indentation
    lines = [l.strip() for l in msg.strip().splitlines()]
    return "\n".join(l for l in lines if l)

# ── COMPLEX generators (2000-8000 chars) ─────────────────────────────────────

def _tf_snippet(service: str, env: str, region: str) -> str:
    cidr = f"10.{rint(0,254)}.0.0/16"
    return f"""
terraform {{
  required_version = ">= 1.5"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
  backend "s3" {{
    bucket         = "tfstate-{env}-{rint(1000,9999)}"
    key            = "{service}/terraform.tfstate"
    region         = "{region}"
    encrypt        = true
    dynamodb_table = "tf-lock-{env}"
  }}
}}

provider "aws" {{
  region = "{region}"
  default_tags {{
    tags = {{
      Environment = "{env}"
      Service     = "{service}"
      ManagedBy   = "terraform"
    }}
  }}
}}

module "vpc" {{
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.1.2"

  name = "{service}-{env}-vpc"
  cidr = "{cidr}"

  azs             = ["{region}a", "{region}b", "{region}c"]
  private_subnets = ["10.{rint(0,254)}.1.0/24", "10.{rint(0,254)}.2.0/24", "10.{rint(0,254)}.3.0/24"]
  public_subnets  = ["10.{rint(0,254)}.101.0/24", "10.{rint(0,254)}.102.0/24", "10.{rint(0,254)}.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = {"false" if env == "producción" else "true"}
  enable_dns_hostnames = true

  tags = {{
    "kubernetes.io/cluster/{service}-{env}" = "shared"
  }}
}}

module "eks" {{
  source  = "terraform-aws-modules/eks/aws"
  version = "20.0.0"

  cluster_name    = "{service}-{env}"
  cluster_version = "1.{rint(28,31)}"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {{
    general = {{
      min_size       = {rint(1,3)}
      max_size       = {rint(5,20)}
      desired_size   = {rint(2,6)}
      instance_types = ["{pick('m5.large','m5.xlarge','m5.2xlarge','c5.xlarge','t3.xlarge')}"]

      labels = {{
        role = "general"
      }}
    }}

    spot = {{
      min_size       = 0
      max_size       = {rint(10,30)}
      desired_size   = {rint(2,8)}
      instance_types = ["{pick('m5.xlarge','m5.2xlarge','c5.2xlarge')}",
                        "{pick('m4.xlarge','m4.2xlarge','c4.xlarge')}"]
      capacity_type  = "SPOT"
    }}
  }}

  cluster_addons = {{
    coredns = {{
      most_recent = true
    }}
    kube-proxy = {{
      most_recent = true
    }}
    vpc-cni = {{
      most_recent = true
    }}
    aws-ebs-csi-driver = {{
      most_recent              = true
      service_account_role_arn = module.ebs_csi_irsa_role.iam_role_arn
    }}
  }}
}}
""".strip()


def _k8s_snippet(app: str, replicas: int, cpu: str, mem: str) -> str:
    port = rint(3000, 9000)
    return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app}
  namespace: {pick("default","production","services","platform")}
  labels:
    app: {app}
    version: v{rint(1,5)}.{rint(0,20)}.{rint(0,10)}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {app}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: {app}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "{port}"
    spec:
      serviceAccountName: {app}-sa
      securityContext:
        runAsNonRoot: true
        runAsUser: {rint(1000,65535)}
        fsGroup: {rint(1000,65535)}
      containers:
        - name: {app}
          image: registry.example.com/{app}:v{rint(1,5)}.{rint(0,20)}.{rint(0,10)}
          ports:
            - containerPort: {port}
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: {app}-secrets
                  key: database_url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: {app}-secrets
                  key: redis_url
            - name: LOG_LEVEL
              value: {pick("info","debug","warn")}
          resources:
            requests:
              cpu: {cpu}
              memory: {mem}
            limits:
              cpu: {str(int(cpu.replace('m','')) * 2)}m
              memory: {str(int(mem.replace('Gi','').replace('Mi','')) * 2)}{"Gi" if "Gi" in mem else "Mi"}
          livenessProbe:
            httpGet:
              path: /healthz
              port: {port}
            initialDelaySeconds: {rint(10,30)}
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /ready
              port: {port}
            initialDelaySeconds: {rint(5,15)}
            periodSeconds: 5
            failureThreshold: 3
          volumeMounts:
            - name: config
              mountPath: /app/config
              readOnly: true
      volumes:
        - name: config
          configMap:
            name: {app}-config
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: {app}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {app}-pdb
spec:
  minAvailable: {max(1, replicas - 1)}
  selector:
    matchLabels:
      app: {app}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {app}-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {app}
  minReplicas: {max(2, replicas // 2)}
  maxReplicas: {replicas * 4}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {rint(60,80)}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {rint(70,85)}
""".strip()


def _error_log(service: str) -> str:
    err_types = [
        f"OOMKilled: container {service} exceeded memory limit {rint(256,2048)}Mi",
        f"CrashLoopBackOff: back-off {rint(1,5)}m restarting failed container",
        f"Error from server (Timeout): etcd leader changed",
        f"Failed to pull image: rpc error: code = Unknown, ImagePullBackOff",
        f"Liveness probe failed: HTTP probe failed with statuscode: {pick(500,502,503)}",
        f"0/10 nodes are available: {rint(1,5)} Insufficient cpu, {rint(1,5)} Insufficient memory",
    ]
    ts_base = f"2026-04-{rint(1,30):02d}T{rint(0,23):02d}:{rint(0,59):02d}:{rint(0,59):02d}Z"
    lines = []
    for i in range(rint(8, 15)):
        lines.append(f"[{ts_base}] {pick(*err_types)}")
    return "\n".join(lines)


def _complex() -> str:
    app   = pick("api-gateway","payment-service","auth-service","order-service",
                 "notification-svc","inventory-api","reporting-service","ml-inference")
    env   = pick(*ENVS)
    reg   = pick(*REGIONS)
    cpu   = pick("100m","250m","500m","1000m")
    mem   = pick("128Mi","256Mi","512Mi","1024Mi")
    rep   = rint(2, 10)

    scenarios = [
        lambda: f"""
Revisa esta configuración de Terraform para un cluster EKS en {env} y dime:
1. ¿Hay problemas de seguridad?
2. ¿El sizing de los node groups es correcto para {rint(50,500)} pods?
3. ¿Falta algo crítico (addons, logging, monitoring)?
4. ¿Cómo optimizaría los costos manteniendo la HA?

```hcl
{_tf_snippet(app, env, reg)}
```

Contexto: es un cluster nuevo que va a correr {rint(10,50)} microservicios en {pick(*LANGS)}.
Budget de infraestructura: ~${rint(3000,15000)}/mes. Necesitamos PCI-DSS compliance.
        """,
        lambda: f"""
Tengo el siguiente manifiesto de Kubernetes para el servicio {app} y estoy
viendo CrashLoopBackOff en {env}. Estos son los últimos logs:

```
{_error_log(app)}
```

Y este es el deployment actual:

```yaml
{_k8s_snippet(app, rep, cpu, mem)}
```

¿Qué está causando el CrashLoopBackOff? ¿El resource request de {cpu} CPU y {mem}
es suficiente para este tipo de servicio? ¿Hay algún problema con las probes?
¿Qué cambios haría al manifiesto para que sea production-ready?
        """,
        lambda: f"""
Necesito hacer una migración compleja en {env}:
- De: {pick(*AWS)} en {pick(*REGIONS)} (versión legacy, {rint(3,8)} años de deuda técnica)
- A: {pick(*AWS)} con arquitectura moderna en {reg}
- Sin downtime (SLA 99.95%)
- {rint(100,5000)} GB de datos
- {rint(10,200)} millones de usuarios activos

Aquí está el estado actual del Terraform que gestiona el recurso origen:

```hcl
{_tf_snippet(app, env, reg)}
```

¿Cuál es el plan de migración paso a paso? ¿Cómo testeo cada fase?
¿Cómo hago el cutover final en menos de {rint(5,30)} minutos?
¿Qué rollback plan tengo si algo falla en el paso final?
        """,
        lambda: f"""
Tenemos una alerta crítica en producción. El servicio {app} lleva {rint(15,120)} minutos
degradado con errores 5xx al {rint(20,80)}%. Estos son los síntomas:

Logs del pod:
```
{_error_log(app)}
```

Estado del cluster:
```bash
$ kubectl get pods -n production | grep {app}
{app}-7d9f8b-xxxxx   0/1   CrashLoopBackOff   {rint(5,50)}   {rint(10,120)}m
{app}-7d9f8b-yyyyy   1/1   Running            0              {rint(1,10)}h
{app}-7d9f8b-zzzzz   0/1   OOMKilled          {rint(3,30)}   {rint(5,60)}m

$ kubectl top nodes
NAME              CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%
node-{rint(1,10)}   {rint(1000,3900)}m        {rint(70,99)}%   {rint(10,28)}Gi           {rint(70,99)}%
```

Métricas de {pick(*OBS)}:
- p99 latency: {rint(2000,10000)}ms (baseline {rint(50,200)}ms)
- Error rate: {rint(20,80)}%
- Memory: {rint(80,99)}% de {rint(512,4096)}Mi limit

¿Cómo triaggio esto? ¿Cuál es el orden de acciones para restaurar el servicio
lo antes posible? ¿Qué análisis root cause haría una vez estabilizado?
        """,
    ]
    msg = RNG.choice(scenarios)()
    lines = [l.rstrip() for l in msg.strip().splitlines()]
    return "\n".join(lines)


# ── EXTREME generators (> 8000 chars) ────────────────────────────────────────

def _github_actions_pipeline(app: str, lang: str) -> str:
    return f"""
name: CI/CD Pipeline — {app}

on:
  push:
    branches: [main, develop]
    paths:
      - 'services/{app}/**'
      - '.github/workflows/{app}.yml'
  pull_request:
    branches: [main]
    paths:
      - 'services/{app}/**'

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: myorg/{app}
  CLUSTER_NAME: prod-eks-{rint(1,5)}
  AWS_REGION: {pick(*REGIONS)}

jobs:
  lint-and-test:
    name: Lint & Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up {lang}
        uses: {'actions/setup-python@v5' if lang == 'Python' else 'actions/setup-go@v5' if lang == 'Go' else 'actions/setup-node@v4'}
        with:
          {'python-version: "3.12"' if lang == 'Python' else 'go-version: "1.22"' if lang == 'Go' else 'node-version: "20"'}
          cache: {'pip' if lang == 'Python' else 'go' if lang == 'Go' else 'npm'}

      - name: Install dependencies
        run: |
          {'pip install -r requirements.txt' if lang == 'Python' else 'go mod download' if lang == 'Go' else 'npm ci'}

      - name: Run linter
        run: |
          {'ruff check . && mypy .' if lang == 'Python' else 'golangci-lint run ./...' if lang == 'Go' else 'npm run lint'}

      - name: Run unit tests
        run: |
          {'pytest tests/unit/ -v --cov=. --cov-report=xml' if lang == 'Python' else 'go test ./... -race -coverprofile=coverage.out' if lang == 'Go' else 'npm run test:unit -- --coverage'}

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          token: ${{{{ secrets.CODECOV_TOKEN }}}}

  security-scan:
    name: Security Scanning
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4

      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scan-ref: 'services/{app}'
          format: 'sarif'
          output: 'trivy-results.sarif'
          severity: 'CRITICAL,HIGH'
          exit-code: '1'

      - name: Run Semgrep
        uses: returntocorp/semgrep-action@v1
        with:
          config: >-
            p/security-audit
            p/{lang.lower()}
            p/owasp-top-ten

      - name: Dependency audit
        run: |
          {'pip-audit --requirement requirements.txt' if lang == 'Python' else 'govulncheck ./...' if lang == 'Go' else 'npm audit --audit-level=high'}

      - name: Generate SBOM
        uses: anchore/sbom-action@v0
        with:
          path: services/{app}
          format: spdx-json

  build-and-push:
    name: Build & Push Image
    runs-on: ubuntu-latest
    needs: [lint-and-test, security-scan]
    permissions:
      contents: read
      packages: write
    outputs:
      image-digest: ${{{{ steps.build.outputs.digest }}}}
      image-tag: ${{{{ steps.meta.outputs.tags }}}}
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{{{ env.REGISTRY }}}}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}
          tags: |
            type=sha,prefix={{{{branch}}}}-
            type=semver,pattern={{{{version}}}}
            type=raw,value=latest,enable=${{{{ github.ref == 'refs/heads/main' }}}}

      - name: Build and push
        id: build
        uses: docker/build-push-action@v5
        with:
          context: services/{app}
          push: true
          tags: ${{{{ steps.meta.outputs.tags }}}}
          labels: ${{{{ steps.meta.outputs.labels }}}}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true
          sbom: true

      - name: Sign image with cosign
        env:
          COSIGN_EXPERIMENTAL: 1
        run: |
          cosign sign --yes ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}@${{{{ steps.build.outputs.digest }}}}

  scan-image:
    name: Scan Container Image
    runs-on: ubuntu-latest
    needs: build-and-push
    steps:
      - name: Run Trivy on built image
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ${{{{ env.REGISTRY }}}}/${{{{ env.IMAGE_NAME }}}}@${{{{ needs.build-and-push.outputs.image-digest }}}}
          format: 'sarif'
          exit-code: '1'
          severity: 'CRITICAL'

  deploy-staging:
    name: Deploy to Staging
    runs-on: ubuntu-latest
    needs: scan-image
    environment: staging
    if: github.ref == 'refs/heads/develop'
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{{{ secrets.AWS_ROLE_ARN_STAGING }}}}
          aws-region: ${{{{ env.AWS_REGION }}}}

      - name: Update kubeconfig
        run: aws eks update-kubeconfig --name ${{{{ env.CLUSTER_NAME }}}}-staging --region ${{{{ env.AWS_REGION }}}}

      - name: Deploy with Helm
        run: |
          helm upgrade --install {app} charts/{app} \\
            --namespace staging \\
            --create-namespace \\
            --set image.tag=${{{{ needs.build-and-push.outputs.image-tag }}}} \\
            --set image.digest=${{{{ needs.build-and-push.outputs.image-digest }}}} \\
            --values charts/{app}/values-staging.yaml \\
            --atomic \\
            --timeout 5m \\
            --wait

      - name: Run smoke tests
        run: |
          kubectl run smoke-test --image=curlimages/curl --restart=Never --rm -i \\
            -- curl -f http://{app}.staging.svc.cluster.local/healthz

  deploy-production:
    name: Deploy to Production
    runs-on: ubuntu-latest
    needs: [scan-image, deploy-staging]
    environment: production
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{{{ secrets.AWS_ROLE_ARN_PROD }}}}
          aws-region: ${{{{ env.AWS_REGION }}}}

      - name: Update kubeconfig
        run: aws eks update-kubeconfig --name ${{{{ env.CLUSTER_NAME }}}} --region ${{{{ env.AWS_REGION }}}}

      - name: Canary deploy ({rint(5,20)}% traffic)
        run: |
          helm upgrade --install {app}-canary charts/{app} \\
            --namespace production \\
            --set image.tag=${{{{ needs.build-and-push.outputs.image-tag }}}} \\
            --set replicaCount=1 \\
            --values charts/{app}/values-production.yaml \\
            --values charts/{app}/values-canary.yaml \\
            --atomic --timeout 5m

      - name: Monitor canary for 5 minutes
        run: |
          sleep 300
          ERROR_RATE=$(kubectl exec -n monitoring deploy/prometheus \\
            -- promtool query instant 'rate(http_requests_total{{status=~"5..",service="{app}-canary"}}[5m])')
          if (( $(echo "$ERROR_RATE > 0.01" | bc -l) )); then
            echo "Canary error rate too high: $ERROR_RATE"
            helm rollback {app}-canary
            exit 1
          fi

      - name: Full production deploy
        run: |
          helm upgrade --install {app} charts/{app} \\
            --namespace production \\
            --set image.tag=${{{{ needs.build-and-push.outputs.image-tag }}}} \\
            --values charts/{app}/values-production.yaml \\
            --atomic --timeout 10m \\
            --wait

      - name: Delete canary
        if: always()
        run: helm uninstall {app}-canary -n production || true
""".strip()


def _prometheus_rules(app: str) -> str:
    return f"""
groups:
  - name: {app}.rules
    interval: {rint(15,60)}s
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(http_requests_total{{service="{app}",status=~"5.."}}[5m]))
          /
          sum(rate(http_requests_total{{service="{app}"}}[5m])) > 0.01
        for: {rint(2,5)}m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "High error rate on {{{{ $labels.service }}}}"
          description: "Error rate is {{{{ $value | humanizePercentage }}}} for {app}"
          runbook: "https://runbooks.internal/{app}/high-error-rate"

      - alert: HighLatency
        expr: |
          histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{app}"}}[5m])) by (le))
          > {rint(1,5)}
        for: {rint(3,8)}m
        labels:
          severity: warning
        annotations:
          summary: "High p99 latency on {app}"
          description: "p99 latency is {{{{ $value | humanizeDuration }}}}"

      - alert: PodCrashLooping
        expr: |
          increase(kube_pod_container_status_restarts_total{{
            namespace="production", pod=~"{app}-.*"
          }}[15m]) > {rint(3,8)}
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Pod {{{{ $labels.pod }}}} is crash looping"

      - alert: MemoryPressure
        expr: |
          container_memory_working_set_bytes{{pod=~"{app}-.*"}}
          /
          container_spec_memory_limit_bytes{{pod=~"{app}-.*"}} > 0.{rint(80,95)}
        for: {rint(5,15)}m
        labels:
          severity: warning
        annotations:
          summary: "Memory pressure on {app}"
          description: "Container using {{{{ $value | humanizePercentage }}}} of memory limit"

      - alert: HpaMaxedOut
        expr: |
          kube_horizontalpodautoscaler_status_current_replicas{{
            horizontalpodautoscaler="{app}-hpa"
          }} == kube_horizontalpodautoscaler_spec_max_replicas{{
            horizontalpodautoscaler="{app}-hpa"
          }}
        for: {rint(10,30)}m
        labels:
          severity: warning
        annotations:
          summary: "HPA {app} is at maximum replicas"
          description: "Consider increasing maxReplicas or optimizing resource usage"
""".strip()


def _extreme() -> str:
    app  = pick("payment-gateway","api-platform","data-pipeline","ml-serving",
                "auth-service","event-processor","reporting-engine","notification-hub")
    env  = pick(*ENVS)
    reg  = pick(*REGIONS)
    lang = pick(*LANGS)

    scenarios = [
        lambda: f"""
Necesito una revisión completa de nuestro pipeline de CI/CD para el servicio {app}
escrito en {lang}. Llevamos {rint(6,24)} meses con esta configuración y queremos
mejorar: tiempo de pipeline actual {rint(25,60)} minutos, queremos bajar a {rint(8,15)} minutos.

Aquí está el workflow completo de GitHub Actions:

```yaml
{_github_actions_pipeline(app, lang)}
```

Y aquí están las alertas de Prometheus que tenemos configuradas:

```yaml
{_prometheus_rules(app)}
```

Y el manifiesto de Kubernetes del servicio:

```yaml
{_k8s_snippet(app, rint(3,10), pick('250m','500m','1000m'), pick('256Mi','512Mi','1Gi'))}
```

Preguntas específicas:
1. ¿Cómo paralelizo los jobs para reducir el tiempo total a menos de {rint(8,15)} minutos?
2. ¿Hay pasos innecesarios o que puedo mover a un schedule nocturno?
3. ¿Las alertas de Prometheus están bien configuradas o hay false positives/negatives?
4. ¿El canary deployment del {rint(5,20)}% es suficiente o debería hacer progressive delivery?
5. ¿Cómo integro tests de carga automáticos (k6/locust) como quality gate antes de producción?
6. El manifiesto de K8s tiene algún problema de seguridad o performance que debería corregir?
7. ¿Cómo implemento rollback automático si el error rate sube durante el deploy?
        """,
        lambda: f"""
Análisis de incidente post-mortem: el servicio {app} estuvo caído {rint(45,240)} minutos
en producción el {rint(1,28):02d}/04/2026. Impacto: {rint(1000,100000)} usuarios afectados,
pérdida estimada de ${rint(10000,500000)}.

Timeline del incidente:
```
{_error_log(app)}
{_error_log(app)}
{_error_log(app)}
```

Infraestructura afectada:
```yaml
{_k8s_snippet(app, rint(3,10), pick('250m','500m'), pick('256Mi','512Mi'))}
```

Terraform state antes del incidente:
```hcl
{_tf_snippet(app, env, reg)}
```

Alertas que se dispararon (demasiado tarde):
```yaml
{_prometheus_rules(app)}
```

Necesito:
1. Análisis root cause completo basado en los logs y métricas
2. Timeline detallada de qué falló y por qué
3. ¿Las alertas estaban mal configuradas? ¿Por qué tardaron en disparar?
4. Lista de acciones correctivas priorizada (quick wins vs largo plazo)
5. ¿Cómo evito que esto vuelva a pasar?
6. Template de comunicación para stakeholders (técnico y ejecutivo)
7. Cambios específicos al Terraform, K8s manifests y reglas de Prometheus
   para prevenir el mismo incidente
        """,
        lambda: f"""
Arquitectura completa para una plataforma SaaS B2B en {pick('AWS','GCP','Azure')}.
Requisitos:

**Funcionales:**
- {rint(100,10000)} clientes empresariales con SLA 99.{rint(9,99)}%
- Multi-tenancy con aislamiento de datos (PCI-DSS + SOC2 + GDPR)
- API REST + GraphQL + WebSockets en tiempo real
- ML inference con modelos {pick('PyTorch','TensorFlow','ONNX')} (<{rint(50,200)}ms p99)
- Data warehouse para analytics de clientes ({rint(1,100)} TB/mes)
- CI/CD con rollback automático < {rint(30,120)} segundos

**No funcionales:**
- Peak: {rint(50,500)}k rpm, baseline {rint(5,50)}k rpm
- Latencia API: p50 < {rint(20,100)}ms, p99 < {rint(200,500)}ms
- RPO: {rint(1,15)} minutos, RTO: {rint(5,30)} minutos
- Budget: ${rint(30000,200000)}/mes

**Infraestructura actual a migrar:**
```hcl
{_tf_snippet(app, env, reg)}
```

**Deployment actual a modernizar:**
```yaml
{_k8s_snippet(app, rint(5,15), pick('500m','1000m'), pick('512Mi','1Gi','2Gi'))}
```

**Monitoring actual:**
```yaml
{_prometheus_rules(app)}
```

Necesito:
1. Diagrama de arquitectura completo (texto/ASCII) con todos los componentes
2. Decisiones de diseño para multi-tenancy (namespace vs cluster isolation)
3. Estrategia de datos: cómo separo el data plane por tenant con {pick('PostgreSQL','MySQL')} + {pick('Redis','Memcached')}
4. Cómo sirvo ML inference con GPUs en Kubernetes con autoscaling basado en cola
5. Pipeline de CI/CD completo con GitHub Actions + ArgoCD (GitOps)
6. Observabilidad: instrumentación de {rint(20,60)} microservicios con OpenTelemetry
7. Security posture: implementación de zero-trust en el cluster
8. Cost optimization: cómo bajo el compute al 40% del budget con Spot + Reserved
9. DR plan multi-region activo-activo para la base de datos
10. Roadmap de migración de la arquitectura actual a la nueva en {rint(6,18)} meses
        """,
    ]
    msg = RNG.choice(scenarios)()
    lines = [l.rstrip() for l in msg.strip().splitlines()]
    return "\n".join(lines)


# ── generators dispatch ────────────────────────────────────────────────────────

GENERATORS = {
    "simple":  _simple,
    "medium":  _medium,
    "complex": _complex,
    "extreme": _extreme,
}

# ── validate token ranges ─────────────────────────────────────────────────────

EXPECTED_RANGES = {
    "simple":  (1,   400),
    "medium":  (400, 2000),
    "complex": (2000, 8000),
    "extreme": (8000, 999999),
}

def _generate_one(category: str, attempt_limit: int = 30) -> str:
    lo, hi = EXPECTED_RANGES[category]
    for _ in range(attempt_limit):
        msg = GENERATORS[category]()
        chars = len(msg)
        if lo <= chars < hi:
            return msg
    # Return last attempt even if out of range (avoids infinite loop)
    return msg


# ── main ──────────────────────────────────────────────────────────────────────

def build_dataset(n: int, distribution: dict[str, float], prefix: str) -> list[dict]:
    entries = []
    idx = 0
    for category, frac in distribution.items():
        count = round(n * frac)
        for _ in range(count):
            msg = _generate_one(category)
            entries.append(entry(msg, category, idx, prefix))
            idx += 1
    RNG.shuffle(entries)
    # Re-index after shuffle
    for new_idx, e in enumerate(entries):
        e["index"] = new_idx
    return entries


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"  Written {len(records)} records -> {path}")


def print_stats(records: list[dict]) -> None:
    from collections import Counter
    cats = Counter(r["expected_category"] for r in records)
    for cat in ["simple", "medium", "complex", "extreme"]:
        count  = cats.get(cat, 0)
        tokens = [r["estimated_tokens_in"] for r in records if r["expected_category"] == cat]
        if tokens:
            avg = sum(tokens) / len(tokens)
            mn, mx = min(tokens), max(tokens)
            print(f"    {cat:8s}: {count:4d} records | tokens avg={avg:6.0f} min={mn:5d} max={mx:6d}")


if __name__ == "__main__":
    print("Generating test_payloads.jsonl (1 000 requests)…")
    normal = build_dataset(
        n=1000,
        distribution={"simple": 0.60, "medium": 0.25, "complex": 0.10, "extreme": 0.05},
        prefix="load",
    )
    print_stats(normal)
    write_jsonl(OUT_NORMAL, normal)

    print("\nGenerating test_payloads_burst.jsonl (200 burst requests)…")
    burst = build_dataset(
        n=200,
        distribution={"complex": 0.60, "extreme": 0.40},
        prefix="burst",
    )
    print_stats(burst)
    write_jsonl(OUT_BURST, burst)

    print("\nDone.")
    print(f"  {OUT_NORMAL}  -> {OUT_NORMAL.stat().st_size / 1024:.1f} KB")
    print(f"  {OUT_BURST}   -> {OUT_BURST.stat().st_size / 1024:.1f} KB")
    print("\nSample entries:")
    for cat in ["simple", "medium", "complex", "extreme"]:
        sample = next(r for r in normal if r["expected_category"] == cat)
        preview = sample["message"][:120].replace("\n", " ")
        print(f"  [{cat}] ~{sample['estimated_tokens_in']} tok | {preview}…")
