"""Outfit Matcher: comptes, garde-robes et photos autonomes pour une VM Oracle."""
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import fcntl
import shutil
import smtplib
import ssl
import logging
import tempfile
import zipfile
from email.message import EmailMessage
from starlette.background import BackgroundTask
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from inspiration import canonical_fields, profile as inspiration_profile, reference_candidates, search_queries, ranked_web_candidates, integrated_candidates
from photo_preparation import prepare_garment, preparation_available
from fitting_state import clean_avatar, clean_fitting, clean_cutout_bounds
import admin_stats
import reminders

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('OUTFIT_DATA', str(ROOT / 'data')))
DB_PATH = DATA / 'outfit.sqlite3'
COOKIE = 'outfit_session'
SECURE = os.environ.get('COOKIE_SECURE', '1') != '0'
ORIGIN = os.environ.get('PUBLIC_ORIGIN', '').rstrip('/')
SESSION_SECONDS = 30 * 24 * 3600
PHOTO_ID = re.compile(r'^[a-f0-9-]{36}$')
CATEGORIES = {'haut', 'bas', 'robe', 'veste', 'chaussures', 'accessoire'}
OCCASIONS = {'quotidien', 'cours', 'travail', 'sortie', 'fete', 'date', 'sport'}
Image.MAX_IMAGE_PIXELS = 25_000_000
INSPIRATION_CACHE = OrderedDict()
INSPIRATION_PENDING = {}
INSPIRATION_SEMAPHORE = asyncio.Semaphore(3)
INSPIRATION_COOLDOWN = 0


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'photos').mkdir(exist_ok=True)
    with database() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS wardrobes (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          settings TEXT NOT NULL DEFAULT '{}', items TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS auth_attempts (
          ip TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS password_resets (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS demo_access (
          token_hash TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS demo_sessions (
          token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS attempts_ip_time ON auth_attempts(ip, at);
        ''')


def provision_demos():
    seed = ROOT / 'ops' / 'demo-seed.json'
    if not seed.exists():
        return
    with database() as db:
        for account in json.loads(seed.read_text())['accounts']:
            existing = db.execute('SELECT id FROM users WHERE id=? OR email=?', (account['id'], account['email'])).fetchone()
            if existing and existing['id'] != account['id']:
                raise RuntimeError('Adresse de démonstration déjà utilisée : aucun compte existant modifié.')
            if not existing:
                db.execute('INSERT INTO users VALUES(?,?,?,?)', (account['id'], account['email'], account['name'], account['password_hash']))
                db.execute('INSERT INTO wardrobes VALUES(?,?,?)', (account['id'], json.dumps({'name': account['name'], 'city': 'Strasbourg', 'style': 'casual', 'hair': account['hair'], 'onboardingDone': True, 'lookView': 'pieces'}), '[]'))
            # Never reset a wardrobe or password when redeploying.
            db.execute('DELETE FROM demo_access WHERE user_id=? AND token_hash<>?', (account['id'], account['token_hash']))
            db.execute('INSERT OR IGNORE INTO demo_access VALUES(?,?)', (account['token_hash'], account['id']))


def demo_session(request):
    token = request.cookies.get(COOKIE, '')
    if not token:
        return False
    with database() as db:
        return bool(db.execute('SELECT 1 FROM demo_sessions JOIN sessions USING(token_hash) WHERE token_hash=? AND expires_at>?', (token_hash(token), time.time())).fetchone())


def user_payload(user, request):
    with database() as db:
        administrator = admin_stats.is_admin(db, user['id'])
    return {'name': user['name'], 'email': user['email'], 'demo': demo_session(request), 'admin': administrator}


@asynccontextmanager
async def lifespan(app):
    initialize()
    provision_demos()
    with database() as db:
        admin_stats.initialize(db)
        key_path = reminders.initialize(db, DATA)
    task = asyncio.create_task(reminders.run(database, key_path, reminder_subject())) if reminder_subject() else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protection(request: Request, call_next):
    # Cookie authentication: browser writes must carry the same origin.
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        expected = ORIGIN or str(request.base_url).rstrip('/')
        origin = request.headers.get('origin')
        referer = request.headers.get('referer', '')
        if not origin and referer:
            parts = urlsplit(referer)
            origin = f'{parts.scheme}://{parts.netloc}'
        if origin != expected:
            return JSONResponse({'error': 'Requête non autorisée. Actualisez la page.'}, status_code=403)
    if request.url.path.startswith('/api/') and demo_session(request):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.url.path != '/api/auth/logout' and not request.url.path.startswith('/api/auth/'):
            return JSONResponse({'error': 'Mode démonstration : le dressing partagé est protégé.'}, status_code=403)
        if request.url.path == '/api/account/export':
            return JSONResponse({'error': 'Export indisponible en démonstration.'}, status_code=403)
    length = request.headers.get('content-length')
    request_limit = 25_000_000 if request.method == 'PUT' and request.url.path.startswith('/api/photo/') else 5_000_000
    if length and (not length.isdigit() or int(length) > request_limit):
        return JSONResponse({'error': 'Fichier ou requête trop volumineux.'}, status_code=413)
    # Même verrou partagé par les sauvegardes : aucune photo ne disparaît pendant un instantané.
    lock = None
    if request.url.path.startswith('/api/'):
        DATA.mkdir(parents=True, exist_ok=True)
        lock = (DATA / '.storage.lock').open('a')
        await asyncio.to_thread(fcntl.flock, lock, fcntl.LOCK_EX if request.url.path == '/api/account/delete' or request.method == 'DELETE' and request.url.path == '/api/state' else fcntl.LOCK_SH)
    try:
        response = await call_next(request)
    finally:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/') or request.url.path in {'/', '/app.html', '/login.html', '/admin.html'}:
        response.headers['Cache-Control'] = 'no-store'
    elif request.url.path in {'/sw.js', '/manifest.webmanifest'} or request.url.path.endswith(('.js', '.css')):

        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(sqlite3.Error)
async def storage_error(request, exc):
    return JSONResponse({'error': 'Sauvegarde indisponible. Réessayez.'}, status_code=503)


def password_hash(password: str, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return salt + ':' + digest.hex()


def check_password(password, encoded):
    try:
        salt, _ = encoded.split(':')
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except ValueError:
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request):
    token = request.cookies.get(COOKIE, '')
    if not token:
        raise HTTPException(401, 'Connectez-vous pour retrouver votre garde-robe.')
    with database() as db:
        user = db.execute('SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?', (token_hash(token), time.time())).fetchone()
    if not user:
        raise HTTPException(401, 'Votre session a expiré. Reconnectez-vous.')
    expected = request.headers.get('x-outfit-account')
    if expected and expected != user['email']:
        raise HTTPException(409, 'Le compte a changé. Reconnectez-vous avant de modifier le dressing.', headers={'X-Outfit-Session': 'changed'})
    return user


def limit_auth(request):
    ip = request.client.host if request.client else 'unknown'
    with database() as db:
        now = time.time()
        db.execute('DELETE FROM auth_attempts WHERE at<?', (now - 900,))
        count = db.execute('SELECT count(*) FROM auth_attempts WHERE ip=?', (ip,)).fetchone()[0]
        if count >= 25:
            raise HTTPException(429, 'Trop de tentatives. Réessayez dans 15 minutes.')
        db.execute('INSERT INTO auth_attempts VALUES(?,?)', (ip, now))


def session_response(user, request, demo=False):
    token = secrets.token_urlsafe(32)
    with database() as db:
        old_hash = token_hash(request.cookies.get(COOKIE, ''))
        previous = db.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?', (old_hash, time.time())).fetchone()
        duration = 8 * 3600 if demo else SESSION_SECONDS
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), user['id'], time.time() + duration))
        # A normal repeated login to the SAME account keeps its explicitly enabled reminder.
        # An explicit logout/account switch still deletes the subscription through the FK.
        if not demo and previous and previous['user_id'] == user['id']:
            db.execute('UPDATE push_subscriptions SET session_hash=? WHERE session_hash=? AND user_id=?',
                       (token_hash(token), old_hash, user['id']))
        db.execute('DELETE FROM sessions WHERE token_hash=? OR expires_at<?', (old_hash, time.time()))
        if demo:
            db.execute('INSERT INTO demo_sessions VALUES(?)', (token_hash(token),))
    response = JSONResponse({'ok': True, 'user': {'email': user['email'], 'name': user['name']}})
    response.set_cookie(COOKIE, token, max_age=duration, secure=SECURE, httponly=True, samesite='lax', path='/')
    return response


class DemoToken(BaseModel):
    token: str = Field(min_length=32, max_length=128)


@app.post('/api/auth/demo')
def enter_demo(data: DemoToken, request: Request):
    # Separate from password attempts: a classroom shares one public IP.
    with database() as db:
        user = db.execute('SELECT users.* FROM users JOIN demo_access ON users.id=demo_access.user_id WHERE token_hash=?', (token_hash(data.token),)).fetchone()
    if not user:
        raise HTTPException(401, 'Ce lien de démonstration est invalide ou désactivé.')
    return session_response(user, request, demo=True)


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default='', max_length=100)


def clean_email(value):
    email = value.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(400, 'Indiquez une adresse e-mail valide.')
    return email


@app.post('/api/auth/register')
def register(data: Credentials, request: Request):
    limit_auth(request)
    email = clean_email(data.email)
    user_id = str(uuid.uuid4())
    name = data.name.strip() or email.split('@')[0]
    try:
        with database() as db:
            db.execute('INSERT INTO users VALUES(?,?,?,?)', (user_id, email, name, password_hash(data.password)))
            db.execute('INSERT INTO wardrobes(user_id,settings) VALUES(?,?)', (user_id, json.dumps({'name': name, 'locationMode': 'auto', 'style': 'casual'})))
            admin_stats.record_signup(db, user_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Cette adresse possède déjà un compte. Connectez-vous.')
    return session_response({'id': user_id, 'email': email, 'name': name}, request)


class LoginCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


@app.post('/api/auth/login')
def login(data: LoginCredentials, request: Request):
    limit_auth(request)
    with database() as db:
        user = db.execute('SELECT * FROM users WHERE email=?', (clean_email(data.email),)).fetchone()
    # Same expensive computation for unknown and known accounts.
    encoded = user['password_hash'] if user else password_hash('dummy-password')
    correct = check_password(data.password, encoded)
    if not user or not correct:
        raise HTTPException(401, 'Adresse e-mail ou mot de passe incorrect.')
    return session_response(user, request)


@app.post('/api/auth/logout')
def logout(request: Request):
    try:
        current_user(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
    with database() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(request.cookies.get(COOKIE, '')),))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/', secure=SECURE, httponly=True, samesite='lax')
    return response


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPassword(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=10, max_length=128)


class ConfirmPassword(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def recovery_configured():
    return bool(os.environ.get('SMTP_HOST') and os.environ.get('MAIL_FROM') and ORIGIN)


def send_recovery(email, token):
    message = EmailMessage()
    message['Subject'] = 'Outfit Matcher — Réinitialiser votre mot de passe'
    message['From'] = os.environ['MAIL_FROM']
    message['To'] = email
    message.set_content('Vous avez demandé un nouveau mot de passe. Ce lien expire dans 30 minutes et ne fonctionne qu’une fois.\n\n'
                        + ORIGIN + '/login.html#reset=' + token
                        + '\n\nSi vous n’êtes pas à l’origine de cette demande, ignorez ce message.')
    host, port = os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '587'))
    if os.environ.get('SMTP_SECURITY', 'starttls') == 'ssl':
        client = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=10)
        client.starttls(context=ssl.create_default_context())
    with client:
        if os.environ.get('SMTP_USER'):
            client.login(os.environ['SMTP_USER'], os.environ.get('SMTP_PASSWORD', ''))
        client.send_message(message)


@app.get('/api/public-config')
def public_config():
    return {'recovery': recovery_configured(), 'backgroundRemoval': preparation_available(),
            'operator': os.environ.get('APP_OPERATOR', ''), 'supportEmail': os.environ.get('SUPPORT_EMAIL', ''),
            'publicOrigin': ORIGIN, 'version': '28',
            'editorialReferences': os.environ.get('ALLOW_EDITORIAL_REFERENCES') == '1'}


@app.post('/api/auth/forgot-password')
async def forgot_password(data: ResetRequest, request: Request, background_tasks: BackgroundTasks):
    limit_auth(request)
    if not recovery_configured():
        raise HTTPException(503, 'La récupération par e-mail n’est pas encore activée. Contactez l’assistance.')
    email = clean_email(data.email)
    with database() as db:
        user = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        db.execute('DELETE FROM password_resets WHERE expires_at<?', (time.time(),))
    if user:
        token = secrets.token_urlsafe(32)
        with database() as db:
            db.execute('DELETE FROM password_resets WHERE user_id=?', (user['id'],))
            db.execute('INSERT INTO password_resets VALUES(?,?,?)', (token_hash(token), user['id'], time.time()+1800))
        def deliver():
            try:
                send_recovery(email, token)
            except (OSError, smtplib.SMTPException, ValueError):
                with database() as db:
                    db.execute('DELETE FROM password_resets WHERE token_hash=?', (token_hash(token),))
                logging.warning('Outfit Matcher: recovery email delivery failed')
        background_tasks.add_task(deliver)
    return {'ok': True, 'message': 'Si cette adresse possède un compte, un lien de récupération lui sera envoyé. Vérifiez aussi les indésirables.'}


@app.post('/api/auth/reset-password')
def reset_password(data: ResetPassword, request: Request):
    limit_auth(request)
    encoded = password_hash(data.password)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT user_id FROM password_resets WHERE token_hash=? AND expires_at>?', (token_hash(data.token), time.time())).fetchone()
        if not row:
            raise HTTPException(400, 'Lien expiré ou déjà utilisé. Demandez un nouveau lien.')
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, row['user_id']))
        db.execute('DELETE FROM password_resets WHERE user_id=?', (row['user_id'],))
        db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/', secure=SECURE, httponly=True, samesite='lax')
    return response


@app.post('/api/account/delete')
def delete_account(data: ConfirmPassword, request: Request):
    user = current_user(request)
    limit_auth(request)
    if not check_password(data.password, user['password_hash']):
        raise HTTPException(403, 'Mot de passe incorrect. Le compte n’a pas été supprimé.')
    directory = DATA / 'photos' / user['id']
    removed = DATA / ('deleted-' + str(uuid.uuid4()))
    moved = False
    try:
        with database() as db:
            if directory.exists():
                directory.rename(removed)
                moved = True
            db.execute('DELETE FROM users WHERE id=?', (user['id'],))
    except Exception:
        if moved and removed.exists():
            removed.rename(directory)
        raise
    with (DATA / 'deleted-users.jsonl').open('a') as ledger:
        ledger.write(json.dumps({'user_id': user['id'], 'at': time.time()}) + '\n')
        ledger.flush()
        os.fsync(ledger.fileno())
    if moved:
        shutil.rmtree(removed)
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/', secure=SECURE, httponly=True, samesite='lax')
    return response


@app.get('/api/account/export')
def export_account(request: Request):
    user = current_user(request)
    with database() as db:
        row = db.execute('SELECT settings,items FROM wardrobes WHERE user_id=?', (user['id'],)).fetchone()
        schedule = [dict(r) for r in db.execute('SELECT endpoint,hour,minute,days,timezone,consented_at,updated_at FROM push_subscriptions WHERE user_id=?', (user['id'],))]
        activity_days = [r['day'] for r in db.execute('SELECT day FROM daily_activity WHERE user_id=? ORDER BY day', (user['id'],))]
    handle, name = tempfile.mkstemp(prefix='export-', suffix='.zip', dir=DATA)
    os.close(handle)
    try:
        with zipfile.ZipFile(name, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            payload = {'user': user_payload(user, request), 'settings': json.loads(row['settings']) if row else {}, 'items': json.loads(row['items']) if row else []}
            archive.writestr('mon-dressing.json', json.dumps(payload, ensure_ascii=False, indent=2))
            archive.writestr('mes-rappels.json', json.dumps(schedule, ensure_ascii=False, indent=2))
            archive.writestr('mes-jours-activite.json', json.dumps(activity_days))
            directory = DATA / 'photos' / user['id']
            if directory.exists():
                for photo in directory.iterdir():
                    if photo.is_file():
                        archive.write(photo, 'photos/' + photo.name + '.png')
        return FileResponse(name, filename='outfit-matcher-mes-donnees.zip', media_type='application/zip', background=BackgroundTask(Path(name).unlink, missing_ok=True))
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


@app.post('/api/photo/{photo_id}/prepare')
async def prepare_photo(photo_id: str, request: Request):
    user = current_user(request)
    validate_photo('/api/photo/' + photo_id, user['id'])
    try:
        result = await asyncio.to_thread(prepare_garment, DATA / 'photos' / user['id'] / photo_id)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result


@app.get('/.well-known/assetlinks.json')
def asset_links():
    fingerprint = os.environ.get('ANDROID_SHA256', '')
    package = os.environ.get('ANDROID_PACKAGE', 'fr.outfitmatcher.app')
    if not re.fullmatch(r'(?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}', fingerprint):
        return []
    return [{'relation': ['delegate_permission/common.handle_all_urls'], 'target': {'namespace': 'android_app', 'package_name': package, 'sha256_cert_fingerprints': [fingerprint.upper()]}}]


@app.get('/api/auth/session')
def auth_session(request: Request):
    user = current_user(request)
    return {'user': user_payload(user, request)}


@app.get('/api/state')
def get_state(request: Request):
    user = current_user(request)
    with database() as db:
        row = db.execute('SELECT settings,items FROM wardrobes WHERE user_id=?', (user['id'],)).fetchone()
        admin_stats.record_activity(db, user['id'])
    return {'user': user_payload(user, request), 'exists': bool(row), 'settings': json.loads(row['settings']) if row else {}, 'items': json.loads(row['items']) if row else []}


@app.post('/api/activity')
def activity(request: Request):
    user = current_user(request)
    with database() as db:
        admin_stats.record_activity(db, user['id'])
    return {'ok': True}


def reminder_subject():
    if os.environ.get('REMINDERS_ENABLED', '1') == '0':
        return ''
    contact = os.environ.get('SUPPORT_EMAIL', '')
    if ORIGIN.startswith('https://'):
        return 'mailto:'+contact if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact) else ORIGIN
    if ORIGIN.startswith(('http://127.0.0.1:', 'http://localhost:')):
        return 'mailto:developer@localhost'
    return ''


def reminder_user(request):
    user = current_user(request)
    with database() as db:
        if demo_session(request) or db.execute('SELECT 1 FROM demo_access WHERE user_id=?', (user['id'],)).fetchone():
            raise HTTPException(403, 'Les rappels ne sont pas disponibles sur les comptes de démonstration.')
    return user


async def reminder_body(request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Requête invalide.')
    return body


@app.get('/api/reminders/config')
def reminder_config(request: Request):
    user = reminder_user(request)
    with database() as db:
        dismissed = bool(db.execute('SELECT 1 FROM reminder_offers WHERE user_id=?', (user['id'],)).fetchone())
    return {'available': bool(reminder_subject()), 'publicKey': reminders.public_key(DATA/'push-vapid.pem') if reminder_subject() else '',
            'offered': dismissed}


@app.post('/api/reminders/offer-dismiss')
def reminder_offer(request: Request):
    user = reminder_user(request)
    with database() as db:
        db.execute('INSERT OR IGNORE INTO reminder_offers VALUES(?)', (user['id'],))
    return {'ok': True}


@app.post('/api/reminders/status')
async def reminder_status(request: Request):
    user = reminder_user(request)
    body = await reminder_body(request)
    endpoint = body.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise HTTPException(400, 'Appareil invalide.')
    with database() as db:
        row = db.execute('SELECT hour,minute,days,timezone FROM push_subscriptions WHERE endpoint_hash=? AND user_id=? AND session_hash=?',
                         (reminders.digest(endpoint), user['id'], token_hash(request.cookies.get(COOKIE, '')))).fetchone()
    return {'enabled': bool(row), **({'hour': row['hour'], 'minute': row['minute'], 'days': json.loads(row['days']), 'timezone': row['timezone']} if row else {})}


@app.post('/api/reminders/subscribe')
async def reminder_subscribe(request: Request):
    user = reminder_user(request)
    if not reminder_subject():
        raise HTTPException(503, 'Les rappels ne sont pas encore disponibles sur ce serveur.')
    body = await reminder_body(request)
    try:
        with database() as db:
            reminders.subscribe(db, user['id'], token_hash(request.cookies.get(COOKIE, '')), body)
    except (ValueError, TypeError):
        raise HTTPException(400, 'Vérifiez l’heure, les jours et l’abonnement de ce téléphone.')
    return {'ok': True}


@app.post('/api/reminders/disable')
async def reminder_disable(request: Request):
    user = reminder_user(request)
    body = await reminder_body(request)
    endpoint = body.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise HTTPException(400, 'Appareil invalide.')
    with database() as db:
        db.execute('DELETE FROM push_subscriptions WHERE endpoint_hash=? AND user_id=?', (reminders.digest(endpoint), user['id']))
    return {'ok': True}


@app.post('/api/reminders/test')
async def reminder_test(request: Request):
    user = reminder_user(request)
    if not reminder_subject():
        raise HTTPException(503, 'Les rappels sont indisponibles.')
    body = await reminder_body(request)
    endpoint = body.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise HTTPException(400, 'Appareil invalide.')
    now = time.time()
    with database() as db:
        row = db.execute('SELECT * FROM push_subscriptions WHERE endpoint_hash=? AND user_id=? AND session_hash=?',
                         (reminders.digest(endpoint), user['id'], token_hash(request.cookies.get(COOKIE, '')))).fetchone()
        if not row:
            raise HTTPException(404, 'Activez d’abord le rappel sur cet appareil.')
        previous = db.execute('SELECT at FROM push_test_limits WHERE user_id=?', (user['id'],)).fetchone()
        if previous and now-previous['at'] < 60:
            raise HTTPException(429, 'Attendez une minute avant un autre test.')
        db.execute('INSERT INTO push_test_limits VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET at=excluded.at', (user['id'], now))
    try:
        await asyncio.to_thread(reminders.send, dict(row), DATA/'push-vapid.pem', reminder_subject(), True)
    except reminders.WebPushException as exc:
        if getattr(exc.response, 'status_code', None) in (404, 410):
            with database() as db:
                db.execute('DELETE FROM push_subscriptions WHERE endpoint_hash=?', (reminders.digest(endpoint),))
        raise HTTPException(503, 'Test non distribué. Réactivez le rappel si nécessaire.')
    except Exception:
        raise HTTPException(503, 'Test indisponible. Réessayez plus tard.')
    return {'ok': True, 'message': 'Test accepté par le service de notifications. Vérifiez votre téléphone.'}


def require_admin(request):
    user = current_user(request)
    with database() as db:
        if demo_session(request) or not admin_stats.is_admin(db, user['id']):
            raise HTTPException(403, 'Cet espace est réservé à l’administrateur.')
    return user


@app.get('/api/admin/stats')
def administration_stats(request: Request):
    require_admin(request)
    with database() as db:
        return admin_stats.snapshot(db)


@app.get('/admin.html')
def administration_page(request: Request):
    try:
        require_admin(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse('/login.html', status_code=303)
        raise
    return FileResponse(ROOT / 'static' / 'admin.html')


def validate_photo(value, user_id):
    if not value:
        return ''
    if not isinstance(value, str) or not value.startswith('/api/photo/'):
        raise HTTPException(400, 'Photo invalide.')
    photo_id = value.removeprefix('/api/photo/')
    if not PHOTO_ID.fullmatch(photo_id) or not (DATA / 'photos' / user_id / photo_id).is_file():
        raise HTTPException(400, 'Cette photo ne se trouve pas dans votre compte.')
    return value


@app.put('/api/state')
async def put_state(request: Request):
    user = current_user(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, 'Requête invalide.')
    if not isinstance(body, dict):
        raise HTTPException(400, 'Requête invalide.')
    settings, items = body.get('settings'), body.get('items')
    if not isinstance(settings, dict) or not isinstance(items, list) or len(items) > 300:
        raise HTTPException(400, 'Garde-robe invalide (300 pièces maximum).')
    clean_items = []
    ids = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('category'), str) or item.get('category') not in CATEGORIES or type(item.get('id')) is not int or item['id'] in ids:
            raise HTTPException(400, 'Pièce invalide.')
        ids.add(item['id'])
        occasions = item.get('occasions', [])
        if not isinstance(occasions, list):
            raise HTTPException(400, 'Occasions invalides.')
        try:
            catalogue_fields = canonical_fields(item)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        photo = validate_photo(item.get('photo', ''), user['id'])
        clean_items.append({'id': item['id'], **catalogue_fields, 'category': item['category'], 'color': str(item.get('color', ''))[:30], 'photo': photo, 'sourcePhoto': validate_photo(item.get('sourcePhoto', ''), user['id']), 'cutoutBounds': clean_cutout_bounds(item.get('cutoutBounds')), 'fitting': clean_fitting(item.get('fitting'), photo), 'cutout': item.get('cutout') is True, 'availability': item.get('availability') if item.get('availability') in ['available','laundry','stored'] else 'available', 'occasions': [o for o in occasions if isinstance(o, str) and o in OCCASIONS][:len(OCCASIONS)]})
    clean_settings = {k: str(settings.get(k, ''))[:limit] for k, limit in {'name': 100, 'city': 100, 'style': 50, 'hair': 100, 'avatarSkin': 20, 'budget': 20}.items()}
    clean_settings['experienceVersion'] = 1
    clean_settings['locationMode'] = 'manual' if settings.get('locationMode') == 'manual' else 'auto'
    clean_settings['profilePhoto'] = validate_photo(settings.get('profilePhoto', ''), user['id'])
    h = settings.get('heightCm')
    clean_settings['heightCm'] = h if type(h) in (int, float) and 100 <= h <= 230 else None
    clean_settings['clothingSize'] = str(settings.get('clothingSize', ''))[:30]
    clean_settings['onboardingDone'] = settings.get('onboardingDone') is True
    clean_settings['personPhoto'] = validate_photo(settings.get('personPhoto', ''), user['id'])
    clean_settings['avatar'] = clean_avatar(settings.get('avatar'))
    clean_settings['lookView'] = 'avatar' if settings.get('lookView') == 'avatar' else 'pieces'
    prefs = settings.get('preferences', {})
    if not isinstance(prefs, dict): prefs = {}
    clean_settings['preferences'] = {k: prefs.get(k) if prefs.get(k) in values else values[0] for k, values in {'fit':['any','ample','ajuste'], 'shoes':['any','flat'], 'length':['any','long'], 'cold':['normal','sensitive','warm']}.items()}
    clean_settings['formality'] = settings.get('formality') if settings.get('formality') in ['auto','casual','smart'] else 'auto'
    favorites = settings.get('favorites', [])
    clean_settings['favorites'] = [x[:250] for x in favorites if isinstance(x,str)][-100:] if isinstance(favorites,list) else []
    worn = settings.get('worn', [])
    clean_settings['worn'] = [{'key':str(x.get('key',''))[:250], 'ids':[i for i in x.get('ids',[]) if type(i) is int][:6], 'at':x['at']} for x in (worn if isinstance(worn,list) else [])[-100:] if isinstance(x,dict) and isinstance(x.get('ids'),list) and type(x.get('at')) in (int,float)]
    feedback = settings.get('feedback', [])
    clean_settings['feedback'] = [{'key': str(x.get('key', ''))[:250], 'vote': x['vote'], 'reason': str(x.get('reason',''))[:80], 'features': [f[:110] for f in x.get('features', [])[:36] if isinstance(f, str)], 'at': x.get('at', 0) if type(x.get('at', 0)) in {int, float} else 0} for x in (feedback if isinstance(feedback, list) else [])[-150:] if isinstance(x, dict) and type(x.get('vote')) is int and x.get('vote') in {-1, 1} and isinstance(x.get('features', []), list)]
    with database() as db:
        db.execute('INSERT INTO wardrobes(user_id,settings,items) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings,items=excluded.items', (user['id'], json.dumps(clean_settings), json.dumps(clean_items)))
    return {'ok': True}


@app.delete('/api/state')
def clear_state(request: Request):
    user = current_user(request)
    # Clear references first: retries never leave a wardrobe pointing to removed files.
    with database() as db:
        db.execute('UPDATE wardrobes SET settings=?,items=? WHERE user_id=?', ('{}', '[]', user['id']))
    directory = DATA / 'photos' / user['id']
    if directory.exists():
        for photo in directory.iterdir():
            if photo.is_file():
                photo.unlink(missing_ok=True)
    return {'ok': True}


@app.put('/api/photo/{photo_id}')
async def upload_photo(photo_id: str, request: Request):
    user = current_user(request)
    if not PHOTO_ID.fullmatch(photo_id):
        raise HTTPException(400, 'Identifiant photo invalide.')
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 25_000_000:
            raise HTTPException(413, 'Photo trop volumineuse (25 Mo maximum).')
        chunks.append(chunk)
    try:
        image = Image.open(io.BytesIO(b''.join(chunks)))
        image.load()
        image = ImageOps.exif_transpose(image)
        if image.width * image.height > 25_000_000:
            raise ValueError()
        image.thumbnail((1800, 1800))
        image = image.convert('RGBA')
        output = io.BytesIO()
        image.save(output, format='PNG')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, 'Photo non reconnue. Utilisez un fichier JPEG ou PNG.')
    directory = DATA / 'photos' / user['id']
    directory.mkdir(exist_ok=True)
    existing = list(directory.iterdir())
    if len(existing) >= 800 or sum(p.stat().st_size for p in existing) + len(output.getvalue()) > 300_000_000:
        raise HTTPException(413, 'Votre espace photo est plein. Supprimez des pièces avant de réessayer.')
    destination = directory / photo_id
    if destination.exists():
        raise HTTPException(409, 'Une photo possède déjà cet identifiant.')
    with destination.open('xb') as photo:
        photo.write(output.getvalue())
    return {'ok': True}


@app.get('/api/photo/{photo_id}')
def get_photo(photo_id: str, request: Request):
    user = current_user(request)
    if not PHOTO_ID.fullmatch(photo_id):
        raise HTTPException(404, 'Photo introuvable.')
    path = DATA / 'photos' / user['id'] / photo_id
    if not path.is_file():
        raise HTTPException(404, 'Photo introuvable.')
    return FileResponse(path, media_type='image/png')


@app.delete('/api/photo/{photo_id}')
def delete_photo(photo_id: str, request: Request):
    user = current_user(request)
    if not PHOTO_ID.fullmatch(photo_id):
        raise HTTPException(400, 'Photo invalide.')
    with database() as db:
        row = db.execute('SELECT settings,items FROM wardrobes WHERE user_id=?', (user['id'],)).fetchone()
    if row and '/api/photo/' + photo_id in row['settings'] + row['items']:
        raise HTTPException(409, 'Cette photo est encore utilisée dans votre garde-robe.')
    (DATA / 'photos' / user['id'] / photo_id).unlink(missing_ok=True)
    return {'ok': True}


@app.get('/api/tryon')
def tryon(request: Request):
    current_user(request)
    return {'available': False}


async def discover_inspiration(description):
    """Short independent searches; a slow look never locks all the other cards."""
    global INSPIRATION_COOLDOWN
    queries = search_queries(description['rows'])
    async with httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
        async def search(query):
            global INSPIRATION_COOLDOWN
            async with INSPIRATION_SEMAPHORE:
                if INSPIRATION_COOLDOWN > time.time():
                    return [], False
                try:
                    response = await client.get('https://api.openverse.org/v1/images/', params={
                        'q': query, 'page_size': 40, 'mature': 'false',
                        'category': 'photograph', 'license': 'cc0,by,by-sa',
                    }, headers={'User-Agent': 'OutfitMatcher/2.0'})
                    if response.status_code == 429:
                        INSPIRATION_COOLDOWN = time.time() + 120
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid image response')
                    return ranked_web_candidates(payload, description['rows']), True
                except (httpx.HTTPError, ValueError, TypeError):
                    return [], False
        batches = await asyncio.gather(*(search(query) for query in queries))
    images = merge_inspiration([image for batch, _ in batches for image in batch])
    status = 'ok' if images else 'no_match' if any(ok for _, ok in batches) else 'unavailable'
    cache_key = description['query']
    INSPIRATION_CACHE[cache_key] = {'expires': time.time() + (21600 if images else 90), 'status': status, 'images': images}
    while len(INSPIRATION_CACHE) > 128:
        INSPIRATION_CACHE.popitem(last=False)
    return INSPIRATION_CACHE[cache_key]


def merge_inspiration(images):
    unique = {}
    for image in sorted(images, key=lambda image: image.get('score', 0), reverse=True):
        identity = image['thumbnail'] + (json.dumps(image['region'], sort_keys=True) if image.get('region') else '')
        unique.setdefault(identity, image)
    return list(unique.values())[:8]


@app.get('/api/inspiration')
async def get_inspiration(request: Request, ids: str = '', expand: bool = False):
    """Search public photo descriptions using only the signed-in wardrobe's garment metadata."""
    user = current_user(request)
    if not re.fullmatch(r'\d{1,16}(?:,\d{1,16}){1,9}', ids):
        raise HTTPException(400, 'Choisissez de 2 à 10 pièces.')
    requested = [int(value) for value in ids.split(',')]
    if len(set(requested)) != len(requested):
        raise HTTPException(400, 'Pièces invalides.')
    with database() as db:
        row = db.execute('SELECT items FROM wardrobes WHERE user_id=?', (user['id'],)).fetchone()
    saved = {item['id']: item for item in json.loads(row['items'])} if row else {}
    if any(item_id not in saved for item_id in requested):
        raise HTTPException(404, 'Pièces introuvables dans votre garde-robe.')
    description = inspiration_profile([saved[item_id] for item_id in requested])
    result = {'query': description['query'], 'search_url': description['search_url'], 'images': [], 'missing': description['missing']}
    local = integrated_candidates(description['rows'])
    indexed = reference_candidates(description['rows']) if os.environ.get('ALLOW_EDITORIAL_REFERENCES') == '1' else []
    if local and not expand:
        return {**result, 'status': 'ok', 'images': local, 'method': 'integrated'}
    # Real indexed web photographs are ready immediately, even if one old title is ambiguous.
    if indexed and not expand:
        return {**result, 'status': 'ok', 'images': indexed, 'method': 'indexed'}
    cache_key = description['query']
    cached = INSPIRATION_CACHE.get(cache_key)
    if not cached or cached['expires'] <= time.time() or expand and not cached.get('images'):
        task = INSPIRATION_PENDING.get(cache_key)
        if not task:
            # Bound uncached work without affecting wardrobe/account endpoints.
            if len(INSPIRATION_PENDING) >= 12:
                return {**result, 'status': 'ok' if indexed or local else 'unavailable', 'images': indexed + local}
            task = asyncio.create_task(discover_inspiration(description))
            INSPIRATION_PENDING[cache_key] = task
            task.add_done_callback(lambda finished: INSPIRATION_PENDING.pop(cache_key, None))
        try:
            cached = await asyncio.wait_for(asyncio.shield(task), timeout=16)
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError):
            cached = {'status': 'unavailable', 'images': []}
    else:
        INSPIRATION_CACHE.move_to_end(cache_key)
    images = merge_inspiration(indexed + cached['images'] + local)
    return {**result, 'status': 'ok' if images else cached['status'], 'images': images, 'method': 'web' if cached['images'] else 'indexed'}


@app.get('/health')
def health():
    with database() as db:
        db.execute('SELECT count(*) FROM users').fetchone()
    try:
        with tempfile.TemporaryFile(dir=DATA) as probe:
            probe.write(b'health')
    except OSError:
        raise HTTPException(503, 'Stockage indisponible.')
    return {'status': 'ok', 'version': '28'}


@app.get('/')
def home():
    # Une arrivée sur le lien public commence toujours par la connexion.
    return RedirectResponse('/login.html', status_code=303)


@app.get('/app.html')
def wardrobe_page(request: Request):
    try:
        current_user(request)
    except HTTPException:
        return RedirectResponse('/login.html#reminder' if request.query_params.get('reminder') == '1' else '/login.html', status_code=303)
    return FileResponse(ROOT / 'static' / 'app.html')


app.mount('/', StaticFiles(directory=ROOT / 'static'), name='static')
