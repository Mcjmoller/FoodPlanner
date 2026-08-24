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


# Store preference is kept in a cookie rather than the database: it is a
# per-browser display choice, and a cookie means a phone and a laptop can each
# follow their own local shops without one overwriting the other.
STORE_COOKIE = "fp_stores"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # one year


def selected_stores(request):
    """
    Returns the chosen store names, or None when the visitor has not chosen yet
    (which is what triggers the first-visit picker).
    """
    raw = request.cookies.get(STORE_COOKIE)
    if not raw:
        return None
    names = [n for n in raw.split("|") if n in engine.STORE_CATALOG]
    return names or None


def _base_context(request, page):
    chosen = selected_stores(request)
    return {
        "request": request,
        "page": page,
        "catalog": engine.STORE_CATALOG,
        "chosen_stores": chosen or engine.DEFAULT_STORES,
        # Drives the first-visit modal in base.html
        "needs_store_choice": chosen is None,
    }


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


@app.post("/stores")
def save_stores(request: Request, stores: list[str] = Form(default=[])):
    """
    Persists the store choice. Submitting nothing still writes the cookie (with
    the defaults) so the picker does not reappear on every page load.
    """
    valid = [s for s in stores if s in engine.STORE_CATALOG] or engine.DEFAULT_STORES
    response = _redirect(request.headers.get("referer") or "/")
    response.set_cookie(
        STORE_COOKIE, "|".join(valid),
        max_age=COOKIE_MAX_AGE, samesite="lax", path="/",
    )
    return response


@app.post("/run")
def run_pipeline(request: Request, refresh: str = Form(default="")):
    """
    Regenerates the plan. Uses cached deals unless 'refresh' is set, in which case
    every store is re-scraped (slow - roughly 30 seconds).
    """
    picked = selected_stores(request)
    run_id = store.start_run()
    try:
        # "Generer igen" replans from cached prices and is instant. Only the
        # explicit refresh is allowed to open a browser and re-scrape.
        deals = (engine.collect_deals(force_refresh=True, store_names=picked)
                 if refresh else engine.cached_deals(store_names=picked))

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
    picked = selected_stores(request)
    # Never scrapes; refresh is an explicit action on the dashboard.
    deals = engine.cached_deals(store_names=picked)
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
        "stores": list(engine.resolve_stores(picked).keys()),
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
