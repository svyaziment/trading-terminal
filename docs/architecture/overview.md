# Architecture Overview

High-level modules:

1. Frontend
   - React
   - TypeScript

2. Backend API
   - Python
   - FastAPI

3. Broker Gateway
   - Tinkoff/T-Bank Invest API client
   - sandbox first

4. Market Data Collector
   - candles
   - order books
   - instrument status

5. Trading Gateway
   - order preview
   - risk check
   - human confirmation
   - order submission

6. Risk Engine
   - limits
   - kill switch
   - order validation

7. Storage
   - PostgreSQL
   - Redis

8. Agent System
   - Orchestrator
   - DevOps
   - Backend Developer
   - QA
   - Security/Risk reviewer
