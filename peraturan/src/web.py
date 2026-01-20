"""Web interface for Peraturan Crawler."""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import Config
from src.services.database import Database

app = FastAPI(title="Peraturan Viewer", description="인도네시아 법령 뷰어")

# Setup templates
templates_dir = Path(__file__).parent.parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Config
config = Config()


def get_db():
    """Get database connection."""
    db = Database(config=config)
    db.connect()
    return db


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with statistics."""
    db = get_db()
    try:
        stats = db.get_statistics()
        crawl_state = db.get_crawl_state()
        download_state = db.get_download_state()

        # Get recent documents
        cursor = db._connection.cursor()
        cursor.execute("""
            SELECT slug, jenis, nomor, tahun, tentang, status, local_pdf_path
            FROM peraturan
            ORDER BY created_at DESC, tahun DESC
            LIMIT 10
        """)
        recent_docs = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        return templates.TemplateResponse("index.html", {
            "request": request,
            "stats": stats,
            "crawl_state": crawl_state,
            "download_state": download_state,
            "recent_docs": recent_docs,
        })
    finally:
        db.close()


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: Optional[str] = Query(None, description="Search query"),
    jenis: Optional[str] = Query(None, description="Document type"),
    tahun: Optional[str] = Query(None, description="Year"),
    has_pdf: Optional[str] = Query(None, description="PDF availability"),
    status: Optional[str] = Query(None, description="Document status"),
    sort: Optional[str] = Query("enacted_desc", description="Sort order"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Search page."""
    db = get_db()
    try:
        # Build SQL query
        conditions = []
        params = []

        if q:
            conditions.append("tentang LIKE ?")
            params.append(f"%{q}%")

        if jenis:
            conditions.append("jenis = ?")
            params.append(jenis.upper())

        if tahun:
            if "-" in tahun:
                start, end = tahun.split("-")
                conditions.append("tahun BETWEEN ? AND ?")
                params.extend([int(start), int(end)])
            else:
                conditions.append("tahun = ?")
                params.append(int(tahun))

        if has_pdf == "yes":
            conditions.append("local_pdf_path IS NOT NULL AND local_pdf_path != ''")
        elif has_pdf == "no":
            conditions.append("(local_pdf_path IS NULL OR local_pdf_path = '')")

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Count total
        count_sql = f"SELECT COUNT(*) FROM peraturan WHERE {where_clause}"
        cursor = db._connection.cursor()
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

        # Get results with pagination
        offset = (page - 1) * limit

        # Determine sort order
        sort_map = {
            "tahun_desc": "tahun DESC, nomor ASC",
            "tahun_asc": "tahun ASC, nomor ASC",
            "enacted_desc": "tanggal_penetapan DESC, tahun DESC",
            "enacted_asc": "tanggal_penetapan ASC, tahun ASC",
        }
        order_by = sort_map.get(sort, "tanggal_penetapan DESC, tahun DESC")

        sql = f"""
            SELECT slug, jenis, nomor, tahun, tentang, status, local_pdf_path, tanggal_penetapan
            FROM peraturan
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        cursor.execute(sql, params + [limit, offset])
        results = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        # Get unique types for filter
        cursor = db._connection.cursor()
        cursor.execute("SELECT DISTINCT jenis FROM peraturan ORDER BY jenis")
        types = [row[0] for row in cursor.fetchall()]
        cursor.close()

        total_pages = (total + limit - 1) // limit

        return templates.TemplateResponse("search.html", {
            "request": request,
            "results": results,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "q": q or "",
            "jenis": jenis.upper() if jenis else "",
            "tahun": tahun or "",
            "has_pdf": has_pdf or "",
            "status": status or "",
            "sort": sort or "enacted_desc",
            "types": types,
        })
    finally:
        db.close()


@app.get("/detail/{slug}", response_class=HTMLResponse)
async def detail(request: Request, slug: str):
    """Detail page for a single document."""
    db = get_db()
    try:
        peraturan = db.get_peraturan(slug)
        if not peraturan:
            return templates.TemplateResponse("404.html", {
                "request": request,
                "message": f"Document '{slug}' not found",
            }, status_code=404)

        return templates.TemplateResponse("detail.html", {
            "request": request,
            "peraturan": peraturan,
        })
    finally:
        db.close()


@app.get("/pdf/{slug}")
async def get_pdf(slug: str):
    """Serve PDF file for inline viewing (preview)."""
    db = get_db()
    try:
        peraturan = db.get_peraturan(slug)
        if not peraturan or not peraturan.local_pdf_path:
            return {"error": "PDF not found"}

        pdf_path = Path(peraturan.local_pdf_path)
        if not pdf_path.exists():
            return {"error": "PDF file not found on disk"}

        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"}
        )
    finally:
        db.close()


@app.get("/pdf/{slug}/download")
async def download_pdf(slug: str):
    """Download PDF file as attachment."""
    db = get_db()
    try:
        peraturan = db.get_peraturan(slug)
        if not peraturan or not peraturan.local_pdf_path:
            return {"error": "PDF not found"}

        pdf_path = Path(peraturan.local_pdf_path)
        if not pdf_path.exists():
            return {"error": "PDF file not found on disk"}

        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"{slug}.pdf"
        )
    finally:
        db.close()


@app.get("/api/stats")
async def api_stats():
    """API endpoint for statistics."""
    db = get_db()
    try:
        return db.get_statistics()
    finally:
        db.close()


@app.get("/api/download-status")
async def api_download_status():
    """API endpoint for real-time download status."""
    db = get_db()
    try:
        cursor = db._connection.cursor()

        # Get total count
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        total = cursor.fetchone()[0]

        # Get counts by download status (derived from local_pdf_path and pdf_url)
        # Downloaded: has local PDF file
        cursor.execute("SELECT COUNT(*) FROM peraturan WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''")
        downloaded = cursor.fetchone()[0]

        # No PDF: no PDF URL available
        cursor.execute("SELECT COUNT(*) FROM peraturan WHERE pdf_url IS NULL OR pdf_url = ''")
        no_pdf = cursor.fetchone()[0]

        # Pending: has PDF URL but not downloaded yet
        cursor.execute("""
            SELECT COUNT(*) FROM peraturan
            WHERE pdf_url IS NOT NULL AND pdf_url != ''
            AND (local_pdf_path IS NULL OR local_pdf_path = '')
        """)
        pending = cursor.fetchone()[0]

        # crawled = downloaded + pending (items with PDF URL)
        crawled = downloaded + pending

        # Get download state for session info
        download_state = db.get_download_state()

        # Check if scheduler is running (check for recent updates)
        cursor.execute("""
            SELECT last_updated_at FROM download_state WHERE id = 1
        """)
        row = cursor.fetchone()

        is_active = False
        if row and row[0]:
            from datetime import datetime, timedelta
            try:
                last_update = datetime.fromisoformat(row[0])
                is_active = (datetime.now() - last_update) < timedelta(minutes=15)
            except:
                pass

        cursor.close()

        # Get failed items count
        cursor = db._connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM failed_items WHERE item_type = 'metadata'")
        metadata_errors = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM failed_items WHERE item_type = 'pdf'")
        pdf_errors = cursor.fetchone()[0]
        cursor.close()

        return {
            "total": total,
            "crawled": crawled,
            "downloaded": downloaded,
            "pending": pending,
            "no_pdf": no_pdf,
            "remaining": pending,  # remaining = pending (not no_pdf)
            "percentage": round(crawled / total * 100, 1) if total > 0 else 0,
            "download_percentage": round(downloaded / crawled * 100, 1) if crawled > 0 else 0,
            "is_active": is_active,
            "errors": {
                "metadata": metadata_errors,
                "pdf": pdf_errors,
                "total": metadata_errors + pdf_errors,
            },
            "session": {
                "target": download_state.total_pdfs if download_state else 0,
                "completed": download_state.completed_count if download_state else 0,
                "status": download_state.status if download_state else "idle",
            }
        }
    finally:
        db.close()


@app.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request):
    """Errors detail page."""
    db = get_db()
    try:
        cursor = db._connection.cursor()
        # JOIN with peraturan to get source_url for PDF errors
        cursor.execute("""
            SELECT
                f.id,
                f.url,
                f.item_type,
                f.error_message,
                f.retry_count,
                f.failed_at,
                p.source_url,
                p.slug,
                p.tentang
            FROM failed_items f
            LEFT JOIN peraturan p ON f.url = p.pdf_url
            ORDER BY f.failed_at DESC
        """)
        errors = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        # Get category stats from DB
        cursor = db._connection.cursor()
        cursor.execute("""
            SELECT
                jenis,
                COUNT(*) as total,
                SUM(CASE WHEN pdf_url IS NOT NULL AND pdf_url != '' THEN 1 ELSE 0 END) as has_pdf_url,
                SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
            FROM peraturan
            GROUP BY jenis
            ORDER BY total DESC
        """)
        db_stats = {row[0]: {"total": row[1], "has_pdf_url": row[2], "downloaded": row[3]} for row in cursor.fetchall()}
        cursor.close()

        # RFP (제안요청서) 기준 매핑
        rfp_categories = [
            {"code": "UUD", "name": "Undang-Undang Dasar", "name_kr": "헌법", "rfp_count": 1, "target": True, "db_jenis": None},
            {"code": "TAP MPR", "name": "Ketetapan Majelis Permusyawaratan Rakyat", "name_kr": "국민협의회 결의", "rfp_count": 41, "target": True, "db_jenis": None},
            {"code": "UU", "name": "Undang-Undang", "name_kr": "법률", "rfp_count": 1902, "target": True, "db_jenis": "UNDANG-UNDANG"},
            {"code": "UUDRT", "name": "Undang-Undang Darurat", "name_kr": "긴급법령", "rfp_count": 177, "target": True, "db_jenis": None},
            {"code": "UUDS", "name": "Undang-Undang Dasar Sementara", "name_kr": "임시헌법", "rfp_count": 1, "target": True, "db_jenis": None},
            {"code": "PERPPU", "name": "Peraturan Pemerintah Pengganti UU", "name_kr": "대체법령", "rfp_count": 217, "target": True, "db_jenis": "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG"},
            {"code": "PP", "name": "Peraturan Pemerintah", "name_kr": "정부령", "rfp_count": 4939, "target": True, "db_jenis": "PERATURAN PEMERINTAH"},
            {"code": "PERPRES", "name": "Peraturan Presiden", "name_kr": "대통령령", "rfp_count": 2580, "target": True, "db_jenis": "PERATURAN PRESIDEN"},
            {"code": "PENPRES", "name": "Penetapan Presiden", "name_kr": "대통령 확정", "rfp_count": 76, "target": True, "db_jenis": None},
            {"code": "KEPPRES", "name": "Keputusan Presiden", "name_kr": "대통령 결정", "rfp_count": 5339, "target": False, "db_jenis": "KEPUTUSAN PRESIDEN"},
            {"code": "INPRES", "name": "Instruksi Presiden", "name_kr": "대통령 지시", "rfp_count": 390, "target": False, "db_jenis": None},
            {"code": "PERMEN", "name": "Peraturan Menteri", "name_kr": "장관령", "rfp_count": 18880, "target": True, "db_jenis": "PERATURAN MENTERI"},
            {"code": "PERBAN", "name": "Peraturan Badan/Lembaga", "name_kr": "기관규정", "rfp_count": 6242, "target": True, "db_jenis": "PERATURAN BADAN/LEMBAGA"},
            {"code": "PERDA", "name": "Peraturan Daerah", "name_kr": "지방규정", "rfp_count": 19603, "target": False, "db_jenis": "PERATURAN DAERAH"},
            {"code": "번역법령", "name": "Terjemah Resmi Peraturan", "name_kr": "번역법령", "rfp_count": 368, "target": True, "db_jenis": None},
        ]

        # Build comparison stats
        category_stats = []
        for cat in rfp_categories:
            db_data = db_stats.get(cat["db_jenis"], {"total": 0, "has_pdf_url": 0, "downloaded": 0})
            category_stats.append({
                "code": cat["code"],
                "name": cat["name"],
                "name_kr": cat["name_kr"],
                "rfp_count": cat["rfp_count"],
                "target": cat["target"],
                "db_total": db_data["total"],
                "has_pdf_url": db_data["has_pdf_url"],
                "downloaded": db_data["downloaded"],
                "diff": db_data["total"] - cat["rfp_count"],
                "no_pdf": db_data["total"] - db_data["has_pdf_url"],
                "remaining": db_data["has_pdf_url"] - db_data["downloaded"],
            })

        return templates.TemplateResponse("errors.html", {
            "request": request,
            "errors": errors,
            "category_stats": category_stats,
        })
    finally:
        db.close()


@app.get("/api/errors")
async def api_errors():
    """API endpoint for error details."""
    db = get_db()
    try:
        cursor = db._connection.cursor()
        cursor.execute("""
            SELECT id, url, item_type, error_message, retry_count, failed_at
            FROM failed_items
            ORDER BY failed_at DESC
        """)
        errors = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        return {"errors": errors, "total": len(errors)}
    finally:
        db.close()


@app.get("/api/search")
async def api_search(
    q: Optional[str] = Query(None),
    jenis: Optional[str] = Query(None),
    tahun: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """API endpoint for search."""
    db = get_db()
    try:
        conditions = []
        params = []

        if q:
            conditions.append("tentang LIKE ?")
            params.append(f"%{q}%")

        if jenis:
            conditions.append("jenis = ?")
            params.append(jenis.upper())

        if tahun:
            conditions.append("tahun = ?")
            params.append(int(tahun))

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT slug, jenis, nomor, tahun, tentang, status, pdf_url, local_pdf_path
            FROM peraturan
            WHERE {where_clause}
            ORDER BY tahun DESC, nomor ASC
            LIMIT ?
        """

        cursor = db._connection.cursor()
        cursor.execute(sql, params + [limit])
        results = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        return {"results": results, "total": len(results)}
    finally:
        db.close()


@app.get("/api/category-stats")
async def api_category_stats():
    """API endpoint for category statistics."""
    db = get_db()
    try:
        cursor = db._connection.cursor()
        cursor.execute("""
            SELECT
                jenis,
                COUNT(*) as total,
                SUM(CASE WHEN pdf_url IS NOT NULL AND pdf_url != '' THEN 1 ELSE 0 END) as has_pdf_url,
                SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
            FROM peraturan
            GROUP BY jenis
            ORDER BY total DESC
        """)
        stats = []
        for row in cursor.fetchall():
            jenis = row[0]
            total = row[1]
            has_pdf_url = row[2]
            downloaded = row[3]
            no_pdf = total - has_pdf_url

            stats.append({
                "jenis": jenis,
                "total": total,
                "has_pdf_url": has_pdf_url,
                "downloaded": downloaded,
                "no_pdf": no_pdf,
                "remaining": has_pdf_url - downloaded,
            })
        cursor.close()

        return {"stats": stats}
    finally:
        db.close()


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
