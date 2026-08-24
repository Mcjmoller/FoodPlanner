"""
FoodPlanner web app.

Server-rendered FastAPI + Jinja2. Deliberately dependency-light: no CDN, no
frontend framework, no build step - the whole UI is one stylesheet and a few
lines of vanilla JS, so it runs offline and there is nothing to keep updated.

Run it with:
    python -m uvicorn web.app:app --reload --app-dir src
"""
import os
import sys

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# main.py lives one level up; the app is started with --app-dir src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as engine
import store

HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="FoodPlanner")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


@app.on_event("startup")
def _startup():
    store.init_db()
    store.seed_from_fallback_json()


def _base_context(request, page):
    return {"request": request, "page": page}


def _redirect(url):
    # 303 so the browser re-issues as GET after a form POST
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------
#  Dashboard
# ------------------------------------------------------------------

@app.get("/")
def dashboard(request: Request):
    plan = store.get_latest_plan()
    stats = None
    if plan:
        matched = [i for i in plan["shopping"] if i.get("found_name")]
        stats = {
            # NB: not "items" - Jinja resolves stats.items to dict.items, the method.
            "count": len(plan["shopping"]),
            "matched": len(matched),
            "total": sum(i.get("price") or 0 for i in plan["shopping"]),
            "savings": plan["savings"],
        }
        # Regroup by store for display; the stored list is flat.
        grouped = {}
        for item in plan["shopping"]:
            grouped.setdefault(item.get("store") or "Ukendt", []).append(item)
        plan["grouped"] = dict(sorted(grouped.items(),
                                      key=lambda kv: (kv[0] == "General/Other", kv[0])))

    return templates.TemplateResponse("dashboard.html", {
        **_base_context(request, "dashboard"),
        "plan": plan,
        "stats": stats,
        "runs": store.list_runs(5),
    })


@app.post("/run")
def run_pipeline(refresh: str = Form(default="")):
    """
    Regenerates the plan. Uses cached deals unless 'refresh' is set, in which case
    every store is re-scraped (slow - roughly 30 seconds).
    """
    run_id = store.start_run()
    try:
        # "Generer igen" replans from cached prices and is instant. Only the
        # explicit refresh is allowed to open a browser and re-scrape.
        deals = engine.collect_deals(force_refresh=True) if refresh else engine.cached_deals()

        # A Gemini key is optional here: without one the deterministic scorer runs,
        # so the app is still usable before anything is configured.
        client = None
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if api_key:
            try:
                client = engine.make_gemini_client(api_key)
            except Exception as e:
                engine.logger.warning(f"[WARNING] Gemini unavailable: {e}")

        result = engine.generate_plan(
            store.buying_as_lines(), store.pantry_as_lines(), deals, client)
        store.save_plan(result["schedule"], result["flat"],
                        result["savings"], result["source"])
        store.finish_run(run_id, "ok", result["deals_found"],
                         f"{result['source']} plan, {len(result['flat'])} items")
    except Exception as e:
        store.finish_run(run_id, "failed", 0, str(e))
    return _redirect("/")


# ------------------------------------------------------------------
#  Pantry
# ------------------------------------------------------------------

@app.get("/pantry")
def pantry_page(request: Request):
    return templates.TemplateResponse("pantry.html", {
        **_base_context(request, "pantry"),
        "items": store.get_pantry(),
    })


@app.post("/pantry/add")
def pantry_add(name: str = Form(...), qty: str = Form(default=""), unit: str = Form(default="stk")):
    if name.strip():
        parsed_qty = None
        try:
            parsed_qty = float(qty.replace(",", ".")) if qty.strip() else None
        except ValueError:
            parsed_qty = None
        store.upsert_pantry_item(name, parsed_qty, unit.strip() or "stk")
    return _redirect("/pantry")


@app.post("/pantry/{item_id}/delete")
def pantry_delete(item_id: int):
    store.delete_pantry_item(item_id)
    return _redirect("/pantry")


# ------------------------------------------------------------------
#  Buying list
# ------------------------------------------------------------------

@app.get("/buying")
def buying_page(request: Request):
    return templates.TemplateResponse("buying.html", {
        **_base_context(request, "buying"),
        "items": store.get_buying_list(),
    })


@app.post("/buying/add")
def buying_add(name: str = Form(...)):
    if name.strip():
        store.add_buying_item(name)
    return _redirect("/buying")


@app.post("/buying/{item_id}/toggle")
def buying_toggle(item_id: int):
    store.toggle_buying_item(item_id)
    return _redirect("/buying")


@app.post("/buying/{item_id}/delete")
def buying_delete(item_id: int):
    store.delete_buying_item(item_id)
    return _redirect("/buying")


# ------------------------------------------------------------------
#  Deals browser
# ------------------------------------------------------------------

@app.get("/deals")
def deals_page(request: Request, q: str = "", shop: str = ""):
    deals = engine.cached_deals()  # never scrapes; refresh is an explicit action
    if shop:
        deals = [d for d in deals if d["store"] == shop]
    if q:
        needle = q.lower()
        deals = [d for d in deals if needle in d["item"].lower()]
    deals.sort(key=lambda d: d["price"])

    return templates.TemplateResponse("deals.html", {
        **_base_context(request, "deals"),
        "deals": deals[:300],
        "total": len(deals),
        "stores": list(engine.STORES.keys()),
        "q": q,
        "shop": shop,
    })


# ------------------------------------------------------------------
#  Plan history
# ------------------------------------------------------------------

@app.get("/plans")
def plans_page(request: Request):
    return templates.TemplateResponse("plans.html", {
        **_base_context(request, "plans"),
        "plans": store.list_plans(30),
    })


@app.get("/plans/{plan_id}")
def plan_detail(request: Request, plan_id: int):
    plan = store.get_plan(plan_id)
    if not plan:
        return _redirect("/plans")
    grouped = {}
    for item in plan["shopping"]:
        grouped.setdefault(item.get("store") or "Ukendt", []).append(item)
    plan["grouped"] = grouped
    return templates.TemplateResponse("plan_detail.html", {
        **_base_context(request, "plans"),
        "plan": plan,
    })
