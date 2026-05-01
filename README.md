# Chatbot Infraestructura

Asistente conversacional full-stack construido con **FastAPI** (backend), **Mistral AI** como LLM, **Tavily** para búsqueda en tiempo real y **Firebase Firestore** para persistencia de conversaciones. El frontend es HTML/CSS/JS puro, sin frameworks adicionales.

## Estructura del proyecto

```
chatbot-infraestructura/
├── backend/
│   ├── main.py               # Servidor FastAPI y rutas
│   ├── agent.py              # Lógica del agente (Mistral + Tavily)
│   ├── auth.py               # Verificación de tokens Bearer
│   ├── firestore_service.py  # Lectura/escritura en Firestore
│   ├── requirements.txt      # Dependencias Python
│   └── .env.example          # Variables de entorno requeridas
├── frontend/
│   ├── index.html            # Página de inicio
│   ├── chat.html             # Interfaz del chat
│   ├── app.js                # Lógica del cliente
│   └── style.css             # Estilos
├── service-account.json      # Credenciales Firebase (NO subir a git)
├── .gitignore
└── README.md
```

## Requisitos previos

- Python 3.10+
- Cuenta en [Mistral AI](https://mistral.ai/) con API key
- Cuenta en [Tavily](https://tavily.com/) con API key
- Proyecto Firebase con Firestore habilitado y archivo `service-account.json`

## Instalación y ejecución

### 1. Clonar el repositorio

```bash
git clone <url-del-repo>
cd chatbot-infraestructura
```

### 2. Configurar variables de entorno

```bash
cp backend/.env.example backend/.env
# Editar backend/.env con tus claves reales
```

### 3. Instalar dependencias

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Iniciar el servidor

```bash
uvicorn main:app --reload
```

El servidor estará disponible en `http://localhost:8000`.

### 5. Abrir el frontend

Abre `frontend/index.html` en tu navegador (o sírvelo con cualquier servidor estático).

## Variables de entorno

| Variable                      | Descripción                                        |
|-------------------------------|----------------------------------------------------|
| `MISTRAL_API_KEY`             | API key de Mistral AI                              |
| `TAVILY_API_KEY`              | API key de Tavily Search                           |
| `FIREBASE_PROJECT_ID`         | ID del proyecto en Firebase                        |
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta al archivo `service-account.json`          |
