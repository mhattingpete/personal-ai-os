# Personal AI Operating System

## Vision

An app that runs locally on your computer, but whose state and storage can be teleported anywhere. It connects to all your data—email, Git, Notion, internal company systems, whatever matters to you. It can execute LLM-generated code safely in sandboxes. It has an LLM at the core, but the model is not the product. The system is.

This is the IDE and CLI for non-developers. Everyone can express their intent, wire systems together, and automate real work. Everyone becomes a builder.

---

## Core Feature Set

### Core Runtime
- Local daemon that runs persistently, lightweight and invisible
- SQLite or similar for state (portable, no server, battle-tested)
- Sync layer for state teleportation: encrypted, conflict-resolved, works across devices
- Model-agnostic LLM interface: local (Ollama), cloud (Claude API), or hybrid based on task sensitivity/complexity

### Universal Data Plane
- MCP-style connector architecture but with a unified abstraction layer
- Connectors for the obvious: Gmail, Calendar, GitHub, Notion, Slack, file system
- Local semantic index: embeddings over your connected data, updated incrementally
- Schema inference: understand the shape of your data without configuration
- Query interface that works across sources ("my emails about the Kuatro website project")

### Sandbox Execution
- Container or WASM-based isolation for generated code
- Filesystem, network, and time isolation by default
- Explicit capability grants: "this automation can read my calendar and write to Notion"
- Resource limits and timeouts
- Persistent scratch space per automation

### Intent Layer
- Natural language to workflow translation
- Decomposition: break intent into steps, show the plan, get approval
- Memory of past intents: "do that thing I set up for expense reports"
- Refinement loop: "not quite, only on weekdays"
- Templates and examples: learn from what others have built

### Automation Primitives
- Triggers: time, webhook, file change, email arrival, manual
- Actions: any connected system, code execution, LLM calls
- Conditions and branching
- Loops over data sets
- Human-in-the-loop checkpoints for high-stakes actions

### Trust & Safety
- Permission model: what can each automation access?
- Approval workflows: "ask me before sending any email"
- Audit log: what ran, when, what it touched
- Undo/rollback where possible
- Dry-run mode: show what would happen

### Interface Modes
- Chat (primary): conversational automation building
- CLI: for power users and scripting
- Visual canvas: for complex multi-step workflows
- Tray/menubar: quick actions and status
- Mobile companion: approvals and monitoring on the go

### Sharing & Ecosystem
- Export automations as portable packages
- Community library: discover what others have built
- Fork and customize
- Private sharing within teams/orgs

---

## Architecture

### The Stack

```
┌─────────────────────────────────────────────────────┐
│                   Interface Layer                   │
│         Chat / CLI / Canvas / Tray / Mobile         │
├─────────────────────────────────────────────────────┤
│                   Intent Engine                     │
│    Parse → Plan → Confirm → Execute → Learn         │
├─────────────────────────────────────────────────────┤
│                 Automation Runtime                  │
│      Triggers │ Actions │ Conditions │ State        │
├─────────────────────────────────────────────────────┤
│                  Execution Layer                    │
│        Sandbox │ Capability Grants │ Timeouts       │
├─────────────────────────────────────────────────────┤
│                    Data Plane                       │
│   Connectors │ Local Index │ Query │ Schema Cache   │
├─────────────────────────────────────────────────────┤
│                    Core Runtime                     │
│     Daemon │ State DB │ Sync │ Model Router         │
└─────────────────────────────────────────────────────┘
```

### Key Design Decisions

#### Local-first, sync-second
- SQLite for everything: automations, state, logs, index metadata
- Use something like cr-sqlite or Electric SQL for CRDT-based sync
- Encryption at rest and in transit, user holds keys
- Cloud is optional: storage, sync, sharing — but never required

#### Model routing
- Task classifier determines: local vs cloud, which model, how much context
- Sensitive data (emails, credentials) → local model or redacted before cloud
- Complex reasoning → Claude or GPT-4 class
- Simple extraction/formatting → small local model
- User can override: "always keep my health data local"

#### Connector architecture
- Steal from MCP but simplify: every connector exposes read/write/subscribe
- OAuth flows handled by the daemon, tokens stored encrypted
- Schema discovery: connectors describe their data shapes
- Incremental sync: don't re-fetch everything, track deltas
- Connector health monitoring: surface broken auth, API changes

#### Execution sandboxing
- Primary: container-based (Firecracker microVMs for speed, or Docker for simplicity)
- Secondary: WASM for lightweight, pure-compute tasks
- Every execution gets: isolated filesystem, no network by default, resource caps
- Capability grants are explicit: `{ "read": ["calendar"], "write": ["notion:tasks"] }`
- Results are validated before being written back to real systems

#### State model
- Every automation has: config, execution history, learned refinements
- Global context: user preferences, common entities (my team, my projects), timezone
- Conversation memory: past intents map to automations for easy recall
- Everything versioned: rollback to previous automation state

---

## Intent Engine (Deep Dive)

The core innovation. Everything else is infrastructure.

### The Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                        INTENT ENGINE                            │
│                                                                 │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌───────┐ │
│  │ PARSE   │→ │ CLARIFY │→ │  PLAN   │→ │ EXECUTE │→ │ LEARN │ │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └───────┘ │
│       ↓            ↓            ↓            ↓           ↓      │
│   Intent       Dialog       Automation    Runtime     Feedback  │
│   Graph        State        Spec          State       Loop      │
└─────────────────────────────────────────────────────────────────┘
```

### Stage 1: Parse

Transform natural language into a structured intent graph.

**Example Input**: "When a client emails me with an invoice, save the PDF to their Dropbox folder and log it in my billing spreadsheet"

**Example Output**: Intent Graph
```yaml
intent:
  id: inv_001
  type: automation
  confidence: 0.85
  
trigger:
  type: email_arrival
  conditions:
    - field: from
      match: entity:client  # references user's known clients
    - field: attachments
      match: contains_pdf
    - field: subject_or_body
      match: semantic:invoice_related
  confidence: 0.7  # "client" needs clarification

actions:
  - type: extract
    target: email.attachments
    filter: pdf
    output: $invoice_pdf
    
  - type: resolve
    entity: client
    from: email.from
    output: $client
    confidence: 0.6  # how do we map email to client?
    
  - type: file_save
    source: $invoice_pdf
    destination: dropbox:/$client.folder/invoices/
    confidence: 0.5  # folder structure unknown
    
  - type: spreadsheet_append
    target: sheets:billing_log  # which spreadsheet?
    row:
      date: $email.date
      client: $client.name
      amount: $invoice_pdf.extracted_amount  # OCR needed
      file_link: $saved_file.url
    confidence: 0.4  # spreadsheet not identified

ambiguities:
  - "client" recognition method unclear
  - dropbox folder structure unknown
  - billing spreadsheet not identified
  - invoice amount extraction not specified
```

**How it works**:
- LLM extracts structured intent with confidence scores
- Entity resolution against user's known data (clients, projects, folders)
- Capability matching: what connectors are needed?
- Ambiguity detection: what's underspecified?

### Stage 2: Clarify

Resolve ambiguities through targeted questions. Not a generic chatbot — surgical disambiguation.

**Principles**:
- Ask the minimum questions needed
- Offer smart defaults based on user's data
- Batch related questions
- Learn from answers to avoid re-asking

**Example Dialog**:

```
System: I'll set this up. A few questions:

1. Client recognition
   I found 12 clients in your contacts. Should I match emails by:
   → Domain (e.g., anyone from @acme.com = Acme Corp)
   → Specific addresses (you approve each new sender once)
   → Let me figure it out from context (I'll ask if unsure)

2. Dropbox structure
   I see you have folders like /Clients/Acme Corp/. Should I:
   → Use that pattern: /Clients/{client}/Invoices/
   → Create a flat /Invoices/{client}/ structure
   → Let me scan your existing folders and match the pattern

3. Billing spreadsheet
   I found "2024 Client Billing" in your Google Sheets. Is that the one?
   → Yes, use that
   → No, let me pick

4. Invoice amounts
   Want me to try extracting the amount from the PDF?
   → Yes, add to spreadsheet (I'll flag uncertain extractions)
   → No, just log the file without amount
```

**State tracking**:
```yaml
clarification_state:
  intent_id: inv_001
  questions_asked: 4
  questions_answered: 0
  blocking_ambiguities:
    - client_recognition  # must resolve before proceeding
    - spreadsheet_target
  optional_ambiguities:
    - amount_extraction  # can default to "no"
  defaults_available:
    - dropbox_structure: inferred from existing folders
```

### Stage 3: Plan

Generate a concrete automation specification. This is the "source code" of the automation, but human-readable.

**Automation Spec**:
```yaml
automation:
  id: auto_inv_001
  name: "Client Invoice Processing"
  created: 2025-01-19T10:30:00Z
  status: draft
  
trigger:
  type: email
  account: user@example.com
  conditions:
    - type: sender_match
      method: domain_to_entity
      entity_type: client
      source: contacts:clients
    - type: attachment_present
      mime_types: [application/pdf]
    - type: semantic_match
      field: subject
      intent: invoice_related
      threshold: 0.8

variables:
  - name: client
    type: entity:client
    resolved_from: trigger.email.sender
  - name: invoice_pdf
    type: file
    resolved_from: trigger.email.attachments[mime=pdf][0]

actions:
  - id: save_to_dropbox
    type: file.write
    connector: dropbox
    path: "/Clients/{{client.name}}/Invoices/{{invoice_pdf.filename}}"
    source: invoice_pdf
    on_conflict: rename_with_timestamp
    
  - id: extract_amount
    type: document.extract
    source: invoice_pdf
    fields:
      - name: total_amount
        type: currency
        confidence_threshold: 0.7
    on_low_confidence: flag_for_review
    
  - id: log_to_sheet
    type: spreadsheet.append
    connector: google_sheets
    spreadsheet_id: "1a2b3c..."
    sheet_name: "Invoices"
    row:
      - column: A  # Date
        value: "{{trigger.email.date | format:'YYYY-MM-DD'}}"
      - column: B  # Client
        value: "{{client.name}}"
      - column: C  # Amount
        value: "{{extract_amount.total_amount | default:'REVIEW'}}"
      - column: D  # File Link
        value: "{{save_to_dropbox.url}}"
      - column: E  # Status
        value: "{{extract_amount.confidence > 0.7 ? 'Auto' : 'Needs Review'}}"

error_handling:
  - condition: client_not_found
    action: create_review_task
    message: "Unknown sender: {{trigger.email.sender}}"
  - condition: extraction_failed
    action: continue_with_flag
    flag_column: E
    flag_value: "Extraction Failed"

monitoring:
  notify_on: [error, low_confidence_extraction]
  daily_digest: true
```

**User-facing summary**:
```
Here's what I'll set up:

TRIGGER: New email arrives
  • From: One of your 12 known clients (matched by domain)
  • Has: PDF attachment
  • About: Invoices (I'll check the subject line)

ACTIONS:
  1. Save PDF → Dropbox /Clients/{client}/Invoices/
  2. Extract amount from PDF (I'll flag uncertain ones)
  3. Add row to "2024 Client Billing" spreadsheet:
     Date | Client | Amount | File Link | Status

IF SOMETHING'S WRONG:
  • Unknown sender → Creates a task for you to review
  • Can't read amount → Logs it anyway, marks "Needs Review"

You'll get a daily summary of what ran.

[Test with recent emails] [Activate] [Edit manually]
```

### Stage 4: Execute

Run the automation safely with full observability.

**Execution model**:
```
┌─────────────────────────────────────────────────────┐
│                  EXECUTION CONTEXT                  │
├─────────────────────────────────────────────────────┤
│  Trigger Event                                      │
│  ├─ email_id: msg_12345                            │
│  ├─ received: 2025-01-19T11:42:00Z                 │
│  └─ matched_conditions: [sender, pdf, semantic]    │
├─────────────────────────────────────────────────────┤
│  Variable Resolution                                │
│  ├─ client: "Acme Corp" (confidence: 0.95)         │
│  └─ invoice_pdf: "Invoice-2025-01-19.pdf" (2.3MB)  │
├─────────────────────────────────────────────────────┤
│  Action Execution                                   │
│  ├─ [✓] save_to_dropbox (1.2s)                     │
│  │   └─ path: /Clients/Acme Corp/Invoices/...      │
│  ├─ [✓] extract_amount (3.4s)                      │
│  │   └─ amount: €4,500.00 (confidence: 0.89)       │
│  └─ [✓] log_to_sheet (0.8s)                        │
│       └─ row: 247                                   │
├─────────────────────────────────────────────────────┤
│  Result: SUCCESS (5.4s total)                       │
└─────────────────────────────────────────────────────┘
```

**Safety mechanisms**:
- Capability enforcement: automation can only access declared resources
- Rate limiting: max 100 executions/hour by default
- Cost tracking: LLM calls, API calls, storage used
- Dry-run mode: show what would happen without doing it
- Approval gates: high-stakes actions require confirmation
- Rollback hooks: where possible, track how to undo

**Failure handling**:
```yaml
execution_failure:
  automation_id: auto_inv_001
  execution_id: exec_789
  failed_at: actions.extract_amount
  error: "PDF appears to be image-based, OCR failed"
  
  recovery_options:
    - retry_with_ocr: "Try enhanced OCR (costs ~$0.05)"
    - skip_extraction: "Continue without amount, mark for review"
    - pause_automation: "Stop until you review"
    - manual_input: "Enter the amount yourself"
  
  user_notification: sent
  auto_recovery: skip_extraction  # based on user's error_handling config
```

### Stage 5: Learn

Improve over time from corrections, feedback, and observed patterns.

**Learning signals**:

| Signal | Example | What we learn |
|--------|---------|---------------|
| Explicit correction | User edits extracted amount from €450 to €4,500 | Improve extraction, maybe decimal handling |
| Undo action | User deletes the spreadsheet row | Something was wrong, ask why |
| Pattern observation | User always moves invoices from "Needs Review" to "Done" without changes | Confidence threshold is too conservative |
| New data | User adds a new client | Entity list updated |
| Direct feedback | "This shouldn't have triggered for newsletters" | Refine semantic matching |

**Refinement loop**:
```yaml
learning_event:
  type: explicit_correction
  automation_id: auto_inv_001
  execution_id: exec_789
  
  original:
    field: extract_amount.total_amount
    value: "€450.00"
  
  corrected:
    value: "€4,500.00"
  
  inferred_cause: decimal_parsing_european_format
  
  proposed_fix:
    type: extraction_rule
    rule: "For EU-formatted invoices, treat period as thousands separator"
    confidence: 0.7
    
  user_prompt: |
    I noticed you corrected an invoice amount from €450 to €4,500.
    I think I misread the European number format.
    Should I always treat periods as thousands separators for EU documents?
    [Yes, for all documents] [Only for this client] [No, this was a one-off]
```

**Model personalization**:
- Store user-specific few-shot examples
- Build prompt context: "This user prefers...", "This user's clients are..."
- Fine-tuning vector: if usage is high enough, adapt embeddings to user's domain

---

## GTM & Positioning

### Target Persona

**Primary**: Solo agency owner or small agency (2-5 people) doing B2B services (marketing, design, dev, consulting).

**Characteristics**:
- Revenue: €100K-500K/year
- Tools: Gmail, Google Sheets, Dropbox/Drive, Notion or Asana, Stripe or invoicing tool
- Pain: 5-10 hours/week on admin (email, invoicing, reporting, client updates)
- Current solution: Manual + some Zapier, probably broken or underused
- Willingness to pay: €50-100/month without hesitation if it works

**Persona example**: "Sarah runs a 3-person branding agency. She has 8 active clients. She spends her mornings on email and her Fridays on invoicing and reporting. She's tried Zapier but the zaps keep breaking and she doesn't have time to fix them."

### Positioning Options

- **"Your ops team of one"**
- **"The back-office that runs itself"**
- **"Personal automation that actually works"**

Key message: You describe what you want done, it figures out how to do it, and it gets better as you use it.

### Moat Candidates

- **Data network effects**: The more you use it, the better it understands your systems and preferences
- **Connector quality**: MCP is a standard, but execution matters — yours just work
- **Community automations**: Library of proven workflows, forkable and customizable
- **Local-first trust**: In a world of AI slop, "your data stays yours" is a differentiator

---

## MVP Specification

### Workflow Cluster (MVP)

Three interconnected workflows that deliver immediate value:

#### Workflow 1: Client Email Triage
```
TRIGGER: Email arrives
LOGIC:
  - Identify if from a client (domain matching)
  - Categorize: urgent / needs response / FYI / spam
  - Extract: deadlines mentioned, questions asked, action items
OUTPUT:
  - Label/tag the email
  - Add to daily client digest
  - Create task if action item detected
```

#### Workflow 2: Invoice Processing
```
TRIGGER: Email with PDF attachment containing invoice-related content
LOGIC:
  - Identify client
  - Extract invoice details (amount, date, invoice number)
  - Check for duplicates
OUTPUT:
  - Save PDF to client folder
  - Log in billing spreadsheet
  - Flag for review if uncertain
```

#### Workflow 3: Weekly Client Report
```
TRIGGER: Friday 4pm (or on-demand)
LOGIC:
  - Aggregate: emails per client, tasks completed, invoices sent/paid
  - Summarize: what happened this week with each client
OUTPUT:
  - Generate summary document or email
  - Highlight: overdue items, upcoming deadlines, clients gone quiet
```

### Connector Set (MVP)

| Connector | Operations | Priority |
|-----------|------------|----------|
| Gmail | Read, label, send, search | P0 |
| Google Sheets | Read, append, update | P0 |
| Google Drive | Read, write, list | P0 |
| Google Contacts | Read (for client matching) | P0 |
| Google Calendar | Read (for deadline context) | P1 |
| Notion | Read, create page, update | P1 |
| Dropbox | Read, write, list | P1 |
| Slack | Send message | P2 |

**P0 = Must have for launch. P1 = First month. P2 = Fast follow.**

### Technical Scope (MVP)

| Layer | MVP Scope | NOT in MVP |
|-------|-----------|------------|
| **Runtime** | macOS desktop app (Electron or Tauri), SQLite state, no sync | Windows, Linux, mobile, cloud sync |
| **Model** | Claude API only, simple routing (all tasks → Claude) | Local models, model selection, hybrid routing |
| **Connectors** | Gmail, Sheets, Drive, Contacts (Google OAuth) | Notion, Dropbox, Slack, custom connectors |
| **Execution** | Node.js sandbox (vm2 or isolated-vm), no containers | Full container isolation, WASM |
| **Intent Engine** | Parse + Clarify + Plan (full), Execute (basic), Learn (manual only) | Automatic learning, pattern detection |
| **Interface** | Chat primary, simple automation list view | CLI, visual canvas, mobile |
| **Sharing** | Export as JSON | Community library, import from others |

### Intent Engine (MVP Simplifications)

- **Parse**: Full capability, using Claude with structured output
- **Clarify**: Single-turn clarification (batch all questions), not multi-turn dialog
- **Plan**: Generate automation spec, user can edit YAML if they want
- **Execute**: Sequential actions only, no branching or loops
- **Learn**: Manual feedback button, no automatic refinement

### User Flow (MVP)

```
1. ONBOARD (10 minutes)
   - Download app, sign in
   - Connect Google account (OAuth)
   - App scans: recent emails, sheets, drive folders
   - Suggests: "I found 15 unique client domains in your email. Want me to set up client recognition?"

2. FIRST AUTOMATION (5 minutes)
   - User: "When I get an email from a client, label it with their name"
   - System: Shows plan, asks for confirmation
   - User: Confirms
   - System: Dry-runs against last 20 emails, shows results
   - User: "Looks good, activate"

3. DAILY USE
   - Automations run in background
   - Daily digest email: "Yesterday I processed 12 emails, filed 2 invoices, found 3 action items"
   - User can chat anytime: "What did you do with the email from Acme?"
   - User can refine: "Don't trigger on newsletters, only real client emails"

4. WEEKLY RHYTHM
   - Friday: "Here's your weekly client summary" (generated report)
   - User: "Add time tracking data to this" → new capability request
```

---

## Development Timeline

| Week | Focus | Deliverable |
|------|-------|-------------|
| 1-2 | Core runtime | Electron app shell, SQLite state, Google OAuth flow |
| 3-4 | Gmail connector | Full read/label/send, email parsing, entity extraction |
| 5-6 | Intent engine (parse + clarify) | NL → structured intent, clarification flow |
| 7-8 | Plan + execute | Automation spec generation, basic execution engine |
| 9-10 | Sheets + Drive connectors | File operations, spreadsheet append |
| 11-12 | Interface polish | Chat experience, automation management, daily digest |
| 13-14 | Alpha testing | 10 users, intensive feedback, bug fixing |
| 15-16 | Beta prep | Onboarding flow, documentation, waitlist |

**Total: 4 months to closed beta with 10-20 users.**

---

## Success Metrics (MVP)

### Quantitative
- User activates first automation within 30 minutes of install
- 3+ automations active per user within first week
- 80%+ automation success rate (no errors, no manual correction needed)
- <5% of automations disabled due to problems
- NPS > 50 among alpha users

### Qualitative
- "It actually works" — reliability is the bar
- "It understood what I meant" — intent engine is good enough
- "I saved 2 hours this week" — tangible value
- "I want to set up more" — engagement loop working

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Google OAuth approval takes too long | Start process in week 1, use "testing" mode for alpha |
| Intent parsing fails too often | Constrain to known workflow templates initially |
| Automation errors damage user data | Mandatory dry-run, undo where possible, conservative defaults |
| Users don't trust it | Radical transparency (show everything it does), approval gates |
| Scope creep | Ruthless focus on 3 workflows, say no to everything else |

---

## Key Technical Challenges

1. **The intent gap** — going from "handle my expenses" to actual working automation requires context, clarification, and trust-building
2. **Connector maintenance** — APIs change, auth expires, schemas drift
3. **Failure modes** — what happens when the LLM misunderstands or the sandbox fails mid-execution?
4. **Security model** — local-first helps, but "connect to everything" is a massive attack surface

---

## Data Models

### Core Entities

```typescript
// User's connected accounts
interface Connector {
  id: string;
  type: 'gmail' | 'google_sheets' | 'google_drive' | 'dropbox' | 'notion' | 'slack';
  accountId: string;
  credentials: EncryptedCredentials;
  status: 'active' | 'expired' | 'error';
  lastSync: Date;
  schema?: ConnectorSchema;
}

// Known entities extracted from user's data
interface Entity {
  id: string;
  type: 'client' | 'project' | 'person' | 'folder' | 'spreadsheet';
  name: string;
  aliases: string[];
  metadata: Record<string, any>;
  sources: EntitySource[];  // where we learned about this entity
}

// Automation definition
interface Automation {
  id: string;
  name: string;
  description: string;
  status: 'draft' | 'active' | 'paused' | 'error';
  trigger: Trigger;
  variables: Variable[];
  actions: Action[];
  errorHandling: ErrorHandler[];
  monitoring: MonitoringConfig;
  capabilities: Capability[];  // what this automation can access
  createdAt: Date;
  updatedAt: Date;
  version: number;
}

// Execution record
interface Execution {
  id: string;
  automationId: string;
  automationVersion: number;
  triggeredAt: Date;
  completedAt?: Date;
  status: 'running' | 'success' | 'partial' | 'failed';
  triggerEvent: TriggerEvent;
  variables: ResolvedVariable[];
  actionResults: ActionResult[];
  error?: ExecutionError;
}

// User's conversation with the system
interface Conversation {
  id: string;
  messages: Message[];
  context: ConversationContext;  // entities, automations mentioned
  createdAt: Date;
  updatedAt: Date;
}

// Learning from user corrections
interface LearningEvent {
  id: string;
  type: 'correction' | 'undo' | 'feedback' | 'pattern';
  automationId?: string;
  executionId?: string;
  original: any;
  corrected: any;
  inferredCause?: string;
  proposedFix?: ProposedFix;
  userResponse?: 'accepted' | 'rejected' | 'modified';
  createdAt: Date;
}
```

### Trigger Types

```typescript
type Trigger = 
  | EmailTrigger
  | ScheduleTrigger
  | WebhookTrigger
  | FileChangeTrigger
  | ManualTrigger;

interface EmailTrigger {
  type: 'email';
  account: string;
  conditions: EmailCondition[];
}

interface EmailCondition {
  field: 'from' | 'to' | 'subject' | 'body' | 'attachments';
  operator: 'equals' | 'contains' | 'matches' | 'semantic';
  value: string | EntityReference | SemanticMatcher;
}

interface ScheduleTrigger {
  type: 'schedule';
  cron?: string;
  interval?: { value: number; unit: 'minutes' | 'hours' | 'days' };
  timezone: string;
}
```

### Action Types

```typescript
type Action =
  | FileAction
  | SpreadsheetAction
  | EmailAction
  | ExtractAction
  | TransformAction
  | ConditionalAction;

interface FileAction {
  type: 'file.read' | 'file.write' | 'file.move' | 'file.delete';
  connector: string;
  path: TemplateLiteral;
  source?: VariableReference;
  onConflict?: 'overwrite' | 'rename' | 'skip' | 'error';
}

interface SpreadsheetAction {
  type: 'spreadsheet.read' | 'spreadsheet.append' | 'spreadsheet.update';
  connector: string;
  spreadsheetId: string;
  sheetName: string;
  row?: SpreadsheetRow;
  range?: string;
}

interface ExtractAction {
  type: 'document.extract';
  source: VariableReference;
  fields: ExtractionField[];
  onLowConfidence: 'flag' | 'skip' | 'error';
}
```

---

## API Design

### Intent Engine API

```typescript
// Parse natural language into intent
POST /api/intent/parse
{
  "text": "When a client emails me with an invoice...",
  "context": {
    "connectors": ["gmail", "google_sheets", "dropbox"],
    "entities": [...],
    "recentAutomations": [...]
  }
}
→ {
  "intent": IntentGraph,
  "ambiguities": Ambiguity[],
  "suggestedClarifications": Question[]
}

// Resolve ambiguities
POST /api/intent/clarify
{
  "intentId": "inv_001",
  "answers": [
    { "questionId": "q1", "answer": "domain_matching" },
    { "questionId": "q2", "answer": "use_existing_pattern" }
  ]
}
→ {
  "intent": IntentGraph,  // updated
  "remainingAmbiguities": Ambiguity[],
  "readyToPlan": boolean
}

// Generate automation spec
POST /api/intent/plan
{
  "intentId": "inv_001"
}
→ {
  "automation": AutomationSpec,
  "humanReadableSummary": string,
  "requiredCapabilities": Capability[]
}
```

### Execution API

```typescript
// Dry-run an automation
POST /api/automation/:id/dry-run
{
  "sampleData": [...],  // optional: specific data to test with
  "limit": 10  // how many historical items to test
}
→ {
  "results": DryRunResult[],
  "summary": {
    "wouldTrigger": 8,
    "wouldSucceed": 7,
    "wouldFail": 1,
    "sampleOutputs": [...]
  }
}

// Activate automation
POST /api/automation/:id/activate
→ {
  "status": "active",
  "nextRun": Date | null  // for scheduled triggers
}

// Get execution history
GET /api/automation/:id/executions
→ {
  "executions": Execution[],
  "summary": {
    "total": 150,
    "successful": 142,
    "failed": 8,
    "lastRun": Date
  }
}
```

### Connector API

```typescript
// List available connectors
GET /api/connectors
→ {
  "available": ConnectorType[],
  "connected": Connector[]
}

// Connect a new account
POST /api/connectors/:type/connect
→ {
  "authUrl": string  // OAuth redirect
}

// OAuth callback
GET /api/connectors/:type/callback?code=...
→ {
  "connector": Connector,
  "discoveredEntities": Entity[]  // clients, projects, etc.
}

// Query connected data
POST /api/connectors/:id/query
{
  "query": "emails from clients this week",
  "limit": 50
}
→ {
  "results": ConnectorResult[],
  "schema": ResultSchema
}
```

---

## File Structure (MVP)

```
personal-ai-os/
├── apps/
│   └── desktop/                 # Electron/Tauri app
│       ├── src/
│       │   ├── main/           # Main process
│       │   │   ├── daemon.ts   # Background service
│       │   │   ├── ipc.ts      # IPC handlers
│       │   │   └── tray.ts     # System tray
│       │   ├── renderer/       # UI
│       │   │   ├── components/
│       │   │   ├── pages/
│       │   │   └── hooks/
│       │   └── preload/
│       └── package.json
├── packages/
│   ├── core/                    # Core runtime
│   │   ├── src/
│   │   │   ├── db/             # SQLite wrapper
│   │   │   ├── state/          # State management
│   │   │   └── config/
│   │   └── package.json
│   ├── intent-engine/          # Intent processing
│   │   ├── src/
│   │   │   ├── parse/          # NL → Intent
│   │   │   ├── clarify/        # Disambiguation
│   │   │   ├── plan/           # Intent → Automation
│   │   │   └── llm/            # LLM integration
│   │   └── package.json
│   ├── execution/              # Automation runtime
│   │   ├── src/
│   │   │   ├── sandbox/        # Isolated execution
│   │   │   ├── triggers/       # Trigger handlers
│   │   │   ├── actions/        # Action executors
│   │   │   └── scheduler/
│   │   └── package.json
│   ├── connectors/             # Data connectors
│   │   ├── src/
│   │   │   ├── base/           # Connector interface
│   │   │   ├── gmail/
│   │   │   ├── google-sheets/
│   │   │   ├── google-drive/
│   │   │   └── google-contacts/
│   │   └── package.json
│   └── shared/                 # Shared types & utils
│       ├── src/
│       │   ├── types/
│       │   └── utils/
│       └── package.json
├── docs/
│   ├── architecture.md
│   ├── intent-engine.md
│   └── connectors.md
├── package.json                # Workspace root
├── tsconfig.json
└── README.md
```

---

## Prompts for Intent Engine

### Parse Prompt

```
You are parsing a user's natural language request into a structured automation intent.

User's connected systems: {{connectors}}
Known entities: {{entities}}
Recent automations: {{recentAutomations}}

User request: "{{userInput}}"

Output a JSON object with:
1. `intent`: The structured intent graph (see schema below)
2. `ambiguities`: List of unclear elements with questions to ask
3. `confidence`: Overall confidence score (0-1)

Intent Schema:
{
  "type": "automation",
  "trigger": {
    "type": "email" | "schedule" | "webhook" | "file_change" | "manual",
    "conditions": [...]
  },
  "actions": [
    {
      "type": "extract" | "file.write" | "spreadsheet.append" | ...,
      "params": {...},
      "confidence": 0.0-1.0
    }
  ]
}

Be specific about what's unclear. Don't assume — mark low confidence.
```

### Clarify Prompt

```
You are helping clarify an ambiguous automation request.

Original intent: {{intent}}
Ambiguities: {{ambiguities}}
User's data context: {{dataContext}}

Generate clarifying questions that:
1. Are specific and actionable
2. Offer smart defaults where possible
3. Can be batched (ask related questions together)
4. Reference the user's actual data when helpful

Format as a list of questions with multiple choice answers where appropriate.
```

### Plan Prompt

```
You are generating an automation specification from a clarified intent.

Intent: {{intent}}
User's clarifications: {{clarifications}}
Available connectors: {{connectors}}

Generate a complete automation spec in YAML format that:
1. Is executable by our runtime
2. Handles edge cases gracefully
3. Includes appropriate error handling
4. Sets up monitoring and notifications

Also generate a human-readable summary (2-3 sentences) of what this automation does.
```

---

## Security Considerations

### Capability Model

```typescript
interface Capability {
  connector: string;
  operations: ('read' | 'write' | 'delete')[];
  scope?: {
    // e.g., only emails from certain senders
    filter: Record<string, any>;
  };
}

// Example: Invoice processor capabilities
const invoiceProcessorCaps: Capability[] = [
  {
    connector: 'gmail',
    operations: ['read'],
    scope: { filter: { hasAttachment: true } }
  },
  {
    connector: 'google_drive',
    operations: ['write'],
    scope: { filter: { pathPrefix: '/Clients/' } }
  },
  {
    connector: 'google_sheets',
    operations: ['read', 'write'],
    scope: { filter: { spreadsheetId: '1a2b3c...' } }
  }
];
```

### Approval Gates

High-stakes actions that always require user confirmation:
- Sending emails
- Deleting files
- Modifying shared documents
- Any action on a new connector not previously used
- Actions exceeding cost threshold ($X in API calls)

### Audit Log

Every execution is logged:
- What triggered it
- What data was read
- What was written/modified
- What LLM calls were made (with costs)
- Any errors or anomalies

User can always see: "Why did this automation do X?"
