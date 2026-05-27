from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, Response
import sqlite3, os, csv, io, math

app = FastAPI(title="Traffic Data")

BASE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


def resolve_db(env_key, bundled_name, local_rel):
    candidates = [
        os.environ.get(env_key, ""),
        os.path.join(BASE, "data", bundled_name),
        os.path.join(BASE, local_rel),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return candidates[-1]


TL_DB  = resolve_db("TL_DB",  "traffic.db", os.path.join("..", "ทล", "traffic.db"))
TCH_DB = resolve_db("TCH_DB", "aadt.db",    os.path.join("..", "ทช", "aadt.db"))

PAGE_SIZE = 50


def get_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def build_where(conds):
    return ("WHERE " + " AND ".join(conds)) if conds else ""


def paginate(page, total_pages, window=2):
    pages = sorted({1, total_pages,
                    *range(max(1, page - window), min(total_pages + 1, page + window + 1))})
    result, prev = [], None
    for p in pages:
        if prev and p - prev > 1:
            result.append(0)
        result.append(p)
        prev = p
    return result


# ─── Home ────────────────────────────────────────────────────────────────────

@app.head("/")
def health():
    return Response()

@app.get("/debug")
def debug():
    return {
        "BASE": BASE,
        "TL_DB": TL_DB,
        "TL_DB_exists": os.path.exists(TL_DB),
        "TCH_DB": TCH_DB,
        "TCH_DB_exists": os.path.exists(TCH_DB),
        "data_dir": os.listdir(os.path.join(BASE, "data")) if os.path.exists(os.path.join(BASE, "data")) else "missing",
    }

@app.get("/")
def index(request: Request):
    with get_conn(TL_DB) as c:
        tl_count = c.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]
        tl_years = [r[0] for r in c.execute(
            "SELECT DISTINCT year_be FROM traffic ORDER BY year_be")]
    with get_conn(TCH_DB) as c:
        tch_count = c.execute("SELECT COUNT(*) FROM aadt").fetchone()[0]
        tch_years = [r[0] for r in c.execute(
            "SELECT DISTINCT budget_year FROM aadt ORDER BY budget_year")]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tl_count": f"{tl_count:,}", "tl_years": tl_years,
        "tch_count": f"{tch_count:,}", "tch_years": tch_years,
    })


# ─── ทล (Department of Highways) ─────────────────────────────────────────────

@app.get("/tl")
def tl(request: Request,
       year: str = "", province: str = "",
       highway_no: str = "", search: str = "", page: int = 1):
    with get_conn(TL_DB) as conn:
        cur = conn.cursor()
        years = [r[0] for r in cur.execute(
            "SELECT DISTINCT year_be FROM traffic ORDER BY year_be DESC")]
        provinces = [r[0] for r in cur.execute(
            "SELECT DISTINCT province FROM traffic WHERE province != '' ORDER BY province")]

        conds, params = [], []
        if year:        conds.append("year_be = ?");       params.append(int(year))
        if province:    conds.append("province = ?");      params.append(province)
        if highway_no:  conds.append("highway_no LIKE ?"); params.append(f"%{highway_no}%")
        if search:      conds.append("road_name LIKE ?");  params.append(f"%{search}%")
        where = build_where(conds)

        total  = cur.execute(f"SELECT COUNT(*) FROM traffic {where}", params).fetchone()[0]
        offset = (page - 1) * PAGE_SIZE
        rows   = cur.execute(
            f"""SELECT year_be, highway_no, control_section, road_name, survey_point,
                       total, heavy_pct, district, province
                FROM traffic {where}
                ORDER BY year_be DESC, CAST(highway_no AS INTEGER)
                LIMIT ? OFFSET ?""",
            params + [PAGE_SIZE, offset],
        ).fetchall()

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    return templates.TemplateResponse("tl.html", {
        "request": request, "rows": rows,
        "years": years, "provinces": provinces,
        "year": year, "province": province,
        "highway_no": highway_no, "search": search,
        "page": page, "total": total,
        "total_pages": total_pages,
        "page_nums": paginate(page, total_pages),
    })


@app.get("/tl/download")
def tl_download(year: str = "", province: str = "",
                highway_no: str = "", search: str = ""):
    with get_conn(TL_DB) as conn:
        cur = conn.cursor()
        conds, params = [], []
        if year:       conds.append("year_be = ?");       params.append(int(year))
        if province:   conds.append("province = ?");      params.append(province)
        if highway_no: conds.append("highway_no LIKE ?"); params.append(f"%{highway_no}%")
        if search:     conds.append("road_name LIKE ?");  params.append(f"%{search}%")
        where = build_where(conds)
        cur.execute(
            f"SELECT * FROM traffic {where} ORDER BY year_be DESC, highway_no", params)
        keys = [d[0] for d in cur.description]
        rows = [tuple(r) for r in cur.fetchall()]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(keys)
    w.writerows(rows)
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tl_data.csv"},
    )


# ─── ทช (Department of Rural Roads) ──────────────────────────────────────────

@app.get("/tch")
def tch(request: Request,
        year: str = "", road_code: str = "",
        search: str = "", page: int = 1):
    with get_conn(TCH_DB) as conn:
        cur = conn.cursor()
        years = [r[0] for r in cur.execute(
            "SELECT DISTINCT budget_year FROM aadt ORDER BY budget_year DESC")]

        conds, params = [], []
        if year:      conds.append("budget_year = ?");  params.append(int(year))
        if road_code: conds.append("road_code LIKE ?"); params.append(f"%{road_code}%")
        if search:    conds.append("road_name LIKE ?"); params.append(f"%{search}%")
        where = build_where(conds)

        total  = cur.execute(f"SELECT COUNT(*) FROM aadt {where}", params).fetchone()[0]
        offset = (page - 1) * PAGE_SIZE
        rows   = cur.execute(
            f"""SELECT budget_year, road_code, road_name,
                       mc, sv, svt, tb2, tb3, t4, art3, art4, art5, art6, bd, drt, sum_aadt
                FROM aadt {where}
                ORDER BY budget_year DESC, road_code
                LIMIT ? OFFSET ?""",
            params + [PAGE_SIZE, offset],
        ).fetchall()

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    return templates.TemplateResponse("tch.html", {
        "request": request, "rows": rows,
        "years": years, "year": year,
        "road_code": road_code, "search": search,
        "page": page, "total": total,
        "total_pages": total_pages,
        "page_nums": paginate(page, total_pages),
    })


@app.get("/tch/download")
def tch_download(year: str = "", road_code: str = "", search: str = ""):
    with get_conn(TCH_DB) as conn:
        cur = conn.cursor()
        conds, params = [], []
        if year:      conds.append("budget_year = ?");  params.append(int(year))
        if road_code: conds.append("road_code LIKE ?"); params.append(f"%{road_code}%")
        if search:    conds.append("road_name LIKE ?"); params.append(f"%{search}%")
        where = build_where(conds)
        cur.execute(
            f"SELECT * FROM aadt {where} ORDER BY budget_year DESC, road_code", params)
        keys = [d[0] for d in cur.description]
        rows = [tuple(r) for r in cur.fetchall()]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(keys)
    w.writerows(rows)
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tch_data.csv"},
    )
