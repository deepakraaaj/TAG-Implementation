# TAG (Table-Augmented Generation) Backend

A robust, modular backend for the TAG chatbot, capable of intelligently routing queries between SQL generation, Vector Search (RAG), and General Chat. Built with FastAPI, LangGraph, and Docker support.

![TAG Architecture](https://img.shields.io/badge/Architecture-TAG-blue)

## 🏗️ Architecture

```mermaid
graph TD
    User[User / Frontend] -->|Query| API[API Gateway / FastAP]
    
    subgraph "TAG Backend System"
        API --> Context[Context & PII Sanitization]
        Context --> Router{Smart Router}
        
        %% SQL Flow
        Router -->|Database Query| SQLNode[SQL Agent]
        SQLNode -->|Get Schema| Schema[Schema Service]
        SQLNode -->|Generate SQL| LLM1[LLM - SQL]
        LLM1 -->|Execute| DB[(MySQL Database)]
        DB -->|Results| SQLResp[SQL Response]
        
        %% Vector Flow
        Router -->|Knowledge Query| VectorNode[RAG Agent]
        VectorNode -->|Search| ES[(Elasticsearch)]
        ES -->|Documents| VectorResp[Synthesized Response]
        
        %% General Flow
        Router -->|Greeting / General| ChatNode[General Chat]
        
        %% Shared Services
        Router -.-> Cache[(Redis Semantic Cache)]
        Context -.-> Cache
    end
    
    SQLResp --> Final[Final Response]
    VectorResp --> Final
    ChatNode --> Final
    Final --> User
```

## 🚀 Features

- **Intelligent Routing**: Classifies intent (SQL vs. Knowledge vs. General) dynamically.
- **SQL Generation**: Safe, read-only SQL execution against MySQL databases.
- **Vector Search**: RAG implementation using Elasticsearch and FastEmbed.
- **Semantic Caching**: Redis-based caching to speed up repeated queries.
- **Dockerized**: Full stack support with Docker Compose.

## 🛠️ Project Structure

```bash
tag_backend/
├── app/
│   ├── main.py            # Entry point
│   ├── config.py          # Configuration
│   ├── services/          # Core Services (Cache, Vector, Schema, etc.)
│   └── workflow/          # LangGraph Workflow Nodes
├── tests/                 # Unit and Integration Tests
├── scripts/               # Utility Scripts (Inspect DB, Index Data)
├── Dockerfile             # Backend Container
└── docker-compose.yml     # Orchestration
```

## 🏁 Getting Started

### Option 1: Docker (Recommended)

1.  **Configure Environment**:
    Ensure your existing database container (`lightning_db` or similar) is running on the `lightningbot_ai_network` network.

2.  **Start the Stack**:
    ```bash
    docker compose up --build -d
    ```
    This spins up `tag_backend`, `tag_redis`, and `tag_es`.

3.  **Run Tests**:
    ```bash
    docker exec -it tag_backend python3 tests/test_history.py
    ```

### Option 2: Local Development

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure `.env`**:
    Update `.env` to point to localhost services:
    ```ini
    DATABASE_URL=mysql+aiomysql://root:Root%4012345@localhost:3309/dev-fitss
    ELASTICSEARCH_URL=http://localhost:9200
    REDIS_URL=redis://localhost:6379
    ```

3.  **Run Server**:
    ```bash
    uvicorn app.main:app --reload --port 8001
    ```

## 🧪 Testing

Run the integration suite to verify all flows:
```bash
python3 -m unittest discover tests
```
