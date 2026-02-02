"""
Football Agent Empire - Entry point.
Thin module that wires up Flask app, security, and imports routes from modules.
"""
import os
import secrets
import logging

from flask import Flask, request, session, abort

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App & config
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Redis connection
redis_url = os.environ.get("REDIS_URL")
_redis_client = None

logger.info(f"REDIS_URL configured: {bool(redis_url)}")
if redis_url:
    # Mask the URL for logging
    logger.info(f"REDIS_URL starts with: {redis_url[:20]}...")

if redis_url:
    from flask_session import Session
    import redis as _redis
    try:
        _redis_client = _redis.from_url(redis_url)
        # Test the connection
        _redis_client.ping()
        logger.info("Redis connection successful!")
        app.config["SESSION_TYPE"] = "redis"
        app.config["SESSION_REDIS"] = _redis_client
        app.config["SESSION_USE_SIGNER"] = True
        app.config["SESSION_KEY_PREFIX"] = "fae:"
        Session(app)
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        _redis_client = None
        app.config["SESSION_TYPE"] = "filesystem"
else:
    logger.warning("No REDIS_URL set, using filesystem sessions")
    app.config["SESSION_TYPE"] = "filesystem"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES", "0") == "1",
    PERMANENT_SESSION_LIFETIME=86400,
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

storage_uri = redis_url if redis_url else "memory://"
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per minute"],
    storage_uri=storage_uri,
)

# ---------------------------------------------------------------------------
# Security headers (flask-talisman)
# ---------------------------------------------------------------------------
from flask_talisman import Talisman

csp = {
    "default-src": "'self'",
    "script-src": ["'self'", "https://cdn.tailwindcss.com", "'unsafe-inline'"],
    "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    "img-src": "'self' data:",
    "connect-src": "'self'",
    "font-src": ["'self'", "https://fonts.gstatic.com"],
    "object-src": "'none'",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

force_https = os.environ.get("FORCE_HTTPS", "0") == "1"
Talisman(
    app,
    force_https=force_https,
    content_security_policy=csp,
    session_cookie_secure=os.environ.get("SECURE_COOKIES", "0") == "1",
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,
    x_content_type_options=True,
    x_xss_protection=True,
    referrer_policy="strict-origin-when-cross-origin",
)

# ---------------------------------------------------------------------------
# CSRF token management
# ---------------------------------------------------------------------------
@app.before_request
def ensure_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


def check_csrf():
    token = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
    )
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(403, "CSRF validation failed")


@app.after_request
def add_extra_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp

# ---------------------------------------------------------------------------
# Database init
# ---------------------------------------------------------------------------
from db import init_db, close_db

init_db()
app.teardown_appcontext(close_db)

# ---------------------------------------------------------------------------
# Register blueprints
# ---------------------------------------------------------------------------
from routes import pages_bp, auth_bp, game_bp, admin_bp, init_routes

init_routes(_redis_client, check_csrf, limiter)

# Rate limit only login/register to prevent brute force (not /api/me which is called frequently)
# Admin money endpoint is rate limited separately in routes.py

app.register_blueprint(pages_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(game_bp)
app.register_blueprint(admin_bp)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
