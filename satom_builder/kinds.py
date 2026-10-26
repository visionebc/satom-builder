"""What a view, a widget and an app are, and which one a request needs.

The same words as SATOM's own ``plugin_package.KIND_GUIDE`` (SATOM shows them
in Report Studio and Plugin Studio, and its author guide gives them to the
AI): change them in both places together. Shown on New project, in the
project settings (where the kind can be changed), in the editor and in the
AI's answer when an app is asked for something only a view can do.
"""
from __future__ import annotations

ORDER = ("view", "widget", "app")

GUIDE = {
    "view": {
        "label": "View", "icon": "bi-window",
        "what": "A page in SATOM (Custom Views). It READS SATOM's data -- every dataset: the "
                "device cache (FortiGate rules and addresses, FortiWeb policies, FortiADC "
                "pools...), the fleet, the administrator datasets -- and shows it, filtered by "
                "input parameters such as an IP or a device. It never changes anything.",
        "use_for": "search, look up, report, list, compare devices",
        "example": "Which firewall rules use this IP?",
    },
    "widget": {
        "label": "Widget", "icon": "bi-grid-1x2",
        "what": "A small tile on a SATOM dashboard: a view sized for a glance. It reads the same "
                "datasets and never changes anything.",
        "use_for": "a count, a status, a short list",
        "example": "Server policies without a WAF profile: 3",
    },
    "app": {
        "label": "App", "icon": "bi-diagram-3",
        "what": "A Plugin Studio app: a form the user fills in and a diagram of steps that DO "
                "things -- call an API (DDI, NetBox, any HTTP), read and write data stores, run "
                "a sandboxed script, act on appliances (create a FortiWeb server policy, "
                "reserve an IP), ask a person to approve. It reads the list of devices SATOM "
                "manages (step satom_appliances) but not the other datasets, and it can run on "
                "a schedule (Automations), with no person filling in the form.",
        "use_for": "requests and workflows that create or change something, also every day "
                   "on their own",
        "example": "Publish a site: reserve the IP in DDI, create the FortiWeb policy, ask for "
                   "approval. Or, every day: put the IP of every FortiWeb in a FortiGate "
                   "address group",
    },
}

RULE = ("Show or search what SATOM already knows: a view (or a widget on a dashboard). "
        "Do something -- create, change, call another system, ask for approval, keep "
        "something in step on a schedule: an app (with an automation for the schedule).")

SWITCH_NOTE = ("Changing the kind keeps everything in the draft, but only the new kind's parts "
               "go into the package: a view packs its files, datasets and parameters; an app "
               "packs its form, diagram, stores, connections and scripts.")


def label(kind: str) -> str:
    return GUIDE.get(kind, {}).get("label", kind)
