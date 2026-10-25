# The editor

The editor is where you build a SATOM plugin (a **view** or a **widget**) or a complete Plugin Studio **app**. Open it from a project with **Edit**.

The editor never decides on its own whether something is valid. The rules pack of your target SATOM decides on every **Check**, and SATOM decides again on **Validate** and **Import**. The hints you see while you type are only there to help you get it right the first time.

## The page

The **header** shows:
- the project name, kind, slug and version;
- the SATOM target the project is linked to;
- the result of the last check;
- an **Unsaved changes** badge while there is something to save.

Open **Project details** to edit the name, slug, version (`X.Y.Z`), icon (a Bootstrap icon class such as `bi-puzzle`) and description.

Below the header are the **tabs**, which depend on the kind:

| Kind | Tabs |
|---|---|
| view, widget | Files, Datasets, Params, Preview, Raw JSON |
| app | Form, Diagram, Actions, Stores, Connections, API Catalog, Design, Scripts, Raw JSON |

Use the arrow keys to move between tabs once one of them has the focus.

When the project has no target, or the target's schema cannot be loaded, a banner tells you so. The pickers then offer only what the package itself contains, and the diagram has no step kinds.

### Buttons (every tab)

| Button | What it does |
|---|---|
| **Save** (Ctrl+S / Cmd+S) | Saves the project and checks it with the rules pack. The result appears in the **Checks** panel. |
| **Check** | Checks the saved project with the rules pack of the target's SATOM version. |
| **Validate on SATOM** | Sends the package to the target SATOM for a dry run. Nothing is installed. |
| **Rehearse** | App: runs the diagram on the target, unarmed (see below). View or widget: opens **Preview** and renders the plugin. |
| **Import** | Imports the package into the target SATOM. If a connection the package creates needs a secret, you type it here (see below). |
| **Download package** | Downloads the signed `.satomplugin` file. |
| **AI** | Generates or changes the draft with an AI profile (see below). |
| **Checks** | Opens or closes the Checks panel. The number is the count of problems. |

Every button that talks to SATOM saves first when there are unsaved changes.

### The Checks panel

The panel lists the **problems** (they block import) and the **warnings** of the latest check, save, validation or generation, and says which one it was. Each entry shows where the problem is, for example `app.graph.nodes[record]` or `files[style.css]:3`.

Click an entry to go there. The editor opens the tab, selects the step, field, store or block, or puts the cursor on the line. A place the editor has no tab for opens **Raw JSON**.

The **On import** part summarises what an import would do:
- where it lands (draft or testing);
- who signed it;
- what it creates and what it keeps;
- which connections need a secret.

At 1280 px and wider the panel sits on the right. On narrower screens it opens over the page; close it with the **×**.

### Unsaved work

- **Local draft.** Every change is also written to your browser's local storage. If you come back and the browser holds a draft newer than the saved project, a banner offers to **Restore** or **Discard** it. A restored draft still has to be saved. A successful save removes the local draft.
- **Leaving the page.** The browser asks for confirmation before you leave with unsaved changes.
- **Secrets.** Secrets are never written to the draft or to the project.

## View and widget tabs

### Files

The three files of the plugin, each in a code editor with line numbers:
- `body.jinja`: Jinja and HTML. Datasets are `{{ data.<key>.rows }}` and inputs are `{{ params.<name> }}`.
- `style.css`
- `script.js`

**Tab** indents and **Shift+Tab** outdents. To leave the editor with the keyboard, press **Esc**, then **Tab**.

Lint findings from the last check are marked in the line-number gutter (red for problems, amber for warnings) and listed under the editor. Click a finding to go to its line.

Plugin CSS rules:
- use the `--fw-*` tokens and the `.fw-badge-*` status colours;
- no `@import`;
- no external URLs;
- `data:` URLs only for images and fonts;
- no `position: fixed` overlays over the frame.

### Datasets

Tick the curated datasets the plugin reads. The list comes from the target. A dataset the project declares that the target does not offer stays listed and ticked, so you can untick it.

### Params

These are the inputs the viewer fills to filter the view. Each one has:
- a **name**: lowercase letters, digits and `_`, read as `{{ params.<name> }}`;
- a **label**;
- a **type**: device, select, text, number or date, as offered by the target;
- a **default** and a **required** flag.

A `select` takes one option per line, written `value|Label`.

Params only filter data that is already loaded. They never widen access.

### Preview

Fill in the inputs and press **Render on SATOM**. The editor saves, the target renders the plugin with its own datasets (limited to what your token can see), and the HTML appears in a sandboxed frame. Scripts run in the frame, but it has no same-origin access, no forms and no pop-ups.

## App tabs

### Form

These are the fields an operator fills before a run. Steps read them as `{{ form.<name> }}`.

For each field you set a name, label, type, required flag, default (except for passwords) and help text. Some types need more:

| Type | Extra settings |
|---|---|
| `select` | Options, one per line. |
| `store_select` | A data store (from this package or from the target), a value column and an optional label column. |
| `api_select` | An HTTP connection, a path, the JSON path of the list, the value key and the label key. |
| `satom_select` | Which SATOM list to show (appliances, certificates, zones, …). |

Any list type can also be **Searchable**. Reorder fields with ↑ and ↓.

The number of fields is limited by the target (40 by default).

### Diagram

The diagram is the process the app runs.

- **Palette** (left): the step kinds of the target, grouped as Flow, Data, Actions, Checks and SATOM, each with its colour. Steps that write are marked **writes**. Click a kind to add a step. A diagram has exactly one **Start**, so the Start button is disabled once there is one.
- **Canvas**:
  - drag a step to move it;
  - drag the empty background to pan;
  - use the mouse wheel or the zoom buttons to zoom;
  - press **Fit** to show everything;
  - press **Auto-layout** to arrange the steps top to bottom from Start.
- **Arrows**: to add one, do either of these:
  - drag from the round port under a step onto another step;
  - select a step, press **Connect**, then click the target step.

  A new arrow takes the first outcome the step allows that is still free:

  | Step | Outcomes, in order |
  |---|---|
  | Most steps | `always`, `ok`, `error` |
  | Decision | `yes`, `no`, `always` |
  | Checks and Ask a person | `yes`, `no`, `error`, `always` |
  | End and Fail | none (they end the run) |

  Change the outcome in the inspector.
- **Keyboard**, with the canvas focused:
  - arrow keys move the selected step (Shift moves it further);
  - **Delete** removes the selection;
  - **Esc** cancels Connect;
  - **+** and **−** zoom.
- **Inspector** (right):
  - the **Step** list selects any step without the mouse;
  - for a step: its key (arrows follow a rename), its label, and the **parameters of its kind**. The parameter form is built from the target's definition of that kind: text, long text, a list of choices, or JSON. Required parameters are marked `*` and each has its help text. For **Call an API**, the **Endpoint** list offers the endpoints of the API Catalog version the connection is pinned to; picking one fills the method and the path;
  - also for a step: its outgoing arrows, an **Add arrow** form (target step and outcome), and **Delete step**;
  - for an arrow: its outcome and **Delete arrow**.
- **Problems**: a step that the last check names gets a red frame and a count. The list under the canvas shows the check's problems for the diagram and the editor's own hints. Click an entry to select the step.
- **Rehearsal**: after a rehearsal, each step shows its outcome on the canvas. **Clear rehearsal** removes the outcomes.

A step of a kind the target does not know is drawn dashed. Its parameters are kept and can be edited as JSON.

### Actions

These are the buttons a finished run offers:
- save to a data store;
- download (CSV or JSON);
- send to a connection;
- run another app;
- raise a change request.

Pick a kind and press **Add action**. Each action has a key, a button label, an optional **Ask to confirm**, and the parameters of its kind, generated the same way as step parameters.

An action never changes a device. A change request goes through SATOM's approval.

### Stores

These are the data stores the package creates. Each store has a slug, name, description and columns. Each column has a name, label, type (text, number, boolean, date or json) and a required flag.

You can enter **seed rows** in the grid, up to 500. You can also open **Paste CSV**: the first line names the columns, and the separator can be a comma, a semicolon or a tab. Then:
- **Append rows** adds the rows;
- **Replace all rows** replaces them.

Values are converted to the column types. Columns that do not exist in the store are reported and ignored.

On import, a store the target already has keeps its rows. New columns are appended.

### Connections

These are the HTTP connections the package creates. Each one has:
- a slug, name and description;
- a base URL and a timeout;
- TLS verification;
- an authentication type, as offered by the target, and its non-secret parts: user or client id, header name, token URL or login path, scope;
- optionally, the **API Catalog version** it is pinned to.

There is **no secret field**. The secret is typed in SATOM at import, either in this editor's Import dialog or in SATOM's own wizard. It is stored only in SATOM.

A connection that carries a secret value (for example from an AI draft or a pasted JSON) is flagged, and **Remove the secret values** strips it. A PostgreSQL connection cannot travel in an app package and is flagged too.

### API Catalog

These are the API versions the package creates. Each one has:
- a slug, version, name, owner, documentation URL, description and base path;
- an **endpoints** table: method, path, id, summary, parameters (as JSON), whether it takes a body, and whether it is deprecated.

To fill the endpoints from a specification, open **Import OpenAPI (JSON)** and paste an OpenAPI 3 or Swagger 2 document in its JSON form. It is converted in the browser exactly as SATOM's own importer does. Then choose:
- **Replace** to replace the endpoints;
- **Add new** to keep the existing endpoints and add the rest.

Warnings (skipped HEAD/OPTIONS operations, operation ids that had to be replaced) are shown. The **Check** validates the result.

YAML is not read in the browser: convert it to JSON first. A version without endpoints is refused on import.

### Design

Design places the run page and the result yourself. Without a design, SATOM draws its default page and result. Press **Start a design** to begin.

Switch between:
- **Run page**: what the operator fills in and starts;
- **Result**: what a finished run shows;
- **Theme**: the colours.

- **Blocks**: pick a block type (only those allowed on the current surface) and press **Add block**. Move blocks with ↑ and ↓, and remove them with the bin. Click a block to edit it in the inspector:
  - its properties: text, Markdown, choices, icon, on/off, number, a form field, an Output step, or an uploaded image (upload it from there);
  - its **Style**: colours as hex values with a picker, plus spacing, radius, shadow, border, size, weight, alignment and width presets;
  - for containers: columns (widths out of 12) or tabs, and **Add inside**.
- **Contrast**: the style section shows the contrast of the block's text on its background, flagged when it is below 4.5:1. The Theme view checks the same pairs SATOM warns about.
- **Raw JSON** shows the whole design object for direct editing. **Apply** checks that it is valid JSON.

The Run button and the Result area on the page, and the status, outputs, actions and steps on the result, are always present. SATOM adds them at the end when you leave them out.

### Scripts

These are the scripts the diagram's **Run a script** steps use. Choose a script on the left. For each script you edit:
- its slug, name and description;
- its **files**: `main.py` (required, it is what runs) plus any other text files, for example `lib/helpers.py`;
- its **settings**:
  - the **form fields** (inputs) the script receives;
  - a timeout from 5 to 900 seconds;
  - the audience;
  - the **connections** the script may call, chosen from the HTTP connections in the package or on the target.

A packaged script has **no network and no credentials**, so neither can be set here. Both are refused if present. Use **Remove network / credentials** to strip them.

The **Script SDK** card is always visible. A script may use only:
- `inputs`: the form values;
- `result(value)`: what the step returns;
- `log(text)`: a line in the run log;
- `connection("slug")`: a declared HTTP connection, which SATOM calls with its secret, for example `connection("crm").get("/tickets").json()` or `.op("getHost", {"id": 7})`.

While you type, the editor flags `secret()`, `tcp()`, `write=True` and network modules. The rules pack checks the code itself.

### Raw JSON

This is the whole project as JSON, including keys that no tab shows. Edit it and press **Apply**, or just **Save**. The text must be valid JSON before anything is applied or saved. `id`, `kind`, `assets`, `updated_at` and `last_check` belong to the server and are not changed from here.

The editor keeps every key it does not understand, at every level, exactly as it was loaded.

## Rehearse (apps)

The dialog asks for the app's own form fields. Lists that SATOM fills (store, API and SATOM lists) are typed as plain values. Choose how **Ask a person** steps are answered: **Yes**, **No**, or **Stop there**, which returns `waiting` with the question.

**Run the rehearsal** saves, then runs the diagram on the target **unarmed**:
- steps that write are recorded, not executed;
- it uses the target's own stores, connections and secrets;
- nothing is kept.

The dialog shows the status, the problems, each step with its outcome, and the outputs. **Show on the diagram** lays the outcomes over the steps. Passwords typed for a rehearsal are dropped once it has run.

## Import

**Import** saves and checks first. Then the dialog shows:
- what the import will create and keep;
- whether your SATOM token is allowed to import;
- one **password field for each connection the package creates that needs a secret**.

The secrets are sent once with the import, stored by SATOM (in its vault or its encrypted settings), and cleared from the dialog straight away, whether or not the import succeeded. The Builder never stores them.

Tick **Allow a downgrade** to replace a newer version on the target. The result lists what was created and kept, what is left to do in SATOM, and any problems.

## AI

1. Pick an **AI profile** (the profiles are managed on the AI profiles page) and describe what to build or change.
2. Optionally press **What will be sent** to see exactly what goes to the provider: the author guide, the target schema, the saved draft and your text. It also states what is never sent: data rows and secrets.
3. Press **Generate**. The editor saves first, then shows each round with its problems.
4. When the job finishes, the project is reloaded and the Checks panel shows the check of the generated draft. Problems that remain after the repair rounds are shown, never fixed silently.

## Accessibility

- Every control has a label.
- Focus is always visible.
- Text colours reach at least 4.5:1 contrast.
- Everything can be done with the keyboard: diagram steps and arrows through the inspector, the code editor through Esc then Tab.
- The page works from 1024 px wide.

## For developers

- **Code**:
  - `satom_builder/templates/editor/index.html`;
  - `satom_builder/static/js/editor/` (ES modules, no build step; `VENDORED.md` lists the code taken from SATOM);
  - `satom_builder/static/css/editor.css`.
- **Backend**: only the internal API of contract section 11, plus `GET /api/ai/profiles` → `[{id, name, adapter, model}]`.
- **Dev server**: `python3 tests/editor/devserver.py` serves the editor with a fake section 11 API built from `tests/editor/fixtures/`. Open `http://127.0.0.1:8199/projects/3/edit` for an app, `/projects/1/edit` for a view, or `/projects/2/edit` for a widget without a target. Bootstrap, the icons and `fortiweb.css` are read from a local SATOM checkout (`--vendor`).
- **Schema fixture**: `python3 tests/editor/gen_schema.py /path/to/satom` rebuilds `tests/editor/fixtures/schema.json` from SATOM's own code.
- **Tests**:
  - `node --test tests/editor/js/` runs the pure modules: graph, form fields, OpenAPI, `where` mapping, design, CSV, round-trip fidelity;
  - `python3 -m unittest discover -s tests/editor -p "test_*.py"` runs the template smoke test, the parity tests against SATOM, and the browser test (headless Chromium over the DevTools protocol).
