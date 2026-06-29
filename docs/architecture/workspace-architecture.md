# Workspace Architecture

This document reflects the current workspace as a single platform made of four connected parts:

- `ChatBot-Widget`: embeddable React/Vite chat UI and Docker-served demo shell
- `NL2SQL Assistant`: FastAPI assistant runtime and domain execution engine
- `OpenMetaData`: schema discovery, onboarding, semantic review, and bundle export pipeline
- `config/` and `output/`: shared discovered-source records and generated onboarding artifacts

Current local defaults seen in this workspace:

- default TAG app/domain: `vts` (`Vehicle Tracking System`)
- LLM endpoint: local Ollama-compatible API at `http://127.0.0.1:11434/v1`
- LLM model: `qwen2.5:0.5b`
- semantic retrieval: enabled with `fastembed`
- cache/state store: Redis on `localhost:6384`
- configured tenant DBs: `VTS`, `IMS`, `REMP`

Business-domain note:

- The active TAG runtime defines `vts` as a vehicle tracking and driver management domain.
- Some onboarding output still labels `output/vts/semantic_model.json` as `platform_ops`, but the runtime-facing domain artifacts under `NL2SQL Assistant/app/domains/vts/generated/` are more specific and are the correct source of truth for architecture.

## 1. System Landscape

```mermaid
flowchart LR
  User[End User]
  Browser[Browser or Host App]
  CDN[CDN or Script Tag Host]
  Widget[ChatBot-Widget\nReact + Vite embed]
  Demo[chatbot_demo\nNginx container]
  TAG[NL2SQL Assistant\nFastAPI assistant runtime]
  Redis[(Redis\nsession + cache + idempotency)]
  LLM[(Local LLM API\nOllama-compatible /v1)]
  DBs[(Tenant Databases\nVTS / IMS / FITS)]
  DomainFiles[app/domains/domain_name/\nmanual + generated + semantic_bundle]
  Search[(Semantic Retrieval\nfastembed or Chroma)]

  Admin[Admin / Domain Reviewer]
  OMAPI[OpenMetaData API\nFastAPI + Jinja review UI]
  OMUI[OpenMetaData ui-next\nNext.js onboarding wizard]
  OMCore[OpenMetaData pipeline\nDiscovery + Introspection + Semantics + Artifacts]
  OMSys[(OpenMetadata stack\nServer + Postgres + Elasticsearch + Ingestion)]
  Workspace[config/ + output/\ndiscovered sources and generated artifacts]

  User --> Browser
  Browser --> CDN
  CDN --> Widget
  Browser --> Demo
  Demo -->|/api proxy| TAG
  Widget -->|POST /session/start\nPOST /chat or /query| TAG

  TAG --> Redis
  TAG --> LLM
  TAG --> DBs
  TAG --> DomainFiles
  TAG --> Search
  Search -. indexes and searches .-> DomainFiles

  Admin --> OMUI
  Admin --> OMAPI
  OMUI --> OMAPI
  OMAPI --> OMCore
  OMCore --> DBs
  OMCore --> Workspace
  OMCore --> OMSys

  Workspace -->|publish reviewed bundle| DomainFiles
  OMUI -->|trigger /api/v1/semantic/reindex| TAG
```

## 2. VTS Business Domain View

This is the business-facing shape of the active `vts` domain, based on the current runtime artifacts and manifest joins.

```mermaid
flowchart LR
  Company[(Company / Tenant)]

  subgraph Fleet["VTS Vehicle Domain"]
    Vehicle[Vehicle]
    Trip[Trip]
    Telemetry[VTS Transaction]
    TripMap[Trip VTS Mapping]
    Route[Route]
    Location[Location]
    Users[User and User Location Mapping]
    Comm[Vehicle Trip Communication]
    Exceptions[DMS Exception and VTS Exception]
    Device[Asset / Device / Product Mapping]
  end

  Company --> Vehicle
  Company --> Route
  Company --> Users
  Company --> Device

  Location --> Route
  Location --> Trip
  Route --> Trip
  Vehicle --> Trip

  Vehicle --> Telemetry
  Trip --> Telemetry
  Trip --> TripMap
  Vehicle --> Comm
  Trip --> Comm
  Vehicle --> Exceptions
  Trip --> Exceptions

  Device --> Vehicle
  Users --> Trip
```

Primary runtime interpretation for `vts`:

- core operational record: `trip`
- key business objects: `vehicle`, `trip`, `route`, `location`, `company`, `user`
- tracking/telemetry path: `vehicle` -> `trip` -> `vts_transaction`
- exception path: `trip` and `vehicle` -> `dms_exception` / `vts_exception`
- tenant scoping is heavily driven by `company_id`

## 3. TAG Runtime Request Flow

```mermaid
flowchart TB
  Client[Widget or API Client] --> Start[POST /session/start]
  Client --> Chat[POST /chat or /query]

  subgraph API[TAG FastAPI]
    Start --> Session[Create session_id]
    Chat --> Entry[chat endpoint]
    Entry --> Context[decode x-user-context\nnormalize metadata and trace]
    Context --> Service[ChatService]
  end

  Service --> History[(Redis history)]
  Service --> State[(Redis flow state\npending select\nlast select\nidempotency)]
  Service --> AppRegistry[AppRegistry + apps.local/apps.docker.yaml]
  AppRegistry --> DomainRegistry[DomainRegistry]
  DomainRegistry --> DomainSpec[Domain artifacts\nmanual + generated + manifest + reports]

  Service --> Pregraph{Special handling?}
  Pregraph -->|yes| Flows[FlowEngine\nYAML flow continuation]
  Pregraph -->|yes| Cached[cache replay / pagination /\nsummary / select follow-up]
  Pregraph -->|no| Graph[LangGraph workflow]

  subgraph Workflow[Compiled assistant graph]
    Graph --> Route[router]
    Route --> Intermediate[intermediate]
    Intermediate -->|CHAT| ChatNode[chat node]
    Intermediate -->|REPORT| ReportNode[report node]
    Intermediate -->|SQL| IntentNode[intent node]
    IntentNode --> SQLBuild[sql_build]
    SQLBuild --> Decision{sql_query generated?}
    Decision -->|SKIP| EndNoSQL[end]
    Decision -->|yes| SQLValidate[sql_validate]
    SQLValidate -->|blocked| Respond[respond]
    SQLValidate -->|ok| SQLExecute[sql_execute]
    SQLExecute --> Respond
  end

  Route --> RouterLLM[RouterService + LLM]
  ChatNode --> ChatLLM[Chat LLM]
  IntentNode --> IntentLLM[Intent LLM]
  SQLBuild --> SQLLLM[SQL Builder LLM]
  Respond --> ResponseLLM[Response intelligence LLM]

  SQLBuild --> Semantic[DomainSemanticRetriever]
  Route --> Semantic
  Semantic --> SearchProvider[(fastembed in-memory\nor Chroma persistent store)]
  Semantic --> DomainSpec

  SQLExecute --> DBs[(Active tenant DB)]
  ReportNode --> ReportDB[(Report or audit DB\nwhen configured)]
  Service --> Metrics[MetricsService + /metrics]
  Service --> Audit[AuditService]

  Flows --> Service
  Cached --> Service
  ReportNode --> Service
  EndNoSQL --> Service
  Respond --> Guardrail[guardrail verification]
  ChatNode --> Guardrail
  Guardrail --> Service
  Service --> Result[NDJSON stream or buffered JSON result]
```

## 4. Onboarding, Review, and Publish Flow

```mermaid
flowchart TB
  SourceDB[(Source DB URL\nor env preset)]
  Discovery[Discovery\nscan env + config]
  Intro[Introspection\nschema, tables, columns,\nFKs, joins, row counts]
  Norm[Normalization\ninternal canonical models]
  Sem[Semantic enrichment\nbusiness guesses + ambiguity detection]
  Quest[Questionnaire generation]
  Bundle[Artifact generation\nsemantic bundle + TAG bundle]
  Output[output/source_name/\nJSON + YAML + semantic_bundle]
  Config[config/discovered_sources.*]
  ReviewUI[Jinja review UI]
  NextUI[Next.js onboarding wizard]
  Publish[Publish reviewed bundle]
  TagDomain[TAG app/domains/domain_name/\nsemantic_bundle or generated files]
  Reindex[POST /api/v1/semantic/reindex]

  subgraph OM[OpenMetaData]
    Discovery --> Intro --> Norm --> Sem --> Quest --> Bundle
  end

  SourceDB --> Discovery
  Discovery --> Config
  Bundle --> Output
  Output --> ReviewUI
  Output --> NextUI
  ReviewUI -->|human review| Bundle
  NextUI -->|save answers| Output
  NextUI --> Publish
  Publish --> TagDomain
  Publish --> Reindex
  Reindex -. refresh semantic index for .-> TagDomain
```

## 5. Container and Local Dev Topology

```mermaid
flowchart LR
  subgraph Docker["TAG docker-compose.yml"]
    RedisC[redis container]
    TAGC[tag_backend container]
    DemoC[chatbot_demo container]
    DemoC -->|/api| TAGC
    TAGC --> RedisC
    TAGC -->|host.docker.internal| HostDB[(Host MySQL DBs)]
    TAGC -->|host.docker.internal:11434/v1| HostLLM[(Host local LLM)]
  end

  subgraph Local["OpenMetaData optional stack"]
    OMPostgres[(Postgres)]
    OMES[(Elasticsearch)]
    OMServer[openmetadata/server]
    OMIngest[openmetadata/ingestion]
    OMServer --> OMPostgres
    OMServer --> OMES
    OMServer --> OMIngest
  end
```

## Notes

- The widget can run as an embeddable script bundle or as the `chatbot_demo` Docker service behind Nginx.
- The widget primarily talks to TAG through `POST /session/start` and `POST /chat`; the backend supports both NDJSON streaming and buffered JSON mode.
- TAG routes requests through a LangGraph-based assistant pipeline with `CHAT`, `SQL`, and `REPORT` branches.
- In this workspace, `vts` should be interpreted as a vehicle/fleet tracking domain, not as a generic platform-ops domain.
- Runtime behavior is domain-driven: domain manifests, generated artifacts, manual overrides, reports, flows, glossary, and semantic bundles live under `NL2SQL Assistant/app/domains/<domain_name>/`.
- OpenMetaData is the upstream onboarding pipeline. It discovers database sources, generates semantic artifacts into `output/`, and can publish reviewed bundles back into TAG domain folders.
- Semantic retrieval is enabled in the current `.env` and uses `fastembed` by default, with optional persistent Chroma indexing.
- The OpenMetadata server stack under `OpenMetaData/docker/docker-compose.openmetadata.yml` is optional infrastructure that supports metadata ingestion and search, while the custom FastAPI and Next.js layers handle onboarding and review.
