"""Command adapters for independent, persistent user and daily research queues."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import APP_DIR
from .local_commands import ArgumentSpec, CommandSpec, CommandInputError, CommandUnavailable
from .research_automation import (
    ResearchAutomation, ResearchOptions, build_plan, research_tasks,
    researched_pairs, slot_state, speedup_inventory,
)


def research_state_path(service, scope="user", state_dir=None):
    if scope not in {"user", "daily"}:
        raise CommandInputError("Choose the user or daily research queue.")
    uid=str(getattr(getattr(service.client,"credentials",None),"user_id",""))
    if not uid:
        raise CommandUnavailable("The research queue needs a verified account identity.")
    # Real account IDs are public numeric identifiers; unusual test/profile IDs
    # use a fixed digest so a value can never become a path or expose a secret.
    identity=uid if uid.isascii() and uid.isdigit() else hashlib.sha256(uid.encode()).hexdigest()[:20]
    suffix="_daily" if scope=="daily" else ""
    return Path(state_dir or APP_DIR) / f"research_automation_{identity}{suffix}.json"


def research_daily_preflight(service, options, live=None, *, state_dir=None):
    """Read-only availability; an uncertain daily ledger stays visibly blocked."""
    try:
        if isinstance(options,str):options=json.loads(options)
        settings=ResearchOptions.from_dict(options)
        runner=ResearchAutomation(service,research_state_path(service,"daily",state_dir))
        saved=runner._load()
        if saved and saved.get("account_binding")!=runner.binding:
            raise CommandUnavailable("This daily research queue belongs to another account.")
        if saved.get("pending_action"):
            raise CommandUnavailable("The daily research queue has an unresolved request; resume its live-state check separately.")
        if saved and saved.get("status")!="complete" and ResearchOptions.from_dict(saved.get("options")).serialized()!=settings.serialized():
            raise CommandInputError("The active daily research queue uses different saved settings.")
        current=live if live is not None else service.snapshot(refresh=True)
        planned=build_plan(current,service.catalog("card"),service.catalog("combo"),settings)
        tasks=research_tasks(current);known=researched_pairs(current)
        if saved and saved.get("status")!="complete":
            queue=[row for row in saved["queue"] if row["status"]!="completed" and row["pair"] not in known]
            valid={row["pair"] for row in planned}|{task["pair"] for task in tasks.values()}
            if any(row["pair"] not in valid for row in queue):
                raise CommandUnavailable("A queued daily research pair is no longer available.")
        else:queue=planned[:settings.max_pairs]
        if settings.speedups:speedup_inventory(current,service.catalog("item"))
        free,_=slot_state(current)
        active={task["pair"] for task in tasks.values()}
        available=bool(queue) and (bool(free) or any(row["pair"] in active for row in queue))
        status="ready" if available else "complete" if not queue else "blocked_slots"
        reason="Ready to advance the independent daily research queue." if available else "No matching owned unresearched pairs remain." if not queue else "No verified regular research chamber is currently free."
        return {"available":available,"reason":reason,"status":status,"count":len(queue),"free_slots":free}
    except (ValueError,TypeError,CommandUnavailable) as error:
        # Return explicit safe explanations for expected command errors. JSON
        # errors may contain arbitrary input, so never echo their messages.
        reason=str(error) if isinstance(error,(CommandInputError,CommandUnavailable)) else "Daily research options or current metadata could not be read."
        return {"available":False,"reason":reason,"status":"unavailable","count":0,"free_slots":[]}


def register_research_automation(service, *, state_dir=None, boost_callback=None, ad_callback=None):
    def options(value):
        try:parsed=json.loads(value)
        except (ValueError,TypeError):
            raise CommandInputError("Research options must be a valid JSON object.") from None
        return ResearchOptions.from_dict(parsed)

    def engine(current, scope):
        return ResearchAutomation(current,research_state_path(current,scope,state_dir),
            boost_callback=boost_callback or getattr(current,"research_boost_callback",None),
            ad_callback=ad_callback or getattr(current,"research_ad_callback",None))

    def preview(current,args):
        return engine(current,args["scope"]).preview(options(args["options"]))

    def run(current,args):
        settings=options(args["options"]);runner=engine(current,args["scope"])
        resume=args["resume"]
        if args["scope"]=="daily" and resume:
            saved=runner._load()
            if saved.get("status")=="complete" and not saved.get("pending_action"):
                resume=False
        return runner.run(settings,resume=resume,wait=False,max_actions=3)

    def reconcile(current,args):
        return engine(current,args["scope"]).reconcile_only()

    common=(ArgumentSpec("options",label="Options JSON",default="{}",
                         description="Rarity tiers, either/both matching, ads, speedups, budget and inventory reserve."),
            ArgumentSpec("scope",kind="choice",default="user",choices=("user","daily")))
    service.register(CommandSpec("research-plan","Research","Preview real unresearched owned input pairs without starting research.",
                                 common,evidence="Current combo recipe catalog and owned/researched account state"),preview)
    service.register(CommandSpec("research-run","Research","Advance a saved queue by up to three verified actions; stop and resume between requests.",
                                 common+(ArgumentSpec("resume",kind="bool",default=True,
                                     description="No starts a new queue with the selected settings; unresolved requests must first be reconciled."),),
                                 action=True,spends_resources=True,evidence="Established researchCombo/useResearchSpeedUpItem/completeResearch calls"),run)
    service.register(CommandSpec("research-reconcile","Research","Check fresh research evidence and reconcile its saved local ledger without sending research or ad actions.",
                                 (ArgumentSpec("scope",kind="choice",default="user",choices=("user","daily")),),
                                 action=True,evidence="Fresh task, researched-pair and speedup inventory reads; local ledger only"),reconcile)
