# Changelog

All notable changes to SATOM Builder are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Sign-in tab for apps** (request: "this part must also be registered in the
  Builder"). An app can carry its own **sign-in** in SATOM -- a page for
  people without a SATOM console account who see that app and nothing else
  (contract section 20). The tab adds rules for a directory connection of
  the target SATOM (Active Directory, LDAP, RADIUS / FortiAuthenticator), for
  SATOM's local accounts, or for a connection id typed by hand; per rule the
  group DNs and LDAP filter, or the FortiAuthenticator groups, and whether a
  SATOM authenticator code is required; then the Apps only profile and
  whether new accounts wait for an administrator. It checks as you type with
  SATOM's own rules and says which rules the target would leave out (a
  connection it lacks or has with another kind). Never a server, an account
  or a secret: those stay in SATOM, and the import brings the sign-in
  switched off. Needs a SATOM newer than 4.6.0; on an older one the tab says
  the app would be installed without it. The AI may write `"signin"` too
  (a misplaced one is lifted into the app, like `automations`).

## [0.4.0] - 2026-10-26

**Apps that act on a Fortinet device on a schedule, built against the device's
real API (needs SATOM 4.5.0).** An app gets an **Automations** tab (it runs on
a schedule in SATOM's Automations) and reads SATOM's devices; a connection to
a FortiGate, FortiWeb, FortiADC, FortiAuthenticator or FortiAnalyzer is pinned
to a build of **SATOM's API library**, so the AI, the check, the test run and
the import all use the device's real endpoints and fields. Generate runs an app
before calling it done -- and, for an app on a schedule that writes, runs it
again on a simulated device that remembers, so an app that would add the same
thing every day is refused. Also: every project installs in the right SATOM
ADOM, the AI window is a conversation you can minimize and continue, Generate
shows what the AI is doing and says what it built and left out, projects have
search and filters, an icon picker and a kind you can change. On a SATOM older
than 4.5.0 the Builder keeps working and says which features need the upgrade.

### Added

- **Every Fortinet device kind, not just FortiGate** (contract section 19.1).
  Asked: "are all the other device types fine?" With SATOM `be575e43`:
  a FortiADC pin takes child tables (a pool member:
  `POST /api/load_balance_pool_child_pool_member?pkey=<pool>`), a
  FortiAuthenticator pin offers only the methods each resource was measured
  to allow, and FortiAnalyzer can be pinned: one endpoint, `POST /jsonrpc`,
  and the body's `params[].url` must be one of the build's urls (the check,
  the test run and the broker refuse another). The AI reads each kind's
  answers (FortiADC: a missing entry is HTTP 200 `{"payload": null}`, not
  the error arrow). The unpinned-Fortinet-call problem now also names
  FortiADC, FortiAuthenticator and FortiAnalyzer calls, without flagging
  another API's `/api/v1/...` or `/jsonrpc`. A JSON-RPC `get` counts as a read
  for the second walk. Editor: a FortiAnalyzer step searches the urls and
  writes the picked one into the body.
- **Fields are checked, not just paths, and an app is run twice on a device
  that remembers** (lab E2E, FortiADC: the AI wrote a pool member as
  `{"member_ip", "member_port"}` and read `payload.member | default([])`, and
  every check passed). The endpoints a diagram calls carry SATOM's fields; a
  body with a field the device does not have, or a FortiADC create without what
  SATOM knows it needs (`real_server_id`: a member references a real server),
  is a problem that names the real ones -- also on the Check button, which
  showed only SATOM's rules. Test answers hold the real fields, and an app on a
  schedule that writes is walked again on a simulated device that keeps its
  writes: adding the same thing on the second day is a problem (the message
  lists what the second run decided). A FortiADC create without its name in
  `mkey` is a problem too: the next run could not find what it created.

- **The AI calls a Fortinet device with SATOM's real endpoints, and a wrong
  path is refused.** Requested: "the API pack should go to the Builder, so it
  has every API and knows how to work with them" -- the FortiWeb -> FortiGate
  app came back calling `POST /api/firewall/policy/070024/objects`, which no
  FortiGate serves. The API pack is not copied (the client's SATOM, where the
  plugin installs, has the library that counts): a connection is pinned to a
  build of SATOM's API library (`"api": "fortigate", "api_version": "8.0.1"`)
  and the Builder adds SATOM's endpoint list for that build to the package
  (contract section 19), so SATOM's own rules refuse an `api_call` whose path
  is not one of its ~3,700 endpoints -- in the check, in a test run and on
  import -- and the problem names the nearest real endpoints. The AI reads
  the build's endpoints for the products the request names (tables with
  their key, lists such as an address group's `member` as their own
  endpoint), and SATOM's author guide says how FortiGate is written to (a
  PUT of a list replaces it; POST one item to `.../addrgrp/{name}/member`).
  The draft never keeps the list: an AI answer or a save that carries one
  loses it. In the editor the connection offers the library's builds (the
  fleet's first) and **Call an API** searches the build's endpoints. A call
  to a Fortinet REST path on a connection pinned nowhere (it would reach
  SATOM unchecked), and a pinned package connection whose name SATOM already
  has without that pin (the import keeps SATOM's), are problems too; the
  prompt proposes a free slug for the pinned connection (`fortigate-801`).
  Needs the SATOM of the same round.
- **An answer with the app's parts in the wrong place is used, not
  rejected.** `connections`, `stores`, `automations`... written next to
  `"app"` instead of inside it go into the app (gpt-oss lost two of three
  rounds of the lab run to "Unknown key(s)").
- **Generate runs the app before calling it done.** SATOM's rules check a
  diagram's shape, not what it does: the lab's FortiWeb -> FortiGate app
  passed them and failed on its first loop (`{{ ip_list[i] }}` with the
  counter named otherwise). After the rules pass, Generate and the Assistant
  walk the app with SATOM's own runtime on generated test data (the test
  fleet, a pinned connection answered from SATOM's endpoints), with the
  inputs of its first automation; a failure goes back to the AI with the step
  and the error, as a view's failed render already did.
- **An automation's empty value for a required field is flagged** in the
  Automations tab, as SATOM now refuses it (the scheduled run would stop at
  "is required" every time).
- **A scheduled app that writes to a Fortinet device must survive its
  second run.** The lab's armed run on fgt02 added FortiWeb 192.0.2.82 to the
  group, and the next run failed with HTTP 500: the address existed. An app
  with automations that POSTs on a connection pinned to SATOM's API library
  and reads nothing from it first is walked twice; on the second walk every
  POST answers as FortiOS does for an existing entry, and a failure
  (`builder.second_run`) tells the AI to read first (GET `<path>/{key}`: a
  404 takes the error arrow) and create only what is missing. An app that
  reads first decides from what it read, which canned answers cannot
  reproduce: it is not walked again (that flagged a correct app). A path
  written as one expression (`{{ '/api/...' + ip }}`) gets its literal form
  in the problem.
- **The app note says how to loop and what to put in base_url.** Every lab
  run lost a round to an empty `base_url` and another to a counter written
  as `"0"`: the note now gives the loop (`"{{ 0 }}"`, `{{ items[i] }}`,
  `{{ i + 1 }}`), a placeholder URL, and "read before writing".
- **Test answers in the device's own shape.** A connection pinned to SATOM's
  API library is answered as the product answers: FortiGate
  `{"results": [...], "status": "success", ...}` (measured on fgt02),
  FortiWeb `{"results": ...}`, FortiADC `{"payload": ...}`,
  FortiAuthenticator `{"meta", "objects"}`. A bare list made an app written
  against the real FortiGate (`resp.results`) fail its test run.
- **Test data for a connection pinned to a large API version.** One test
  answer per endpoint was 3,715 answers for a FortiGate build, past the 200 a
  test-data connection holds: generating test data failed with 400. A large
  version now gets answers for the endpoints the diagram calls, plus the
  catch-alls.
- **Room for the answer.** An app's prompt no longer carries the views'
  section of SATOM's author guide (22k characters of dataset lists on a1)
  nor the step kinds a second time: 80 KB -> about 52 KB before the endpoint
  index, so a 32k-token model still has room to answer and repair.

- **Automations: an app on a schedule.** Requested: "every time a FortiWeb
  is added to SATOM, send a rule to the FortiGate that adds its IP to a
  specific group" -- the AI answered "make it a view", which can neither run
  on its own nor write -- and "the schedule joins the Automations section: in
  the Builder an Automations tab". An app's new **Automations** tab sets when
  it runs (interval of 5 minutes or more, daily, weekly, monthly, cron) and
  the form values it runs with, checked as you type; the package carries
  them in app.json `automations` (contract section 18) and SATOM creates each
  as a switched-off entry of Automations next to the draft app. Test runs
  answer the step **Read SATOM's appliances** (`satom_appliances`) from the
  test data's fleet with SATOM's own code. Needs the SATOM of the same round.

- **View, widget or app: explained where you choose, and changeable.**
  Requested: "let me change a project from app to view, but how does anyone
  know the difference? Put it in SATOM and in the Builder: why a search is
  not an app". New project offers the three kinds as cards -- what each is,
  what it is for, an example -- with the rule of thumb: *show or search what
  SATOM already knows: a view (or a widget on a dashboard); do something --
  create, change, call another system, ask for approval: an app*. The project
  settings change the kind (the draft is kept; only the new kind's parts are
  packaged; the stale check is dropped); the editor says the kind and links
  there; the kind badge carries the explanation; an app asked for a search
  tells you to switch it to a view. New projects start as a view.
- **Search and filters on Projects** (server-side): text over name, slug and
  description, kind, target, ADOM (where each project installs) and last
  check, with a count and Clear.
- **The SATOM ADOM is a field of the project**: New project and the settings
  offer Auto (from the datasets it reads), Global or an ADOM; the project page
  says where it installs and why; the list has an ADOM column.
- **Icon picker**: every icon field opens a searchable grid of the 2,048
  Bootstrap icons a plugin may use (`/projects/icons.json`, read from the
  vendored font); typing the name still works.
- **What a project is for, on hover**: an eye icon next to its name (list and
  project page) shows its description.

- **Every project installs in the right SATOM ADOM, and nobody has to pick
  it.** Requested: "nowhere in the Builder does it say I have to choose the
  ADOM; and what if I want something from two ADOMs, like every firewall
  rule that holds the IPs of the FortiWeb interfaces? Make it see every
  dataset SATOM has, the basics by default, so the user can see and pick
  them" (contract section 17; needs the SATOM side of it, SATOM `f165d70d`).
  - The project's ADOM follows what it reads: one product's datasets → that
    product's ADOM; datasets of more than one product → **Global** (opened
    from SATOM's Global console); no product dataset → Global. The editor's
    header says **Installs in: …** and changes the moment a dataset is
    ticked (`adom.js`, the server's own rule run in the page; the server
    stays the authority and answers `where` on every save). Project details
    offers **Auto** or any ADOM the token reaches, and warns when a fixed
    ADOM does not offer what the plugin reads.
  - Every call to SATOM for a project runs in that ADOM (`?adom=`): check,
    validate, rehearse, import, test data. Generate and the assistant ask
    SATOM in Global, so the AI sees every dataset; the ADOM then follows
    what it picked. The prompt states the rule ("never say a listed dataset
    does not exist").
  - The Datasets tab groups every dataset by ADOM -- the basics every ADOM
    offers first and open, then one group per product -- and, with a token
    bound to one ADOM, lists the datasets SATOM has in other ADOMs with the
    ADOM that holds them, instead of offering them or calling them missing.
  - **My tokens** says what each connection reaches: a Global token with
    the datasets of every ADOM you may enter, or a token bound to one ADOM
    with how many datasets it cannot reach and the advice to use a Global
    one. Test says it too.
  - A SATOM without section 17 works as before (the token's ADOM).

- **Generate and the assistant show what the AI is doing, as it happens.**
  Requested: "it must show what it is doing, not just say generating: that is
  useless, you cannot see beyond it". Both adapters now stream: the
  OpenAI-compatible one over server-sent events (`stream: true`; a server that
  answers in one JSON body still works), with the reasoning that Ollama, vLLM
  and DeepSeek send apart (`delta.reasoning` / `reasoning_content`) or inside
  `<think>`; the Anthropic one from the SDK's stream events, asking for
  `thinking.display: "summarized"` on the models whose thinking is on by
  default (Claude Opus 5.5 and its families; the default "omitted" streams
  empty thinking). Under the Generate dialog and under each assistant reply:
  - a live line: round, model, phase (reading the request -- timed even
    before the first token, from the server's clock -- thinking, writing,
    finished), tokens, tokens per second, time to first token, elapsed;
  - **What the model is thinking** and **What it is writing**, live;
  - every step with its time and result: the author guide read from SATOM's
    rules pack, the request sent (size, images, "with the problems to
    fix"), the answer read, the package checked with SATOM's rules, the
    problems found (listed on the step), the repair round, done or stopped.
  The activity is stored (schema 5: `job_progress`, `chat_progress`), so
  leaving the page loses nothing, and a finished reply keeps it under
  "What it did". It holds what the model wrote, never the prompt or a secret.
  The project's main page shows it too: its Generate said only "Status:
  running" (requested: "show the state as in the dialog"); now the status
  names the current step and the same activity follows.
- **The AI window is a conversation you can minimize, recover and continue.**
  Requested: "let the process go on in parallel, minimize the window and get
  it back -- if it is closed it is lost; see the history between the user and
  the AI and continue it; that history only of the user and project, never
  mixed". In the editor:
  - **Minimize** (or close / Esc while it works) folds the window into a pill
    at the bottom right with the live line ("Round 2/3 · gpt-oss:120b ·
    writing the answer · ≈640 tokens · 1:12"); it stays on every tab while you
    go on editing, and a click opens the window again. The job runs on the
    server either way.
  - **Nothing is lost on close or reload**: on load the editor asks for your
    conversations and a Generate that still works comes back in the pill
    (also from the project page, or another browser).
  - **History**: your conversations on this project, each request with what it
    came to (passes the rules / problems left / failed), what the AI did, and
    "Load this draft into the editor".
  - **Continue**: the next request goes into the same conversation; the AI gets
    the project as saved plus your earlier requests and what each came to
    ("What will be sent" shows exactly that). "New conversation" starts apart.
  - A draft that arrives while you changed the editor is never loaded over
    your changes: it waits in the conversation.
  The project page picks a conversation to continue, shows it, links it into
  the editor (`#ai=<thread>`) and follows a Generate that still works.
  Schema 7 (`job_turns`) keeps each request; `GET /api/projects/<id>/ai-history`
  answers the caller's own conversations on that project only.
- **Generate says what it built and what it left out.** In the lab a request
  for a DDI reservation and a FortiWeb SPO came back as an IP owner lookup
  that passed every check: the model had replaced the request with something
  easier, and nothing told the author. Generate now asks the model for a
  `message` saying what it built, which parts of the request it covers and
  which it left out and why, and not to swap the request for an easier
  plugin; a request that cannot be built at all is answered with the message
  alone and the draft is not changed. The note is shown when the job ends
  (editor and project page), as a step of the activity and in the
  conversation history (which also feeds the next turn). The activity opens
  with **Your request**, and a repair round repeats the author's request
  after the problems, so it is the last thing the model reads.

### Changed

- **Conversations are their author's alone.** A Generate job, its history and
  an assistant conversation answer only the user who started them (an
  administrator got them by id before); a thread of another project or user
  cannot be continued (404, the same as a missing one).

### Fixed

- **A repair round of an app no longer runs out of room on a 32k-context
  model.** An app's prompt carried every step kind in full (47k characters,
  ~12k tokens) on top of the author guide that already lists them; with
  gpt-oss (32k context) round 2 of a request was cut off ("The output was too
  long"). The prompt now sends each step's summary, whether it writes and its
  parameters' help -- a1's target summary went from 65,541 to 29,651
  characters.
- **The AI sent every request that touched SATOM's data to a view.** The
  note an app's prompt carries (2026-10-25) said "an app cannot read SATOM's
  data: answer that it is a view's job" -- true for "show the rules that use
  this IP", wrong for "every day, put each FortiWeb's IP in a FortiGate
  group". It now says: showing or searching is a view's job; everything that
  acts -- on a schedule too -- is an app, which reads the devices with
  `satom_appliances` and runs with automations. The kind explanations say
  the same (SATOM's words).

- **A view the AI drafts is rendered before it is called done.** In the lab,
  asked for "the FortiGate rules that use an IP", the model wrote a view that
  read `policy.srcaddr`: SATOM's rules passed it (they parse a template, they
  do not run it) and the first real render failed, because a policy's
  addresses are rows of `fortigate_firewall_policy__srcaddr` joined on
  `parent_id`. Generate and the assistant now render a view or widget that
  passes the rules with SATOM's own runtime (as a Test run) over generated
  rows with exactly each dataset's columns, with no input and with sample
  inputs; a failure is a problem of the round and goes back to the model with
  the columns and relations of the datasets it reads and of their sub-object
  datasets. A runtime that cannot run skips the render and says so. Before
  rendering, a `{% set x %}` inside a `{% for %}` whose `x` is read after the
  loop is flagged (Jinja scoping: the template renders but `x` never changes
  outside the loop — the lab view hid every rule this way), with the
  `namespace` rewrite to apply.

- **The project page now shows your AI conversations, as the editor does.**
  Reported: "the Builder has two chats, one on the project and one in the
  editor; the editor's has the history, the project's does not". Both read
  the same conversations, but the project page hid them behind a dropdown
  that started on "New conversation" and showed one line per turn. It now
  lists every conversation (title, requests, date, working), opens one with
  each request and what the AI answered (status badge, its note), **What it
  did** per turn, **Show this result (and apply it)**, the Generate button
  reading **Continue**, and **New conversation** to start over.
- **An app's AI no longer claims SATOM has no FortiGate data.** Asked for "an
  app that looks an IP up in FortiGate: ADOM, UID, rules", the model answered
  that the server had no FortiGate dataset. The app's prompt listed every
  dataset key, but no diagram step can read a dataset (only views and widgets
  do). An app's prompt now says so, counts the datasets per product for a
  view, and tells the model to answer that it is a view's job (a view with an
  input parameter, the IP, reads and filters them) instead of saying the data
  does not exist.

- **Replacing a token kept the old token's library.** The Library, the
  Datasets tab and the AI kept listing what the previous token reached until
  the cache expired; a new or removed token now drops that link's library
  and snapshots. The snapshot cache is per ADOM (schema 8).


- **A Generate cut off by a restart is failed at once, not an hour later.**
  An update restarted the lab while an author's Generate ran: the job stayed
  "running" and the page waited for nothing (only jobs older than an hour
  were failed). A job now records the process that runs it (schema 6:
  `job_runners`, pid and start time, as chat replies already did); at
  start-up every worker fails the jobs whose process is gone, with the
  reason on the job and as the last step of its activity, and leaves the
  jobs of live workers alone.
- **The installers said they were 0.1.6.** `install-satom-builder.sh` and
  `satom-builder-setup.sh` of 0.2.0 and 0.3.0 printed "SATOM Builder 0.1.6",
  stamped `.env` with it and warned "This installer is 0.1.6; the code is
  0.3.0" on every installation: their version was not bumped at release
  (`test_installer_versions_match_the_release` catches it; the releases did
  not run it).
- **An answer that restates `min_satom` or `format` no longer loses its
  round.** The author guide describes plugin.json, so a model (gpt-oss) added
  `"min_satom"` and the Builder refused the whole answer as "Unknown key(s)".
  The plugin.json keys the Builder writes itself (`kind`, `format`,
  `min_satom`) are now dropped, and the output contract says to leave them
  out; any other unknown key is still refused. The crash that started that
  run ("the package could not be checked: 'list' object has no attribute
  'get'", a step's `params` written as a list) is fixed in SATOM: the check
  now names the step and the author guide shows a step.

## [0.3.0] - 2026-10-25

**Every dataset of the target, kept in line with SATOM (SATOM 4.3.0).**
Requested: "create the ~600 plus what the administrator creates, so there are
examples and if something is missing an administrator can create it; the
Builder must receive these datasets created in SATOM so both are in line".

### Added

- **Hundreds of datasets, read from the target.** The Builder reads the
  target's whole catalogue from its library: SATOM's nine built-in datasets,
  one per object type its device cache holds (`fortiweb_vip`,
  `fortigate_firewall_policy`, `fortiadc_load_balance_pool__pool_member`…,
  603 on the lab fleet) and the ones its administrators publish
  (`admin_<name>`). Each with its columns, its relations and how SATOM builds
  it. A dataset published in SATOM appears in the Builder at the next library
  refresh (or **Refresh from SATOM**).
- **Datasets tab**: grouped (built-in, administrator, one group per product
  and cache layer) and searchable; what the plugin reads is listed first.
- **Library page**: the same catalogue, searchable; open a dataset for its
  columns (masked ones marked), its relations and its SQL or query.
- **Database tab › More of SATOM's data**: find any dataset of the target and
  add it to the test data, with SATOM's rows, to explore, join and query it;
  remove it again. The relations SATOM gives (to the fleet, to a parent
  object, to the type a field is named after) are drawn in the diagram.
- **Test runs render device-object and administrator datasets** with their
  columns from SATOM.
- **A package carries the definition of the administrator datasets** its
  view reads; the destination SATOM creates them pending an administrator's
  approval instead of dropping them.

### Changed

- A view's test data holds the datasets it reads, SATOM's built-in ones and
  the ones the author added (no longer every dataset: there are hundreds).
- The AI's request names the built-in and administrator datasets and counts
  the device-object ones per product, instead of listing ~600 keys.

**Test data from SATOM, and the test data as a database.** Requested: "add
the data we have in SATOM to the Builder so something already exists, and
there must also be the database diagram and SQL so it can be queried and it
is easier". **Load from SATOM needs SATOM 4.3.0 or later** on the target (`GET
/api/v1/builder/data`, contract section 15); with an older SATOM the Builder
generates test data as before and says why.

- **Load from SATOM** (Test data tab): the rows the target SATOM shows you,
  read with your token: its datasets, the data stores the project uses and
  the lists of SATOM objects, at most *SATOM rows per table* (200 by
  default, up to 2,000). Scoped and masked by SATOM as a view rendered there
  would be (your ADOM, maintenance devices only with the permission, the
  audit log only with the audit permission). The rows stay on the Builder:
  never in a package, never sent to the AI. Connections and step answers are
  kept.
- **A new project starts from its SATOM's data** the first time its Test
  data, Database or Test run tab opens (generated rows when the target
  cannot send them), and the tab says which.
- **Where every table came from**: *from SATOM* (target, time, *first rows
  of N* when cut), *generated (seed N)*, *package seed rows*, *edited*.
- **Views see SATOM's built-in datasets** in their test data next to the ones
  they read, so an author can look before picking (any other dataset is added
  in the Database tab).
- **Database tab**: the test data as tables (a dataset by its key, a data
  store as `store_<slug>`) in a diagram with their columns, types and the
  relations between them (`appliance_id`, `device`, `server_pool`, `wpp`);
  click a table for its columns, its origin and, for a dataset, the query
  SATOM fills it with. A **SQL console** runs one read-only SQLite SELECT
  over the saved test data (500 rows, 3 s; writes, ATTACH, PRAGMA and file
  functions refused), with example queries per table and per relation.
- **The Assistant knows the test database**: table names, column types, row
  counts and relations go into its request (never a row), and it answers
  questions about the data with a query to run in the Database tab.

## [0.2.0] - 2026-10-25

Everything a plugin author needs to build AND test, with SATOM's own code, on
test data, before anything reaches a SATOM: test data, test runs, the
library of what plugins build on, and an assistant conversation that keeps
its history and files. **Needs SATOM 4.2.0 or later on the target** for test
runs and the Library (an older SATOM keeps working for everything else and
the pages say what they need).

Requested: "the chat must take files and images, and keep its history and
state without being lost when closed; a database with mock data so the user
can test; the API in its structure and format so the user sees the library;
scripts run in the sandbox with SATOM's rules; scripts test-run with the mock
data; then send it to the test SATOM, and users bring it into production."

### Added

- **Test data** (editor tab): what SATOM's runtime reads instead of a
  database. Datasets carry exactly the columns SATOM gives a view (read from
  SATOM's own dataset queries, through the rules pack); data stores start
  from the package's seed rows; connections answer canned responses
  (method + path patterns, status, JSON body; SQL answers for PostgreSQL);
  the steps that would touch a device, the DDI, the Certificate Manager or
  SATOM's fleet answer what the author sets (ok / yes / no / error, a value,
  a detail). Generated values are synthetic and deterministic per seed (lab
  names, documentation addresses 192.0.2.0/24, 198.51.100.0/24,
  203.0.113.0/24, example.com): never a value of a real installation. Edit in
  a table, as JSON, or from a CSV file.
- **Test run** (editor tab): the saved project run by SATOM's own runtime,
  copied verbatim from the target's rules pack (contract section 13), here on
  the Builder, with no network and nothing sent to SATOM:
  - a view or widget renders in SATOM's frame (tokens, class kit) with the
    test data and the chosen params;
  - an app's diagram walks ARMED, because everything it touches is test data:
    store writes change a copy you may keep ("Keep these changes"), an API
    call gets the canned response, SATOM and device steps answer the Test
    data, "Ask a person" is answered yes / no / stop;
  - a script runs in SATOM's jail (bubblewrap: no network, no Builder file)
    with SATOM's SDK, `connection()` answered by the test data through SATOM's
    own connection broker, inputs typed the way SATOM types them.
  Every run is kept (the last 50 per project, with the inputs, passwords
  masked, and the full answer) and goes on if the page is closed.
- **Library** (sidebar): what a plugin builds on, read from the target with
  your token: the datasets and their columns, the data stores and their
  columns, the API Catalog with every version's endpoints, the Fortinet API
  library (builds, then a build's endpoints, then an endpoint's fields), the
  step kinds of a diagram, the script SDK and the author guide. Structure
  only: no row, no secret. Cached per author like the target snapshot.
- **Assistant** (editor tab): conversations about the project with an AI
  profile, kept on the Builder with their files. Attach images (PNG, JPEG,
  GIF, WebP: they reach the model as images, so use a model that reads them)
  and text files (code, JSON, CSV, YAML, logs: they go into the prompt). A
  reply is written on the server: close the page and it is there when you
  come back; a reply cut by a restart says so. The model may answer a
  question without changing anything, or propose a draft, which is checked by
  the rules pack and repaired like Generate; the draft reaches the project
  only with "Apply to the project" (a normal save: the previous draft stays as
  a revision).
- **Brand:** SATOM's emblem with the Builder's badge, in the top bar, on the
  sign-in page and as the favicon.
- The installer installs `bubblewrap` (all three families, also in the
  offline bundles) and checks that the jail works as the service account.

### Fixed

- **A draft made with AI (Generate) was lost on the next reload.** The editor
  marked it as saved although the job never saves (the author applies it). It
  is now an unsaved change, kept in the local draft until Save.
- **The release gate of the update-package build held an attested release**
  when `RELEASE_TRUST_SIGNERS` was a relative path: git verified the
  signatures from `/`, so every signed commit read as untrusted. The path is
  made absolute.
- The editor's tables could widen the whole page (a visually-hidden label in a
  table header escaped its scroll container).

### Upgrading

- Script test runs need `bubblewrap` on the Builder server. A new install or
  a re-run of the installer installs it; after an update package, install the
  `bubblewrap` package with the system's package manager. Until then the Test
  run tab says so and runs views and diagrams without script steps.

## [0.1.6] - 2026-10-25

Private AI models, and an installer RE-RUN that no longer trusts what the
service account wrote. **Upgrade every node, and re-run the installer only
with 0.1.6 or later.**

### Security

- **Re-running the installer could give the service account root.** On a node
  that is already installed, the installer ran the venv's `python`/`pip` as
  root (installing dependencies, generating `FERNET_KEY` and the publisher
  key), sourced `.env` as root shell code, and installed the units, the
  operator CLI, the update runner, the release keys and the TLS helper from
  `/opt/satom-builder/deploy` -- all of which the service account owns and can
  rewrite. Now:
  - root-side files come from a root-only copy of the installer's OWN release
    (the bundle, the tarball, the checkout or the clone it installs from),
    never from the installed tree;
  - the venv, `pip` and `manage` run as the service account, which also reads
    `.env` itself; the publisher key is generated by the account and copied in
    through a descriptor that does not follow a link;
  - `.env` is created, appended and sealed through descriptors, and a `.env`
    that is not root's stops the install (inspect it, restore it as
    `root:<account> 0640`, re-run);
  - the installer refuses to run from inside the installation directory.
  Verified on a real node with the attacks planted: a replaced venv `python`
  ran only as the service account, a modified `deploy/install-cli.sh` never
  ran, the installed runner unit is the release's byte for byte, and a
  `.env` holding shell code was refused without being executed.
- **An update no longer turns a `.env` the service account wrote into
  root's.** The runner re-sealed whatever `.env` it found as
  `root:<account> 0640`; it now re-seals only the root-owned one it opened
  before handing the tree back.

### Added

- **Private AI models, several at once.** *AI profiles* has a **Start from**
  list: Anthropic, a private server (Ollama, vLLM, LM Studio: Qwen, DeepSeek,
  Llama, Mistral…) or another OpenAI-compatible service. It fills the adapter,
  base URL, model and max tokens; the model field suggests common models. A
  profile still naming the placeholder `your-ai-server` is refused. The page
  says that every enabled profile is offered to authors at the same time, and
  hides the Anthropic-only fields (effort, refusal fallbacks) for other
  adapters.

### Fixed

- **A reasoning model's thinking no longer breaks a generation.** When a server
  leaves DeepSeek-R1's or Qwen3's `<think>…</think>` block in the answer (vLLM
  without a reasoning parser, llama.cpp), the JSON was read from the first `{`
  of the reasoning and failed as "not valid JSON". The block is dropped first,
  also in DeepSeek-R1's form that carries only the closing tag.
- **"Spent all of max tokens reasoning"** replaces "the output was too long"
  when the answer is empty and the thinking came in its own field (Ollama),
  and an answer that is only reasoning says to raise max tokens.

## [0.1.5] - 2026-10-25

The console on a phone (measured at 360, 390 and 430 px in a real browser),
and root no longer touches the service account's files by path.

### Security

Found by the release-trust review of `deploy/`. The application tree and
`data/` belong to the service account, which can turn any entry into a symbolic
link at any moment, including between a check and the write that follows it.
Root processes wrote, `chmod`-ed and `chown`-ed there BY PATH. **Upgrade every
node.**

- **`satom-builder update apply` could overwrite and give away any file on the
  system.** It copied the package through a predictable
  `data/update-uploads/.<name>.part` and changed its mode and owner by path: a
  link planted there by the service account made root write the package over
  the file it pointed at (the runner's own code, for one) and hand it to that
  account. `update fetch` and every queued request had the same flaw. They now
  write through descriptors that never follow a link (`O_NOFOLLOW`, a random
  `O_EXCL` temp name, mode and owner set on the open file, atomic rename); a
  package given by a path inside the application tree, other than
  `data/update-uploads/<name>`, is refused.
- **The update runner writes the application tree as the service account.**
  The verified `app.tar.gz` is still validated by root (no links, devices,
  absolute or `..` paths, size ceiling); the account extracts it, removes the
  files the new revision dropped, and restores the venv and, on
  `rollback --restore-db`, the databases. The package is copied to root-only
  staging through a descriptor before it is verified, and the database backups
  are read the same way.
- **`VERSION` and the status files are read without following a link.** The
  runner repeated `VERSION` in the status file the service account reads, so a
  link there disclosed any root-readable file; only a plain version is kept.
- **`.env` is read only when it is a regular file owned by root.** A link put
  in its place made the runner read any `KEY=value` file root can read (another
  product's secrets) and pass it to a process running as the service account.
- **`install-cli.sh` and `install-runner.sh` refuse a source that is a link**
  (`cp -a` copied it as a link, and the root-owned copy executed whatever it
  pointed at). `chown -R` of the tree no longer follows links (`-h`).
- **Re-running the installer refuses links** at `.env`, `data/`, its queue
  directories, `state/`, `data/.installed` and the log directory instead of
  writing, `chmod`-ing and `chown`-ing what they point at.

### Fixed

- **The menu button opens the sidebar on a phone.** It only toggled the
  desktop collapse, so below 768 px the sidebar stayed hidden and every page
  outside the current one (Administrator, AI profiles, tokens) was out of
  reach. It is now an overlay: a tap outside or Escape closes it.
- **The footer no longer keeps the sidebar's margin on a phone** (it wrapped
  in a narrow strip on the right).
- **No field below 16 px on a phone**, so iOS Safari does not zoom into the
  page when a field is focused. The script editor's line numbers follow.
- **The editor is one column on a phone**: a three-column field row cut the
  data store's name and broke "Searchable" into one word per line.
- **Users:** the role of each user is visible again (its select had shrunk to
  the arrow).
- Small buttons grow to a tappable size on a phone.

### Changed

- **Projects:** "Last check" is the second column, so the check result is on
  the first screen of a phone; the slug moved after the target.

## [0.1.4] - 2026-10-25

First public release: the repository and its releases are published at
https://github.com/visionebc/satom-builder. No change to the application.

### Changed

- **Built-in upgrade feed.** `satom-builder update fetch` reads the
  `satom-builder-update-latest.json` asset of the latest GitHub release when
  no `--feed` is given. `SATOM_BUILDER_UPGRADE_FEED` (or `--feed`) still
  replaces it with an internal mirror.
- **README** with what Builder guarantees, how to install from a release
  bundle and how to update; the documentation no longer names internal
  development repositories.

## [0.1.3] - 2026-10-25

Fixes found by the first end-to-end runs against a live SATOM, a local model
and a real browser. (0.1.1 and 0.1.2 were lab builds, never published.)

### Fixed

- **The editor reads a stored check from its result.** A project whose last
  check had passed showed a red "Last check: 0 problem(s)" badge.
- **A view's or widget's AI request carries only what a view can use**
  (datasets, parameter types, name patterns, limits), as compact JSON. With
  the whole app schema a view's request was about 27,000 tokens, past a
  local model's default context, and the model never saw the output
  contract. The context window a local server needs is documented on the AI
  profiles page and in the user guide.
- **The local check runs SATOM's real rules pack.** The pack ran in a child
  interpreter whose import path came from `sysconfig`. Inside a virtual
  environment that path has no `lib-dynload`, so SATOM's own pack stopped at
  `_contextvars`. The child now gets the interpreter's real standard library
  path, and still nothing else: no working directory, no user site, no
  Builder source.
- **Registering the publisher key refreshes what the Builder knows about the
  target.** Cached target snapshots taken before the registration made the
  local check report "a key this server does not trust" while SATOM's own
  validation accepted the package.
- **An untrusted key that is this installation's own publisher key gets a
  hint:** register it on the target from the Publisher page.

## [0.1.0] - 2026-10-25

First version. A customer-installed tool to author SATOM plugins and Plugin
Studio apps, check them with the target SATOM's own rules, rehearse and import
them through SATOM's API with each author's own token, and optionally generate
them with an AI model the user chooses.

### Added

- **Editors** for views, widgets and complete Plugin Studio apps:
  - form fields;
  - the diagram of steps;
  - output actions;
  - data stores with columns and seed rows;
  - HTTP connections without secrets;
  - API Catalog versions, including OpenAPI import;
  - design blocks;
  - sandboxed scripts that may use only `inputs`, `result`, `log` and their declared connections.
- **Checks with SATOM's own rules.** The target's rules pack is downloaded, pinned by its sha256 and run in a bare subprocess. SATOM checks again on validate, rehearse and import.
- **Per-author SATOM tokens.** Everything runs with that person's rights and ADOM, and SATOM's audit log shows who acted.
- **A publisher key per installation**, which a SATOM super-admin registers as a Trusted publisher.
- **AI profiles:**
  - Anthropic, through the official SDK;
  - any OpenAI-compatible endpoint, including local models.

  A repair loop sends the rules' problems back to the model. Before anything is sent, the author sees exactly what goes out: never data rows, never secrets.
- **Installer, offline bundles and signed update packages** that follow SATOM's installer and update rules:
  - Debian 12, RHEL 9 and SUSE 15, online and offline;
  - updates through a privileged runner with rollback.
